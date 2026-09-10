"""The voice, and the cache that makes editing cheap.

THE CACHE IS THE POINT. A segment's mp3 is keyed on the exact spoken text PLUS a fingerprint
of the voice. Edit one paragraph and one paragraph is re-synthesised; the other sixty are read
off disk, so a re-render is free and near-instant. Change the provider, the model, the voice
or the narration direction and the whole cache misses ON PURPOSE - a video half-narrated by
one voice and half by another must never be able to ship.

That key is a compatibility surface, not an implementation detail. It is computed here exactly
as the assembler this library replaces computed it, so a project with a warm cache re-renders
from that cache, free, and produces a byte-identical file. Anything that changes the digest -
the model name, the voice name, the brand's narration instructions, the pronunciation rules -
re-synthesises every segment and is billed. Change them only when the voice really changed.

Providers
    openai       gpt-4o-mini-tts, steered per brand. Key: OPENAI_API_KEY, or whatever
                 CCVIDEO_OPENAI_KEY_NAME names instead.
    elevenlabs   the long-form narrator, and the only provider that returns exact character
                 timings for what it said. Key: ELEVENLABS_API_KEY.

Keys are read at the moment of use and never written anywhere.
"""

import hashlib
import json
import os
import urllib.request
from pathlib import Path

from . import credentials
from .brand import spoken
from .shell import probe, run

OPENAI_MODEL = "gpt-4o-mini-tts"
OPENAI_VOICE = "onyx"
# The environment variable the OpenAI key is read from. Standard by default, and overridable
# because an organisation that keeps several keys names them for what each one pays for.
# It is NOT part of the voice cache key: where a key is read from says nothing about how the
# narration sounds, so pointing at a differently named key must not re-buy a whole video.
OPENAI_KEY_NAME = os.environ.get("CCVIDEO_OPENAI_KEY_NAME", "").strip() or "OPENAI_API_KEY"
OPENAI_URL = "https://api.openai.com/v1/audio/speech"

EL_VOICE_ID = "BtWabtumIemAotTjP5sk"
EL_MODEL_ID = "eleven_multilingual_v2"
EL_KEY_NAME = "ELEVENLABS_API_KEY"
EL_SETTINGS = {
    "stability": 0.55,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
}

MIN_AUDIO_BYTES = 2000


class Voice:
    """A provider, a voice, and the brand direction it reads with.

    Held as one object because all three go into the cache key together: it is not meaningful
    to change one without missing the cache, and every past bug in this area came from a
    caller that changed one and expected the others to keep their cached audio.
    """

    def __init__(self, brand, provider="openai", voice=None):
        if provider not in ("openai", "elevenlabs"):
            raise SystemExit("unknown voice provider %r - have openai, elevenlabs" % provider)
        self.brand = brand
        self.provider = provider
        self.voice = voice or (OPENAI_VOICE if provider == "openai" else EL_VOICE_ID)

    @property
    def key_name(self):
        return OPENAI_KEY_NAME if self.provider == "openai" else EL_KEY_NAME

    def api_key(self, credentials_path=None):
        return credentials.get(self.key_name, credentials_path)

    def fingerprint(self):
        """Identifies the exact voice. Any change here misses the cache, which is the point."""
        if self.provider == "openai":
            return "openai" + OPENAI_MODEL + self.voice + self.brand["instructions"]
        return ("elevenlabs" + self.voice + EL_MODEL_ID
                + json.dumps(EL_SETTINGS, sort_keys=True))

    def digest(self, text):
        """The cache digest for one block of spoken text. Ten hex characters, as shipped."""
        return hashlib.sha1(
            (self.fingerprint() + spoken(self.brand, text)).encode("utf-8")
        ).hexdigest()[:10]

    def describe(self):
        if self.provider == "openai":
            return "OpenAI %s / %s (key %s)" % (self.voice, OPENAI_MODEL, OPENAI_KEY_NAME)
        return "ElevenLabs %s / %s (key %s)" % (self.voice, EL_MODEL_ID, EL_KEY_NAME)


def cache_path(cache_dir, segment_id, text, voice):
    """Where this segment's audio lives. The name carries the digest, so a stale file cannot
    be mistaken for a fresh one and two texts can never collide on one path."""
    return Path(cache_dir) / ("seg-%s-%s.mp3" % (segment_id, voice.digest(text)))


def cached(path):
    return path.exists() and path.stat().st_size > MIN_AUDIO_BYTES


def synthesise(text, voice, out_path, api_key):
    """Speak `text` to `out_path`. Returns the path. Overwrites whatever was there."""
    said = spoken(voice.brand, text)
    if voice.provider == "openai":
        body = json.dumps({
            "model": OPENAI_MODEL,
            "voice": voice.voice,
            "input": said,
            "instructions": voice.brand["instructions"],
            "response_format": "mp3",
        }).encode("utf-8")
        request = urllib.request.Request(
            OPENAI_URL, data=body,
            headers={"authorization": "Bearer " + api_key,
                     "content-type": "application/json"})
    else:
        body = json.dumps({
            "text": said,
            "model_id": EL_MODEL_ID,
            "voice_settings": EL_SETTINGS,
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://api.elevenlabs.io/v1/text-to-speech/%s?output_format=mp3_44100_128"
            % voice.voice,
            data=body,
            headers={"xi-api-key": api_key, "content-type": "application/json"})

    with urllib.request.urlopen(request) as response:
        data = response.read()
    if len(data) < MIN_AUDIO_BYTES:
        raise SystemExit(
            "the voice returned %d bytes for %r - that is not audio, and a short file would "
            "render as a silent segment nobody notices" % (len(data), said[:60]))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return out_path


def speak(text, voice, cache_dir, segment_id, api_key=None, allow_synthesis=True,
          credentials_path=None):
    """The cached voice for one segment. Returns (path, was_synthesised)."""
    path = cache_path(cache_dir, segment_id, text, voice)
    if cached(path):
        return path, False
    if not allow_synthesis:
        raise SystemExit(
            "segment %s has no cached audio at %s and synthesis is disabled.\n"
            "Either drop --no-tts, or check that the voice and the brand instructions are "
            "the ones the cache was built with - a changed fingerprint misses every segment."
            % (segment_id, path.name))
    key = api_key or voice.api_key(credentials_path)
    return synthesise(text, voice, path, key), True


def join(parts, out_path):
    """Join narration mp3s into one file, RE-ENCODING.

    Never `-c copy`. Every mp3 carries encoder padding at each end, and a stream copy keeps all
    of it: the joined file runs longer than the sum of its parts, by a little at each seam, and
    captions timed off the parts drift further the longer the video runs. That is a real defect
    that shipped once. Decoding and re-encoding through the concat filter drops the padding.

    The result is asserted against the sum of the parts, so a silent regression here fails the
    build rather than reaching a viewer as slow caption drift.
    """
    parts = [Path(p) for p in parts]
    if not parts:
        raise SystemExit("nothing to join")
    expected = sum(probe(p)["duration"] for p in parts)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for part in parts:
        cmd += ["-i", str(part)]
    joined = "".join("[%d:a]" % i for i in range(len(parts)))
    cmd += ["-filter_complex", "%sconcat=n=%d:v=0:a=1[a]" % (joined, len(parts)),
            "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2", str(out_path)]
    run(cmd)

    measured = probe(out_path)["duration"]
    drift = abs(measured - expected)
    if drift > 0.15:
        raise SystemExit(
            "joined narration is %.3fs but its %d parts sum to %.3fs (drift %.3fs). "
            "Captions timed off the parts would drift by that much by the end."
            % (measured, len(parts), expected, drift))
    return out_path, measured
