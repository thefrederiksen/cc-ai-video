"""The narration's own clock: pre-made voice files, word timings, and caption alignment.

PRE-MADE AUDIO
The owner's trained voice runs on a GPU on another machine (the Mac mini), so a narration may
arrive as finished files - ONE PER SEGMENT - rather than being synthesised here. `--audio DIR`
points the assembler at them. A segment's file is the one its `AUDIO:` line names, or else the
one in DIR whose stem is the segment id (`001.wav`, `001.mp3`, ...). Exactly one must match: a
segment with no file, or with two, stops the build before anything renders.

A file made elsewhere carries whatever lead-in and tail its tool padded it with, and the
assembler adds its own beat at each end. So each file is TIMED with the same word-timed
transcript `ccvideo transcribe` makes, and only the speech is used: from just before the first
word to just after the last. The voice cache is not involved and is not changed.

CAPTIONS
A caption shows the segment's TEXT - what we wrote, spelled the way we spell it - and is timed
by the words the transcript HEARD. The two are aligned word by word; a written word the
transcriber heard differently ("twelve" for "12") takes its time from its neighbours. A file
in which most of the written words cannot be found is the wrong file for that segment, and the
build stops rather than captioning one segment with another's words.
"""

import difflib
import os
import re
from pathlib import Path

from ..shell import probe
from ..words import glue_fragments

AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg")

LEAD_MARGIN = 0.15      # seconds kept before the first heard word
TAIL_MARGIN = 0.35      # ... and after the last: a transcriber ends a word a little early
MIN_MATCH = 0.5         # the share of written words that must be heard in a segment's file


def find_audio(segment, audio_dir):
    """The pre-made narration file for `segment` in `audio_dir`."""
    audio_dir = Path(audio_dir)
    if segment.audio:
        path = audio_dir / segment.audio
        if not path.exists():
            raise SystemExit("segment %s: AUDIO: %s is not in %s"
                             % (segment.id, segment.audio, audio_dir))
        return path
    found = sorted(p for p in audio_dir.iterdir()
                   if p.suffix.lower() in AUDIO_EXTENSIONS and p.stem == segment.id)
    if not found:
        raise SystemExit("segment %s has no narration file in %s - expected %s.wav (or .mp3, "
                         ".m4a, .flac), or an AUDIO: line naming one"
                         % (segment.id, audio_dir, segment.id))
    if len(found) > 1:
        raise SystemExit("segment %s has %d narration files in %s (%s) - keep one, or name it "
                         "with an AUDIO: line"
                         % (segment.id, len(found), audio_dir, ", ".join(p.name for p in found)))
    return found[0]


def heard_words(audio, words_dir, model="base.en", language="en"):
    """Word timings of one narration file, cached in `words_dir`, redone when the file is
    newer than its transcript (a re-made take must never keep the old take's timings)."""
    from ..transcribe import transcribe, words_path
    from ..words import all_words, load
    audio = Path(audio)
    out = words_path(audio, words_dir)
    stale = out.exists() and os.path.getmtime(str(out)) < os.path.getmtime(str(audio))
    path = transcribe(audio, out_dir=words_dir, model_name=model, language=language,
                      force=stale)
    return glue_fragments(all_words(load(path)))


def speech_window(words, length):
    """(start, seconds) of the speech in a file `length` seconds long."""
    start = max(0.0, words[0]["start"] - LEAD_MARGIN)
    end = min(length, words[-1]["end"] + TAIL_MARGIN)
    return start, end - start


def _norm(token):
    return re.sub(r"[^0-9a-z]+", "", token.lower())


def align(text, heard, segment_id="-"):
    """The written words of `text`, each with a start and end taken from `heard`.

    Returns a list of {"start", "end", "word"} in the same clock as `heard`.
    """
    written = text.split()
    if not written:
        return []
    if not heard:
        raise SystemExit("segment %s: the narration has no words to time captions by"
                         % segment_id)
    a = [_norm(w) for w in written]
    b = [_norm(w["word"]) for w in heard]
    times = [None] * len(written)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = 0
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            h = heard[block.b + k]
            times[block.a + k] = (h["start"], h["end"])
            matched += 1
    if matched < MIN_MATCH * len(written):
        raise SystemExit(
            "segment %s: only %d of %d written words are heard in its narration file - it is "
            "the wrong file for this segment, or the TEXT no longer matches what was recorded"
            % (segment_id, matched, len(written)))

    # Spread every unmatched run across the gap between the matched words around it, by the
    # length of each word, so a misheard word is shown about when it was said.
    i = 0
    while i < len(written):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(written) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else heard[0]["start"]
        hi = times[j][0] if j < len(written) else heard[-1]["end"]
        hi = max(hi, lo)
        weights = [max(1, len(w)) for w in written[i:j]]
        total, at = float(sum(weights)), lo
        for k, weight in zip(range(i, j), weights):
            step = (hi - lo) * weight / total
            times[k] = (at, at + step)
            at += step
        i = j
    return [{"start": s, "end": e, "word": w} for w, (s, e) in zip(written, times)]


def split_narration(segments, heard, length):
    """Split ONE continuous narration into the segments' slices.

    The segments' TEXT, in order, is the script that was read. All of it is aligned against
    the heard words at once; each segment must find at least MIN_MATCH of its words, or the
    build stops. Between two segments the cut goes in the LONGEST PAUSE between the last word
    heard for the one and the first word heard for the next - the breath between sentences,
    never inside a word.

    Returns one (start, seconds, heard words) per segment: the slice of the recording, from
    just before its first word to just after its last, never reaching past a cut, and the heard
    words inside it (in the recording's clock), for the captions.
    """
    if not heard:
        raise SystemExit("the narration has no words to split it by")
    written, owner = [], []
    for k, segment in enumerate(segments):
        for token in segment.text.split():
            written.append(_norm(token))
            owner.append(k)
    matcher = difflib.SequenceMatcher(None, written, [_norm(w["word"]) for w in heard],
                                      autojunk=False)
    found = [[] for _ in segments]
    for block in matcher.get_matching_blocks():
        for n in range(block.size):
            found[owner[block.a + n]].append(block.b + n)
    for k, segment in enumerate(segments):
        need = len(segment.text.split())
        if len(found[k]) < MIN_MATCH * need:
            raise SystemExit(
                "segment %s: only %d of its %d words are found in the narration - its TEXT "
                "is not what was read, or it is out of order. Nothing was rendered."
                % (segment.id, len(found[k]), need))

    # Heard-word index ranges per segment, cut at the longest pause between neighbours.
    first, last = [0], []
    for k in range(len(segments) - 1):
        i, j = max(found[k]), min(found[k + 1])
        m = max(range(i, j), key=lambda x: heard[x + 1]["start"] - heard[x]["end"])
        last.append(m)
        first.append(m + 1)
    last.append(len(heard) - 1)

    out, previous_cut = [], 0.0
    for k in range(len(segments)):
        a, b = first[k], last[k]
        cut = ((heard[b]["end"] + heard[b + 1]["start"]) / 2.0
               if k + 1 < len(segments) else length)
        start = max(previous_cut, heard[a]["start"] - LEAD_MARGIN)
        end = min(cut, heard[b]["end"] + TAIL_MARGIN)
        out.append((start, end - start, heard[a:b + 1]))
        previous_cut = cut
    return out


def audio_length(path):
    shape = probe(path)
    return shape["audio_duration"] or shape["duration"]
