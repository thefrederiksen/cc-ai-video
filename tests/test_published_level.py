"""The one published loudness, and the only way to reach it.

These tests exist because of a specific afternoon. On 5 November 2026 the owner listened to a
video of his own voice and said it was too loud, and the reason was that four numbers were in
play with nothing reconciling them, plus a true-peak ceiling that three renders asked for and
none of them met (+0.3, -0.5 and -4.20 dBTP against the same -1.5 request). So:

  one number, in one place, on every target - test_one_number_everywhere;
  reached by measurement, not by request - test_reaches_the_number_and_holds_the_ceiling;
  never a dynamic pass that rides the speech - test_chain_is_one_gain_and_one_limiter;
  and nothing at all to audio that already arrived at the number - test_already_there.

The last one is what stops a narration being levelled twice.
"""
import shutil
import struct
import subprocess
import wave

import pytest

from ccvideo import targets
from ccvideo.level import measure, publish_chain

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                reason="these measure real audio, so they need ffmpeg")


def _hard_to_level(path, seconds=8.0, rate=48000):
    """A signal with the shape that makes this hard: quiet body, a few tall transients.

    A real narration of this voice joins near -23 LUFS with its true peak ALREADY at -1.5 dBTP,
    so there is no headroom and pure gain cannot reach -18. Something has to give on the peaks,
    and this reproduces that without needing his voice in the repository.
    """
    n = int(seconds * rate)
    state, body = 12345, []
    for i in range(n):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF      # repeatable, no numpy
        v = int(((state / 0x7FFFFFFF) - 0.5) * 2 * 0.035 * 32767)
        if i % (rate // 2) < 24:                               # a transient twice a second
            v = 29000 if (i % rate) < 24 else -29000
        body.append(max(-32768, min(32767, v)))
    with wave.open(str(path), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", s) for s in body))
    return path


def _render(src, dst, chain):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", chain,
                    "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)], check=True)
    return dst


def test_one_number_everywhere():
    """Every target publishes at the same level, and it is the owner's number.

    The four that fought were -23 (the narration tool), -14 (three targets), -16 (narration)
    and None at all (both tutorial targets, which forced a seat to type one by hand).
    """
    assert targets.PUBLISHED_LUFS == -18.0
    levels = {name: t["loudness"] for name, t in targets.TARGETS.items()}
    assert set(levels.values()) == {targets.PUBLISHED_LUFS}, levels
    assert None not in levels.values(), "a target with no level makes a seat guess one"


def test_chain_is_one_gain_and_one_limiter(tmp_path):
    chain = publish_chain(_hard_to_level(tmp_path / "in.wav"))
    assert chain, "a file 5 dB off the number must be levelled"
    assert "loudnorm" not in chain, "a dynamic pass riding the speech is what this replaced"
    assert chain.count("volume=") == 1, chain
    assert chain.count("alimiter") == 1, chain


def test_reaches_the_number_and_holds_the_ceiling(tmp_path):
    src = _hard_to_level(tmp_path / "in.wav")
    before_lufs, before_peak = measure(src)
    assert before_lufs < targets.PUBLISHED_LUFS - 2, "the fixture is meant to be too quiet"
    assert before_peak > targets.PUBLISHED_TP - 2.0, "the fixture is meant to have no headroom"

    out = _render(src, tmp_path / "out.wav", publish_chain(src))
    lufs, peak = measure(out)
    assert abs(lufs - targets.PUBLISHED_LUFS) <= targets.PUBLISHED_BAND, lufs
    assert peak <= targets.PUBLISHED_TP, "the ceiling is held, not requested: got %.2f" % peak


def test_already_there_is_left_completely_alone(tmp_path):
    """The second pass that did the damage. A narration arrives at the number already."""
    src = _hard_to_level(tmp_path / "in.wav")
    out = _render(src, tmp_path / "out.wav", publish_chain(src))
    assert publish_chain(out) is None, "levelling it twice is the defect, not the feature"


def test_a_file_it_cannot_level_stops_rather_than_shipping(tmp_path):
    """Asked for a level no amount of gain under the ceiling can reach, it refuses.

    It does not quietly deliver the nearest thing it managed, because that is indistinguishable
    from working and is how audio nobody chose gets published.
    """
    src = _hard_to_level(tmp_path / "in.wav")
    with pytest.raises(SystemExit) as e:
        publish_chain(src, lufs=0.0)          # 0 LUFS is unreachable under any ceiling
    assert "conflict between the level and the ceiling" in str(e.value)
