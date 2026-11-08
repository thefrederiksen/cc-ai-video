"""faster-whisper must be able to open audio with the av this package installs.

av 19.0.2 dropped a keyword faster-whisper 1.2.1 passes to av.open, and every transcription - the
word timings the whole library cuts on, and the qa hear-back - failed after a fresh install."""

import subprocess


def test_faster_whisper_can_decode_audio(tmp_path):
    from faster_whisper.audio import decode_audio
    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=1", str(wav)], check=True)
    assert len(decode_audio(str(wav))) == 16000
