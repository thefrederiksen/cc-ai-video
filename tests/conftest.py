"""Shared fixtures.

The paid voice providers are refused unless CCVIDEO_ALLOW_PAID_VOICE is set in the same command
(narrate.PAID_VOICE_ENV, added 6 Nov 2026 - the owner's own voice is the default and forgetting a
flag must not be able to put a stock voice in a published video). Many tests here construct a paid
Voice on purpose, to compare cache digests or to check which model is sent; the switch is set for
the suite so those keep testing what they are about.

The switch itself is tested in test_paid_voice_switch.py, which clears it deliberately.
"""
import pytest

from ccvideo import narrate


@pytest.fixture(autouse=True)
def _allow_paid_voice_in_tests(monkeypatch):
    monkeypatch.setenv(narrate.PAID_VOICE_ENV, "1")
