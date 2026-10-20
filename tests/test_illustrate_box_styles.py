"""The box styles a narration can name: a colour of its own, a ring that marks part of a
screenshot without covering it, and a box that changes style when it is lit."""
from ccvideo.illustrate.render import Canvas


def _frame(el, t=5.0):
    box = dict({"type": "box", "t_in": 0.0, "x": 400, "y": 300, "w": 600, "h": 200, "text": ""}, **el)
    return Canvas().frame([{"t0": 0.0, "t1": 60.0, "elements": [box]}], t)


def test_a_colour_box_is_filled_with_the_colour_the_scene_names():
    px = _frame({"style": "colour", "fill": [220, 40, 40]}).getpixel((700, 330))
    assert px[0] > 180 and px[1] < 90 and px[2] < 90


def test_a_ring_draws_an_outline_and_leaves_the_middle_uncovered():
    plain = Canvas().frame([{"t0": 0.0, "t1": 60.0, "elements": []}], 5.0)
    ring = _frame({"style": "ring"})
    assert ring.getpixel((700, 400)) == plain.getpixel((700, 400))
    assert ring.getpixel((402, 400)) != plain.getpixel((402, 400))


def test_a_lit_box_takes_its_lit_style_and_lit_fill():
    el = {"style": "solid", "lit_t": 3.0, "lit_style": "colour", "lit_fill": [40, 90, 230]}
    before = _frame(el, t=2.5).getpixel((700, 330))
    after = _frame(el, t=5.0).getpixel((700, 330))
    assert after[2] > 180 and after[0] < 90
    assert before != after
