"""The transcript rules. Everything this library cuts on comes through here."""

import pytest

from ccvideo import words as wordlib


def w(start, end, word):
    return {"start": start, "end": end, "word": word}


def test_a_fragment_is_glued_to_the_word_before_it():
    # A transcriber emits the tail of a hyphenated or punctuated word as its own token. Joined
    # with a space they become words nobody said: "WELL -TRAINED", "100 ,000".
    glued = wordlib.glue_fragments([w(0, 1, "well"), w(1, 1.2, "-trained"),
                                    w(2, 3, "100"), w(3, 3.2, ",000")])
    assert [x["word"] for x in glued] == ["well-trained", "100,000"]
    assert glued[0]["end"] == 1.2


def test_a_glued_fragment_carries_the_end_time_of_its_tail():
    glued = wordlib.glue_fragments([w(0, 1, "it"), w(1, 1.4, "'s")])
    assert glued[0]["start"] == 0 and glued[0]["end"] == 1.4


def test_join_never_puts_a_space_before_a_fragment():
    tokens = ["100", ",000"]
    assert wordlib.join_tokens(tokens, tokens) == "100,000"
    assert wordlib.join_tokens(["a", "b"], ["a", "b"]) == "a b"


def test_terminal_punctuation_ends_a_sentence():
    words = [w(0, 1, "Done."), w(1.0, 2, "Next")]
    assert wordlib.is_boundary(words, 0)


def test_a_long_pause_ends_a_sentence_even_without_punctuation():
    # Run-on speakers get almost no punctuation from a transcriber, so the pause is all there is.
    words = [w(0, 1, "anyway"), w(1.6, 2, "right")]
    assert wordlib.is_boundary(words, 0)


def test_a_capitalised_opener_alone_does_not_end_a_sentence():
    # THE REGRESSION THIS FILE EXISTS FOR. "I" is a sentence opener and also an ordinary word
    # mid-clause, and a transcriber capitalises it wherever it falls. Without requiring a
    # breath, "What am I working on here" contains a boundary after "am", and a cut asked to
    # end on the previous sentence ended on the words "What am" instead. Measured on real
    # footage, timings taken from that file.
    words = [w(6.38, 6.50, "am"), w(6.50, 6.62, "I")]
    assert not wordlib.is_boundary(words, 0)


def test_a_capitalised_opener_after_a_breath_does_end_a_sentence():
    words = [w(0, 1.0, "working"), w(1.2, 1.5, "So")]
    assert wordlib.is_boundary(words, 0)


def test_window_rebases_times_to_the_clip():
    doc = {"segments": [{"words": [w(0, 1, "one"), w(10, 11, "two"), w(20, 21, "three")]}]}
    inside = wordlib.window(doc, 9.5, 12.0)
    assert [x["word"] for x in inside] == ["two"]
    assert inside[0]["start"] == pytest.approx(0.5)


def test_window_can_keep_source_times():
    doc = {"segments": [{"words": [w(10, 11, "two")]}]}
    assert wordlib.window(doc, 9.5, 12.0, rebase=False)[0]["start"] == 10


def test_a_transcript_without_word_timings_is_refused():
    # A segment-level transcript cannot be cut on word boundaries. Accepting one silently is
    # how a timeline of zero-length cuts gets built and looks fine in the JSON.
    with pytest.raises(SystemExit):
        wordlib.all_words({"segments": [{"start": 0, "end": 1, "text": "hello"}]})
