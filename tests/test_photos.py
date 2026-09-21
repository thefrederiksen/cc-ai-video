"""Real photographs: free licenses only, and the credit travels with the file."""

import json

import pytest

from ccvideo import photos


@pytest.mark.parametrize("name", ["Public domain", "CC0", "CC BY 2.0", "CC BY-SA 4.0",
                                  "CC BY-SA 3.0 de", "CC BY 4.0"])
def test_free_licenses_are_accepted(name):
    assert photos.is_free(name)


@pytest.mark.parametrize("name", ["CC BY-NC 2.0", "CC BY-ND 4.0", "CC BY-NC-SA 3.0", "Fair use",
                                  "All rights reserved", "", None])
def test_anything_else_is_refused(name):
    assert not photos.is_free(name)


def test_the_credit_line_names_title_author_and_license():
    info = {"title": "File:Lettvin Pitts.jpg", "author": "Iapx86", "license": "CC BY-SA 3.0"}
    assert photos.credit_line(info) == "Lettvin Pitts - Iapx86, CC BY-SA 3.0, via Wikimedia Commons"


def test_credits_lists_every_photo_and_refuses_one_without_a_credit(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"")
    (tmp_path / "a.jpg.json").write_text(json.dumps({"credit": "A - x, CC0, via Wikimedia Commons",
                                                    "source": "https://commons.example/a"}))
    assert photos.credits(tmp_path) == ["a.jpg: A - x, CC0, via Wikimedia Commons  https://commons.example/a"]
    (tmp_path / "b.jpg").write_bytes(b"")
    with pytest.raises(SystemExit):
        photos.credits(tmp_path)
