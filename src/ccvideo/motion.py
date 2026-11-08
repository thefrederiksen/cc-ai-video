"""Camera moves - zoom and pan - drawn at SUB-PIXEL precision, on stills and on footage.

WHY THIS MODULE EXISTS

ffmpeg's zoompan snaps its crop window to whole pixels on every frame. Under a slow move a point
of the picture then stands still for several frames and jumps a pixel: the whole video shakes.
ccvideo stopped using zoompan for its own slide zoom on 23 Oct 2026, but it had no way to make a
move you CHOOSE - zoom from here to there, pan down a page - and none at all for recorded footage.
So sessions that needed one wrote it themselves, with zoompan, and the course chapters shipped
shaking (measured: 1.1 to 1.2 on the jitter meter, against 0.11 for a smooth zoom).

This is the one place a frame is drawn through a camera. A camera is a zoom (1 = the whole frame)
and a centre in frame pixels. The crop box it implies is FRACTIONAL, and the frame is resized from
that box, so a point near the edge moves by an even fraction of a pixel on every frame instead of
standing still and jumping. The stills zoom in generate/assemble.py is this same routine with the
centre held in the middle.

`ccvideo move` (render_move) puts a move on a part of a video or on an image and writes an mp4:
one ffmpeg decodes (trim, speed, frame rate, hold, any masking filter, scale to the frame), every
frame is drawn here, and a second ffmpeg encodes.
"""

import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from . import budget, targets
from .shell import _require, stream_frames

EASES = ("smooth", "linear")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def view_box(w, h, zoom, cx, cy):
    """The fractional crop box (left, top, right, bottom) a camera at `zoom` centred on (cx, cy)
    sees in a w x h frame, kept inside the frame.

    The clamp is the one the hand-written zoompan moves used (x = max(0, min(iw - iw/zoom,
    cx - iw/zoom/2))), so a move written for zoompan frames the same picture here - only the
    rounding to whole pixels is gone. The arithmetic for a centred box is written exactly as
    the stills zoom always computed it, so the stills render is byte-identical.
    """
    if zoom < 1:
        raise ValueError("zoom %.4f is below 1: a camera cannot see more than the whole frame"
                         % zoom)
    half_w, half_h = w / (2.0 * zoom), h / (2.0 * zoom)
    cx = min(max(cx, half_w), w - half_w)
    cy = min(max(cy, half_h), h - half_h)
    return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def draw(frame, zoom, cx=None, cy=None):
    """`frame` seen through a camera at `zoom` centred on (cx, cy), at sub-pixel precision.
    The centre defaults to the middle of the frame."""
    w, h = frame.size
    cx = w / 2.0 if cx is None else cx
    cy = h / 2.0 if cy is None else cy
    box = view_box(w, h, zoom, cx, cy)
    if box == (0.0, 0.0, float(w), float(h)):
        return frame            # the whole frame: resizing it onto itself would only cost time
    return frame.resize((w, h), Image.BICUBIC, box=box)


def ease(t, kind="smooth"):
    """0..1 -> 0..1. `smooth` starts and stops gently (smoothstep), so a move neither starts
    nor stops with a jolt; `linear` is constant speed, as the old zoompan moves were."""
    t = min(max(t, 0.0), 1.0)
    if kind == "linear":
        return t
    if kind == "smooth":
        return t * t * (3 - 2 * t)
    raise ValueError("unknown ease %r: use one of %s" % (kind, ", ".join(EASES)))


class Move:
    """A camera move: zoom z0 -> z1 and centre c0 -> c1 over `frames` frames, then held.

    Zoom is interpolated on a log scale, so a zoom from 1.0 to 1.7 grows by the same ratio every
    frame and does not seem to speed up as it closes in. The centre moves in a straight line.
    """

    def __init__(self, z0, z1, c0, c1, frames, kind="smooth"):
        if z0 < 1 or z1 < 1:
            raise ValueError("zoom must be 1 or more (got %s to %s)" % (z0, z1))
        self.z0, self.z1 = float(z0), float(z1)
        self.c0 = (float(c0[0]), float(c0[1]))
        self.c1 = (float(c1[0]), float(c1[1]))
        self.size = None
        self.frames = max(int(frames), 1)
        self.kind = kind
        ease(0.0, kind)         # an unknown ease fails here, before any frame is drawn

    def fit(self, w, h):
        """Bring the start and end centres inside what the camera can see at the start and
        end zoom, BEFORE moving between them.

        Clamping only frame by frame (as the zoompan moves did) makes a camera asked to start
        outside the frame slide along the edge and then turn away from it - a kink in the
        path, felt as a jolt. Clamped first, the path between two framings is straight; each
        frame is still clamped for safety, and the start and end framings are the same."""
        b0 = view_box(w, h, self.z0, *self.c0)
        b1 = view_box(w, h, self.z1, *self.c1)
        self.c0 = ((b0[0] + b0[2]) / 2.0, (b0[1] + b0[3]) / 2.0)
        self.c1 = ((b1[0] + b1[2]) / 2.0, (b1[1] + b1[3]) / 2.0)
        self.size = (w, h)
        return self

    def at(self, n):
        """(zoom, cx, cy) of frame n."""
        t = 1.0 if self.frames == 1 else ease(n / float(self.frames - 1), self.kind)
        zoom = self.z0 * math.exp(math.log(self.z1 / self.z0) * t)
        return (zoom,
                self.c0[0] + (self.c1[0] - self.c0[0]) * t,
                self.c0[1] + (self.c1[1] - self.c0[1]) * t)

    def draw(self, frame, n):
        if self.size != frame.size:
            self.fit(*frame.size)
        zoom, cx, cy = self.at(n)
        return draw(frame, zoom, cx, cy)


def is_image(path):
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def fit_filter(w, h, pad="black"):
    """Fit the source INSIDE w x h, keeping its shape, with bars of `pad` on the free sides.

    Never a plain stretch: a 16:10 screenshot or a portrait recording squashed into 16:9 is a
    distorted picture no check catches. A source already w x h passes through untouched."""
    return ("scale=%d:%d:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s,setsar=1" % (w, h, w, h, pad))


def decode_command(source, w, h, fps, seconds, start=None, end=None, speed=1.0, before="",
                   pad="black"):
    """The ffmpeg command that turns `source` into exactly round(seconds * fps) raw RGB frames
    of w x h on stdout: the part [start, end] played at `speed`, its last frame held to fill
    `seconds`, fitted inside the frame. `before` is a filter in SOURCE pixels (a mask, a blur)
    applied first."""
    frames = int(round(seconds * fps))
    pre = (before.rstrip(",") + ",") if before else ""
    fit = fit_filter(w, h, pad)
    if is_image(source):
        inputs = ["-loop", "1", "-framerate", str(fps), "-i", str(source)]
        chain = pre + fit
    else:
        inputs = []
        if start is not None:
            inputs += ["-ss", "%.3f" % start]
        if end is not None:
            inputs += ["-to", "%.3f" % end]
        inputs += ["-i", str(source)]
        # The hold is padded past the end and the frame count cuts it, so frame rounding can
        # never leave the shot short.
        chain = (pre + "setpts=(PTS-STARTPTS)/%.6f,fps=%d,%s,"
                 "tpad=stop_mode=clone:stop_duration=%.3f" % (speed, fps, fit, seconds + 1.0))
    return (["ffmpeg", "-v", "error"] + inputs
            + ["-vf", chain, "-frames:v", str(frames), "-an",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]), frames


def decoded_frames(cmd, w, h):
    """Yield the PIL frames a decode command writes. A decode that fails stops the move with
    ffmpeg's own words, so a half-drawn shot is never written as a whole one."""
    _require("ffmpeg")
    size = w * h * 3
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(budget.ffmpeg(cmd), stdout=subprocess.PIPE, stderr=log)
        try:
            while True:
                raw = proc.stdout.read(size)
                if len(raw) < size:
                    break
                yield Image.frombuffer("RGB", (w, h), raw, "raw", "RGB", 0, 1)
        finally:
            proc.stdout.close()
            code = proc.wait()
        if code:
            log.seek(0)
            raise SystemExit("decoding for the move failed (exit %d): %s"
                             % (code, log.read().decode("utf-8", "replace").strip()[-800:]))


def render_move(source, out, seconds, zoom=(1.0, 1.0), centre=None, start=None, end=None,
                speed=1.0, move_seconds=None, kind="smooth", before="", size=(1920, 1080),
                fps=30, profile="footage", pad="black"):
    """Write one shot to `out`: `source` (a video or an image) under a camera move.

    zoom      (z0, z1); centre ((x0, y0), (x1, y1)) in frame pixels, default the middle.
    start/end the part of a video to use; speed plays it faster; its last frame is held.
    move_seconds  how long the move takes (default: the whole shot), then the camera holds.
    pad       the colour of the bars when the source is not the frame's shape.
    The shot is written under a temporary name and renamed only when every frame arrived, so a
    failed move never leaves a file that a re-run would take for a finished shot.
    Returns the number of frames written."""
    w, h = size
    middle = (w / 2.0, h / 2.0)
    c0, c1 = centre if centre else (middle, middle)
    cmd, frames = decode_command(source, w, h, fps, seconds, start, end, speed, before, pad)
    if frames < 1:
        raise SystemExit("a %.3fs shot at %d fps has no frames" % (seconds, fps))
    move_frames = frames if move_seconds is None else max(1, int(round(move_seconds * fps)))
    move = Move(zoom[0], zoom[1], c0, c1, move_frames, kind)
    encode = dict(targets.PROFILES[profile], fps=fps)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.stem + ".partial" + out.suffix)
    enc = (["ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h),
            "-r", str(fps), "-i", "-"]
           + targets.video_args(encode)
           + ["-an", "-frames:v", str(frames), "-movflags", "+faststart", str(part)])
    count = [0]

    def drawn():
        for n, frame in enumerate(decoded_frames(cmd, w, h)):
            count[0] = n + 1
            yield move.draw(frame, n)

    try:
        stream_frames(enc, drawn())
        if count[0] != frames:
            raise SystemExit("the move expected %d frames from %s and got %d"
                             % (frames, source, count[0]))
        part.replace(out)
    finally:
        if part.exists():
            part.unlink()
    return frames
