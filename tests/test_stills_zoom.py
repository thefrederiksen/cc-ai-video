"""The slow zoom on a narrated still must glide, not shake.

ffmpeg's zoompan snapped the picture to whole pixels every frame, so a point near the edge
stood still for several frames and then jumped. These tests track a dot near a corner through
a zoom and require it to MOVE on every frame, by an even amount, in one direction - a presence
of steady motion, not an absence of complaints.
"""

import subprocess

import numpy as np
from PIL import Image, ImageDraw

from ccvideo import targets
from ccvideo.generate.assemble import ZOOM, render_segment, zoom_at, zoom_frame
from ccvideo.shell import probe


def _dot_canvas(w=640, h=360):
    img = Image.new("RGB", (w, h), (0, 0, 0))
    ImageDraw.Draw(img).ellipse((60, 40, 76, 56), fill=(255, 255, 255))
    return img


def _centroid(img):
    a = np.asarray(img.convert("L"), dtype=np.float64)[:140, :200]
    ys, xs = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    return (a * xs).sum() / a.sum(), (a * ys).sum() / a.sum()


def test_zoom_runs_the_full_range_and_alternates():
    assert zoom_at(0, 90, 0) == 1.0 and abs(zoom_at(89, 90, 0) - ZOOM) < 1e-9
    assert abs(zoom_at(0, 90, 1) - ZOOM) < 1e-9 and zoom_at(89, 90, 1) == 1.0


def test_a_point_near_the_edge_follows_the_zoom_on_every_frame():
    """Each frame puts the dot where the zoom says it should be. zoompan was off by up to a
    whole pixel, then caught up in one jump; this has to stay within a tenth of a pixel."""
    canvas, frames = _dot_canvas(), 90           # three seconds at 30 fps
    x0, cx = _centroid(canvas)[0], canvas.width / 2.0
    actual = np.array([_centroid(zoom_frame(canvas, zoom_at(n, frames, 0)))[0]
                       for n in range(frames)])
    expected = np.array([cx + (x0 - cx) * zoom_at(n, frames, 0) for n in range(frames)])
    assert np.abs(actual - expected).max() < 0.1, "the dot must sit where the zoom puts it"
    steps = np.diff(actual)
    assert (steps[5:-5] < 0).all(), "away from the eased ends it moves outward every frame"
    assert np.abs(steps).max() < 0.3, "no frame may jump"


def test_a_rendered_segment_has_the_frames_and_length_it_should(tmp_path):
    picture, audio, out = tmp_path / "shot.png", tmp_path / "voice.wav", tmp_path / "seg.mp4"
    _dot_canvas().save(picture)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=mono", "-t", "1.0", str(audio)], check=True)
    target = targets.target("tutorial-desktop")
    target = dict(target, width=640, height=360)
    brand = {"bg": (11, 21, 38)}
    seconds = render_segment(picture, audio, 0, out, brand, target)
    info = probe(out)
    assert (info["width"], info["height"]) == (640, 360)
    assert abs(info["fps"] - 30) < 0.01
    assert abs(info["duration"] - seconds) < 0.1
