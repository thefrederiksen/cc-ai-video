"""The one ffmpeg and ffprobe wrapper.

Every tool this library replaces carried its own copy of run() and probe(), and they had
drifted: one raised on a non-zero exit, one printed and continued, one used check=True and
lost the stderr that said why. There is one of each here.

Nothing in this module guesses. A missing binary, a failed command or a file ffprobe cannot
read is an error that names the command and shows what ffmpeg said.
"""

import json
import shutil
import subprocess


class MediaError(RuntimeError):
    """A media command failed. The message carries the command and ffmpeg's own words."""


def _require(binary):
    if shutil.which(binary) is None:
        raise MediaError(
            "%s is not on PATH. Install ffmpeg and put its bin directory on PATH; "
            "every render, probe and transcript in this library needs it." % binary
        )


def run(cmd, cwd=None):
    """Run a command to completion. Returns stdout. Raises MediaError with stderr on failure."""
    _require(str(cmd[0]))
    cmd = [str(c) for c in cmd]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if p.returncode != 0:
        raise MediaError(
            "command failed (exit %d): %s\n%s"
            % (p.returncode, " ".join(cmd[:8]), (p.stderr or "")[-4000:])
        )
    return p.stdout


def run_capture_stderr(cmd):
    """Run a command whose OUTPUT IS ITS STDERR - ebur128, silencedetect, volumedetect.

    These filters report through the log, and ffmpeg exits 0 while writing to stderr, so the
    caller wants the log rather than a raised error. A non-zero exit is still an error: an
    empty log from a crashed ffmpeg would otherwise read as "nothing detected", which is the
    check-that-fails-open shape this library refuses.
    """
    _require(str(cmd[0]))
    cmd = [str(c) for c in cmd]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise MediaError(
            "command failed (exit %d): %s\n%s"
            % (p.returncode, " ".join(cmd[:8]), (p.stderr or "")[-4000:])
        )
    return p.stderr


def probe(path):
    """Shape of a media file: duration, video size, frame rate, whether it carries audio."""
    out = run([
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height,r_frame_rate,duration:format=duration",
        "-of", "json", str(path),
    ])
    d = json.loads(out)
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = d.get("format", {}).get("duration")
    if duration is None:
        duration = (v or a or {}).get("duration")
    if duration is None:
        raise MediaError("ffprobe found no duration in %s - is it a media file?" % path)
    return {
        "duration": float(duration),
        "width": int(v["width"]) if v else 0,
        "height": int(v["height"]) if v else 0,
        "fps": _fps(v),
        "video": v is not None,
        "audio": a is not None,
        "audio_duration": float(a["duration"]) if a and a.get("duration") else None,
    }


def duration(path):
    return probe(path)["duration"]


def _fps(stream):
    if not stream:
        return 0.0
    raw = stream.get("r_frame_rate") or "0/1"
    num, _, den = raw.partition("/")
    den = float(den or 1)
    if den == 0:
        return 0.0
    return float(num) / den
