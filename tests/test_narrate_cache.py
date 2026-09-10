"""The voice cache key.

THIS IS A COMPATIBILITY SURFACE, NOT AN IMPLEMENTATION DETAIL. Projects hold caches of
synthesised narration keyed by these digests. A change here does not break a build - it
silently misses every cached segment, re-synthesises the whole video, and bills for it. On a
twenty minute script that is several thousand words of speech.

The value below is frozen deliberately. If this test fails, the question is not "what is the
new digest" - it is whether the voice genuinely changed. If it did, the miss is correct and
the frozen value is updated in the same commit that changed the voice. If it did not,
something was tidied that should not have been.
"""

import json

import pytest

from ccvideo import brand as brandlib
from ccvideo.narrate import Voice, cache_path

PHRASE = "The quick brown fox says DevThrottle."

OTHER = {
    "other": {
        "bg": [0, 0, 0], "bg2": [10, 10, 10], "accent": [1, 2, 3], "rule": [4, 5, 6],
        "product": "Other",
        "pronunciation": [["DevThrottle", "Dev Throttle"]],
        "instructions": "A completely different narration direction.",
    }
}


@pytest.fixture
def other_brands(tmp_path):
    path = tmp_path / "brands.json"
    path.write_text(json.dumps(OTHER), encoding="utf-8")
    return str(path)


def test_the_default_brand_digest_is_frozen():
    assert Voice(brandlib.get("default"), "openai").digest(PHRASE) == "eb8c2ca77e"


def test_the_narration_direction_changes_the_digest(other_brands):
    # `instructions` is inside the fingerprint, so the same words read with different direction
    # are different audio and must not collide in one cache directory. This is also why brand
    # rows are configuration: a docstring tidy-up in this repository could not otherwise be
    # told apart from a real change of voice.
    a = Voice(brandlib.get("default"), "openai").digest(PHRASE)
    b = Voice(brandlib.get("other", other_brands), "openai").digest(PHRASE)
    assert a != b


def test_changing_the_voice_misses_the_cache():
    brand = brandlib.get("default")
    assert Voice(brand, "openai", "onyx").digest(PHRASE) \
        != Voice(brand, "openai", "alloy").digest(PHRASE)


def test_changing_the_provider_misses_the_cache():
    brand = brandlib.get("default")
    assert Voice(brand, "openai").digest(PHRASE) != Voice(brand, "elevenlabs").digest(PHRASE)


def test_pronunciation_is_inside_the_key(other_brands):
    # spoken() rewrites the name before synthesis, so the key must cover the rewritten text -
    # otherwise changing a pronunciation rule quietly reuses audio saying the old thing.
    brand = brandlib.get("other", other_brands)
    assert brandlib.spoken(brand, PHRASE) == "The quick brown fox says Dev Throttle."
    plain = dict(brand)
    plain["pronunciation"] = []
    assert Voice(brand, "openai").digest(PHRASE) != Voice(plain, "openai").digest(PHRASE)


def test_the_cache_filename_carries_the_segment_and_the_digest(tmp_path):
    voice = Voice(brandlib.get("default"), "openai")
    path = cache_path(tmp_path, "007", PHRASE, voice)
    assert path.name == "seg-007-eb8c2ca77e.mp3"


def test_two_texts_never_share_a_path(tmp_path):
    voice = Voice(brandlib.get("default"), "openai")
    assert cache_path(tmp_path, "001", "one thing", voice) \
        != cache_path(tmp_path, "001", "another thing", voice)


def test_where_the_key_is_read_from_is_not_part_of_the_digest(monkeypatch):
    # An organisation naming its key for what it pays for must not re-buy every video.
    import importlib

    from ccvideo import narrate
    before = Voice(brandlib.get("default"), "openai").digest(PHRASE)
    monkeypatch.setenv("CCVIDEO_OPENAI_KEY_NAME", "SOME_OTHER_KEY_NAME")
    importlib.reload(narrate)
    try:
        assert narrate.OPENAI_KEY_NAME == "SOME_OTHER_KEY_NAME"
        assert narrate.Voice(brandlib.get("default"), "openai").digest(PHRASE) == before
    finally:
        monkeypatch.delenv("CCVIDEO_OPENAI_KEY_NAME")
        importlib.reload(narrate)


def test_a_brand_file_row_replaces_a_builtin_rather_than_merging(tmp_path):
    # Half one palette and half another, with no way to tell which is on screen, is worse than
    # either. An overlay row wins outright.
    path = tmp_path / "brands.json"
    path.write_text(json.dumps({"default": OTHER["other"]}), encoding="utf-8")
    assert brandlib.get("default", str(path))["product"] == "Other"
