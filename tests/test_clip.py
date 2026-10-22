"""The clip element: stock footage that moves under the narration.

Proves three things on a real (generated) video file: the clip is cut into frames once and the
frames advance with narration time; a clip shorter than its time on screen stops the build; and
an inset clip lands inside its frame and nowhere else.
"""

import subprocess

import numpy as np
import pytest

from ccvideo.illustrate import render


def _make_clip(path, seconds):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=640x360:rate=30:duration=%g" % seconds,
                    "-pix_fmt", "yuv420p", str(path)], check=True)


def _scenes(path, t1, **extra):
    el = {"type": "clip", "path": str(path), "t_in": 0.0}
    el.update(extra)
    return [{"t0": 0.0, "t1": t1, "bg": "dark", "elements": [el]}]


def test_clip_frames_advance_with_time(tmp_path, monkeypatch):
    monkeypatch.setenv("CCVIDEO_CACHE", str(tmp_path / "cache"))
    clip = tmp_path / "c.mp4"
    _make_clip(clip, 4)
    scenes = _scenes(clip, 3.0, scrim=False)
    render.prepare_clips(scenes)
    el = scenes[0]["elements"][0]
    assert el["clip_count"] >= 3 * render.FPS
    canvas = render.Canvas()
    a = np.asarray(canvas.frame(scenes, 1.0), dtype=np.int16)
    b = np.asarray(canvas.frame(scenes, 2.0), dtype=np.int16)
    # The picture is footage, not a still: one second later it is a different frame.
    assert np.abs(a - b).mean() > 2.0
    # Preparing again reuses the frames rather than cutting the clip a second time.
    stamp = (render.clip_dir(el) / "frames.txt").stat().st_mtime
    render.prepare_clips(_scenes(clip, 3.0, scrim=False))
    assert (render.clip_dir(el) / "frames.txt").stat().st_mtime == stamp


def test_short_clip_stops_the_build(tmp_path, monkeypatch):
    monkeypatch.setenv("CCVIDEO_CACHE", str(tmp_path / "cache"))
    clip = tmp_path / "short.mp4"
    _make_clip(clip, 1)
    with pytest.raises(SystemExit) as err:
        render.prepare_clips(_scenes(clip, 3.0))
    assert "pick a longer clip" in str(err.value)


def test_inset_clip_stays_in_its_frame(tmp_path, monkeypatch):
    monkeypatch.setenv("CCVIDEO_CACHE", str(tmp_path / "cache"))
    clip = tmp_path / "c.mp4"
    _make_clip(clip, 3)
    scenes = _scenes(clip, 2.5, x=600, y=300, w=640, h=360)
    render.prepare_clips(scenes)
    canvas = render.Canvas()
    empty = np.asarray(canvas.bases["dark"], dtype=np.int16)
    img = np.asarray(canvas.frame(scenes, 1.5), dtype=np.int16)
    inside = np.abs(img[320:640, 620:1220] - empty[320:640, 620:1220]).mean()
    outside = np.abs(img[700:1000, 50:500] - empty[700:1000, 50:500]).mean()
    assert inside > 20
    assert outside < 1


def test_scale_frames_the_clip_tighter(tmp_path, monkeypatch):
    monkeypatch.setenv("CCVIDEO_CACHE", str(tmp_path / "cache"))
    clip = tmp_path / "c.mp4"
    _make_clip(clip, 3)
    plain = _scenes(clip, 2.5, scrim=False, zoom=0.0)
    tight = _scenes(clip, 2.5, scrim=False, zoom=0.0, scale=1.5, pan_x=0.0, pan_y=0.0)
    render.prepare_clips(plain)
    render.prepare_clips(tight)
    canvas = render.Canvas()
    a = np.asarray(canvas.frame(plain, 1.0), dtype=np.int16)
    b = np.asarray(canvas.frame(tight, 1.0), dtype=np.int16)
    # Same clip, same moment: framed tighter on its top-left corner, the picture differs.
    assert np.abs(a - b).mean() > 10


def test_box_icon_fades_with_its_box():
    from PIL import ImageDraw
    canvas = render.Canvas()
    p = render.THEMES["dark"]
    el = {"type": "box", "text": "Set up quickly", "icon": "check", "x": 100, "y": 100,
          "w": 600, "h": 120, "t_in": 0.0}
    img = canvas.bases["dark"].copy()
    canvas.box(img, ImageDraw.Draw(img), p, el, 1.0, 0.1)
    chip = np.asarray(img, dtype=np.int16)[130:190, 125:190]
    # At 10% the white icon chip is nearly background, not a bright square left behind.
    assert chip.max() < 60
