"""Script parsing and the lint that runs before anything is spent."""

import pytest

from ccvideo import brand as brandlib
from ccvideo.generate import script as scriptlib

BRAND = brandlib.get("default")

GOOD = """# TITLE: A name
# FOOTER: example.com

=== SEG 001
IMAGE: CARD
CARD: kicker|A title|A subtitle
CHAPTER: Opening
TEXT:
This is what the narrator says.

=== SEG 002
IMAGE: shot.png
TEXT:
And this is the second thing.
"""


def write(tmp_path, text, shots=("shot.png",)):
    path = tmp_path / "script.txt"
    path.write_text(text, encoding="utf-8")
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir(exist_ok=True)
    for name in shots:
        (shots_dir / name).write_bytes(b"not really a png")
    return path, str(shots_dir)


def test_a_good_script_parses_and_lints_clean(tmp_path):
    path, shots = write(tmp_path, GOOD)
    doc = scriptlib.parse(path)
    assert doc.title == "A name" and doc.footer == "example.com"
    assert [s.kind for s in doc.segments] == ["card", "image"]
    assert doc.segments[0].chapter == "Opening"
    assert scriptlib.lint(doc, shots, BRAND) == []


def test_a_missing_frame_is_an_error(tmp_path):
    path, shots = write(tmp_path, GOOD, shots=())
    problems = scriptlib.lint(scriptlib.parse(path), shots, BRAND)
    assert scriptlib.has_errors(problems)


def test_markup_that_would_be_read_aloud_is_an_error(tmp_path):
    text = GOOD.replace("This is what the narrator says.", "See **the docs** in README.md")
    path, shots = write(tmp_path, text)
    problems = scriptlib.lint(scriptlib.parse(path), shots, BRAND)
    assert scriptlib.has_errors(problems)


def test_the_title_card_cap_advises_and_does_not_refuse(tmp_path):
    # The assembler this replaces hard-refused a fourth card, and a deck-style video was worked
    # around by generating its cards outside the tool, which is worse than the thing prevented.
    body = "".join(
        "\n=== SEG %03d\nIMAGE: CARD\nCARD: k|Title %d|s\nTEXT:\nSome words here.\n" % (i, i)
        for i in range(1, 7))
    path, shots = write(tmp_path, "# TITLE: T\n# FOOTER: f\n" + body)
    problems = scriptlib.lint(scriptlib.parse(path), shots, BRAND, card_advice=3)
    assert not scriptlib.has_errors(problems)
    assert any("title cards" in msg for _, _, msg in problems)


SHOT = """# TITLE: A name
# FOOTER: example.com

=== SEG 001
IMAGE: SHOT
SHOT: the hygiene skill actually running
TEXT:
Here is the thing we have not filmed yet.
"""


def test_a_production_placeholder_is_reported_in_a_draft(tmp_path):
    path, shots = write(tmp_path, SHOT)
    doc = scriptlib.parse(path)
    assert doc.shots and doc.shots[0].shot == "the hygiene skill actually running"
    problems = scriptlib.lint(doc, shots, BRAND)
    assert not scriptlib.has_errors(problems)
    assert any("production placeholder" in msg for _, _, msg in problems)


def test_a_production_placeholder_cannot_be_published(tmp_path):
    # A twenty minute walkthrough went out at 53 percent placeholders and passed every machine
    # check, because nothing could tell a card for the viewer from a card for the crew.
    path, shots = write(tmp_path, SHOT)
    problems = scriptlib.lint(scriptlib.parse(path), shots, BRAND, publish=True)
    assert scriptlib.has_errors(problems)


def test_a_competitor_name_is_clean_by_default_and_flagged_when_positioning(tmp_path):
    # Naming a supported tool in a product tutorial is the feature list. The same name in a
    # video arguing against it is a different matter, so the policy is chosen per script.
    text = GOOD.replace("This is what the narrator says.", "It runs Claude Code and Cursor.")
    path, shots = write(tmp_path, text)
    doc = scriptlib.parse(path)
    assert scriptlib.lint(doc, shots, BRAND) == []
    flagged = scriptlib.lint(doc, shots, BRAND, words_policy="positioning")
    assert any("cursor" in msg for _, _, msg in flagged)


def test_a_script_with_no_segments_is_refused(tmp_path):
    path, _ = write(tmp_path, "# TITLE: T\n# FOOTER: f\n")
    with pytest.raises(SystemExit):
        scriptlib.parse(path)


def test_japanese_is_estimated_by_character_not_by_word():
    # Japanese has no spaces, so counting words called a three and a half minute video 23
    # seconds. Built from code points so this file holds no non-ASCII byte.
    japanese = "".join(chr(c) for c in (0x3053, 0x308C, 0x306F, 0x30C6, 0x30B9, 0x30C8)) * 60
    by_word = len(japanese.split()) / 150.0 * 60.0
    assert by_word < 1.0, "there are no spaces to count"
    assert scriptlib.speech_seconds(japanese, 150.0) > 60
