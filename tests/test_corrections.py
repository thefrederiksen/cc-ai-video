"""Caption corrections read from a file: every entry is checked before anything renders."""

import json

import pytest

from ccvideo.cli import load_corrections
from ccvideo import cues


def write(tmp_path, data):
    path = tmp_path / "corrections.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_pairs_load_in_order(tmp_path):
    pairs = load_corrections(write(tmp_path, [[r"\bchat tbt\b", "ChatGPT"], ["Rose and Blatt", "Rosenblatt"]]))
    assert pairs == [(r"\bchat tbt\b", "ChatGPT"), ("Rose and Blatt", "Rosenblatt")]


def test_a_loaded_correction_reaches_the_caption(tmp_path):
    pairs = load_corrections(write(tmp_path, [[r"\bchat tbt\b", "ChatGPT"]]))
    words = [{"start": 0.0, "end": 0.4, "word": " chat"}, {"start": 0.4, "end": 0.8, "word": " TBT"},
             {"start": 0.8, "end": 1.2, "word": " works."}]
    text = " ".join(c["text"] for c in cues.build(words, "full", corrections=pairs))
    assert "ChatGPT" in text and "TBT" not in text


def test_a_missing_file_stops(tmp_path):
    with pytest.raises(SystemExit):
        load_corrections(tmp_path / "nope.json")


@pytest.mark.parametrize("bad", [{"a": "b"}, [["only one"]], [["a", 1]], ["a", "b"]])
def test_anything_but_a_list_of_string_pairs_stops(tmp_path, bad):
    with pytest.raises(SystemExit):
        load_corrections(write(tmp_path, bad))


def test_an_invalid_pattern_stops_rather_than_being_skipped(tmp_path):
    with pytest.raises(SystemExit):
        load_corrections(write(tmp_path, [["(unclosed", "x"]]))
