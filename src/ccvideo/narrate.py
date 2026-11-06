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
    THE DEFAULT IS `local` - the owner's own trained voice - and the two paid providers below are
    refused unless CCVIDEO_ALLOW_PAID_VOICE=1 is set in the same command. Forgetting a flag used to
    be enough to narrate a published video in a stock voice; it is not any more.

    openai       gpt-4o-mini-tts, steered per brand. Key: OPENAI_API_KEY, or whatever
                 CCVIDEO_OPENAI_KEY_NAME names instead.
    elevenlabs   the long-form narrator, and the only provider that returns exact character
                 timings for what it said. Key: ELEVENLABS_API_KEY.
    local        the owner's own trained voice (VoxCPM2 plus his LoRA). Made on a RENTED GPU by
                 the `narrate` command when this machine has it - about real time and 5 cents a
                 minute - and otherwise on this machine by `soren-voice` (CPU, 23-40x slower than
                 real time). Never on this machine where that is forbidden (NO_LOCAL_VOICING).
                 On the rented GPU every segment still missing from the cache is voiced on ONE
                 pod, the voice loaded once: a pod costs ~6 minutes and ~9 cents to start, so one
                 per segment would pay that seventy times for a film. With a voice budget the run
                 waits that long, brings home what is finished and stops with "PARTIAL" (exit 3)
                 while the pod keeps working; the SAME command run again picks that pod up. The
                 cache key does not change with where the voice was made: it is the same voice.

Keys are read at the moment of use and never written anywhere.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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
# The models a video may be narrated on. The default stays the older model on purpose: moving it
# would miss every warm cache and bill each video again. A new video asks for v4 with --el-model.
EL_MODELS = (EL_MODEL_ID, "eleven_v4", "eleven_v4_turbo")
EL_KEY_NAME = "ELEVENLABS_API_KEY"
LOCAL_VOICE = "soren"
LOCAL_COMMAND = os.environ.get("CCVIDEO_LOCAL_VOICE_COMMAND", "").strip() or "soren-voice"
# The rented GPU. `narrate` (devthrottle_internal marketing/voice/runpod/narrate) rents one pod,
# voices with every word check and retry soren-voice does, brings the audio back and terminates it.
RENTED_COMMAND = os.environ.get("CCVIDEO_RENTED_VOICE_COMMAND", "").strip() or "narrate"
# Machines that must never voice locally: ~14 GB at the peak on a 16 GB Mac mini that also runs
# the Director drove it into swap on 4 Nov 2026.
NO_LOCAL_VOICING = {"devthrottle-mac-mini"}
# A paid provider is reachable only when this is set in the same command. The default voice is
# the owner's own, and forgetting a flag must never be able to put a stock voice in a video
# (owner, 5 Nov 2026: he is the voice of everything DevThrottle publishes; code-level switch,
# no quality gate, his choice of enforcement). It is NOT part of the voice cache key: where
# permission came from says nothing about how the narration sounds.
PAID_VOICE_ENV = "CCVIDEO_ALLOW_PAID_VOICE"
PARTIAL_EXIT = 3
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

    def __init__(self, brand, provider="local", voice=None, el_model=None, budget=None):
        if provider not in ("openai", "elevenlabs", "local"):
            raise SystemExit("unknown voice provider %r - have openai, elevenlabs, local" % provider)
        if provider != "local" and not os.environ.get(PAID_VOICE_ENV, "").strip():
            raise SystemExit(
                "refusing to voice with %r: the voice is the owner's own (the 'local' provider).\n"
                "  A paid provider is still here for work that genuinely needs one, but it must be\n"
                "  asked for on purpose - set %s=1 in the SAME command, so it is visible to anyone\n"
                "  reading that command and cannot happen by forgetting a flag:\n"
                "    %s=1 ccvideo ... --tts %s\n"
                "  Nothing DevThrottle publishes uses one (owner, 5 Nov 2026)."
                % (provider, PAID_VOICE_ENV, PAID_VOICE_ENV, provider))
        if budget and provider != "local":
            raise SystemExit("--voice-budget only applies to --tts local")
        self.brand = brand
        self.provider = provider
        if el_model and provider != "elevenlabs":
            raise SystemExit("--el-model only applies to --tts elevenlabs")
        if el_model and el_model not in EL_MODELS:
            raise SystemExit("unknown ElevenLabs model %r - have %s"
                             % (el_model, ", ".join(EL_MODELS)))
        self.voice = voice or {"openai": OPENAI_VOICE, "elevenlabs": EL_VOICE_ID,
                               "local": LOCAL_VOICE}[provider]
        # When the voice budget runs out (local only): seconds since the epoch, or None.
        self.deadline = time.time() + budget if budget else None
        self.el_model = el_model or EL_MODEL_ID

    @property
    def key_name(self):
        return OPENAI_KEY_NAME if self.provider == "openai" else EL_KEY_NAME

    def api_key(self):
        if self.provider == "local":
            return None
        return credentials.get(self.key_name)

    def fingerprint(self):
        """Identifies the exact voice. Any change here misses the cache, which is the point."""
        if self.provider == "openai":
            return "openai" + OPENAI_MODEL + self.voice + self.brand["instructions"]
        if self.provider == "local":
            return "local" + LOCAL_COMMAND + self.voice
        return ("elevenlabs" + self.voice + self.el_model
                + json.dumps(EL_SETTINGS, sort_keys=True))

    def digest(self, text):
        """The cache digest for one block of spoken text. Ten hex characters, as shipped."""
        return hashlib.sha1(
            (self.fingerprint() + spoken(self.brand, text)).encode("utf-8")
        ).hexdigest()[:10]

    def describe(self):
        if self.provider == "local":
            return "local %s / %s (no key%s)" % (
                self.voice, "rented GPU via " + RENTED_COMMAND
                if shutil.which(RENTED_COMMAND) and not voice_here()
                else LOCAL_COMMAND + " on this machine",
                ", voice budget %ds" % (self.deadline - time.time()) if self.deadline else "")
        if self.provider == "openai":
            return "OpenAI %s / %s (key %s)" % (self.voice, OPENAI_MODEL, OPENAI_KEY_NAME)
        return "ElevenLabs %s / %s (key %s)" % (self.voice, self.el_model, EL_KEY_NAME)


def cache_path(cache_dir, segment_id, text, voice):
    """Where this segment's audio lives. The name carries the digest, so a stale file cannot
    be mistaken for a fresh one and two texts can never collide on one path."""
    return Path(cache_dir) / ("seg-%s-%s.mp3" % (segment_id, voice.digest(text)))


def cached(path):
    return path.exists() and path.stat().st_size > MIN_AUDIO_BYTES


def partial(why):
    """Stop the run cleanly: the voice budget is spent. The cache keeps everything voiced."""
    print("PARTIAL: %s - run the same command again to carry on" % why, flush=True)
    sys.exit(PARTIAL_EXIT)


def voice_here():
    return os.environ.get("CCVIDEO_VOICE_HERE", "").strip() == "1"


def local_voice_where():
    """("rented", narrate) when this machine can reach the rented GPU, else ("here", soren-voice).
    Set CCVIDEO_VOICE_HERE=1 to voice on this machine even when narrate is available."""
    import platform
    # Lower case: macOS reports the name as the user typed it ("Devthrottle-Mac-mini.local").
    host = platform.node().split(".")[0].lower()
    rented = shutil.which(RENTED_COMMAND)
    if rented and not voice_here():
        return "rented", rented
    if host in NO_LOCAL_VOICING:
        raise SystemExit(
            "--tts local: %s is not allowed to voice on this machine (it needs ~14 GB of 16), and "
            "the rented-GPU command %r is %s. Put narrate on PATH, or set "
            "CCVIDEO_RENTED_VOICE_COMMAND to it." % (
                host, RENTED_COMMAND,
                "switched off by CCVIDEO_VOICE_HERE=1" if rented else "not on PATH"))
    local = shutil.which(LOCAL_COMMAND)
    if not local:
        raise SystemExit("--tts local needs %r (the rented GPU) or %r on PATH"
                         % (RENTED_COMMAND, LOCAL_COMMAND))
    return "here", local


def _work(out_path, said):
    out_path = Path(out_path)
    work = out_path.parent / "local-voice" / out_path.stem
    work.mkdir(parents=True, exist_ok=True)
    (work / "text.txt").write_text(said, encoding="utf-8")
    return work


def _finish_local(work, out_path, said):
    """The segment's mp3 from the voiced wav: soren-voice pads 0.4 s lead and 2 s tail for a whole
    narration; a segment has its own gaps from the assembler, so keep only the speech."""
    out_path = Path(out_path)
    tmp = Path(tempfile.mkstemp(suffix=".mp3", dir=str(out_path.parent))[1])
    run(["ffmpeg", "-v", "error", "-y", "-i", str(work / "voice.wav"),
         "-af", "atrim=start=0.4,areverse,atrim=start=2.0,areverse",
         "-ar", "44100", "-ac", "1", "-b:a", "128k", str(tmp)])
    if tmp.stat().st_size < MIN_AUDIO_BYTES:
        tmp.unlink()
        # Remove the wav too, or every re-run would find it "done" and fail the same way.
        (work / "voice.wav").unlink()
        raise SystemExit("the local voice made no audio for %r - run again to voice it afresh"
                         % said[:60])
    tmp.replace(out_path)
    return out_path


def voice_rented(jobs, voice, cache_dir, command):
    """Voice [(said, out_mp3)] on ONE rented GPU. The batch file and the pod record have ONE fixed
    name per cache directory - never one named after what is missing, which shrinks after every
    PARTIAL and would lose the pod still voicing it."""
    cache_dir = Path(cache_dir)
    spec, todo = [], []
    for said, out_path in jobs:
        work = _work(out_path, said)
        spec.append({"text": str(work / "text.txt"), "out": str(work / "voice.wav")})
        todo.append((work, out_path, said))
    jf = cache_dir / "local-voice" / "rented-batch.json"
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    cmd = [command, "--batch", str(jf)]
    if voice.deadline:
        left = voice.deadline - time.time()
        if left <= 0:
            partial("voice budget spent")
        cmd += ["--budget", "%d" % max(1, left)]
    print("voice   : %d segment(s) to voice on one rented GPU" % len(jobs), flush=True)
    r = subprocess.run(cmd)
    if r.returncode == PARTIAL_EXIT:
        partial("the rented GPU is still voicing")
    if r.returncode != 0:
        raise SystemExit("%s failed (exit %d) voicing %d segment(s)"
                         % (Path(command).name, r.returncode, len(jobs)))
    for work, out_path, said in todo:
        if not (work / "voice.wav").exists():
            raise SystemExit("%s finished but brought back no audio for %r"
                             % (Path(command).name, said[:60]))
        _finish_local(work, out_path, said)


def prefetch_local(segments, voice, cache_dir):
    """Voice every segment the cache lacks in ONE rented batch, before the assembler asks for them
    one at a time. Does nothing unless the voice is local and the rented GPU is the way."""
    if voice.provider != "local":
        return 0
    jobs = [(spoken(voice.brand, s.text), cache_path(cache_dir, s.id, s.text, voice))
            for s in segments]
    jobs = [(said, p) for said, p in jobs if not cached(p)]
    if not jobs:
        return 0
    where, command = local_voice_where()
    if where != "rented":
        return 0              # soren-voice here, segment by segment, as before
    voice_rented(jobs, voice, cache_dir, command)
    return len(jobs)


def synthesise_local(said, voice, out_path):
    """Voice one segment with the owner's voice: on the rented GPU when this machine has it, else
    with the local command, whose chunks are cached beside the segment cache so a segment cut short
    by the budget resumes mid-segment on the next run."""
    where, command = local_voice_where()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if where == "rented":
        voice_rented([(said, out_path)], voice, out_path.parent, command)
        return out_path
    work = _work(out_path, said)
    cmd = [command, str(work / "text.txt"), str(work / "voice.wav"), "--parts", str(work / "parts")]
    if voice.deadline:
        left = voice.deadline - time.time()
        if left <= 0:
            partial("voice budget spent")
        cmd += ["--budget", "%d" % max(1, left)]
    r = subprocess.run(cmd)
    if r.returncode == PARTIAL_EXIT:
        partial("voice budget spent inside segment %s" % out_path.stem)
    if r.returncode != 0:
        raise SystemExit("%s failed (exit %d) on %r" % (LOCAL_COMMAND, r.returncode, said[:60]))
    return _finish_local(work, out_path, said)


def synthesise(text, voice, out_path, api_key):
    """Speak `text` to `out_path`. Returns the path. Overwrites whatever was there."""
    said = spoken(voice.brand, text)
    if voice.provider == "local":
        return synthesise_local(said, voice, out_path)
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
            "model_id": voice.el_model,
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


def speak(text, voice, cache_dir, segment_id, api_key=None, allow_synthesis=True):
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
    key = api_key or voice.api_key()
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
