"""The hook layout - the headline that becomes a Short's thumbnail."""

import pytest

from ccvideo import layout


def test_a_hook_is_one_or_two_lines():
    assert layout.parse_hook("Six agents.|One person.") == ["Six agents.", "One person."]
    assert layout.parse_hook("Just the one") == ["Just the one"]


def test_a_three_line_hook_is_refused():
    # Three lines cannot be read at browse size, which is the only size a thumbnail is seen at.
    with pytest.raises(SystemExit):
        layout.parse_hook("one|two|three")


def test_an_empty_hook_is_refused():
    with pytest.raises(SystemExit):
        layout.parse_hook("  |  ")


def test_a_hook_must_be_ascii():
    with pytest.raises(SystemExit):
        layout.parse_hook("caf" + chr(0xE9))


def test_the_footage_box_sits_above_the_caption_strip_and_below_the_brand_bar():
    width, height = 1080, 1920
    box_w, box_h, box_x, box_y = layout.hook_box(width, height, 1920, 1080)
    assert box_x >= 0 and box_x + box_w <= width
    assert box_y > height * layout.BRAND_BAND, "the box overlaps the brand bar"
    strip_top = height - int(height * layout.FOOTER_BAND) - int(height * layout.CAPTION_BAND)
    assert box_y + box_h <= strip_top + 1, "the box overlaps the caption strip"


def test_the_box_keeps_the_source_aspect_ratio():
    box_w, box_h, _, _ = layout.hook_box(1080, 1920, 1920, 1080)
    assert abs((box_w / box_h) - (1920 / 1080)) < 0.05


def test_box_dimensions_are_even():
    # An odd dimension breaks yuv420p encoding.
    box_w, box_h, _, _ = layout.hook_box(1080, 1920, 1920, 1080)
    assert box_w % 2 == 0 and box_h % 2 == 0


def test_a_source_that_would_become_a_sliver_is_refused():
    # A very tall source does not overflow - it is capped by height and its WIDTH collapses.
    # 400x3000 lands at 153px wide in a 1080 frame: a hook above a sliver, which is worse than
    # no hook at all, and nothing downstream would have noticed.
    with pytest.raises(SystemExit):
        layout.hook_box(1080, 1920, 400, 3000)


def test_an_ordinary_landscape_source_is_not_refused():
    assert layout.hook_box(1080, 1920, 1920, 1080)[0] > 1080 * layout.BOX_MIN_WIDTH


def test_captions_sit_below_the_footage_and_above_the_footer():
    height = 1920
    baseline = layout.caption_baseline(height)
    _, box_h, _, box_y = layout.hook_box(1080, height, 1920, 1080)
    assert baseline > box_y + box_h, "captions would sit on the picture"
    assert baseline < height - int(height * layout.FOOTER_BAND) + 1
