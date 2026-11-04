"""The local voice: its own cache key, no key needed, and a budget that stops cleanly."""
import time

import pytest

from ccvideo import narrate

BRAND = {"instructions": "x"}


def test_local_has_its_own_fingerprint_and_needs_no_key():
    local = narrate.Voice(BRAND, "local")
    assert local.voice == narrate.LOCAL_VOICE
    assert local.api_key() is None
    assert local.digest("hello") != narrate.Voice(BRAND, "elevenlabs").digest("hello")
    assert local.digest("hello") != narrate.Voice(BRAND, "openai").digest("hello")


def test_voice_budget_is_local_only():
    with pytest.raises(SystemExit):
        narrate.Voice(BRAND, "elevenlabs", budget=60)


def test_spent_budget_stops_with_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(narrate.shutil, "which", lambda c: "/bin/true")
    v = narrate.Voice(BRAND, "local", budget=60)
    v.deadline = time.time() - 1
    with pytest.raises(SystemExit) as e:
        narrate.synthesise_local("hello there.", v, tmp_path / "seg-001-abc.mp3")
    assert e.value.code == narrate.PARTIAL_EXIT
