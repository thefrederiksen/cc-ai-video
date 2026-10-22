"""A caption cue never runs across a pause."""

from ccvideo import cues


def w(start, end, word):
    return {"start": start, "end": end, "word": word}


def test_a_long_pause_ends_the_cue():
    words = [w(0.0, 0.3, " no"), w(0.3, 0.6, " long"), w(0.6, 1.0, " contract,"),
             w(3.0, 3.3, " new"), w(3.3, 3.7, " product,")]
    built = cues.build(words, "centre")
    assert not any("CONTRACT" in c["plain"] and "NEW" in c["plain"] for c in built)
    new = next(c for c in built if "NEW" in c["plain"])
    assert new["start"] >= 3.0, "the next line went up before it was said"


def test_a_short_gap_does_not_split():
    words = [w(0.0, 0.3, " What"), w(0.5, 0.7, " if"), w(0.8, 1.0, " you")]
    assert len(cues.build(words, "centre")) == 1
