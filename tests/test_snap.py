"""snap() - the core primitive of the edit half."""

import pytest

from ccvideo.edit.snap import find_phrase, snap, snapped_to_sentence


def w(start, end, word):
    return {"start": start, "end": end, "word": word}


# Two sentences with a real pause between them, then a third.
WORDS = [
    w(2.64, 3.28, "Alright,"), w(3.48, 3.60, "let"), w(3.60, 3.74, "me"),
    w(3.74, 4.00, "show"), w(4.00, 4.20, "you"), w(4.20, 4.42, "Dev"),
    w(4.42, 5.02, "Throttle."),
    w(5.92, 6.38, "What"), w(6.38, 6.50, "am"), w(6.50, 6.62, "I"),
    w(6.62, 6.82, "working"), w(6.82, 7.14, "on"), w(7.14, 7.32, "here?"),
    w(8.00, 8.40, "Right."),
]


def test_a_requested_window_lands_on_sentence_boundaries():
    start, end, notes = snap(WORDS, 3.9, 4.9)
    assert snapped_to_sentence(notes)
    assert start <= 2.64 and 5.02 <= end <= 5.9


def test_padding_never_crosses_into_a_neighbouring_word():
    start, end, _ = snap(WORDS, 3.9, 4.9, pad=5.0)
    assert end < WORDS[7]["start"], "the pad reached into the next sentence"


def test_the_end_does_not_swallow_the_following_sentence():
    # A three second floor inherited from a Shorts renderer pushed the end past the requested
    # sentence and into the next one. The floor belongs to the target, not to snap().
    _, end, _ = snap(WORDS, 3.9, 5.0)
    assert end < 5.9, "snap swallowed the sentence after the one that was asked for"


def test_notes_say_when_only_a_word_boundary_was_found():
    words = [w(0, 1, "one"), w(1, 2, "two"), w(2, 3, "three")]
    _, _, notes = snap(words, 0.9, 2.1, reach=0.05)
    assert not snapped_to_sentence(notes)
    assert any("WORD" in n for n in notes)


def test_find_phrase_ignores_case_and_punctuation():
    start, end = find_phrase(WORDS, "let me show you dev throttle")
    assert start == pytest.approx(3.48)
    assert end == pytest.approx(5.02)


def test_a_phrase_that_was_never_said_is_an_error_not_a_guess():
    # Cutting the nearest-looking sentence instead would be a silently wrong cut, which is
    # worse than stopping and saying so.
    with pytest.raises(SystemExit):
        find_phrase(WORDS, "let me show you the invoice")


def test_snapping_against_an_empty_transcript_is_an_error():
    with pytest.raises(SystemExit):
        snap([], 1.0, 2.0)
