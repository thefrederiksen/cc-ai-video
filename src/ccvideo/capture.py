"""Record an animated HTML page, frame by exact frame, under a narration.

The page is the animation: an interactive figure, a chart, anything a browser draws. It
exposes one function, `window.ccvideoFrame(t)`, that poses everything for time `t` seconds
(it may return a promise). The recorder sets each frame's time itself and screenshots it, so
the result never depends on how fast the browser or the machine runs - a slow frame is slow
to capture, never late on screen - and every frame is exactly where the narration says.

Because every frame depends only on its own time, the frames can be split into runs, each
captured by its own browser in its own process, and joined without re-encoding. One browser
draws one frame at a time on one core, so on a machine with cores to spare this is close to
N times faster. The split is by GLOBAL frame index, never by seconds: cutting 60 s into eight
7.5 s pieces and rounding each piece's own frame count loses half a frame per piece, and the
joined picture drifts against the narration.

Nothing is written at --out until the whole file is joined, levelled and checked. Everything
before that lives in a work folder beside it, and a failed run deletes that folder: a partial
mp4 at the output path once ran through the next step as if it were the real output.
"""

import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import budget
from .shell import probe, run

HOOK = "window.ccvideoFrame"
SHOT_TIMEOUT = 30.0     # seconds; Playwright's own default for a screenshot
SHOT_RETRIES = 2        # fresh browsers per stalled frame before the run stops


def split(frames, workers):
    """Frame-index bounds for `workers` runs over `frames` frames: run n is
    [bounds[n], bounds[n + 1]). Every frame is in exactly one run and the runs differ by at
    most one frame."""
    return [frames * n // workers for n in range(workers + 1)]


def frame_count(start, end, fps):
    """The frames in [start, end) at `fps` - counted ONCE, for the whole range."""
    return int(round((end - start) * fps))


def run_count(requested, frames, fps):
    """How many runs to split `frames` into: what was asked, no run shorter than two seconds,
    and never more than the budget allows."""
    return budget.workers(min(requested, frames // (fps * 2) or 1))


class _Browser:
    """One headless browser with the page loaded and its hook checked."""

    def __init__(self, pw, url, page, width, height):
        self.browser = pw.chromium.launch()
        self.launched = time.time()
        self.shots = 0
        self.tab = self.browser.new_page(viewport={"width": width, "height": height})
        self.errors = []
        self.crashed = False
        self.tab.on("pageerror", lambda err: self.errors.append(str(err)))
        self.tab.on("crash", lambda _: setattr(self, "crashed", True))
        self.tab.goto(url)
        self.tab.wait_for_load_state("networkidle")
        if not self.tab.evaluate("typeof %s === 'function'" % HOOK):
            self.close()
            raise SystemExit("%s does not define %s(t) - the recorder poses each frame "
                             "through it" % (page, HOOK))

    def broken(self):
        return self.crashed or not self.browser.is_connected()

    def evidence(self):
        """What the browser looked like when a frame stalled - so the next one says why. How
        long it had run and how many frames it had shot tell a browser that goes bad after a
        while from a frame that is heavy in any browser."""
        return ("page crashed=%s, browser connected=%s, page errors=%d, "
                "%d frames shot in %.0fs since launch" % (
                    self.crashed, self.browser.is_connected(), len(self.errors),
                    self.shots, time.time() - self.launched))

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass                          # a hung or crashed browser is being thrown away anyway


def shoot(open_browser, browser, i, t, timeout, retries=SHOT_RETRIES, say=print):
    """Pose frame `i` at time `t` and screenshot it. Returns (png, browser).

    A screenshot that stalls past `timeout`, or a browser that crashes or disconnects, is
    retried in a FRESH browser, up to `retries` times. That is safe because a frame depends
    only on its time, so a new browser draws the same picture. Each stall is reported with
    Playwright's whole message (its call log says how far the screenshot got) and what the old
    browser looked like, because a retry that hides the evidence is how the cause stays
    unknown. Retries spent, the run stops. A page that THROWS is a defect in the page, not a
    stall, and stops the run at once.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    for attempt in range(retries + 1):
        try:
            browser.tab.evaluate("t => %s(t)" % HOOK, t)
            if browser.errors:
                raise SystemExit("the page threw at frame %d (%.2fs): %s"
                                 % (i, t, browser.errors[0]))
            png = browser.tab.screenshot(type="png", timeout=timeout * 1000)
            browser.shots += 1
            return png, browser
        except PlaywrightError as err:
            if not isinstance(err, PlaywrightTimeout) and not browser.broken():
                raise SystemExit("the page threw at frame %d (%.2fs): %s" % (i, t, err))
            said = str(err).strip() or type(err).__name__
            evidence = browser.evidence()
            browser.close()
            if attempt == retries:
                raise SystemExit("frame %d (%.2fs) stalled %d times (%s), last:\n%s"
                                 % (i, t, retries + 1, evidence, said))
            say("  frame %d (%.2fs) stalled (%s) - fresh browser, retry %d/%d:\n%s"
                % (i, t, evidence, attempt + 1, retries, said), flush=True)
            browser = open_browser()


def _capture_run(job):
    """Capture frames [first, last) into one video-only part. Runs in a worker process."""
    from playwright.sync_api import sync_playwright

    page, start, first, last, fps, width, height, part, threads, timeout, label = job
    url = Path(page).resolve().as_uri()
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "image2pipe", "-c:v", "png", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-threads", str(threads), str(part)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        with sync_playwright() as pw:
            def open_browser():
                return _Browser(pw, url, page, width, height)
            browser = open_browser()
            try:
                for i in range(first, last):
                    t = start + i / float(fps)
                    png, browser = shoot(open_browser, browser, i, t, timeout)
                    proc.stdin.write(png)
                    if (i - first) % (fps * 30) == 0:
                        print("  %s%5.1fs" % (label, t), flush=True)
            finally:
                browser.close()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    proc.stdin.close()
    code = proc.wait()
    if code != 0:
        raise SystemExit("ffmpeg failed encoding %s (exit %d)" % (part, code))
    return str(part)


class RunFailed(Exception):
    """A run stopped. Carried as an Exception because a pool worker only reports those back;
    a SystemExit raised in a worker kills it silently and leaves the pool waiting forever."""


def _pool_run(job):
    try:
        return _capture_run(job)
    except SystemExit as err:
        raise RunFailed(str(err)) from None


def _join(parts, audio, joined, start, end):
    """Join the parts in order, without re-encoding, under [start, end) of the narration.

    No -shortest: a narration that ends a fraction of a frame early would make it drop the
    whole last frame, and the frame check would then refuse a file that is fine."""
    listing = Path(joined).parent / "concat.txt"
    # ffmpeg reads a relative name in a concat list against the LIST's folder, so every part
    # is named absolutely.
    listing.write_text("".join("file '%s'\n" % Path(p).resolve().as_posix() for p in parts),
                       encoding="utf-8")
    run(["ffmpeg", "-y", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(listing),
         "-ss", "%.3f" % start, "-t", "%.3f" % (end - start), "-i", str(audio),
         "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", str(joined)])


def _check(path, frames, fps):
    """The joined file must hold exactly the frames that were captured, and the narration."""
    counted = int(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                       "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0",
                       str(path)]).strip())
    if counted != frames:
        raise SystemExit("%s holds %d frames, %d were captured" % (path, counted, frames))
    shape = probe(path)
    if not shape["audio"]:
        raise SystemExit("%s has no narration track" % path)
    seconds = frames / float(fps)
    if abs(shape["duration"] - seconds) > 0.25:
        raise SystemExit("%s runs %.2fs, its %d frames are %.2fs"
                         % (path, shape["duration"], frames, seconds))


def capture(page, audio, out, start, end, width, height, fps=30, workers=1,
            loudness=None, encode=None, timeout=SHOT_TIMEOUT):
    """Capture [start, end) of `page` at `fps` under the narration, into `out`.

    `workers` above one splits the frames into that many runs, each in its own browser and
    process. `loudness` (LUFS) levels the joined file once, so there is no step at a seam.
    `out` appears only when the file is whole; a failure leaves nothing behind.
    """
    import multiprocessing

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = frame_count(start, end, fps)
    # The narration must reach the last frame. A narration that ends inside the last frame
    # is normal - it rarely ends on a frame boundary - and the picture simply runs that
    # fraction longer. One short by a whole frame or more is a wrong --end, and a one-second
    # probe says so now rather than after the capture. The suggestion is the last frame
    # boundary the narration covers, so following it always passes this check.
    have = probe(audio)
    heard = max((have["audio_duration"] or have["duration"]) - start, 0.0)
    if not have["audio"] or heard <= (frames - 1) / float(fps):
        covered = math.floor(heard * fps + 1e-6)
        raise SystemExit("the narration %s has %.3fs from --start %.3f; %d frames at %d fps "
                         "need %.3fs. Bring --end in to at most %.3f."
                         % (audio, heard, start, frames, fps, frames / float(fps),
                            start + covered / float(fps)))
    workers = run_count(workers, frames, fps)
    bounds = split(frames, workers)
    # Each encoder gets its share of the HALF of the cores this library may use.
    threads = budget.encoder_threads(workers)
    # A fresh folder per capture, so two captures (or an illustrate render) aimed at the same
    # name never delete each other's parts.
    work = Path(tempfile.mkdtemp(prefix=out.stem + ".parts-", dir=str(out.parent)))
    budget.lower_priority()
    jobs = [(page, start, bounds[n], bounds[n + 1], fps, width, height,
             work / ("part-%03d.mp4" % n), threads, timeout,
             "run %d/%d " % (n + 1, workers) if workers > 1 else "")
            for n in range(workers)]
    print("  %d frames of %s at %dx%d in %d run(s)"
          % (frames, Path(page).name, width, height, workers), flush=True)
    began = time.time()
    try:
        if workers == 1:
            parts = [_capture_run(jobs[0])]
        else:
            with multiprocessing.Pool(workers) as pool:
                # Unordered, so a failed run stops the capture at once rather than after every
                # run ahead of it has finished.
                parts = []
                try:
                    for n, part in enumerate(pool.imap_unordered(_pool_run, jobs)):
                        parts.append(part)
                        print("  run %d/%d done" % (n + 1, workers), flush=True)
                except RunFailed as err:
                    raise SystemExit(str(err)) from None
                parts.sort()
        joined = work / ("joined" + out.suffix)
        _join(parts, audio, joined, start, end)
        if loudness is not None:
            from .generate.assemble import normalise_loudness
            normalise_loudness(joined, loudness, encode)
            print("  levelled to %.1f LUFS" % loudness)
        _check(joined, frames, fps)
        os.replace(str(joined), str(out))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("  %d frames in %.0fs" % (frames, time.time() - began), flush=True)
    return out
