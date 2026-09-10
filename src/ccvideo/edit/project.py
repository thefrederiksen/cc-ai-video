"""An edit project: the takes that were recorded, and the timeline built out of them.

A project is one directory holding `project.json` and the word timings for each take. THE
FOOTAGE IS NEVER COPIED IN. A take records where its video lives and nothing more, because a
screen recording is gigabytes and because a library has no business making a second copy of
somebody's material in a place they did not choose.

The timeline is a list of clips, each naming a take and a window in that take's own timebase.
Every window in it has been through snap(), so it lies on real word boundaries. Nothing here
ever writes a time that a human typed straight into the timeline.
"""

import json
import re
from pathlib import Path

from .. import words as wordlib
from ..shell import probe, run_capture_stderr
from ..transcribe import transcribe
from .snap import find_phrase, snap

PROJECT_FILE = "project.json"


class Project:
    def __init__(self, root, data):
        # Resolved, so every path derived from it is absolute wherever the caller ran from.
        self.root = Path(root).resolve()
        self.data = data

    # ---------------------------------------------------------------- lifecycle

    @classmethod
    def open(cls, root):
        root = Path(root)
        path = root / PROJECT_FILE
        if not path.exists():
            raise SystemExit(
                "no edit project at %s. Create one by importing a take: "
                "ccvideo import <recording> --project %s" % (root, root))
        return cls(root, json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def open_or_create(cls, root):
        root = Path(root)
        if (root / PROJECT_FILE).exists():
            return cls.open(root)
        root.mkdir(parents=True, exist_ok=True)
        project = cls(root, {"takes": [], "timeline": []})
        project.save()
        return project

    def save(self):
        (self.root / PROJECT_FILE).write_text(
            json.dumps(self.data, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------- takes

    @property
    def takes(self):
        return self.data["takes"]

    @property
    def timeline(self):
        return self.data["timeline"]

    def take(self, name=None):
        if not self.takes:
            raise SystemExit("this project has no takes yet - import one first")
        if name is None:
            if len(self.takes) > 1:
                raise SystemExit(
                    "this project has %d takes (%s) - name one with --take"
                    % (len(self.takes), ", ".join(t["name"] for t in self.takes)))
            return self.takes[0]
        for t in self.takes:
            if t["name"] == name:
                return t
        raise SystemExit("no take named %r - have %s"
                         % (name, ", ".join(t["name"] for t in self.takes)))

    def add_take(self, source, name=None, model="base.en"):
        """Import one take. Accepts a recording directory or any video file.

        A recording directory written by a screen recorder usually ships its own transcript.
        It is NOT used for timings. Those transcripts are segment-level, and one opened during
        this library's design carried 0.0 to 0.0 on every segment and the wrong language
        entirely - which would have produced a timeline of zero-length cuts that looked fine
        in the JSON. The words file is always derived here, from the audio, by this library.
        """
        video, kind = resolve_source(source)
        name = name or _slug(video.parent.name if kind == "recording-dir" else video.stem)
        if any(t["name"] == name for t in self.takes):
            raise SystemExit("this project already has a take called %r" % name)

        words_file = transcribe(video, out_dir=self.root / "words", model_name=model)
        document = wordlib.load(words_file)
        take = {
            "name": name,
            "source": str(source),
            "source_kind": kind,
            "video": str(video),
            "words": str(words_file.relative_to(self.root)),
            "duration": probe(video)["duration"],
            "word_count": len(wordlib.all_words(document)),
        }
        self.takes.append(take)
        self.save()
        return take

    def words_of(self, take):
        return wordlib.all_words(self.root / take["words"])

    # ---------------------------------------------------------------- reading

    def transcript_lines(self, take_name=None):
        """The take as text with timings, for a person or an agent to read before cutting."""
        take = self.take(take_name)
        document = wordlib.load(self.root / take["words"])
        lines = ["# take %s  (%s, %.1fs)" % (take["name"], take["video"], take["duration"])]
        for segment in document["segments"]:
            lines.append("[%7.2f -%7.2f] %s"
                         % (segment["start"], segment["end"], segment["text"]))
        return lines

    def timeline_seconds(self):
        return sum(c["end"] - c["start"] for c in self.timeline)

    # ---------------------------------------------------------------- cutting

    def cut(self, take=None, keep=(), windows=(), append=False, crop=None):
        """Add clips to the timeline by naming what to keep.

        `keep` is text the speaker actually said; `windows` are "start-end" seconds. Both go
        through snap(), so both land on word boundaries. A phrase that is not in the take is
        an error, never a nearest guess - cutting the wrong sentence silently is worse than
        stopping.
        """
        row = self.take(take)
        words = self.words_of(row)
        limit = row["duration"]

        requested = []
        for phrase in keep:
            requested.append(find_phrase(words, phrase))
        for spec in windows:
            match = re.fullmatch(r"\s*([\d.]+)\s*-\s*([\d.]+)\s*", spec)
            if not match:
                raise SystemExit("a --window looks like 12.5-31.0, not %r" % spec)
            start, end = float(match.group(1)), float(match.group(2))
            if end <= start:
                raise SystemExit("--window %r ends before it starts" % spec)
            if end > limit + 0.5:
                raise SystemExit("--window %r runs past the end of the take (%.1fs)"
                                 % (spec, limit))
            requested.append((start, end))
        if not requested:
            raise SystemExit("nothing to cut - pass --keep or --window")

        quiet = detect_silence(row["video"])
        clips = []
        for start, end in requested:
            s, e, notes = snap(words, start, end)
            notes = list(notes) + edge_notes(words, quiet, s, e)
            clips.append({
                "take": row["name"],
                "start": s,
                "end": e,
                "crop": list(crop) if crop else None,
                "text": wordlib.text_of(wordlib.window(
                    {"segments": [{"words": words}]}, s, e, rebase=False)),
                "notes": notes,
                "clean_edges": [clean_edge(words, quiet, s),
                                clean_edge(words, quiet, e)],
            })

        if append:
            self.timeline.extend(clips)
        else:
            self.data["timeline"] = clips
        self.save()
        return clips

    def set_crop(self, crop, clips=None):
        """Set the visible region on some or all clips. `crop` of None clears it.

        Validated against each clip's own source BEFORE anything is written. A crop is checked
        at render time too, but finding out there means the bad value is already saved in the
        project and the next command starts from a broken state.
        """
        chosen = list(range(len(self.timeline))) if clips is None else list(clips)
        for i in chosen:
            if i < 0 or i >= len(self.timeline):
                raise SystemExit("this timeline has %d clips, so there is no clip %d"
                                 % (len(self.timeline), i))
            if crop:
                shape = probe(self.take(self.timeline[i]["take"])["video"])
                x, y, w, h = (int(v) for v in crop)
                if x < 0 or y < 0 or w <= 0 or h <= 0                         or x + w > shape["width"] or y + h > shape["height"]:
                    raise SystemExit(
                        "crop %d,%d,%d,%d falls outside clip %d's %dx%d source - nothing "
                        "was changed" % (x, y, w, h, i, shape["width"], shape["height"]))
        for i in chosen:
            self.timeline[i]["crop"] = list(crop) if crop else None
        self.save()
        return len(chosen)

    def trim_silence(self, over=0.9, leave=0.25):
        """Split every clip around dead air longer than `over`, leaving `leave` of breath.

        The threshold is stated and recorded, never guessed.

        A REMOVAL CAN NEVER OVERLAP A WORD. Two sources are consulted and only what BOTH agree
        on is cut. Silence detection alone is not safe: at any usable threshold it marks the
        quiet onset of a word as silence, and on the first run of this method it proposed a
        cut starting 0.25s inside the word "Alright" - a cut through a word, which is the one
        thing this library exists to prevent. Word gaps alone are not enough either, because a
        transcriber stretches a word over the pause that follows it and so under-reports the
        real dead air. The intersection is quiet AND has no word in it.

        A stretch of dead air at the very start or end of a clip loses its breath on the
        outside edge, so trimming a leading pause yields one clip rather than a clip plus a
        quarter second of silence nobody asked for.
        """
        if leave * 2 >= over:
            raise SystemExit(
                "--leave %.2f twice over is not less than --over %.2f, so trimming would "
                "lengthen the clip instead of shortening it" % (leave, over))

        silence_by_take = {}
        trimmed, removed = [], 0
        for clip in self.timeline:
            take = self.take(clip["take"])
            if take["name"] not in silence_by_take:
                silence_by_take[take["name"]] = detect_silence(take["video"])
            words = self.words_of(take)
            quiet = silence_by_take[take["name"]]

            removals = []
            for gap_start, gap_end in _word_gaps(words, clip["start"], clip["end"]):
                for qs, qe in quiet:
                    rs, re_ = max(gap_start, qs), min(gap_end, qe)
                    if re_ - rs <= over:
                        continue
                    # Keep a breath only on an edge that has kept audio beside it.
                    if rs > clip["start"] + 0.01:
                        rs += leave
                    if re_ < clip["end"] - 0.01:
                        re_ -= leave
                    if re_ - rs > 0.05:
                        removals.append((rs, re_))
            removals.sort()

            cursor, pieces = clip["start"], []
            for rs, re_ in removals:
                if rs > cursor + 0.2:
                    pieces.append((cursor, rs))
                cursor = max(cursor, re_)
                removed += 1
            if clip["end"] - cursor > 0.2:
                pieces.append((cursor, clip["end"]))

            for piece_start, piece_end in pieces:
                inside = wordlib.window({"segments": [{"words": words}]},
                                        piece_start, piece_end, rebase=False)
                if not inside:
                    # A piece with no words in it is dead air we chose to keep, stranded on its
                    # own. As a separate clip it buys nothing and costs an encode boundary.
                    continue
                piece = dict(clip)
                piece["start"] = round(piece_start, 2)
                piece["end"] = round(piece_end, 2)
                piece["text"] = wordlib.text_of(inside)
                trimmed.append(piece)

        if not trimmed:
            raise SystemExit(
                "trimming at --over %.2f would leave nothing with words in it. Raise the "
                "threshold rather than shipping an empty timeline." % over)
        self.data["timeline"] = trimmed
        self.save()
        return removed


def _word_gaps(words, start, end):
    """The stretches inside [start, end] where no word is being spoken."""
    inside = [w for w in words if w["end"] > start and w["start"] < end]
    if not inside:
        return [(start, end)]
    gaps = [(start, inside[0]["start"])]
    for a, b in zip(inside, inside[1:]):
        gaps.append((a["end"], b["start"]))
    gaps.append((inside[-1]["end"], end))
    return [(max(a, start), min(b, end)) for a, b in gaps if min(b, end) > max(a, start)]


# ---------------------------------------------------------------- sources

def resolve_source(source):
    """(video path, kind) for anything that can be imported.

    Two kinds, and the second is a plain file so that a phone clip, a downloaded talk or
    anything else a person drops in works without being wrapped in a fake recording directory.
    """
    path = Path(source)
    if not path.exists():
        raise SystemExit("no such recording or video: %s" % path)
    if path.is_dir():
        video = path / "recording.mp4"
        if not video.exists():
            candidates = sorted(p for p in path.iterdir()
                                if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"))
            if len(candidates) != 1:
                raise SystemExit(
                    "%s is a directory with no recording.mp4 and %d other video files - "
                    "name the file you mean instead of the directory"
                    % (path, len(candidates)))
            video = candidates[0]
        return video, "recording-dir"
    if path.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm", ".m4v"):
        raise SystemExit("%s does not look like a video file" % path)
    return path, "file"


def clean_edge(words, quiet, at, slack=0.05):
    """Is it safe to cut here, judged from the AUDIO and the word timings only?

    Two conditions, and the second is why asking about the instant alone was not enough. An
    instant can sit inside a real pause and still fall outside a detected silence span, because
    a level takes a moment to fall: a cut 0.25s after the last word lands in a genuine 0.9s
    pause whose silence is only measured from 0.35s in. Asking "is this instant silent" called
    that a hard chop.

    So: the instant must not be inside a word, AND the gap it sits in must contain real
    silence. A junction with no gap at all fails the second condition, which is exactly the
    shape of a cut through continuous speech.
    """
    for word in words:
        if word["start"] + slack < at < word["end"] - slack:
            return False
    gaps = _word_gaps(words, at - 3.0, at + 3.0)
    here = [(a, b) for a, b in gaps if a - slack <= at <= b + slack]
    if not here:
        return False
    a, b = here[0]
    return any(min(b, qe) - max(a, qs) > 0.05 for qs, qe in quiet)


def edge_notes(words, quiet, start, end):
    """Confirm each cut edge against the AUDIO, with no reference to the transcript.

    THIS CHECK SHARES NO CODE WITH THE THING THAT CHOSE THE CUT, and that is its entire value.
    The boundary rule decides WHERE a sentence ends; if that rule is wrong, every check built
    on it agrees with it and reports success in exactly the case you needed a failure. That is
    not hypothetical: a batch of 66 shorts was cut and cleared by a check that imported its
    boundary rule from the cutter, and sixteen of them end in the middle of continuous speech.
    Every one had a gap of 0.000 seconds at the cut - which the audio says plainly and no
    linguistic rule was ever consulted about.

    So: words choose the boundary, audio confirms it, and a disagreement is reported rather
    than resolved in favour of either.
    """
    notes = []
    if not clean_edge(words, quiet, start):
        notes.append("EDGE: no real pause at the start (%.2f) - the cut opens inside "
                     "continuous speech" % start)
    if not clean_edge(words, quiet, end):
        notes.append("EDGE: no real pause at the end (%.2f) - the cut lands inside continuous "
                     "speech, which is heard as a hard chop" % end)
    return notes


def detect_silence(video, noise_db=-35, minimum=0.3):
    """Stretches of silence in a media file, as (start, end) in its own timebase."""
    log = run_capture_stderr([
        "ffmpeg", "-hide_banner", "-i", str(video),
        "-af", "silencedetect=noise=%ddB:d=%s" % (noise_db, minimum),
        "-f", "null", "-",
    ])
    starts = [float(m) for m in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end: (-?[\d.]+)", log)]
    # zip truncates a trailing unmatched start, which is a silence running to the end of file.
    spans = list(zip(starts, ends))
    if len(starts) > len(ends):
        spans.append((starts[-1], probe(video)["duration"]))
    return spans


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "take"
