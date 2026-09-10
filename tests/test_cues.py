"""Caption cues, and the QA arithmetic that grades a finished file."""

from ccvideo import cues as cuelib
from ccvideo.qa.run import heard_ratio


def words(*pairs):
    """(word, seconds each) -> word timings running back to back from zero."""
    out, at = [], 0.0
    for word, length in pairs:
        out.append({"start": at, "end": at + length, "word": word})
        at += length
    return out


SENTENCE = words(("Most", 0.4), ("people", 0.4), ("run", 0.4), ("one", 0.4),
                 ("coding", 0.4), ("agent.", 0.4))


def test_the_centre_style_keeps_cues_short_enough_to_read():
    for cue in cuelib.build(SENTENCE, "centre"):
        assert len(cue["plain"].split()) <= 4
        assert len(cue["plain"]) <= 30


def test_the_centre_style_is_upper_case_and_the_strip_style_is_not():
    assert cuelib.build(SENTENCE, "centre")[0]["plain"].isupper()
    assert not cuelib.build(SENTENCE, "strip")[0]["plain"].isupper()


def test_a_cue_never_opens_on_the_back_half_of_a_word():
    # A break falling between "10" and ",000" put ",000 PROBABILITY THAT MOVE" on screen -
    # a string the speaker never uttered. The fragment rule has to run before cues are built.
    numbers = words(("There", 0.3), ("is", 0.3), ("a", 0.3), ("10", 0.3), (",000", 0.3),
                    ("to", 0.3), ("one", 0.3), ("chance.", 0.3))
    for cue in cuelib.build(numbers, "centre"):
        assert not cue["plain"].startswith((",", ".", "-", "'"))
    assert any("10,000" in cue["plain"] for cue in cuelib.build(numbers, "centre"))


def test_corrections_apply_across_a_word_boundary():
    # Every useful correction spans two words, so applying them per word matches none of them.
    heard = words(("cloud", 0.4), ("code", 0.4), ("is", 0.4), ("running.", 0.4))
    built = cuelib.build(heard, "strip",
                         corrections=[(r"\b(claude|cloud|clod) code\b", "Claude Code")])
    assert "Claude Code" in " ".join(c["plain"] for c in built)


def test_an_emphasised_word_is_coloured_and_the_rest_is_not():
    built = cuelib.build(SENTENCE, "centre", emphasis=["agent"], colour="&H00FFE600")
    coloured = [c for c in built if "\\c&H00FFE600" in c["text"]]
    assert coloured, "the emphasised word was not coloured"
    assert "AGENT" in coloured[0]["plain"]


def test_a_cue_too_brief_to_read_is_merged_or_held():
    flash = words(("Yes", 0.05), ("really", 0.05), ("now.", 0.05))
    for cue in cuelib.build(flash, "centre"):
        assert cue["end"] - cue["start"] >= 0.3


def test_a_fragment_left_by_a_cut_is_dropped_not_flashed():
    built = [{"start": 0.0, "end": 2.0, "text": "A", "plain": "A", "kind": "clip"},
             {"start": 2.0, "end": 2.05, "text": "B", "plain": "B", "kind": "clip"}]
    kept, dropped = cuelib.hold_into_gaps(built, until=2.06)
    assert [c["plain"] for c in dropped] == ["B"]
    assert [c["plain"] for c in kept] == ["A"]


def test_heard_ratio_counts_a_word_once_however_often_it_is_heard():
    # Matched against a pool, not a set. Against a set, one word repeated in the audio would
    # certify every occurrence of it in the script, and a video that said its first sentence
    # four times would score as if it had said all four.
    assert heard_ratio(["alpha", "alpha", "bravo"], "alpha alpha alpha alpha") == 2 / 3


def test_heard_ratio_ignores_words_too_short_to_be_evidence():
    # Two-letter words match by accident and inflate the score.
    assert heard_ratio(["to", "be", "orchestration"], "orchestration") == 1.0
