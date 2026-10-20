"""ccvideo capture: a page posed frame by frame through window.ccvideoFrame(t), under a narration."""
import json
import subprocess

import pytest

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
