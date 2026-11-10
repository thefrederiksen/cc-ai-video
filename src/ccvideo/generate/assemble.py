"""The assembler: a segment script plus a folder of frames becomes one narrated mp4.

This is the core of the GENERATE half, and it is deliberately not clever. Each segment is
rendered on its own - one picture, one block of narration, a beat at each end and a slow
alternating zoom so it does not read as a dead slideshow - and the segments are concatenated
with the chapters written into the container. The zoom frames are drawn here, not by
ffmpeg's zoompan, which shook (see zoom_frame and ccvideo.motion).

TWO THINGS THAT LOOK LIKE DETAILS AND ARE NOT

*Frames are letterboxed, never cropped.* Each image is fitted inside a box small enough that
the zoom can run to its maximum without ever pushing interface off the frame. A crop that eats
the edge of a menu is the kind of defect that survives every machine check.

*The zoom alternates direction by segment index.* In one direction throughout, a long video
reads as a single slow drift and viewers report it as "drifting". Alternating reads as
intentional.

The output is deterministic. Given the same script, the same frames and a warm voice cache,
this produces a byte-identical file - which is what makes it possible to prove a change to
this module changed nothing.
"""

import os
import re
from pathlib import Path

from PIL import Image

from .. import motion, subtitles, targets
from ..draw import render_card
from ..level import publish_chain
from ..narrate import speak
from ..shell import duration, run, stream_frames

ZOOM = 1.05         # maximum zoom; the image is pre-fitted so nothing is ever cropped
LEAD_IN = 0.4       # seconds of held picture before the voice starts
TAIL = 0.7          # seconds of held picture after the voice stops

# With captions on, the bottom of the frame is a strip of its own and the picture is fitted
# ABOVE it, so a caption never covers the interface being shown. A fraction of the height,
# so it scales with the frame; rounded to an even number of pixels for the encoder.
CAPTION_STRIP = 0.11
CAPTION_SIZE = 52 / 1080.0      # caption type size as a fraction of the frame height


def picture_box(target, captions):
    """(width, height) the picture is fitted into: the whole frame, or the frame less the
    caption strip."""
    w, h = target["width"], target["height"]
    if not captions:
        return w, h
    strip = int(round(h * CAPTION_STRIP / 2.0)) * 2
    return w, h - strip


class Build:
    """Where the intermediate work for one build lives.

    The audio directory is the expensive one and is deliberately separate from the throwaway
    directories: cards and segment mp4s can be deleted at any time and cost seconds to rebuild,
    while audio costs money.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.audio = self.root / "audio"
        self.cards = self.root / "cards"
        self.segments = self.root / "segments"

    def make(self):
        for d in (self.audio, self.cards, self.segments):
            d.mkdir(parents=True, exist_ok=True)
        return self


def picture_for(segment, script, brand, target, build, stem, shots_dir):
    """The still this segment shows, and a human label for the log."""
    if segment.kind in ("image", "clip"):
        return os.path.join(shots_dir, segment.image), segment.image
    path = build.cards / ("card-%s-%s.png" % (stem, segment.id))
    if segment.kind == "card":
        kicker, title, sub = segment.card
        render_card(brand, target["width"], target["height"], kicker, title, sub,
                    script.footer, path)
        return str(path), "CARD %s" % title
    if segment.kind == "shot":
        # Drawn so nobody can mistake it for a viewer's card, and so a still pulled from the
        # render says what it is on its own, out of context.
        render_card(brand, target["width"], target["height"],
                    "PRODUCTION PLACEHOLDER", "SHOT TO CAPTURE", segment.shot,
                    script.footer, path)
        return str(path), "SHOT %s" % segment.shot
    raise SystemExit("segment %s has no picture" % segment.id)


def zoom_at(n, frames, index):
    """The zoom factor of frame n: 1 to ZOOM (or back) over the segment, eased at both ends so
    it neither starts nor stops with a jolt. Even segments zoom in, odd ones out."""
    t = n / float(frames - 1)
    t = t * t * (3 - 2 * t)
    return 1 + (ZOOM - 1) * t if index % 2 == 0 else ZOOM - (ZOOM - 1) * t


def letterbox(picture, w, h, bg, room=ZOOM):
    """The picture fitted on a w x h canvas of the brand background, small enough that a zoom
    of `room` never pushes any of it off the frame."""
    img = Image.open(picture).convert("RGB")
    scale = min(w / room / img.width, h / room / img.height)
    # Round DOWN, then to the canvas's parity, so the picture is never wider than the zoom
    # leaves room for and sits exactly centred - not half a pixel to one side.
    fw, fh = int(img.width * scale), int(img.height * scale)
    size = (max(1, fw - (w - fw) % 2), max(1, fh - (h - fh) % 2))
    img = img.resize(size, Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), tuple(bg[:3]))
    canvas.paste(img, ((w - size[0]) // 2, (h - size[1]) // 2))
    return canvas


def zoom_frame(canvas, z):
    """The canvas magnified z times about its centre, at SUB-PIXEL precision.

    This is the reason the assembler draws its own frames. ffmpeg's zoompan snaps the crop to
    whole pixels on every frame, so a slow zoom stands still for several frames and then jumps:
    measured on a shipped 23 Oct 2026 tutorial, one corner of a screenshot sat still on 84 of
    118 frames and then leapt up to 1.4 px, and the viewer reported that "the screen moves
    around a lot". A fractional resize box moves it an even 0.03 px a frame instead.

    The drawing itself is motion.draw, the one camera routine every zoom and pan in ccvideo
    goes through (`ccvideo move` uses it for footage), so the two can never drift apart.
    """
    return motion.draw(canvas, z)


def _audio_filter(encode):
    layout = "mono" if encode["channels"] == "1" else "stereo"
    return ("adelay=delays=%d:all=1,apad=pad_dur=%.2f,"
            "aformat=channel_layouts=%s:sample_rates=%s"
            % (int(LEAD_IN * 1000), TAIL, layout, encode["sample_rate"]))


def _audio_input(audio, window):
    """The narration as an ffmpeg input: the whole file, or just its speech window."""
    if window is None:
        return ["-i", str(audio)]
    return ["-ss", "%.3f" % window[0], "-t", "%.3f" % window[1], "-i", str(audio)]


def segment_seconds(audio, window=None):
    """How long a segment runs: its narration plus a beat at each end."""
    return (window[1] if window else duration(audio)) + LEAD_IN + TAIL


def render_segment(picture, audio, index, out, brand, target, window=None, box=None):
    """One segment: a still, its narration, a beat at each end and a slow zoom.

    `window` is (start, seconds) of the narration file to use, for a pre-made file; `box` is
    the (width, height) at the top of the frame the picture lives in, when captions take the
    strip below it. Without either this is exactly the renderer it always was."""
    encode = target["encode"]
    w, h, fps = target["width"], target["height"], encode["fps"]

    seconds = segment_seconds(audio, window)
    frames = max(2, int(round(seconds * fps)))
    if box is None or tuple(box) == (w, h):
        canvas = letterbox(picture, w, h, brand["bg"])
        frame = lambda n: zoom_frame(canvas, zoom_at(n, frames, index))  # noqa: E731
    else:
        canvas = letterbox(picture, box[0], box[1], brand["bg"])
        ground = Image.new("RGB", (w, h), tuple(brand["bg"][:3]))

        def frame(n):
            full = ground.copy()
            full.paste(zoom_frame(canvas, zoom_at(n, frames, index)), ((w - box[0]) // 2, 0))
            return full

    cmd = (["ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h),
            "-r", str(fps), "-i", "-"] + _audio_input(audio, window)
           + ["-af", _audio_filter(encode)]
           + targets.video_args(encode)
           + targets.audio_args(encode)
           + ["-t", "%.3f" % seconds, "-movflags", "+faststart", str(out)])
    stream_frames(cmd, (frame(n) for n in range(frames)))
    return seconds


def render_clip_segment(clip, clip_fit, audio, out, brand, target, window=None, box=None):
    """One segment over a recorded clip: the part of the clip `clip_fit` chose, at its speed,
    its last frame held to the end, fitted (never cropped) into `box`, under the narration.
    The clip's own sound, if it has any, is not used."""
    encode = target["encode"]
    w, h, fps = target["width"], target["height"], encode["fps"]
    bw, bh = box or (w, h)
    seconds = segment_seconds(audio, window)
    colour = "0x%02x%02x%02x" % tuple(brand["bg"][:3])
    # The hold is padded past the segment's end and the output is cut at -t, so frame rounding
    # can never leave the last frames of a segment black.
    video = ("[0:v]setpts=(PTS-STARTPTS)/%.6f,fps=%d,"
             "scale=%d:%d:force_original_aspect_ratio=decrease:force_divisible_by=2"
             ":flags=lanczos,setsar=1,"
             "pad=%d:%d:(%d-iw)/2:(%d-ih)/2:color=%s,"
             "tpad=stop_mode=clone:stop_duration=%.3f[v]"
             % (clip_fit.speed, fps, bw, bh, w, h, w, bh, colour, clip_fit.hold + 1.0))
    cmd = (["ffmpeg", "-y", "-loglevel", "error",
            "-ss", "%.3f" % clip_fit.start, "-t", "%.3f" % clip_fit.used, "-i", str(clip)]
           + _audio_input(audio, window)
           + ["-filter_complex", video + ";[1:a]" + _audio_filter(encode) + "[a]",
              "-map", "[v]", "-map", "[a]"]
           + targets.video_args(encode)
           + targets.audio_args(encode)
           + ["-t", "%.3f" % seconds, "-movflags", "+faststart", str(out)])
    run(cmd)
    return seconds


def write_chapters(segments, timings, path):
    """One chapter per segment carrying a CHAPTER: line, running to the next one."""
    marks = [(t[1], s.chapter) for s, t in zip(segments, timings) if s.chapter]
    end = timings[-1][1] + timings[-1][2]
    if not marks or marks[0][0] > 0:
        marks.insert(0, (0.0, "Start"))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(";FFMETADATA1\n")
        for i, (start, title) in enumerate(marks):
            stop = marks[i + 1][0] if i + 1 < len(marks) else end
            safe = re.sub(r"([=;#\\])", r"\\\1", title)
            fh.write("[CHAPTER]\nTIMEBASE=1/1000\nSTART=%d\nEND=%d\ntitle=%s\n"
                     % (round(start * 1000), round(stop * 1000), safe))
    return path


def assemble(script, shots_dir, out_path, brand, target, voice, build,
             allow_synthesis=True, on_segment=None, audio_dir=None, captions=False,
             max_speed=None, words_model="base.en", narration=None, read_segments=None):
    """Build the whole video. Returns (timings, total seconds).

    `on_segment` is called with (segment, seconds, label, was_synthesised) as each one lands,
    so a caller can report progress without this function knowing how it wants to.

    `audio_dir` takes the narration from pre-made files, one per segment, instead of `voice`;
    `narration` takes it from ONE continuous recording, split at the pauses between segments
    by matching each segment's TEXT to the heard words (see generate/timing.py).
    `read_segments` is every segment that was READ into that recording, when only some of them
    are built (--only): the split is made against all of them, so the speech of a segment left
    out never lands in a neighbour's slice. `captions` burns the segment TEXT into a strip under the picture,
    timed by the narration's heard words, and writes the same cues as an .srt beside the mp4.
    `max_speed` caps how fast a clip may be played to fit its narration (generate/fit.py).
    """
    from . import fit as fitlib
    from . import timing

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    build.make()
    stem = out_path.stem
    box = picture_box(target, captions)
    words_dir = build.root / "words"
    max_speed = fitlib.DEFAULT_MAX_SPEED if max_speed is None else max_speed

    # Every pre-made file is found before anything renders: a missing one is a stop, not a
    # half-built video.
    premade = ({s.id: timing.find_audio(s, audio_dir) for s in script.segments}
               if audio_dir else {})
    # One continuous recording: transcribed once, split at the pauses between segments, and
    # every slice found before anything renders.
    slices = {}
    if narration:
        whole = timing.heard_words(narration, words_dir, model=words_model)
        read = read_segments or script.segments
        for segment, piece in zip(read, timing.split_narration(
                read, whole, timing.audio_length(narration))):
            slices[segment.id] = piece

    timings, elapsed, cue_words = [], 0.0, []
    for index, segment in enumerate(script.segments):
        picture, label = picture_for(segment, script, brand, target, build, stem, shots_dir)
        window, heard = None, None
        if narration:
            start, seconds, heard = slices[segment.id]
            audio, synthesised, window = Path(narration), False, (start, seconds)
        elif audio_dir:
            audio, synthesised = premade[segment.id], False
            heard = timing.heard_words(audio, words_dir, model=words_model)
            window = timing.speech_window(heard, timing.audio_length(audio))
        else:
            audio, synthesised = speak(segment.text, voice, build.audio, segment.id,
                                       allow_synthesis=allow_synthesis)
            if captions:
                heard = timing.heard_words(audio, words_dir, model=words_model)
        # A pre-made file is checked against its TEXT before it renders, captions or not: the
        # wrong file under a segment must stop the build, not play.
        aligned = (timing.align(segment.text, heard, segment.id)
                   if captions or audio_dir else None)
        piece = build.segments / ("seg-%s-%s.mp4" % (stem, segment.id))
        if segment.kind == "clip":
            seconds = segment_seconds(audio, window)
            clip_fit = fitlib.fit(duration(picture), seconds, segment.clip_start,
                                  segment.clip_end, max_speed)
            render_clip_segment(picture, clip_fit, audio, piece, brand, target, window, box)
            label = "CLIP %s %s" % (segment.image, clip_fit.describe())
            if clip_fit.trimmed > 0.005:
                print("  TRIMMED segment %s: %s needs more than x%.2f to fit %.1fs of "
                      "narration, so its last %.1fs are not shown. Set CLIP-END, or narrate "
                      "longer." % (segment.id, segment.image, max_speed, seconds,
                                   clip_fit.trimmed), flush=True)
        else:
            seconds = render_segment(picture, audio, index, piece, brand, target, window,
                                     box if captions else None)
        if captions:
            shift = elapsed + LEAD_IN - (window[0] if window else 0.0)
            cue_words.append(([{"start": word["start"] + shift, "end": word["end"] + shift,
                                "word": word["word"]} for word in aligned],
                              elapsed + seconds))
        timings.append((segment.id, elapsed, seconds, label))
        elapsed += seconds
        if on_segment:
            on_segment(segment, seconds, label, synthesised)

    listing = build.segments / ("concat-%s.txt" % stem)
    with open(listing, "w", encoding="utf-8") as fh:
        for segment in script.segments:
            piece = build.segments / ("seg-%s-%s.mp4" % (stem, segment.id))
            fh.write("file '%s'\n" % str(piece).replace("\\", "/"))

    chapters = write_chapters(script.segments, timings,
                              build.segments / ("chapters-%s.txt" % stem))
    joined = build.segments / ("joined-%s.mp4" % stem)
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(joined)])
    if not captions:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-i", str(chapters),
             "-map_metadata", "1", "-c", "copy", "-metadata", "title=%s" % script.title,
             "-movflags", "+faststart", str(out_path)])
        return timings, duration(out_path)

    ass = write_captions(cue_words, elapsed, target, build.segments / ("captions-%s.ass" % stem),
                         out_path.with_suffix(".srt"))
    # The picture is re-encoded once, to burn the captions; the narration is copied untouched.
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-i", str(chapters),
         "-map", "0:v", "-map", "0:a", "-map_metadata", "1",
         "-vf", "subtitles='%s'" % subtitles.ass_path_for_filter(ass)]
        + targets.video_args(target["encode"])
        + ["-c:a", "copy", "-metadata", "title=%s" % script.title,
           "-movflags", "+faststart", str(out_path)])
    return timings, duration(out_path)


def write_captions(segment_words, total, target, ass_path, srt_path):
    """The strip captions for a whole video: burned-in .ass and a sidecar .srt, the same cues.

    `segment_words` is one (timed words, segment end) per segment. Cues are built, held and
    clamped segment by segment, so a caption never runs across a seam: merged across one, the
    end of one picture's sentence and the start of the next sat on screen together over the
    cut, and a short last cue held for reading ran over the next segment's first frames.

    Strip style - up to seven words, broken at punctuation and pauses - sits in the band under
    the picture. The sidecar carries exactly those cues, so what a viewer reads with the sound
    off and what a player shows from the .srt never disagree."""
    from .. import cues as cuelib
    w, h = target["width"], target["height"]
    cues = []
    for words, segment_end in segment_words:
        built = cuelib.build(words, "strip")
        for cue in built:
            cue["end"] = min(cue["end"], segment_end - 0.02)
        kept, dropped = cuelib.hold_into_gaps(built, until=min(total, segment_end))
        for cue in dropped:
            print("  dropped an unreadable caption fragment at %.2fs: %r"
                  % (cue["start"], cue["plain"]), flush=True)
        cues.extend(kept)
    strip = h - picture_box(target, True)[1]
    # Sized by the frame's SHORT side, so a phone frame's line fits its width as a desktop one
    # does; a line still too long for the width wraps instead of running off the edge.
    size = int(round(CAPTION_SIZE * min(w, h)))
    # Centre one line of type in the strip: the baseline sits a little under halfway down it.
    baseline = h - int((strip - size) / 2.0)
    subtitles.write_ass(cues, ass_path, w, h, size=size, baseline=baseline, glow=False,
                        wrap=0)
    subtitles.write_srt(cues, srt_path)
    print("  %d captions, sidecar %s" % (len(cues), Path(srt_path).name), flush=True)
    return ass_path


def normalise_loudness(path, lufs, encode):
    """Bring a finished file to a target loudness, IN PLACE, without touching the picture.

    The assembler does no levelling of its own, and a shipped short measured -23.3 LUFS -
    about five decibels under the one level this factory publishes at, so it played noticeably
    quieter than everything around it in a feed. The video stream is copied, so the picture is
    untouched and only the audio is re-encoded; chapters are carried across explicitly, because
    a re-mux that drops them is the classic way this goes wrong.

    ONE FIXED GAIN, FOUND BY MEASUREMENT, with the ceiling held - and NOTHING AT ALL if the
    audio is already at the number, which is the case for a video built on a narration. That
    no-op keeps the assembler's byte-for-byte determinism where it used to be lost. See level.py.
    """
    path = Path(path)
    # The trials are encoded exactly as this re-encode will be, so the ceiling holds in the result.
    chain = publish_chain(path, lufs, encode=encode)
    if chain is None:
        return path
    temp = path.with_name(path.stem + ".levelled" + path.suffix)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
         "-af", chain,
         "-c:v", "copy", "-map_chapters", "0", "-map_metadata", "0",
         "-c:a", "aac", "-b:a", encode["audio_kbps"],
         "-ar", encode["sample_rate"], "-ac", encode["channels"],
         "-movflags", "+faststart", str(temp)])
    os.replace(str(temp), str(path))
    return path


def write_timings(timings, path):
    """The per-segment table. Every caption track this library generates is built from it, so
    it is an output in its own right and not a log."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("segment  start     length  shown\n")
        for sid, start, seconds, label in timings:
            fh.write("%s      %02d:%02d:%02d  %6.1fs  %s\n"
                     % (sid, int(start // 3600), int(start // 60) % 60, int(start) % 60,
                        seconds, label))
    return path
