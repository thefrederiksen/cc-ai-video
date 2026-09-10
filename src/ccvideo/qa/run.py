"""The gate a rendered file must pass.

EVERY CHECK IS A PRESENCE THE FILE MUST SHOW, NEVER AN ABSENCE. A file that cannot be checked
FAILS. This matters more than it sounds: a check whose pass condition is "we found no
problems" passes just as happily when the instrument was broken and looked at nothing.

THE ONE THAT MATTERS IS THE HEAR-BACK. Pulling frames out of a render proves that text was
drawn and proves nothing whatsoever about whether the audio is there, is the right audio, or
is in sync - a desynced video shipped once having been frame-checked and passed. The hear-back
transcribes the finished mp4 and asks how much of what should have been said comes back out
of it. Measured baselines from the pipelines this replaces: 97 to 100 percent on short videos,
98 percent on a twenty minute one.

Exit 1 on any FAIL is the caller's job; this module reports.
"""

import os
import re
from pathlib import Path

from ..shell import probe, run_capture_stderr

SPEECH_PASS = 0.80
SPEECH_WARN = 0.65


def loudness(path):
    """(integrated LUFS, true peak dBTP). Raises if ffmpeg produced no measurement, because an
    unmeasured file must never read as a quiet one."""
    log = run_capture_stderr(["ffmpeg", "-v", "info", "-i", str(path),
                              "-af", "ebur128=peak=true", "-f", "null", "-"])
    integrated = [float(m) for m in re.findall(r"I:\s+(-?[\d.]+) LUFS", log)]
    peaks = [float(m) for m in re.findall(r"Peak:\s+(-?[\d.]+) dBFS", log)]
    if not integrated:
        raise SystemExit(
            "ebur128 reported no integrated loudness for %s. That is a broken measurement, "
            "not a quiet file, so this stops rather than passing it." % path)
    return integrated[-1], (peaks[-1] if peaks else None)


def silences(path, threshold_db=-40, minimum=2.5):
    log = run_capture_stderr(["ffmpeg", "-v", "info", "-i", str(path),
                              "-af", "silencedetect=n=%ddB:d=%s" % (threshold_db, minimum),
                              "-f", "null", "-"])
    return [float(m) for m in re.findall(r"silence_duration: ([\d.]+)", log)]


def spoken_from_script(path):
    """The words a segment script says will be spoken. Only TEXT: blocks - a header comment
    telling the writer what not to say is not the video saying it."""
    words, mode = [], None
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if line.startswith("=== SEG"):
            mode = None
        elif line.startswith("TEXT:"):
            mode = "text"
        elif line.startswith(("IMAGE:", "CARD:", "SHOT:", "CHAPTER:", "#")):
            mode = None
        elif mode == "text" and line.strip():
            words.extend(re.findall(r"[a-z0-9]+", line.lower()))
    if not words:
        raise SystemExit("%s has no TEXT: blocks, so there is nothing to hear back" % path)
    return words


def heard_ratio(expected, heard_text):
    """How much of `expected` comes back in `heard_text`.

    Matched against a POOL, so a word said once and heard three times scores once. Words of
    two letters or fewer are dropped: they match by accident and inflate the score.
    """
    heard = re.findall(r"[a-z0-9]+", heard_text.lower())
    pool = list(heard)
    expected = [w for w in expected if len(w) > 2]
    if not expected:
        raise SystemExit("nothing substantial was expected to be said, so nothing can be proven")
    hits = 0
    for word in expected:
        if word in pool:
            pool.remove(word)
            hits += 1
    return hits / float(len(expected))


TAIL_SECONDS = 6.0
TAIL_WORDS = 6


def tail_audible(video, expected, model, seconds=TAIL_SECONDS, count=TAIL_WORDS):
    """Is the END of the video still speaking the words it should end on?

    A percentage over the whole file cannot answer this. One lost word out of ninety is barely
    one percent, and a short whose final word was faded to silence scored comfortably above any
    threshold while being plainly broken. So the tail is asked about on its own.

    Returns (ok, detail). A tail that transcribes to nothing is a FAILURE, not a pass: the
    check exists to prove speech is present, and an empty result proves the opposite.
    """
    import subprocess
    import tempfile

    from ..transcribe import hear

    wanted = [w for w in expected if len(w) > 2][-count:]
    if not wanted:
        return False, "the script has no substantial words to end on"

    shape = probe(video)
    start = max(0.0, shape["duration"] - seconds)
    handle = tempfile.NamedTemporaryFile(suffix=".m4a", delete=False)
    handle.close()
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % start,
                        "-i", str(video), "-vn", "-c:a", "aac", handle.name],
                       check=True, capture_output=True)
        heard_text = hear(handle.name, model)
    finally:
        os.unlink(handle.name)

    heard = re.findall(r"[a-z0-9]+", heard_text.lower())
    if not heard:
        return False, ("nothing was heard in the last %.0fs. The video should end on %r"
                       % (seconds, " ".join(wanted)))
    missing = [w for w in wanted if w not in heard]
    final = wanted[-1]
    if final not in heard:
        return False, ("the final word %r is not audible in the last %.0fs. A fade longer than "
                       "the last word will do exactly this, and a whole-file percentage will "
                       "not notice." % (final, seconds))
    if missing:
        return False, ("missing from the last %.0fs: %s" % (seconds, ", ".join(missing)))
    return True, "the closing words are audible: %s" % " ".join(wanted)


def run_checks(video, target=None, script_path=None, brand=None, speech=True,
               model="base.en", words_policy="default"):
    """Run the gate. Returns a list of {check, result, detail}."""
    video = Path(video)
    if not video.exists():
        raise SystemExit("no such file: %s" % video)
    results = []

    def record(name, ok, detail, warn=False):
        results.append({"check": name,
                        "result": "PASS" if ok else ("WARN" if warn else "FAIL"),
                        "detail": detail})

    def skip(name, detail):
        """A check that did not run says SKIP, never PASS.

        A skipped check reported as a pass is the exact shape this gate exists to refuse: it
        certifies a thing nobody looked at, and it reads as green in a summary line.
        """
        results.append({"check": name, "result": "SKIP", "detail": detail})

    shape = probe(video)

    # RENDER - the right shape, and it carries its audio.
    problems = []
    if not shape["video"]:
        problems.append("no video stream")
    if not shape["audio"]:
        problems.append("NO AUDIO")
    if target:
        if (shape["width"], shape["height"]) != (target["width"], target["height"]):
            problems.append("size %dx%d, %s wants %dx%d"
                            % (shape["width"], shape["height"], target["name"],
                               target["width"], target["height"]))
        if target["max_seconds"] and shape["duration"] > target["max_seconds"]:
            problems.append("%.1fs over the %s limit of %.0fs"
                            % (shape["duration"], target["name"], target["max_seconds"]))
    record("RENDER", not problems,
           "; ".join(problems) or "%dx%d %.1fs %.0f fps, audio present"
           % (shape["width"], shape["height"], shape["duration"], shape["fps"]))

    # DRIFT - the audio and video run for the same length.
    if shape["audio_duration"] is not None:
        drift = abs(shape["duration"] - shape["audio_duration"])
        record("DRIFT", drift <= 0.5,
               "video %.2fs, audio %.2fs, drift %.2fs"
               % (shape["duration"], shape["audio_duration"], drift))
    else:
        skip("DRIFT", "the container reports no separate audio duration to compare")

    # AUDIO - a level the platforms play well, no clipping, no dead stretch.
    lufs, peak = loudness(video)
    quiet = silences(video)
    ok = -20 <= lufs <= -11 and (peak is None or peak <= -0.1) and not quiet
    record("AUDIO", ok,
           "%.1f LUFS, peak %s dBTP, silences over 2.5s: %s"
           % (lufs, peak, quiet or "none"))

    # SPEECH - the hear-back. This is the check that matters.
    if not speech:
        skip("SPEECH", "not run. Nothing else here proves the audio is present, correct "
                       "or in sync, so this file is not cleared for release.")
        skip("TAIL", "not run. Nothing here proves the video still says its last word.")
    elif not script_path:
        skip("SPEECH", "no --script given, so there is nothing to match what was heard "
                       "against. Pass the script that made this file.")
        skip("TAIL", "no --script given, so the words the video should END on are not known.")
    else:
        from ..transcribe import hear
        heard_text = hear(video, model)
        ratio = heard_ratio(spoken_from_script(script_path), heard_text)
        record("SPEECH", ratio >= SPEECH_PASS,
               "%d%% of the script's words heard back out of the render" % round(ratio * 100),
               warn=SPEECH_WARN <= ratio < SPEECH_PASS)

        ok, detail = tail_audible(video, spoken_from_script(script_path), model)
        record("TAIL", ok, detail)

        if brand:
            from ..brand import banned_hits
            hits = banned_hits(brand, heard_text, words_policy)
            record("WORDS", not hits,
                   "; ".join(hits) or "nothing the %r policy refuses was heard" % words_policy)

    return results
