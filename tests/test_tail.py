"""Calibration for the TAIL check.

The numbers and the mis-hearings below are measured, not invented. They come from running the
fade principle across a corpus of 66 finished shorts: the audio said 20 of them ended with less
trailing silence than their fade, and listening said only 3 had actually lost a word.

That ratio is the whole design constraint. A check that turned the other 17 into failures would
be switched off within a week, and then the 3 would go unnoticed too.
"""

import inspect

from ccvideo.qa import run as qa

# Every pair below is a word that WAS spoken and WAS audible, and the token a transcriber
# returned instead. In each case the sentence completed and nothing was lost.
MISHEARD = [
    ("hacker", "call it the ethical habit"),
    ("interactive", "it's not interesting"),
    ("project", "redoing this entire process"),
    ("respond", "waiting for me to write"),
    ("throttle", "open up devconnect"),
]


def test_string_similarity_cannot_recover_these_and_that_is_the_point():
    # The design decision, held in place by the data that forced it. Four of these five are not
    # close enough in spelling for ANY similarity measure to rescue - "write" for "respond" is
    # not a near miss at all - so a check that asked "is this exact word present", fuzzily or
    # otherwise, would call five audible endings a lost final word.
    from difflib import SequenceMatcher
    rescued = [word for word, heard in MISHEARD
               if any(SequenceMatcher(None, word, h).ratio() >= 0.6 for h in heard.split())]
    assert len(rescued) <= 1, (
        "if similarity started rescuing these, the temptation would be to make the transcript "
        "a verdict again - it still must not be")


def test_the_tail_check_makes_no_claim_about_which_word_survived():
    # It reports the transcript as evidence for a person and never as a verdict. If this ever
    # stops being true, the 17 false failures come back.
    source = inspect.getsource(qa.tail_check)
    assert "LISTEN" in source
    for verdict_on_words in ("survived", "not recognisable", "is missing"):
        assert verdict_on_words not in source


def test_the_deciding_signal_is_silence_not_a_transcript():
    source = inspect.getsource(qa.tail_check)
    decision = source[:source.index("hear(")]
    assert "trailing_silence" in decision, "the transcript is consulted before the audio decides"


def test_only_an_empty_ending_can_fail():
    source = inspect.getsource(qa.tail_check)
    assert source.count('return "FAIL"') == 1
    assert "NOTHING is" in source
