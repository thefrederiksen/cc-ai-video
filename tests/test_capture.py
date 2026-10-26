"""ccvideo capture: a page posed frame by frame through window.ccvideoFrame(t), under a narration."""
import json
import subprocess

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from ccvideo import budget
from ccvideo.capture import _check, capture, frame_count, run_count, shoot, split
from ccvideo.cli import main

PAGE = """<!doctype html><html><body style="margin:0;background:#000">
<div id=b style="position:absolute;top:0;width:100px;height:100px;background:#fff"></div>
<script>window.ccvideoFrame = t => { document.getElementById('b').style.left = (t * 500) + 'px' }</script>
</body></html>"""


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"]


@pytest.fixture
def tone(tmp_path):
    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(wav)], check=True)
    return wav


def test_a_page_is_recorded_at_the_targets_size_with_the_narration(tmp_path, tone):
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    out = tmp_path / "out.mp4"
    assert main(["capture", str(page), "--audio", str(tone), "--out", str(out), "--end", "1",
                 "--target", "shorts", "--fps", "4"]) == 0
    streams = {s["codec_type"]: s for s in _probe(out)}
    assert (streams["video"]["width"], streams["video"]["height"]) == (1080, 1920)
    assert "audio" in streams


def test_a_page_without_the_frame_hook_is_refused(tmp_path, tone):
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><html><body></body></html>", encoding="utf-8")
    with pytest.raises(SystemExit, match="ccvideoFrame"):
        main(["capture", str(page), "--audio", str(tone), "--out", str(tmp_path / "o.mp4"),
              "--end", "1", "--fps", "4"])


def _frames(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                          "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return int(out.strip())


# --- the split: by global frame index, never by seconds ---------------------------------------

@pytest.mark.parametrize("frames,workers", [(900, 1), (900, 4), (900, 8), (11627, 16), (7, 3),
                                            (11934, 13)])
def test_every_frame_is_in_exactly_one_run_and_runs_differ_by_at_most_one(frames, workers):
    bounds = split(frames, workers)
    assert bounds[0] == 0 and bounds[-1] == frames
    sizes = [bounds[n + 1] - bounds[n] for n in range(workers)]
    assert sum(sizes) == frames
    assert max(sizes) - min(sizes) <= 1


def test_splitting_by_seconds_loses_frames_and_by_index_does_not():
    # Eight 7.5 s pieces of a 60 s clip at 15 fps, each rounding its own count: 8 x 112 = 896.
    by_seconds = sum(frame_count(7.5 * n, 7.5 * (n + 1), 15) for n in range(8))
    assert by_seconds == 896
    bounds = split(frame_count(0, 60, 15), 8)
    assert bounds[-1] == 900


def test_no_run_is_shorter_than_two_seconds_and_the_budget_caps_the_rest():
    assert run_count(8, 30, 15) == 1          # 2 s of frames: one run
    assert run_count(8, 60, 15) == 2          # 4 s: two runs of 2 s
    assert run_count(1, 900, 15) == 1
    assert run_count(10 ** 6, 10 ** 6, 15) == budget.cores()


# --- a stalled screenshot: fresh browser, same frame, evidence printed ------------------------

class _Tab:
    def __init__(self, stalls, throws=None):
        self.stalls = stalls
        self.throws = throws
        self.posed = []

    def evaluate(self, script, t):
        self.posed.append(t)
        if self.throws:
            raise PlaywrightError(self.throws)

    def screenshot(self, type, timeout):
        if self.stalls:
            self.stalls -= 1
            raise PlaywrightTimeout("Page.screenshot: Timeout %dms exceeded." % timeout)
        return b"png"


class _FakeBrowser:
    def __init__(self, stalls=0, throws=None):
        self.tab = _Tab(stalls, throws)
        self.errors = []
        self.closed = False
        self.shots = 0

    def broken(self):
        return False

    def evidence(self):
        return "page crashed=False, browser connected=True, page errors=0"

    def close(self):
        self.closed = True


def test_a_stalled_frame_is_retaken_in_a_fresh_browser_and_the_stall_is_reported():
    first = _FakeBrowser(stalls=1)
    fresh = []

    def open_browser():
        fresh.append(_FakeBrowser())
        return fresh[-1]

    said = []
    png, browser = shoot(open_browser, first, 7, 3.5, 30, say=lambda *a, **k: said.append(a[0]))
    assert png == b"png"
    assert first.closed and browser is fresh[0]
    assert browser.tab.posed == [3.5]                 # the SAME time, posed again
    assert "frame 7 (3.50s) stalled" in said[0] and "crashed=False" in said[0]
    assert "Timeout 30000ms exceeded" in said[0]      # Playwright's own words are kept


def test_a_frame_that_keeps_stalling_stops_the_run_with_the_evidence():
    with pytest.raises(SystemExit, match=r"frame 7 \(3.50s\) stalled 3 times.*crashed=False"):
        shoot(lambda: _FakeBrowser(stalls=99), _FakeBrowser(stalls=99), 7, 3.5, 30,
              say=lambda *a, **k: None)


def test_a_page_that_throws_is_a_defect_not_a_stall_and_is_not_retried():
    opened = []
    with pytest.raises(SystemExit, match=r"the page threw at frame 2 \(0.50s\).*photo 7 missing"):
        shoot(lambda: opened.append(1), _FakeBrowser(throws="Error: photo 7 missing"), 2, 0.5,
              30, say=lambda *a, **k: None)
    assert opened == []


# --- real browsers ---------------------------------------------------------------------------

MOVING = """<!doctype html><html><body style="margin:0;background:#000">
<div id=b style="position:absolute;top:0;width:40px;height:100px;background:#fff"></div>
<script>window.ccvideoFrame = t => { document.getElementById('b').style.left = (t * 200) + 'px' }</script>
</body></html>"""


def _box_left(video, n):
    """Where the white box is in frame n: the first bright column of row 50."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf",
                          "select=eq(n\\,%d),format=gray,crop=1080:2:0:50" % n, "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return next(x for x, v in enumerate(raw[:1080]) if v > 128)


def test_runs_in_parallel_join_in_order_each_frame_at_its_own_time(tmp_path):
    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=5", str(wav)], check=True)
    page = tmp_path / "page.html"
    page.write_text(MOVING, encoding="utf-8")
    out = tmp_path / "out.mp4"
    assert main(["capture", str(page), "--audio", str(wav), "--out", str(out), "--end", "4.5",
                 "--target", "shorts", "--fps", "4", "--workers", "2"]) == 0
    assert _frames(out) == 18
    # Frame n is at n / 4 s and the box at 200 px a second: 50 px a frame, in both runs.
    for n in (1, 8, 9, 10, 17):
        assert abs(_box_left(out, n) - 50 * n) <= 2, n
    assert not list(tmp_path.glob("out.parts*"))


def _tone(path, seconds):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=%s" % seconds, str(path)], check=True)
    return path


def test_a_narration_short_by_a_frame_is_refused_and_the_suggested_end_captures(tmp_path):
    short = _tone(tmp_path / "short.wav", 0.6)
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    out = tmp_path / "out.mp4"
    with pytest.raises(SystemExit, match=r"Bring --end in to at most 0\.500") as err:
        capture(page, short, out, 0, 1, 1080, 1920, fps=4)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["page.html", "short.wav"]
    suggested = float(str(err.value).rsplit("at most ", 1)[1].rstrip("."))
    capture(page, short, out, 0, suggested, 1080, 1920, fps=4)
    assert _frames(out) == 2


class _PastThePreflight(Exception):
    pass


@pytest.mark.parametrize("seconds,fps", [(0.95, 4), (1.15, 4), (882.17, 15)])
def test_a_narration_ending_inside_the_last_frame_is_accepted(tmp_path, monkeypatch, seconds,
                                                              fps):
    # 882.17 s at 15 fps is the command from devthrottle#4496: 13233 frames, 882.20 s.
    from ccvideo import capture as cap
    narration = _tone(tmp_path / "n.wav", seconds)
    frames = frame_count(0, seconds, fps)
    assert frames / float(fps) > seconds                  # it does NOT end on a frame

    def stop(count, workers):
        raise _PastThePreflight(count)

    monkeypatch.setattr(cap, "split", stop)
    with pytest.raises(_PastThePreflight) as reached:
        cap.capture(tmp_path / "p.html", narration, tmp_path / "o.mp4", 0, seconds,
                    1080, 1920, fps=fps)
    assert reached.value.args == (frames,)


def test_a_narration_ending_inside_the_last_frame_captures_every_frame(tmp_path):
    narration = _tone(tmp_path / "n.wav", 0.95)
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    out = tmp_path / "out.mp4"
    capture(page, narration, out, 0, 0.95, 1080, 1920, fps=4)
    assert _frames(out) == 4


def test_the_check_refuses_a_file_short_of_the_frames_captured(tmp_path, tone):
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=64x64:r=4:d=0.75", "-i", str(tone), "-shortest",
                    "-c:v", "libx264", "-c:a", "aac", str(clip)], check=True)
    with pytest.raises(SystemExit, match="holds 3 frames, 4 were captured"):
        _check(clip, 4, 4)


def test_runs_in_parallel_join_to_exactly_the_frames_captured(tmp_path):
    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=5", str(wav)], check=True)
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    out = tmp_path / "out.mp4"
    assert main(["capture", str(page), "--audio", str(wav), "--out", str(out), "--end", "4.5",
                 "--target", "shorts", "--fps", "4", "--workers", "2"]) == 0
    assert _frames(out) == 18
    assert "audio" in {s["codec_type"] for s in _probe(out)}
    assert not list(tmp_path.glob("out.parts*"))


STALL = """<!doctype html><html><body>
<script>window.ccvideoFrame = t => { if (t >= 0.5) setTimeout(() => {
  const end = Date.now() + 20000; while (Date.now() < end) {} }, 0) }</script>
</body></html>"""


def test_a_page_that_stalls_leaves_nothing_at_the_output(tmp_path, tone):
    page = tmp_path / "page.html"
    page.write_text(STALL, encoding="utf-8")
    out = tmp_path / "out.mp4"
    with pytest.raises(SystemExit, match="stalled 3 times"):
        capture(page, tone, out, 0, 1, 1080, 1920, fps=4, timeout=1)
    assert not out.exists()
    assert not list(tmp_path.glob("out.parts*"))


def test_a_refused_page_leaves_nothing_behind(tmp_path, tone):
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><html><body></body></html>", encoding="utf-8")
    with pytest.raises(SystemExit, match="ccvideoFrame"):
        main(["capture", str(page), "--audio", str(tone), "--out", str(tmp_path / "o.mp4"),
              "--end", "1", "--fps", "4"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["page.html", "tone.wav"]
