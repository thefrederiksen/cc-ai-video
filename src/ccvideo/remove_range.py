"""Take one or more time ranges out of a FINISHED video, at flat memory.

WHY THIS EXISTS. There was no command for it, so agents wrote their own:

    [0:v]trim=0:A[v0];[0:v]trim=B[v1];[0:a]atrim=0:A[a0];[0:a]atrim=B[a1];
    [v0][a0][v1][a1]concat=n=2:v=1:a=1

That feeds ONE decoded input to two branches. concat reads all of v0 before it reads v1, but
the decoder cannot pause one branch, so every frame meant for v1 is held in memory while v0 is
written. On an 872 s 1080p30 film it reached 34 GB and the kernel killed the whole Director
that had started it, with every session in it - twice, on 16 and 20 November 2026
(devthrottle_internal#3627, cc-ai-video#28).

HOW THIS AVOIDS IT. The source is opened ONCE PER KEPT PIECE, each input with its own -ss/-to,
so each piece has its own decoder and seeks to its own start. concat then pulls from one input
at a time and nothing waits in memory. Measured on a 10-minute 1080p file: 784 MB peak for a
10-minute output, 780 MB for a 2-minute one - the same as a plain transcode plus one decoder,
and it does not grow with the length.

THE AUDIO IS CUT FROM THE SOURCE BY TIMESTAMP, NEVER FROM A DECODED WAV. A WAV keeps the
samples and drops the timestamps, and an AAC track starts with priming that the timestamps
account for: on the Ritchie film a decoded WAV put every sound 26 ms early against the picture.
-ss/-to on the source itself kept the onsets where the picture has them (measured: an onset at
105.355 s by timestamp, 105.355 s through the seek, 105.328 s in the WAV).

THE AUDIO IS LEVELLED, NOT COPIED BLIND. The kept audio is joined first (audio only, cheap),
measured, and published at the one level in targets.py exactly as every other render is. Audio
already at the level passes through untouched (level.py is idempotent), so a cut does not
change the published level. Each seam gets a 10 ms fade so the join does not click; that is
far shorter than any word, unlike JOIN_FADE, which is a design fade between whole clips.

THE CHAPTERS MOVE WITH THE CUT. A finished film carries its chapters, and ffmpeg copies them
unchanged from the first input by default - so every chapter after a cut would start late by
the time removed. They are shifted here; a chapter wholly inside a removed range is dropped.
"""

import json
import os
import tempfile
from pathlib import Path

from . import budget, targets
from .level import publish_chain
from .shell import probe, run

# A de-click at each seam. Ten milliseconds is below anything a listener hears as a fade and
# well below the shortest spoken word, so it cannot take a word with it (see JOIN_FADE in
# targets.py for what a longer fade did).
DECLICK = 0.010
# Threads for each piece's DECODER. Each decoder holds a frame per thread, and ffmpeg's default
# is one per core: on a 32-core machine that alone took a 2-piece cut from 837 MB to 1026 MB.
# Two is plenty to keep ahead of the encoder. Each further piece then costs about 22 MB.
DECODE_THREADS = 2


def parse_range(text):
    """'113.9:115.0' -> (113.9, 115.0). Raises SystemExit with the reason on anything else."""
    a, sep, b = str(text).partition(":")
    if not sep:
        raise SystemExit("--range %r: write it as START:END in seconds, e.g. 113.9:115.0" % text)
    try:
        return float(a), float(b)
    except ValueError:
        raise SystemExit("--range %r: START and END must be numbers of seconds" % text)


def snap(ranges, fps, total=None):
    """Every bound moved to the nearest frame of the source: round(t * fps) / fps.

    The picture can only be cut on a frame; the sound can be cut on any sample. Left off the grid,
    each cut put the sound up to half a frame away from its picture, and the error added up: 13 ms
    for one cut and 73 ms after three, measured in review. Snapped, both are cut at the same instant
    and the chapters move by the same amount.

    THE END OF THE FILE IS NOT ON THE GRID. A container usually runs a little past its last frame
    (the Ritchie film is 872.097 s; its last frame starts at 872.067), so an end bound within one
    frame of `total` - before or after it - IS the end of the file and becomes `total`. Without
    this, a cut to the end was refused either as leaving a sliver or as running outside the file.
    The start needs no such rule: 0 is on the grid.
    """
    if not fps or fps <= 0:
        raise SystemExit("the source reports no frame rate, so its cuts cannot be put on frames")
    frame = 1.0 / fps
    snapped = []
    for a, b in ranges:
        a, b = round(a * fps) / fps, round(b * fps) / fps
        if total is not None and abs(total - b) < frame:
            b = total
        snapped.append((a, b))
    return snapped


def kept_pieces(ranges, total, min_piece=0.0):
    """The (start, end) pieces that remain when `ranges` are taken out of [0, total].

    The ranges must lie inside the file, run forwards, be sorted and not overlap. A range that
    breaks any of these is refused rather than corrected: a cut nobody asked for is worse than
    no cut. So is a kept piece shorter than `min_piece` (one frame): a range that stops within a
    frame of the start or end of the file would leave a sliver nothing can show.
    """
    if not ranges:
        raise SystemExit("remove-range needs at least one --range START:END")
    previous_end = 0.0
    for i, (a, b) in enumerate(ranges):
        if a < 0 or b > total + 1e-6:
            raise SystemExit("range %g:%g lies outside the file, which is %.3f s long"
                             % (a, b, total))
        if b <= a:
            raise SystemExit("range %g:%g ends before it starts, or is shorter than one frame"
                             % (a, b))
        if i and a < previous_end:
            raise SystemExit("range %g:%g overlaps or comes before the range before it; give "
                             "the ranges sorted and apart" % (a, b))
        previous_end = b
    pieces, cursor = [], 0.0
    for a, b in ranges:
        if a > cursor:
            pieces.append((cursor, a))
        cursor = b
    if cursor < total:
        pieces.append((cursor, total))
    if not pieces:
        raise SystemExit("those ranges remove the whole file")
    for start, end in pieces:
        if end - start < min_piece - 1e-9:
            raise SystemExit("the ranges leave %.3f-%.3f, shorter than one frame; take it out "
                             "with the range beside it or leave a whole frame" % (start, end))
    return pieces


def _inputs(src, pieces, total):
    """One -ss/-to input per piece: each gets its own decoder, and that is the whole point.

    A piece that runs to the end of the file gets no -to, so it takes whatever the input really
    holds rather than the container's stated duration.
    """
    args = []
    for start, end in pieces:
        args += ["-threads", str(DECODE_THREADS)]
        if start > 0:
            args += ["-ss", "%.6f" % start]
        if end < total:
            args += ["-to", "%.6f" % end]
        args += ["-i", str(src)]
    return args


def _audio_graph(pieces, total):
    """Concat each piece's audio, with a de-click fade on every side that meets a cut."""
    parts = []
    for i, (start, end) in enumerate(pieces):
        chain = "asetpts=PTS-STARTPTS"
        if start > 0:
            chain += ",afade=t=in:d=%.3f" % DECLICK
        if end < total:
            chain += ",afade=t=out:st=%.6f:d=%.3f" % (max(0.0, end - start - DECLICK), DECLICK)
        parts.append("[%d:a]%s[a%d]" % (i, chain, i))
    joined = "".join("[a%d]" % i for i in range(len(pieces)))
    return ";".join(parts) + ";%sconcat=n=%d:v=0:a=1[a]" % (joined, len(pieces))


def shift(t, ranges):
    """Where source time `t` lands after `ranges` are removed. Inside a range: its start."""
    removed = 0.0
    for a, b in ranges:
        if t >= b:
            removed += b - a
        elif t > a:
            return a - removed
        else:
            break
    return t - removed


def moved_chapters(chapters, ranges, length):
    """[(start, end, title)] in source seconds -> the same chapters after the cut.

    A chapter that the cut leaves with no length is dropped. Ends never pass the new length.
    """
    kept = []
    for start, end, title in chapters:
        s, e = shift(start, ranges), min(shift(end, ranges), length)
        if e - s >= 0.001:
            kept.append((s, e, title))
    return kept


def _chapters(src):
    out = run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(src)])
    rows = []
    for c in json.loads(out).get("chapters", []):
        rows.append((float(c["start_time"]), float(c["end_time"]),
                     (c.get("tags") or {}).get("title", "")))
    return rows


def _escape(text):
    for ch in "\\=;#\n":
        text = text.replace(ch, "\\" + ch)
    return text


def write_ffmetadata(chapters, path):
    lines = [";FFMETADATA1"]
    for start, end, title in chapters:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", "START=%d" % round(start * 1000),
                  "END=%d" % round(end * 1000), "title=%s" % _escape(title)]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_range(src, ranges, out, target_name="youtube-master"):
    """Write `out`: `src` with `ranges` [(start, end), ...] taken out.

    Returns (new duration, the ranges as cut - each bound on the source's frame grid). `out` is
    written under a sibling name and renamed only when the whole render succeeded, so a failed or
    killed run never leaves a half-written file where the finished one belongs.
    """
    src, out = Path(src).resolve(), Path(out).resolve()
    if src == out:
        raise SystemExit("--out must not be the input file: the input is read while the output "
                         "is written")
    target = targets.target(target_name)
    if target["audio_only"]:
        raise SystemExit("remove-range cuts a finished VIDEO; %s is an audio target"
                         % target_name)
    encode = target["encode"]
    shape = probe(src)
    if not shape["video"]:
        raise SystemExit("%s has no video stream" % src)
    total = shape["duration"]
    ranges = snap(ranges, shape["fps"], total)
    pieces = kept_pieces(ranges, total, min_piece=1.0 / shape["fps"])
    threads = ["-threads", str(budget.cores())]   # named, because the per-input -threads
    # make budget.ffmpeg leave a command alone, and the encoder must stay inside half the cores

    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(".%s.partial%s" % (out.stem, out.suffix))
    try:
        _render(src, pieces, ranges, total, shape, target, encode, threads, partial)
        os.replace(partial, out)
    finally:
        if partial.exists():
            partial.unlink()
    return probe(out)["duration"], ranges


def _render(src, pieces, ranges, total, shape, target, encode, threads, out):
    n = len(pieces)
    with tempfile.TemporaryDirectory(prefix="ccvideo-remove-") as tmp:
        tmp = Path(tmp)
        audio = None
        chain = None
        if shape["audio"]:
            # The kept audio, joined and lossless, so it can be measured before it is published.
            # Cut from the SOURCE, one input per piece, so each cut lands on the source's own
            # timestamps (see the module note on why not from a decoded WAV).
            audio = tmp / "kept.wav"
            run(["ffmpeg", "-y", "-loglevel", "error"] + _inputs(src, pieces, total)
                + ["-filter_complex", _audio_graph(pieces, total), "-map", "[a]",
                   "-c:a", "pcm_s16le", "-ar", encode["sample_rate"], "-ac", encode["channels"]]
                + threads + [str(audio)])
            if target["loudness"]:
                chain = publish_chain(audio, target["loudness"], encode=encode)

        inputs = _inputs(src, pieces, total)
        video_in = "".join("[%d:v:0]" % i for i in range(n))
        graph = "%sconcat=n=%d:v=1:a=0[v]" % (video_in, n)
        maps = ["-map", "[v]"]
        extra = []
        if audio is not None:
            extra += ["-i", str(audio)]
            graph += ";[%d:a]%s[a]" % (n, chain or "anull")
            maps += ["-map", "[a]"]
        chapters = _chapters(src)
        if chapters:
            # Ended on what is really there: the kept audio, or the pieces when there is none.
            # The container's stated duration runs past the sound (AAC padding), and a chapter
            # track ending there makes the file look as if its audio stopped early.
            length = (probe(audio)["duration"] if audio is not None
                      else sum(end - start for start, end in pieces))
            meta = tmp / "chapters.txt"
            write_ffmetadata(moved_chapters(chapters, ranges, length), meta)
            meta_index = n + (1 if audio is not None else 0)
            extra += ["-f", "ffmetadata", "-i", str(meta)]
            maps += ["-map_chapters", str(meta_index)]
        else:
            maps += ["-map_chapters", "-1"]
        run(["ffmpeg", "-y", "-loglevel", "error"] + inputs + extra
            + ["-filter_complex", graph] + maps + targets.video_args(encode)
            + (targets.audio_args(encode) if audio is not None else [])
            + ["-movflags", "+faststart"] + threads + [str(out)])
