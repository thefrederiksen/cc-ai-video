"""A paid voice has to be asked for on purpose.

The owner, 5 November 2026: he is the voice of everything DevThrottle publishes. He chose to have
that held in the code and in the seats' secrets rather than by a quality gate, so this is where it
is held for this tool: `local` - his own trained voice - is the default, and the two paid providers
are refused unless CCVIDEO_ALLOW_PAID_VOICE is set in the SAME command, where a reader of that
command can see it.

Before this, `--tts` defaulted to openai: forgetting one flag was enough to narrate a published
video in a stock voice, and nothing downstream objected.
"""
import pytest

from ccvideo import narrate
from ccvideo.narrate import Voice

BRAND = {"instructions": "Calm.", "pronunciation": [], "product": ""}


def test_the_default_voice_is_the_owners_own(monkeypatch):
    monkeypatch.delenv(narrate.PAID_VOICE_ENV, raising=False)
    v = Voice(BRAND)
    assert v.provider == "local"
    assert v.voice == narrate.LOCAL_VOICE
    assert v.api_key() is None


@pytest.mark.parametrize("provider", ["openai", "elevenlabs"])
def test_a_paid_provider_is_refused_without_the_switch(monkeypatch, provider):
    monkeypatch.delenv(narrate.PAID_VOICE_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        Voice(BRAND, provider=provider)
    said = str(e.value)
    assert provider in said
    assert narrate.PAID_VOICE_ENV in said, "the message must name the switch, not just refuse"


@pytest.mark.parametrize("provider", ["openai", "elevenlabs"])
def test_a_paid_provider_works_when_it_is_asked_for(monkeypatch, provider):
    monkeypatch.setenv(narrate.PAID_VOICE_ENV, "1")
    assert Voice(BRAND, provider=provider).provider == provider


def test_an_empty_switch_is_not_permission(monkeypatch):
    monkeypatch.setenv(narrate.PAID_VOICE_ENV, "   ")
    with pytest.raises(SystemExit):
        Voice(BRAND, provider="openai")


def test_the_switch_is_not_part_of_the_cache_key(monkeypatch):
    """Where permission came from says nothing about how the narration sounds. If this were in the
    key, turning the switch on would re-synthesise and re-bill every cached segment."""
    monkeypatch.setenv(narrate.PAID_VOICE_ENV, "1")
    with_switch = Voice(BRAND, provider="openai").digest("hello")
    monkeypatch.setenv(narrate.PAID_VOICE_ENV, "yes-please")
    assert Voice(BRAND, provider="openai").digest("hello") == with_switch


def test_the_owners_voice_keeps_the_digest_it_shipped_with(monkeypatch):
    """The whole point of defaulting to his voice is that warm caches stay warm."""
    monkeypatch.delenv(narrate.PAID_VOICE_ENV, raising=False)
    assert Voice(BRAND, provider="local").fingerprint() == "local" + narrate.LOCAL_COMMAND + "soren"
