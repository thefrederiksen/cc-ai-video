"""STEADY must fail a shaky video and pass a smooth one - on known inputs.

CALIBRATION (tools/steady_calibrate.py, 8 Nov 2026, 1920x1080, footage from the course takes)

Every shot rendered twice: with zoompan exactly as the course builders wrote it, and with
`ccvideo move`. Score = RMS of each frame's stumble against the local trend of the step, as a
fraction of the step or of half a pixel when the step is smaller.

    picture     move          zoompan        ccvideo move linear / smooth
    screenshot  slow zoom     FAIL 1.84      0.13 / 0.27
    screenshot  pan           FAIL 0.43      0.02 / 0.07
    screenshot  zoom and pan  FAIL 0.56      0.12 / 0.05
    terminal    slow zoom     FAIL 1.89      0.08 / 0.13
    terminal    pan           FAIL 0.43      0.01 / 0.01
    terminal    zoom and pan  FAIL 0.48      0.07 / 0.09
    browser     slow zoom     FAIL 1.63      0.16 / 0.19
    browser     pan           FAIL 0.50      0.30 / 0.24
    browser     zoom and pan  FAIL 0.50      0.05 / 0.06
    any         fast zoom     pass 0.30-0.32 0.02-0.06 / 0.06-0.09

Worst smooth 0.30, best shaky 0.43: the threshold is 0.37. The one zoompan move that passes is a
fast zoom (about 4 px a frame at the corners), where zoompan's half-pixel stumble is under a tenth
of the step. The shaking that viewers reported was on slow moves, which score 1.6 to 1.9.

Shipped course chapters, measured with this check: ch01 FAIL (worst 2.62).

These tests run the same measurement on small files made here, so they run anywhere.
"""

import subprocess

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from ccvideo import motion
from ccvideo.qa import steady

W, H, FPS = 640, 360, 30


def _texture(seed=1):
    """Something with detail everywhere, like a screenshot: blurred noise plus lines of 'text'."""
    rng = np.random.default_rng(seed)
    base = Image.fromarray((rng.random((H, W)) * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(2)).convert("RGB")
    d = ImageDraw.Draw(base)
    for y in range(10, H, 18):
        for x in range(10, W - 40, 60):
            d.rectangle((x, y, x + int(rng.integers(15, 50)), y + 6), fill=(250, 250, 250))
    return base


def _encode(frames, out):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        proc.stdin.write(f.tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return out


def _snap(frame, move, n):
    zoom, cx, cy = move.at(n)
    x0, y0, x1, y1 = motion.view_box(W, H, zoom, cx, cy)
    bw, bh = x1 - x0, y1 - y0
    x0, y0 = min(round(x0), int(W - bw)), min(round(y0), int(H - bh))
    return frame.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + bw, y0 + bh))


SLOW_ZOOM = (1.0, 1.06, (320, 180), (318, 184))
PAN = (1.3, 1.3, (260, 150), (380, 210))


def _render(tmp_path, name, spec, snapped=False, seconds=4.0):
    tex = _texture()
    move = motion.Move(spec[0], spec[1], spec[2], spec[3], int(seconds * FPS), "linear").fit(W, H)
    draw = (lambda n: _snap(tex, move, n)) if snapped else (lambda n: move.draw(tex, n))
    return _encode((draw(n) for n in range(move.frames)), tmp_path / (name + ".mp4"))


@pytest.mark.parametrize("spec", [SLOW_ZOOM, PAN], ids=["slow zoom", "pan"])
def test_a_smooth_move_passes(tmp_path, spec):
    result = steady.measure(_render(tmp_path, "smooth", spec))
    assert result["verdict"] == "PASS", result["detail"]
    assert result["glide_seconds"] >= 2.0, "it must actually have measured the move"


@pytest.mark.parametrize("spec", [SLOW_ZOOM, PAN], ids=["slow zoom", "pan"])
def test_the_same_move_in_whole_pixel_steps_fails(tmp_path, spec):
    result = steady.measure(_render(tmp_path, "snapped", spec, snapped=True))
    assert result["verdict"] == "FAIL", result["detail"]


def test_real_zoompan_fails(tmp_path):
    """The filter itself, as the course builders called it, on a still."""
    still, out = tmp_path / "tex.png", tmp_path / "zp.mp4"
    _texture().save(still)
    n = 4 * FPS
    lin = lambda a, b: "(%f+(%f-%f)*min(on/%d\\,1))" % (a, b, a, n)  # noqa: E731
    vf = ("zoompan=z='%s':x='max(0\\,min(iw-iw/zoom\\,%s-iw/zoom/2))':"
          "y='max(0\\,min(ih-ih/zoom\\,%s-ih/zoom/2))':d=1:s=%dx%d:fps=%d"
          % (lin(1.0, 1.06), lin(320, 318), lin(180, 184), W, H, FPS))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS),
                    "-t", "4", "-i", str(still), "-vf", vf + ",trim=duration=4",
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(out)], check=True)
    assert steady.measure(out)["verdict"] == "FAIL"


def test_a_still_camera_is_not_a_pass(tmp_path):
    tex = _texture()
    out = _encode((tex for _ in range(60)), tmp_path / "still.mp4")
    result = steady.measure(out)
    assert result["verdict"] == "N/A" and result["glide_seconds"] == 0
    assert "60 frames" in result["detail"], "it says how much it looked at"


def test_typing_under_a_still_camera_is_not_a_glide(tmp_path):
    """The screen changing in one place - text appearing - is not the camera moving."""
    tex = _texture()

    def frame(n):
        f = tex.copy()
        ImageDraw.Draw(f).rectangle((40, 300, 40 + 6 * n, 316), fill=(255, 255, 0))
        return f

    result = steady.measure(_encode((frame(n) for n in range(90)), tmp_path / "typing.mp4"))
    assert result["verdict"] == "N/A", result["detail"]


def test_a_cut_between_two_smooth_moves_is_not_a_jump(tmp_path):
    a = _texture(1)
    b = _texture(2)
    m1 = motion.Move(1.0, 1.06, (320, 180), (318, 184), 90, "linear").fit(W, H)
    m2 = motion.Move(1.3, 1.3, (260, 150), (380, 210), 90, "linear").fit(W, H)
    frames = [m1.draw(a, n) for n in range(90)] + [m2.draw(b, n) for n in range(90)]
    result = steady.measure(_encode(frames, tmp_path / "cut.mp4"))
    assert result["verdict"] == "PASS", result["detail"]
    assert len(result["glides"]) == 2, "two glides, split at the cut"


def test_an_unreadable_file_fails_the_gate(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video at all")
    with pytest.raises(SystemExit):
        steady.measure(bad)


def test_qa_reports_steady(tmp_path):
    from ccvideo.qa.run import run_checks
    video = _render(tmp_path, "snapped", SLOW_ZOOM, snapped=True)
    with_audio = tmp_path / "with-audio.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=4", "-c:v", "copy", "-shortest",
                    str(with_audio)], check=True)
    rows = {r["check"]: r for r in run_checks(with_audio, speech=False)}
    assert rows["STEADY"]["result"] == "FAIL"
    rows = {r["check"]: r for r in run_checks(with_audio, speech=False, steady=False)}
    assert rows["STEADY"]["result"] == "SKIP", "skipped is said, never passed"
