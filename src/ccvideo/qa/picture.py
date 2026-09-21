"""Score the PICTURE of a rendered video: how long the screen shows nothing, and how long nothing
moves. Pure pixel arithmetic - no model decides anything here, so the same file always scores
the same, and a score can be compared before and after a fix.

How it measures. The video is decoded at 5 frames a second, shrunk to 320x180 grey. For each
frame:

  BUSY  the share of pixels that stand out from the frame's own median brightness by more than
        24 grey levels. It is relative to the median, so a dark theme and a pale theme both read
        0.0 when empty, and a photograph reads high.
  DIFF  the mean change in grey level from the frame before - how much is moving.

Calibrated by eye on a 28-minute illustrated video (2026-09-21), looking at real frames in each
band:

  BUSY < 0.004          EMPTY. The viewer sees a blank screen - at most a tiny grey label.
  0.004 <= BUSY < 0.01  THIN. One small line of text on an otherwise empty screen.
  DIFF < 0.01           STILL. Nothing on screen is moving at all.

A defect is a STRETCH, not a frame: a scene change legitimately passes through a blank frame or
two. What a viewer notices is the screen staying empty for a second or more.

Presence, not absence: a file that decodes to no frames is an error, never a clean score.
"""

import json
import subprocess
from pathlib import Path

import numpy as np

from ..shell import _require

RATE = 5
W, H = 320, 180
BUSY_LEVEL = 24

EMPTY = 0.004
THIN = 0.01
STILL = 0.01

EMPTY_STRETCH = 1.0     # seconds of empty screen that count as a defect
THIN_STRETCH = 3.0      # seconds of empty-or-thin screen that count as a defect
STILL_STRETCH = 6.0     # seconds with nothing moving that count as a defect


def measure(path):
    """Per-frame (busy, diff) arrays at RATE frames a second."""
    _require("ffmpeg")
    cmd = ["ffmpeg", "-v", "error", "-i", str(path),
           "-vf", "fps=%d,scale=%d:%d:flags=area,format=gray" % (RATE, W, H),
           "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    busy, diff, prev = [], [], None
    size = W * H
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        f = np.frombuffer(buf, np.uint8).astype(np.int16)
        b, d = frame_busy(f, prev)
        busy.append(b)
        diff.append(d)
        prev = f
    if proc.wait() != 0 or not busy:
        raise SystemExit("could not decode any frames from %s - that is a broken measurement, "
                         "not a clean picture" % path)
    return np.array(busy), np.array(diff)


def frame_busy(grey, prev=None):
    """(busy, diff) for one 320x180 grey frame given as an int16 array."""
    busy = float((np.abs(grey - np.median(grey)) > BUSY_LEVEL).mean())
    diff = float("inf") if prev is None else float(np.abs(grey - prev).mean())
    return busy, diff


def stretches(mask, minimum):
    """[(start_s, length_s)] of runs where mask holds for at least `minimum` seconds."""
    out, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            if (i - start) / RATE >= minimum:
                out.append((start / RATE, (i - start) / RATE))
            start = None
    return out


def score(busy, diff):
    duration = len(busy) / RATE
    empty = stretches(busy < EMPTY, EMPTY_STRETCH)
    thin = stretches(busy < THIN, THIN_STRETCH)
    still = stretches(diff < STILL, STILL_STRETCH)
    bad = np.zeros(len(busy), bool)
    for runs in (empty, thin, still):
        for s, n in runs:
            bad[int(s * RATE):int((s + n) * RATE)] = True
    return {
        "duration": round(duration, 1),
        "clean_percent": round(100.0 * (1 - bad.mean()), 1),
        "empty_seconds_total": round(float((busy < EMPTY).sum()) / RATE, 1),
        "empty": [{"at": round(s, 1), "seconds": round(n, 1)} for s, n in empty],
        "thin": [{"at": round(s, 1), "seconds": round(n, 1)} for s, n in thin],
        "still": [{"at": round(s, 1), "seconds": round(n, 1)} for s, n in still],
        "thresholds": {"empty_busy": EMPTY, "thin_busy": THIN, "still_diff": STILL,
                       "empty_stretch": EMPTY_STRETCH, "thin_stretch": THIN_STRETCH,
                       "still_stretch": STILL_STRETCH, "rate": RATE},
    }


def clock(s):
    return "%d:%04.1f" % (int(s // 60), s % 60)


def report(result, top=15):
    lines = ["PICTURE  %.1f%% clean  (%s long)" % (result["clean_percent"], clock(result["duration"]))]
    lines.append("  empty screen, total        %6.1f s" % result["empty_seconds_total"])
    for key, label in (("empty", "EMPTY >= %.0fs" % EMPTY_STRETCH),
                       ("thin", "EMPTY-OR-THIN >= %.0fs" % THIN_STRETCH),
                       ("still", "NOTHING MOVING >= %.0fs" % STILL_STRETCH)):
        runs = result[key]
        lines.append("  %-24s %3d stretch(es), %6.1f s" % (label, len(runs), sum(r["seconds"] for r in runs)))
        for r in sorted(runs, key=lambda r: -r["seconds"])[:top]:
            where = "   scene %d" % r["scene"] if r.get("scene") is not None else ""
            lines.append("      at %-8s %5.1f s%s" % (clock(r["at"]), r["seconds"], where))
    return "\n".join(lines)


def run(path, out_json=None):
    busy, diff = measure(path)
    result = score(busy, diff)
    result["file"] = str(path)
    if out_json:
        Path(out_json).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result
