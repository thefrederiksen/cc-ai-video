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


def trailing_silence(path, window=4.0, threshold_db=-40, slack=0.08):
    """How much silence the file ENDS with, measured from the audio alone.

    This is the signal that answers the fade question without a transcript, and it is the one
    to trust: the trailing silence must be at least as long as the fade, or the fade is running
    over speech. No vocabulary, no model, no opinion about what the last word was.

    The silence has to be shown to run TO THE END. An earlier version looked for a silence that
    ffmpeg never closed, on the assumption that a run to end-of-file has no closing event. It
    does emit one, so that version reported 0.00s of trailing silence on a file that plainly
    ends with the better part of a second of it - a broken instrument reporting the alarming
    direction, which is the kind that gets believed.
    """
    shape = probe(path)
    window = min(window, shape["duration"])
    start = max(0.0, shape["duration"] - window)
    log = run_capture_stderr([
        "ffmpeg", "-v", "info", "-ss", "%.3f" % start, "-i", str(path),
        "-af", "silencedetect=n=%ddB:d=0.05" % threshold_db, "-f", "null", "-"])
    starts = [float(m) for m in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end: (-?[\d.]+)", log)]
    if not starts:
        return 0.0
    last_start = starts[-1]
    runs_to_end = len(ends) < len(starts) or (ends and ends[-1] >= window - slack)
    if not runs_to_end:
        return 0.0
    return max(0.0, window - last_start)


def tail_check(video, expected, model, fade, vocabulary=(), seconds=TAIL_SECONDS,
               count=TAIL_WORDS):
    """Did the fade at the end of this file eat the words it was supposed to fade?

    Returns (result, detail) where result is PASS, WARN or FAIL.

    ONE SIGNAL DECIDES, AND IT IS NOT THE TRANSCRIPT.

    The deciding measurement is the trailing silence, taken from the audio: if the file ends
    with at least a fade of silence, the fade lay in silence and cannot have taken anything.
    No model, no vocabulary, no opinion about which word it was.

    THIS CHECK MAKES NO CLAIM ABOUT WHETHER A PARTICULAR WORD SURVIVED, because it was measured
    against a real corpus and it cannot. Of five endings where the spoken word was plainly
    audible, a transcriber returned: "habit" for "hacker", "interesting" for "interactive",
    "process" for "project", "write" for "respond", and one product name in place of another.
    Only one of those is close enough in spelling for any similarity measure to recover. Brand
    and technical vocabulary is exactly where a transcriber is least reliable, and the last
    word of a clip - quiet, clipped, at the edge - is exactly where it gets least context.

    So the transcript is printed as EVIDENCE for a person, never used as a verdict. The
    severities follow what each signal can actually support:

      PASS  the file ends with at least a fade of silence. Decided on audio alone.
      FAIL  nothing at all is audible in the closing seconds. Unambiguous, and not a question
            of which word it was.
      WARN  the fade overlapped speech, so it MAY have thinned or taken the ending. What was
            expected and what was heard are both printed, and a person has to listen.

    Calibrated on 66 finished shorts: 20 warned, 3 were genuinely truncated. A check that
    turned the other 17 into failures would be switched off within a week, and then the 3 would
    go unnoticed too.
    """
    import os
    import subprocess
    import tempfile

    from ..transcribe import hear

    silence = trailing_silence(video)
    if silence >= fade:
        return "PASS", ("ends with %.2fs of silence, longer than the %.2fs fade - the fade "
                        "lies in silence and cannot have taken a word" % (silence, fade))

    shape = probe(video)
    handle = tempfile.NamedTemporaryFile(suffix=".m4a", delete=False)
    handle.close()
    try:
        from .. import budget
        subprocess.run(budget.ffmpeg(["ffmpeg", "-y", "-v", "error",
                                      "-ss", "%.3f" % max(0.0, shape["duration"] - seconds),
                                      "-i", str(video), "-vn", "-c:a", "aac", handle.name]),
                       check=True, capture_output=True)
        heard_text = hear(handle.name, model)
    finally:
        os.unlink(handle.name)

    heard = re.findall(r"[a-z0-9]+", heard_text.lower())
    if not heard:
        return "FAIL", ("only %.2fs of trailing silence against a %.2fs fade, and NOTHING is "
                        "audible in the last %.0fs" % (silence, fade, seconds))

    wanted = [w for w in expected if len(w) > 2][-count:]
    return "WARN", (
        "only %.2fs of trailing silence against a %.2fs fade, so the fade ran over speech and "
        "may have thinned the ending.\n           expected to end: %r\n           heard: %r\n"
        "           LISTEN to the last second. A transcriber mishears the final word of a clip "
        "routinely, so neither of these lines settles it."
        % (silence, fade, " ".join(wanted), " ".join(heard[-count:])))


def run_checks(video, target=None, script_path=None, brand=None, speech=True,
               model="base.en", words_policy="default", fade=None):
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
    audio_only = bool(target and target.get("audio_only"))
    if audio_only and shape["video"]:
        problems.append("a picture in a %s file, which should carry the voice only"
                        % target["name"])
    if not audio_only and not shape["video"]:
        problems.append("no video stream")
    if not shape["audio"]:
        problems.append("NO AUDIO")
    if audio_only:
        record("RENDER", not problems,
               "; ".join(problems) or "voice only, %.1fs, audio present" % shape["duration"])
    elif target:
        if (shape["width"], shape["height"]) != (target["width"], target["height"]):
            problems.append("size %dx%d, %s wants %dx%d"
                            % (shape["width"], shape["height"], target["name"],
                               target["width"], target["height"]))
        if target["max_seconds"] and shape["duration"] > target["max_seconds"]:
            problems.append("%.1fs over the %s limit of %.0fs"
                            % (shape["duration"], target["name"], target["max_seconds"]))
    if not audio_only:
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

        from .. import targets
        verdict, detail = tail_check(video, spoken_from_script(script_path), model,
                                     targets.JOIN_FADE if fade is None else fade)
        results.append({"check": "TAIL", "result": verdict, "detail": detail})

        if brand:
            from ..brand import banned_hits
            hits = banned_hits(brand, heard_text, words_policy)
            record("WORDS", not hits,
                   "; ".join(hits) or "nothing the %r policy refuses was heard" % words_policy)

    return results
