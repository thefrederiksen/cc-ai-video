"""The assembler: a segment script plus a folder of frames becomes one narrated mp4.

This is the core of the GENERATE half, and it is deliberately not clever. Each segment is
rendered on its own - one picture, one block of narration, a beat at each end and a slow
alternating zoom so it does not read as a dead slideshow - and the segments are concatenated
with the chapters written into the container. The zoom frames are drawn here, not by
ffmpeg's zoompan, which shook (see zoom_frame).

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

from .. import targets
from ..draw import render_card
from ..narrate import speak
from ..shell import duration, run, stream_frames

ZOOM = 1.05         # maximum zoom; the image is pre-fitted so nothing is ever cropped
LEAD_IN = 0.4       # seconds of held picture before the voice starts
TAIL = 0.7          # seconds of held picture after the voice stops


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
    if segment.kind == "image":
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
    """
    w, h = canvas.size
    half_w, half_h = w / (2.0 * z), h / (2.0 * z)
    box = (w / 2.0 - half_w, h / 2.0 - half_h, w / 2.0 + half_w, h / 2.0 + half_h)
    return canvas.resize((w, h), Image.BICUBIC, box=box)


def render_segment(picture, audio, index, out, brand, target):
    """One segment: a still, its narration, a beat at each end and a slow zoom."""
    encode = target["encode"]
    w, h, fps = target["width"], target["height"], encode["fps"]

    seconds = duration(audio) + LEAD_IN + TAIL
    frames = max(2, int(round(seconds * fps)))
    canvas = letterbox(picture, w, h, brand["bg"])

    layout = "mono" if encode["channels"] == "1" else "stereo"
    audio_filter = ("adelay=delays=%d:all=1,apad=pad_dur=%.2f,"
                    "aformat=channel_layouts=%s:sample_rates=%s"
                    % (int(LEAD_IN * 1000), TAIL, layout, encode["sample_rate"]))
    cmd = (["ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h),
            "-r", str(fps), "-i", "-", "-i", str(audio), "-af", audio_filter]
           + targets.video_args(encode)
           + targets.audio_args(encode)
           + ["-t", "%.3f" % seconds, "-movflags", "+faststart", str(out)])
    stream_frames(cmd, (zoom_frame(canvas, zoom_at(n, frames, index)) for n in range(frames)))
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
             allow_synthesis=True, on_segment=None):
    """Build the whole video. Returns (timings, total seconds).

    `on_segment` is called with (segment, seconds, label, was_synthesised) as each one lands,
    so a caller can report progress without this function knowing how it wants to.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    build.make()
    stem = out_path.stem

    timings, elapsed = [], 0.0
    for index, segment in enumerate(script.segments):
        picture, label = picture_for(segment, script, brand, target, build, stem, shots_dir)
        audio, synthesised = speak(segment.text, voice, build.audio, segment.id,
                                   allow_synthesis=allow_synthesis)
        piece = build.segments / ("seg-%s-%s.mp4" % (stem, segment.id))
        seconds = render_segment(picture, audio, index, piece, brand, target)
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
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-i", str(chapters),
         "-map_metadata", "1", "-c", "copy", "-metadata", "title=%s" % script.title,
         "-movflags", "+faststart", str(out_path)])

    return timings, duration(out_path)


def normalise_loudness(path, lufs, encode):
    """Bring a finished file to a target loudness, IN PLACE, without touching the picture.

    This is opt-in, and the default is off, for one reason: the assembler is byte-for-byte
    deterministic and that is what makes it possible to prove a change to it changed nothing.
    Silently adding a pass here would end that.

    It is worth turning on. The assembler does no levelling of its own, and a shipped short
    measured -23.3 LUFS - roughly nine decibels under what the platforms normalise to, so it
    plays noticeably quieter than everything around it in a feed. The video stream is copied,
    so the picture is untouched and only the audio is re-encoded; chapters are carried across
    explicitly, because a re-mux that drops them is the classic way this goes wrong.
    """
    path = Path(path)
    temp = path.with_name(path.stem + ".levelled" + path.suffix)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
         "-af", "loudnorm=I=%.1f:TP=-1.5:LRA=11" % lufs,
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
