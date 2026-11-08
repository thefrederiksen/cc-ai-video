"""Camera moves on footage and stills must glide, not shake.

ffmpeg's zoompan snapped the crop to whole pixels every frame, so a point near the edge stood
still for several frames and then jumped. The course chapters shipped like that because ccvideo
had no zoom or pan for footage and sessions wrote their own with zoompan. These tests follow a
dot near a corner through every kind of move ccvideo makes - zoom, pan, zoom and pan, on a still
and on footage that changes every frame - and require it to be where the camera puts it, to move
on EVERY frame, in one direction, by an even amount.

Each assertion has a control: the same frames drawn from a box snapped to whole pixels - what
zoompan did - must FAIL it. A test that cannot fail proves nothing.
"""

import subprocess

import numpy as np
import pytest
from PIL import Image, ImageDraw

from ccvideo import motion
from ccvideo.generate.assemble import zoom_frame
from ccvideo.shell import probe

W, H = 640, 360
DOT = (142, 82, 158, 98)        # a dot towards the top-left corner, centre (150, 90)


def _canvas(noise_seed=None):
    img = Image.new("RGB", (W, H), (0, 0, 0))
    if noise_seed is not None:
        # "footage": the right half of the frame changes every frame, as a screen does
        rng = np.random.default_rng(noise_seed)
        block = (rng.random((H, W // 2, 3)) * 60).astype(np.uint8)
        img.paste(Image.fromarray(block), (W // 2, 0))
    ImageDraw.Draw(img).ellipse(DOT, fill=(255, 255, 255))
    return img


def _centroid(img):
    a = np.asarray(img.convert("L"), dtype=np.float64)[:200, :280]
    ys, xs = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    return (a * xs).sum() / a.sum(), (a * ys).sum() / a.sum()


def _expected(move, n, point=(150.0, 90.0)):
    """Where the camera says a source point lands in frame n. The box is in pixel EDGES (pixel
    i spans i to i+1) and the centroid in pixel indices, hence the half pixels."""
    zoom, cx, cy = move.at(n)
    x0, y0, x1, y1 = motion.view_box(W, H, zoom, cx, cy)
    return ((point[0] + 0.5 - x0) * W / (x1 - x0) - 0.5,
            (point[1] + 0.5 - y0) * H / (y1 - y0) - 0.5)


def _snapped(frame, move, n):
    """The control: the same camera with its box snapped to whole pixels, as zoompan did."""
    zoom, cx, cy = move.at(n)
    x0, y0, x1, y1 = motion.view_box(W, H, zoom, cx, cy)
    bw, bh = x1 - x0, y1 - y0
    x0, y0 = min(round(x0), int(W - bw)), min(round(y0), int(H - bh))
    return frame.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + bw, y0 + bh))


MOVES = {
    "zoom": dict(z0=1.0, z1=1.25, c0=(320, 180), c1=(320, 180)),
    "pan": dict(z0=1.5, z1=1.5, c0=(250, 150), c1=(330, 190)),
    "zoom and pan": dict(z0=1.0, z1=1.4, c0=(320, 180), c1=(300, 170)),
}


def _track(move, footage, drawer):
    frames = []
    for n in range(move.frames):
        src = _canvas(noise_seed=n if footage else None)
        move.fit(W, H)
        frames.append(_centroid(drawer(src, move, n)))
    return np.array(frames)


def _glides(track, move):
    expected = np.array([_expected(move, n) for n in range(move.frames)])
    err = np.abs(track - expected).max()
    steps = np.hypot(*np.diff(track, axis=0).T)
    want = np.hypot(*np.diff(expected, axis=0).T)
    mid = slice(10, -10)          # away from the eased ends, where the step is near zero
    return {"err": err,
            "moves_every_frame": bool((steps[mid] > 0.25 * want[mid]).all()),
            "even": float(np.abs(steps[mid] - want[mid]).max())}


@pytest.mark.parametrize("footage", [False, True], ids=["still", "footage"])
@pytest.mark.parametrize("name", list(MOVES))
def test_a_point_near_the_edge_glides_on_every_frame(name, footage):
    m = MOVES[name]
    move = motion.Move(m["z0"], m["z1"], m["c0"], m["c1"], 90, "linear")
    result = _glides(_track(move, footage, lambda f, mv, n: mv.draw(f, n)), move)
    assert result["err"] < 0.15, "the dot must sit where the camera puts it (%.3f px)" % result["err"]
    assert result["moves_every_frame"], "it must move on every frame, never stand still"
    assert result["even"] < 0.15, "no frame may jump (%.3f px off the even step)" % result["even"]


@pytest.mark.parametrize("name", list(MOVES))
def test_the_control_whole_pixel_steps_fail_the_same_test(name):
    """Teeth: drawn from a whole-pixel box, the same move must fail."""
    m = MOVES[name]
    move = motion.Move(m["z0"], m["z1"], m["c0"], m["c1"], 90, "linear")
    result = _glides(_track(move, False, _snapped), move)
    assert result["err"] > 0.15 or result["even"] > 0.15 or not result["moves_every_frame"]


def test_smooth_ease_starts_and_ends_exactly_and_gently():
    move = motion.Move(1.0, 1.5, (320, 180), (400, 200), 61, "smooth")
    assert move.at(0) == (1.0, 320.0, 180.0)
    z, x, y = move.at(60)
    assert abs(z - 1.5) < 1e-12 and abs(x - 400) < 1e-9 and abs(y - 200) < 1e-9
    zs = [move.at(n)[0] for n in range(61)]
    steps = np.diff(zs)
    assert steps[0] < steps[30] / 10 and steps[-1] < steps[30] / 10, "no jolt at either end"
    assert (steps > 0).all()


def test_zoom_is_even_on_a_log_scale():
    move = motion.Move(1.0, 2.0, (320, 180), (320, 180), 11, "linear")
    ratios = [move.at(n + 1)[0] / move.at(n)[0] for n in range(10)]
    assert max(ratios) - min(ratios) < 1e-12


def test_the_box_stays_inside_the_frame_and_whole_frame_is_untouched():
    assert motion.view_box(W, H, 2.0, 0, 0) == (0.0, 0.0, W / 2.0, H / 2.0)
    assert motion.view_box(W, H, 2.0, W, H) == (W / 2.0, H / 2.0, float(W), float(H))
    img = _canvas()
    assert motion.draw(img, 1.0) is img
    with pytest.raises(ValueError):
        motion.view_box(W, H, 0.9, 320, 180)
    with pytest.raises(ValueError):
        motion.Move(1.0, 1.2, (0, 0), (0, 0), 10, "bouncy")


def test_a_start_outside_the_frame_is_clamped_before_the_move_so_the_path_is_straight():
    """Asked to start at the top edge at zoom 1 (which only one centre can show), the camera
    starts from the middle and goes straight to the end - not along the edge and then away."""
    move = motion.Move(1.0, 1.4, (320, 60), (320, 100), 30, "linear").fit(W, H)
    assert move.c0 == (320.0, 180.0)
    ys = [move.at(n)[2] for n in range(30)]
    assert np.abs(np.diff(ys, 2)).max() < 1e-9, "a straight path, constant speed"


def test_the_stills_zoom_is_the_same_routine():
    img = _canvas()
    for z in (1.0, 1.013, 1.05):
        assert zoom_frame(img, z).tobytes() == motion.draw(img, z).tobytes()


def _source_video(path, seconds=2.0):
    """A small test video: the dot on a frame whose right half changes every frame."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=%dx%d:r=30:d=%.1f" % (W, H, seconds),
                    "-vf", "drawbox=x=0:y=0:w=%d:h=%d:color=black:t=fill,"
                           "drawbox=x=142:y=82:w=16:h=16:color=white:t=fill" % (W // 2, H),
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(path)], check=True)


def test_render_move_on_footage_writes_the_shot_it_was_asked_for(tmp_path):
    src, out = tmp_path / "take.mp4", tmp_path / "shot.mp4"
    _source_video(src, 2.0)
    frames = motion.render_move(src, out, 3.0, zoom=(1.0, 1.3), start=0.5, end=1.5, speed=2.0,
                                size=(W, H), fps=30, profile="master")
    info = probe(out)
    assert frames == 90
    assert (info["width"], info["height"]) == (W, H)
    assert abs(info["duration"] - 3.0) < 0.05, "1 s of source at x2 then held to fill 3 s"


def test_render_move_on_a_still_with_a_mask_before_the_camera(tmp_path):
    still, out = tmp_path / "shot.png", tmp_path / "shot.mp4"
    _canvas().save(still)
    motion.render_move(still, out, 1.0, zoom=(1.0, 1.0), size=(W, H), fps=30, profile="master",
                       before="drawbox=x=130:y=70:w=40:h=40:color=black:t=fill")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(H, W)
    assert a[82:98, 142:158].max() < 40, "the mask covered the dot before the camera saw it"


def test_a_missing_source_stops_with_ffmpeg_words(tmp_path):
    with pytest.raises(SystemExit):
        motion.render_move(tmp_path / "nothing.mp4", tmp_path / "o.mp4", 1.0, size=(W, H))


def test_a_source_of_another_shape_is_fitted_not_stretched(tmp_path):
    """A square stays square: a 1000x1000 picture in a 16:9 frame gets bars, never a squash."""
    src, out = tmp_path / "square.png", tmp_path / "shot.mp4"
    img = Image.new("RGB", (1000, 1000), (0, 0, 0))
    ImageDraw.Draw(img).rectangle((300, 300, 699, 699), fill=(255, 255, 255))
    img.save(src)
    motion.render_move(src, out, 0.2, size=(W, H), fps=30, profile="master")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(H, W) > 128
    ys, xs = np.nonzero(a)
    width, height = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    assert abs(width - height) <= 2, "the square came out %dx%d" % (width, height)
    assert abs(height - 144) <= 2, "fitted to the frame height: 400 * 360/1000"


def test_a_failed_move_leaves_no_file_a_rerun_would_take_as_done(tmp_path):
    src, out = tmp_path / "take.mp4", tmp_path / "shot.mp4"
    _source_video(src, 1.0)
    with pytest.raises(SystemExit):
        motion.render_move(src, out, 1.0, start=10.0, size=(W, H))     # past the end: no frames
    assert not out.exists()
    assert not list(tmp_path.glob("*.partial*"))
