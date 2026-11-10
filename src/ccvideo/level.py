"""The ONE loudness this factory publishes at, and the only way to reach it.

THE NUMBER LIVES IN targets.py. This module is how it is applied.

Why this file exists. On 5 November 2026 the owner listened to a video of his own voice and
said it was too loud. It was: four different numbers were in play and nothing reconciled them.
The narration tool mastered at -23 LUFS. These targets said -14 for youtube, shorts and
linkedin, -16 for narration, and nothing at all for the two tutorial targets - which meant a
seat had to type a level by hand, guessed, and put the finished video about 6 dB over the
master. And the quality gate passed only -20 to -11 LUFS, so audio at the master's own level
failed its own check for being too QUIET. Three renders went by before anyone had a number.

His ruling was one number everywhere: -18 LUFS. See targets.PUBLISHED_LUFS.

The second half of the problem was the ceiling. Three renders all asked ffmpeg's one-pass
loudnorm for TP=-1.5 and got +0.3, -0.5 and -4.20 dBTP. A pass that adjusts as it plays meets
the peak too late to do anything about it, so the ceiling it was given was never more than a
request. A ceiling asked for and not met is not a ceiling.

So nothing here asks. It MEASURES, applies ONE FIXED GAIN - which leaves the shape of the
speech exactly as it was - and holds the ceiling with a limiter that touches only the few
transients that gain would push through. Both numbers are then searched against real
measurements of real trial renders, because neither can be computed in advance:

  the gain, because limiting costs a few tenths of a decibel of loudness;

  the limiter's threshold, because it works on SAMPLE peaks while the ceiling is a TRUE peak,
  measured between the samples. On six real narrations that gap was 0.1 dB five times and
  0.9 dB once - so a fixed margin is exactly the kind of guess that produced +0.3 dBTP.

AND IT IS IDEMPOTENT. Audio already at the published level, under the ceiling, is returned
untouched. That is what stops a narration that left its own tool correctly levelled from being
re-levelled by the video tool - the second pass that did the damage.
"""

import re
import tempfile
from pathlib import Path

from .shell import run_capture_stderr
from .targets import PUBLISHED_LUFS, PUBLISHED_TP

# How close to the number counts as being at the number. 0.3 dB is well under what anyone can
# hear and comfortably inside the +/-1.5 dB band the gate allows around it.
TOLERANCE = 0.3
# Where the limiter starts, before the search moves it. Right five times in six.
START_MARGIN = 0.5
# Attempts before giving up. Six real narrations converged in 2 to 5.
ATTEMPTS = 8


def measure(path):
    """(integrated LUFS, true peak dBTP) for a file's audio.

    Raises rather than returning a default, because an UNMEASURED file must never read as a
    quiet one: that mistake publishes silence or publishes a clip.
    """
    log = run_capture_stderr(["ffmpeg", "-v", "info", "-i", str(path),
                              "-af", "ebur128=peak=true", "-f", "null", "-"])
    integrated = [float(m) for m in re.findall(r"I:\s+(-?[\d.]+) LUFS", log)]
    peaks = [float(m) for m in re.findall(r"Peak:\s+(-?[\d.]+) dBFS", log)]
    if not integrated:
        raise SystemExit(
            "ebur128 reported no integrated loudness for %s. That is a broken measurement, "
            "not a quiet file, so this stops rather than passing it." % path)
    return integrated[-1], (peaks[-1] if peaks else None)


def _chain(gain_db, limit_db):
    return ("volume=%.2fdB,alimiter=limit=%.6f:level=0:attack=0.1:release=50"
            % (gain_db, 10.0 ** (limit_db / 20.0)))


def publish_chain(path, lufs=None, ceiling=PUBLISHED_TP, attempts=ATTEMPTS, encode=None):
    """The audio filter chain that publishes `path` at `lufs`, or None if it is already there.

    Returned as a string rather than applied, because the callers apply it inside a render they
    are doing anyway - levelling in a pass of its own would re-encode a finished file twice.

    The search renders the AUDIO ONLY, which is why it is affordable: a trial is a second or
    two whatever the picture costs. The caller's real render then uses the chain the search
    found. A lossy audio encode downstream can lift the true peak a little further, which the
    limiter's margin absorbs - and the quality gate measures the finished file and says so if
    it did not.

    PASS `encode` WHEN THE RESULT WILL BE ENCODED LOSSILY (the target's encode dict: audio_kbps,
    sample_rate, channels). Then every trial is encoded the same way before it is measured, so the
    ceiling is held in the FINISHED file and not only in a PCM trial. Without it, AAC lifted the
    true peak by 0.4-0.5 dB after the limiter: a tutorial levelled to -1.6 dBTP shipped at -1.1 and
    failed the gate, and a second normalise_loudness found the same chain and failed again
    (9 Nov 2026, course re-narration). The limiter's margin did not absorb it.
    """
    lufs = PUBLISHED_LUFS if lufs is None else lufs
    measured, peak = measure(path)
    if abs(measured - lufs) <= TOLERANCE and (peak is None or peak <= ceiling):
        return None                                  # already published at this level
    gain, limit_db = lufs - measured, ceiling - START_MARGIN
    with tempfile.TemporaryDirectory(prefix="ccvideo-level-") as tmp:
        if encode:
            trial = str(Path(tmp) / "trial.m4a")
            codec = ["-c:a", "aac", "-b:a", encode["audio_kbps"], "-ar", encode["sample_rate"],
                     "-ac", encode["channels"]]
        else:
            trial = str(Path(tmp) / "trial.wav")
            codec = ["-ar", "48000", "-c:a", "pcm_s16le"]
        for _ in range(attempts):
            chain = _chain(gain, limit_db)
            run_capture_stderr(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn",
                                "-af", chain] + codec + [trial])
            measured, peak = measure(trial)
            if peak is not None and peak > ceiling:   # the ceiling is HELD, so move the threshold
                limit_db -= (peak - ceiling) + 0.2
            elif abs(measured - lufs) > TOLERANCE:    # then and only then, chase the loudness
                gain += lufs - measured
            else:
                return chain
    raise SystemExit(
        "could not put %s at %g LUFS under a %g dBTP ceiling in %d attempts - it reached "
        "%.1f LUFS, true peak %.1f dBTP. That is a real conflict between the level and the "
        "ceiling, not a number to tune by hand: report it and stop."
        % (path, lufs, ceiling, attempts, measured, peak))
