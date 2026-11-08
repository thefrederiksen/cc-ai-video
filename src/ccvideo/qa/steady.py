"""STEADY - does the camera glide, or does it stop and jump?

A smooth zoom or pan moves every point of the picture by an even, usually fractional, amount
each frame. ffmpeg's zoompan snaps its crop to whole pixels, so a point stands still for a few
frames and then jumps a pixel: the video shakes, and a viewer sees it at once. The course
chapters shipped like that in Nov 2026 with every other qa check green, because no check looked.

HOW IT MEASURES

1. A grid of patches (3 x 3) is followed from each frame to the next by phase correlation with a
   sub-pixel peak, at full resolution. Frames are streamed, never all held in memory.
2. On each pair of frames the patch movements are fitted to ONE camera: a shift plus a zoom about
   the middle (dx = tx + s * (x - cx), dy = ty + s * (y - cy)). The frame is a camera frame only
   when most patches agree with that fit. Typing, a spinner or a cursor moves one patch against
   the camera and is left out; it is the screen changing, not the camera moving.
3. Where the camera moves in a run of frames - its step at a corner averaged over a second is
   above GLIDE_MIN px a frame - the run is a GLIDE. A cut, a page change or a scroll (a step far
   larger than the glide around it, or patches that lose their match) ends a glide and is never
   scored as a jump.
4. Each glide is scored much as the original jitter meter scored one patch: how far each frame's
   step at the corners strays from the one-second average step, as a fraction of that step -
   or of half a pixel when the step is smaller (SPEED_FLOOR):

        unevenness = RMS( |step - local average step| / max(|local average step|, 0.5 px) )

   Why the floor: on a slow move (0.15 px a frame) the encoder's own noise, a few hundredths of
   a pixel, is a large fraction of the step, and a smooth render scored like a shaky one.
   What the eye sees below half a pixel a frame is the absolute size of the stumble - zoompan's
   is a whole pixel. Why RMS: a very slow zoompan move stands still for twenty frames and jumps
   once; a mean averages that jump away, a root mean square does not.

   A smooth move scores well under the threshold; whole-pixel steps score far above it.

THE GATE is a PRESENCE, like every check in qa. PASS needs at least one second of measured glide
and every glide under the threshold. A file whose camera never moves reads N/A with the number of
frames looked at - it is not a PASS, because nothing was measured. A file that cannot be read
FAILS. The threshold is calibrated on renders known to be smooth and known to shake; the table
is in tests/test_steady.py.
"""

import subprocess
import tempfile

import numpy as np

from .. import budget
from ..shell import _require, probe

GRID = 3                # patches per side
GLIDE_MIN = 0.05        # px a frame at a corner, averaged over a second, for a glide
GLIDE_MAX = 12.0        # above this the "move" is a cut or a scroll, not a camera glide
MIN_GLIDE_FRAMES = 30   # a second at 30 fps: a glide shorter than this is not scored
PEAK_MIN = 0.25         # a patch whose correlation peak is lower than this has lost its match
AGREE_PX = 0.35         # a patch agrees with the camera fit to within this, plus 10% of the step
SPEED_FLOOR = 0.5       # px a frame: below this a stumble is judged against half a pixel
THRESHOLD = 0.37        # calibrated: worst smooth 0.30, best shaky 0.43 - see tests/test_steady.py


def _patch_size(w, h):
    side = min(w, h) // 5
    return max(32, side - side % 2)


def _centres(w, h, p):
    xs = np.linspace(p / 2 + w * 0.06, w - p / 2 - w * 0.06, GRID)
    ys = np.linspace(p / 2 + h * 0.06, h - p / 2 - h * 0.06, GRID)
    return [(float(x), float(y)) for y in ys for x in xs]


def grey_frames(path, w, h):
    """Yield each frame as a float grey array. A file ffmpeg cannot decode raises SystemExit."""
    _require("ffmpeg")
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vf", "format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    size = w * h
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(budget.ffmpeg(cmd), stdout=subprocess.PIPE, stderr=log)
        try:
            while True:
                raw = proc.stdout.read(size)
                if len(raw) < size:
                    break
                yield np.frombuffer(raw, np.uint8).reshape(h, w).astype(np.float32)
        finally:
            proc.stdout.close()
            code = proc.wait()
        if code:
            log.seek(0)
            raise SystemExit("cannot decode %s: %s" % (path, log.read().decode("utf-8", "replace")
                                                       .strip()[-400:]))


class _Correlator:
    def __init__(self, p):
        self.p = p
        self.win = np.outer(np.hanning(p), np.hanning(p)).astype(np.float32)

    def spectrum(self, patch):
        if patch.std() < 2.0:       # flat (an empty background): nothing to follow
            return None
        return np.fft.rfft2((patch - patch.mean()) * self.win)

    def shift(self, fa, fb):
        """(dx, dy, peak): how far the content moved from a to b, to a fraction of a pixel."""
        r = fb * np.conj(fa)
        r /= np.abs(r) + 1e-9
        c = np.fft.irfft2(r, s=(self.p, self.p))
        y, x = np.unravel_index(np.argmax(c), c.shape)
        p = self.p

        def sub(m, q, n):
            den = m - 2 * q + n
            return 0.0 if den == 0 else 0.5 * (m - n) / den

        dx = sub(c[y, (x - 1) % p], c[y, x], c[y, (x + 1) % p])
        dy = sub(c[(y - 1) % p, x], c[y, x], c[(y + 1) % p, x])
        x = x - p if x > p // 2 else x
        y = y - p if y > p // 2 else y
        return x + dx, y + dy, float(c.max())


def fit_camera(points, moves, cx, cy):
    """Least-squares (tx, ty, s) for dx = tx + s*(x-cx), dy = ty + s*(y-cy), and each patch's
    distance from that fit."""
    pts = np.asarray(points, np.float64)
    mv = np.asarray(moves, np.float64)
    rx, ry = pts[:, 0] - cx, pts[:, 1] - cy
    n = len(pts)
    a = np.zeros((2 * n, 3))
    a[:n, 0], a[:n, 2] = 1, rx
    a[n:, 1], a[n:, 2] = 1, ry
    b = np.concatenate([mv[:, 0], mv[:, 1]])
    sol = np.linalg.lstsq(a, b, rcond=None)[0]
    pred = a @ sol
    err = np.hypot(pred[:n] - b[:n], pred[n:] - b[n:])
    return sol, err


CUT = "cut"


def _robust_camera(pts, mvs, cx, cy, extent):
    """The camera most patches agree on: fit, drop the patch furthest from the fit, refit,
    until every patch left agrees or fewer than four are left (then there is no camera)."""
    pts, mvs = list(pts), list(mvs)
    while len(pts) >= 4:
        sol, err = fit_camera(pts, mvs, cx, cy)
        step = np.hypot(sol[0], sol[1]) + abs(sol[2]) * extent / 2
        if (err <= AGREE_PX + 0.1 * step).all():
            return tuple(float(v) for v in sol)
        worst = int(np.argmax(err))
        del pts[worst], mvs[worst]
    return None


def camera_track(path):
    """Per frame step: the camera's (tx, ty, s); None where no single camera explains the
    frame (the screen changed under a still camera, or too little to follow); CUT where nearly
    every patch lost its match. Returns (steps, frames_read, size)."""
    try:
        shape = probe(path)
    except Exception as exc:        # ffprobe could not read it: the gate FAILS, never passes
        raise SystemExit("cannot read %s: %s" % (path, str(exc).strip()[-300:]))
    if not shape.get("video"):
        raise SystemExit("%s has no picture to measure" % path)
    w, h = int(shape["width"]), int(shape["height"])
    p = _patch_size(w, h)
    centres = _centres(w, h, p)
    cor = _Correlator(p)
    cx, cy = w / 2.0, h / 2.0
    steps, prev, count = [], None, 0
    for frame in grey_frames(path, w, h):
        count += 1
        specs = []
        for (x, y) in centres:
            x0, y0 = int(round(x - p / 2)), int(round(y - p / 2))
            specs.append(cor.spectrum(frame[y0:y0 + p, x0:x0 + p]))
        if prev is not None:
            pts, mvs = [], []
            for (pt, a, b) in zip(centres, prev, specs):
                if a is None or b is None:
                    continue
                dx, dy, peak = cor.shift(a, b)
                if peak >= PEAK_MIN:
                    pts.append(pt)
                    mvs.append((dx, dy))
            cam = None
            if len(pts) < 2:
                cam = CUT           # nearly everything lost its match: the picture changed
            elif len(pts) >= 4:
                cam = _robust_camera(pts, mvs, cx, cy, max(w, h))
            steps.append(cam)
        prev = specs
    return steps, count, (w, h)


def _corners(w, h):
    return [(w * 0.1, h * 0.1), (w * 0.9, h * 0.1), (w * 0.1, h * 0.9), (w * 0.9, h * 0.9)]


def score_track(steps, size, fps=30.0):
    """The glides in a camera track and each one's unevenness. Returns a list of
    {start, end, frames, score, worst: [(second, score)]} in frame indices / seconds."""
    w, h = size
    cx, cy = w / 2.0, h / 2.0
    corners = _corners(w, h)
    n = len(steps)
    vec = np.full((n, len(corners), 2), np.nan)
    cut = np.zeros(n, bool)
    for i, cam in enumerate(steps):
        if cam is CUT:
            cut[i] = True
            continue
        if cam is None:
            continue
        tx, ty, s = cam
        for k, (x, y) in enumerate(corners):
            vec[i, k] = (tx + s * (x - cx), ty + s * (y - cy))
    known = ~np.isnan(vec[:, 0, 0]) if n else np.zeros(0, bool)
    mag = np.zeros(n)
    if n:
        mag[known] = np.hypot(vec[known, :, 0], vec[known, :, 1]).mean(axis=1)
    # A cut, a page change or a scroll is a step far out of line with the glide around it.
    # It splits the track; a frame with no camera is only a gap inside a stretch.
    med = float(np.median(mag[known])) if known.any() else 0.0
    cut |= known & ((mag > GLIDE_MAX) | ((mag > 3.0) & (mag > 4 * max(med, 0.05))))
    win = int(round(fps))
    kern = np.ones(win)
    glides = []
    a = 0
    bounds = list(np.nonzero(cut)[0]) + [n]
    for b in bounds:
        k0, k1 = a, b
        a = b + 1
        good = known[k0:k1] & ~cut[k0:k1]
        if good.sum() < MIN_GLIDE_FRAMES:
            continue
        v = np.where(good[:, None, None], vec[k0:k1], 0.0)
        cnt = np.convolve(good.astype(float), kern, "same")
        avg = _local_fit(v, good, win)
        speed = np.hypot(avg[..., 0], avg[..., 1]).mean(axis=1)
        # A frame is scored when it is measured, the camera is gliding around it, and at
        # least half the second around it was measured too.
        moving = good & (speed > GLIDE_MIN) & (speed < GLIDE_MAX) & (cnt >= win / 2)
        # Glides are the runs of scored frames, allowing short gaps (typing under a still
        # camera for a few frames) inside one glide.
        idx = np.nonzero(moving)[0]
        if len(idx) == 0:
            continue
        groups, start = [], 0
        for q in range(1, len(idx) + 1):
            if q == len(idx) or idx[q] - idx[q - 1] > win // 2:
                groups.append(idx[start:q])
                start = q
        for g in groups:
            if len(g) < MIN_GLIDE_FRAMES:
                continue
            dev = np.hypot(v[g, :, 0] - avg[g, :, 0], v[g, :, 1] - avg[g, :, 1]).mean(axis=1)
            unev = dev / np.maximum(speed[g], SPEED_FLOOR)
            per_s = {}
            for t, u in zip(g + k0, unev):
                per_s.setdefault(int((t + 1) // fps), []).append(float(u))
            worst = sorted(((sec, round(_rms(u), 2)) for sec, u in per_s.items()),
                           key=lambda q: -q[1])[:3]
            glides.append({"start": (g[0] + k0 + 1) / fps, "end": (g[-1] + k0 + 2) / fps,
                           "frames": int(len(g)), "score": _rms(unev),
                           "speed": float(np.mean(speed[g])), "worst": worst})
    return glides


def _local_fit(v, good, win):
    """The local trend of each step series: a quadratic fitted through the measured frames of
    the second around each frame, read at that frame.

    A flat one-second average lags whenever the camera speeds up or slows down, so an eased
    move - which does both - scored as if it stumbled. A quadratic follows a smooth change of
    speed and is still blind to a frame-to-frame stumble, which is what is being measured."""
    half = win // 2
    t = np.arange(-half, half + 1, dtype=np.float64)
    m = good.astype(np.float64)
    corr = lambda x, k: np.correlate(x, t ** k, "same")  # noqa: E731
    s = [corr(m, k) for k in range(5)]
    a = np.empty((len(m), 3, 3))
    for r in range(3):
        for c in range(3):
            a[:, r, c] = s[r + c]
    a += np.eye(3) * 1e-9
    out = np.empty_like(v)
    for c in range(v.shape[1]):
        for d in range(2):
            y = v[:, c, d] * m
            rhs = np.stack([corr(y, k) for k in range(3)], -1)
            out[:, c, d] = np.linalg.solve(a, rhs[..., None])[:, 0, 0]
    return out


def _rms(values):
    values = np.asarray(values, np.float64)
    return float(np.sqrt(np.mean(values * values)))


def check(path):
    """(verdict, detail) for qa: PASS, FAIL, or N/A when the camera never moves."""
    try:
        result = measure(path)
    except SystemExit as exc:
        return "FAIL", str(exc)
    return result["verdict"], result["detail"]


def measure(path):
    """{frames, glides, glide_seconds, worst_glide, verdict, detail} for a video file."""
    steps, count, size = camera_track(path)
    if count < 2:
        return {"frames": count, "glides": [], "glide_seconds": 0.0, "verdict": "FAIL",
                "detail": "read %d frames from %s, so nothing could be measured" % (count, path)}
    fps = probe(path).get("fps") or 30.0     # read once already by camera_track
    glides = score_track(steps, size, fps)
    seconds = sum(g["frames"] for g in glides) / fps
    if not glides:
        return {"frames": count, "glides": [], "glide_seconds": 0.0, "verdict": "N/A",
                "detail": "no camera move found in %d frames read, so nothing was measured - "
                          "not a pass" % count}
    worst = max(glides, key=lambda g: g["score"])
    bad = [g for g in glides if g["score"] > THRESHOLD]
    verdict = "FAIL" if bad else "PASS"
    shown = sorted(glides, key=lambda g: -g["score"])[:4]
    detail = ("%d glides, %.1fs of camera movement measured in %d frames; worst %.2f at "
              "%s against %.2f%s" % (
                  len(glides), seconds, count, worst["score"], _clock(worst["start"]), THRESHOLD,
                  "" if not bad else "; shaking: " + ", ".join(
                      "%s-%s %.2f" % (_clock(g["start"]), _clock(g["end"]), g["score"])
                      for g in shown if g["score"] > THRESHOLD)))
    return {"frames": count, "glides": glides, "glide_seconds": seconds,
            "worst_glide": worst, "verdict": verdict, "detail": detail}


def _clock(s):
    return "%d:%04.1f" % (int(s // 60), s % 60)


def main(argv=None):
    """python -m ccvideo.qa.steady VIDEO [VIDEO...] - the STEADY measurement on its own, with
    every glide listed."""
    import sys
    args = argv if argv is not None else sys.argv[1:]
    for path in args:
        result = measure(path)
        print("%-4s STEADY %s  (%s)" % (result["verdict"], result["detail"], path))
        for g in result["glides"]:
            print("      %s-%s  %4d frames  %.2f  worst seconds %s"
                  % (_clock(g["start"]), _clock(g["end"]), g["frames"], g["score"], g["worst"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
