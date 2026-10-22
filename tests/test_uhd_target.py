"""The 4K YouTube target and captions that scale with the frame."""

from ccvideo import targets
from ccvideo.edit.render import caption_size, fit_filter


def test_youtube_uhd_is_4k_with_a_master_encode():
    t = targets.target("youtube-uhd")
    assert (t["width"], t["height"]) == (3840, 2160)
    assert int(t["encode"]["crf"]) <= 16


def test_captions_scale_with_the_frame():
    assert caption_size(1080, "centre") == 80
    assert caption_size(2160, "centre") == 160
    assert caption_size(2160, "strip") == 112


def test_upscaling_uses_lanczos():
    assert "flags=lanczos" in fit_filter(1920, 1080, 3840, 2160)
