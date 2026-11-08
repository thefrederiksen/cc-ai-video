"""Calibrate STEADY: render the same shots two ways and score them.

Every shot is rendered with ffmpeg's zoompan (exactly the filter the hand-written course builders
used, whole-pixel steps) and with `ccvideo move` (sub-pixel), over three kinds of picture: a
screenshot, terminal footage and browser footage. STEADY must pass every ccvideo render and fail
every zoompan one, with room between the worst smooth score and the best shaky score.

    python tools/steady_calibrate.py OUT_DIR --still a.png --terminal t.mp4@35.3 --browser b.mp4@9.5

Each footage source is PATH@START_SECONDS. Writes OUT_DIR/calibration.md with the table.
"""

import argparse
import subprocess
import sys
from pathlib import Path

FPS = 30

# name, seconds, (z0, z1), (c0, c1)
MOVES = [
    ("slow zoom", 8.0, (1.0, 1.08), ((960, 540), (950, 600))),
    ("fast zoom", 3.0, (1.0, 1.5), ((960, 540), (900, 400))),
    ("pan", 5.0, (1.3, 1.3), ((760, 430), (1160, 650))),   # inside the frame at 1.3
    ("zoom and pan", 6.0, (1.0, 1.45), ((960, 420), (1040, 610))),
]


def zoompan(z0, z1, c0, c1, seconds):
    """The course builders' zp(), verbatim in shape."""
    n = max(1, int(seconds * FPS))
    lin = lambda a, b: "(%f+(%f-%f)*min(on/%d\\,1))" % (a, b, a, n)  # noqa: E731
    return ("zoompan=z='%s':x='max(0\\,min(iw-iw/zoom\\,%s-iw/zoom/2))':"
            "y='max(0\\,min(ih-ih/zoom\\,%s-ih/zoom/2))':d=1:s=1920x1080:fps=%d"
            % (lin(z0, z1), lin(c0[0], c1[0]), lin(c0[1], c1[1]), FPS))


def render_zoompan(src, start, move, out):
    name, seconds, (z0, z1), (c0, c1) = move
    if start is None:
        inp = ["-loop", "1", "-framerate", str(FPS), "-t", "%.3f" % seconds, "-i", str(src)]
        pre = "scale=1920:1080,setsar=1,"
    else:
        inp = ["-ss", str(start), "-i", str(src)]
        pre = "setpts=PTS-STARTPTS,fps=%d,tpad=stop_mode=clone:stop_duration=%.3f," % (FPS, seconds)
    subprocess.run(["ffmpeg", "-v", "error", "-y"] + inp
                   + ["-vf", pre + zoompan(z0, z1, c0, c1, seconds) + ",trim=duration=%.3f" % seconds,
                      "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                      "-r", str(FPS), "-an", str(out)], check=True)


def render_move(src, start, move, out, ease):
    name, seconds, (z0, z1), (c0, c1) = move
    cmd = [sys.executable, "-m", "ccvideo", "move", str(src), "--out", str(out),
           "--seconds", str(seconds), "--zoom", "%g:%g" % (z0, z1),
           "--centre", "%g,%g:%g,%g" % (c0[0], c0[1], c1[0], c1[1]), "--ease", ease,
           "--profile", "footage"]
    if start is not None:
        cmd += ["--from", str(start)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--still", required=True)
    ap.add_argument("--terminal", required=True)
    ap.add_argument("--browser", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def src(spec):
        path, _, start = spec.partition("@")
        return path, float(start) if start else 0.0

    contents = [("screenshot", (a.still, None)), ("terminal", src(a.terminal)),
                ("browser", src(a.browser))]
    from ccvideo.qa import steady

    rows = []
    for cname, (path, start) in contents:
        for move in MOVES:
            tag = "%s-%s" % (cname, move[0].replace(" ", "-"))
            files = {"zoompan": out / (tag + "-zoompan.mp4"),
                     "move linear": out / (tag + "-move-linear.mp4"),
                     "move smooth": out / (tag + "-move-smooth.mp4")}
            if not files["zoompan"].exists():
                render_zoompan(path, start, move, files["zoompan"])
            if not files["move linear"].exists():
                render_move(path, start, move, files["move linear"], "linear")
            if not files["move smooth"].exists():
                render_move(path, start, move, files["move smooth"], "smooth")
            for how, f in files.items():
                r = steady.measure(f)
                g = r.get("worst_glide") or {}
                rows.append((cname, move[0], how, r["verdict"], g.get("score"),
                             r["glide_seconds"], g.get("speed")))
                print("%-10s %-13s %-12s %-4s score %s  measured %.1fs" % (
                    cname, move[0], how, r["verdict"],
                    "%.2f" % g["score"] if g else "-", r["glide_seconds"]), flush=True)

    smooth = [r[4] for r in rows if r[2] != "zoompan" and r[4] is not None]
    shaky = [r[4] for r in rows if r[2] == "zoompan" and r[4] is not None]
    lines = ["| picture | move | rendered with | STEADY | score | seconds measured | corner speed px/frame |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s | %.1f | %s |" % (
            r[0], r[1], r[2], r[3], "-" if r[4] is None else "%.2f" % r[4], r[5],
            "-" if r[6] is None else "%.2f" % r[6]))
    lines.append("")
    lines.append("Worst smooth score: %.2f. Best shaky score: %.2f. Threshold: %.2f."
                 % (max(smooth), min(shaky), steady.THRESHOLD))
    (out / "calibration.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[-1:]))
    return 0 if max(smooth) < steady.THRESHOLD < min(shaky) else 1


if __name__ == "__main__":
    raise SystemExit(main())
