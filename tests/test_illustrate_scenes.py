"""Scene anchors: every picture lands on a word the speaker says, never on a guessed second."""

import json

import pytest

from ccvideo.illustrate import scenes
from ccvideo.illustrate.scenes import Anchors

WORDS = [
    {"word": " The", "start": 0.0, "end": 0.2},
    {"word": " one", "start": 0.3, "end": 0.5},
    {"word": " after", "start": 0.6, "end": 0.8},
    {"word": " that.", "start": 0.9, "end": 1.1},
    {"word": " British", "start": 2.0, "end": 2.3},
    {"word": " -born", "start": 2.3, "end": 2.6},
    {"word": " the", "start": 3.0, "end": 3.1},
    {"word": " one", "start": 3.2, "end": 3.4},
    {"word": " after", "start": 3.5, "end": 3.7},
    {"word": " that", "start": 3.8, "end": 4.0},
]


def test_a_phrase_said_twice_resolves_to_the_one_after_the_cursor():
    a = Anchors(WORDS)
    assert a.find("one after that") == 0.3
    assert a.find("one after that", after=1.0) == 3.2


def test_a_hyphenated_word_is_quoted_as_the_transcript_splits_it():
    assert Anchors(WORDS).find("British born") == 2.0


def test_a_phrase_that_was_never_said_is_an_error_not_a_nearest_guess():
    with pytest.raises(SystemExit):
        Anchors(WORDS).find("one before that")


def test_a_number_anchor_is_seconds_plus_delay():
    assert Anchors(WORDS).resolve(1.5, after=0, delay=0.25) == 1.75


def test_load_resolves_scenes_elements_and_parts(tmp_path):
    words = tmp_path / "w.json"
    words.write_text(json.dumps({"segments": [{"words": WORDS}]}), encoding="utf-8")
    doc = {"scenes": [
        {"at": "The", "elements": [
            {"type": "text", "at": "one", "x": 0, "y": 0,
             "parts": [{"text": "a", "at": "The"}, {"text": "b", "at": "after"}]}]},
        {"at": "British born", "bg": "light", "elements": [
            {"type": "box", "text": "x", "at": "one after that", "until": "that",
             "x": 0, "y": 0, "w": 10, "h": 10}]},
    ]}
    path = tmp_path / "s.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    sc = scenes.load(path, words, 0.0, 5.0)
    assert [s["t0"] for s in sc] == [0.0, 2.0]
    assert sc[0]["t1"] == 2.0 and sc[1]["t1"] == 5.0
    assert sc[1]["bg"] == "light"
    text = sc[0]["elements"][0]
    assert text["t_in"] == 0.3 and [p["t_in"] for p in text["parts"]] == [0.0, 0.6]
    box = sc[1]["elements"][0]
    assert box["t_in"] == 3.2 and box["t_out"] == 3.8


def test_scenes_out_of_order_are_refused(tmp_path):
    words = tmp_path / "w.json"
    words.write_text(json.dumps({"segments": [{"words": WORDS}]}), encoding="utf-8")
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"scenes": [{"at": 3.0, "elements": []},
                                           {"at": 1.0, "elements": []}]}), encoding="utf-8")
    with pytest.raises(SystemExit):
        scenes.load(path, words)
