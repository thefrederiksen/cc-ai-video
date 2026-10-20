"""Record an animated HTML page, frame by exact frame, under a narration.

The page is the animation: an interactive figure, a chart, anything a browser draws. It
exposes one function, `window.ccvideoFrame(t)`, that poses everything for time `t` seconds
(it may return a promise). The recorder sets each frame's time itself and screenshots it, so
the result never depends on how fast the browser or the machine runs - a slow frame is slow
to capture, never late on screen - and every frame is exactly where the narration says.
"""

import subprocess
from pathlib import Path

from . import budget

HOOK = "window.ccvideoFrame"


def capture(page, audio, out, start, end, width, height, fps=30):
    from playwright.sync_api import sync_playwright

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    url = Path(page).resolve().as_uri()
    frames = int(round((end - start) * fps))
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "image2pipe", "-c:v", "png", "-r", str(fps), "-i", "-",
           "-ss", "%.3f" % start, "-t", "%.3f" % (end - start), "-i", str(audio),
           "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", "-shortest", str(out)]
    print("  %d frames of %s at %dx%d" % (frames, Path(page).name, width, height), flush=True)
    proc = subprocess.Popen(budget.ffmpeg(cmd), stdin=subprocess.PIPE)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            tab = browser.new_page(viewport={"width": width, "height": height})
            errors = []
            tab.on("pageerror", lambda err: errors.append(str(err)))
            tab.goto(url)
            tab.wait_for_load_state("networkidle")
            if not tab.evaluate("typeof %s === 'function'" % HOOK):
                raise SystemExit("%s does not define %s(t) - the recorder poses each frame "
                                 "through it" % (page, HOOK))
            for i in range(frames):
                t = start + i / float(fps)
                tab.evaluate("t => %s(t)" % HOOK, t)
                if errors:
                    raise SystemExit("the page threw at frame %d (%.2fs): %s" % (i, t, errors[0]))
                proc.stdin.write(tab.screenshot(type="png"))
                if i % (fps * 5) == 0:
                    print("  %5.1fs" % t, flush=True)
            browser.close()
    finally:
        if proc.poll() is None:
            proc.stdin.close()
        code = proc.wait()
    if code != 0:
        raise SystemExit("ffmpeg failed encoding %s (exit %d)" % (out, code))
    return out
