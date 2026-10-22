"""Where burned captions sit."""

import pytest

from ccvideo.edit.render import caption_baseline


def test_default_placement_is_unchanged():
    assert caption_baseline(1080, "centre") == int(1080 * 0.555)
    assert caption_baseline(1080, "strip") == int(1080 * 0.86)


def test_bottom_moves_the_big_style_below_the_pictures():
    assert caption_baseline(1080, "centre", position="bottom") == int(1080 * 0.95)


def test_an_unknown_position_stops_the_render():
    with pytest.raises(SystemExit):
        caption_baseline(1080, "centre", position="top")
