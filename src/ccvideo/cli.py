"""The ccvideo command line.

Two halves, and the split is a product decision rather than a code one.

GENERATE makes AI videos from a script. They are synthetic and there is no attempt to pass as
a human presenter: no fake breath, no manufactured hesitation. The goal is a video that is
clear, correct, fast to produce and cheap to change.

EDIT strings real recorded footage together. It is saving the cost of an editor and moving
faster, and every cut it makes lands on a real word boundary.

Both halves finish at the same QA gate.
"""

import argparse
import sys
from pathlib import Path

from . import brand as brandlib
from . import targets


def main(argv=None):
    from . import budget
    budget.lower_priority()   # before anything starts: every worker and ffmpeg inherits it
    parser = argparse.ArgumentParser(
        prog="ccvideo", description="Make AI videos, and cut real footage together.")
    sub = parser.add_subparsers(dest="command", required=True)

    _add_tutorial(sub)
    _add_lint(sub)
    _add_transcribe(sub)
    _add_edit(sub)
    _add_qa(sub)
    _add_sheet(sub)
    _add_illustrate(sub)
    _add_image(sub)
    _add_photo(sub)
    _add_score(sub)
    _add_illustrate_check(sub)

    args = parser.parse_args(argv)
    return args.run(args) or 0


# ------------------------------------------------------------------ generate

def _add_tutorial(sub):
    p = sub.add_parser("tutorial", help="a narrated video from a segment script and frames")
    p.add_argument("--script", required=True)
    p.add_argument("--shots", required=True, help="directory holding the IMAGE files")
    p.add_argument("--out", required=True)
    p.add_argument("--brand", default="default")
    p.add_argument("--brands", default="", help="JSON file of extra or replacement brands")
    p.add_argument("--size", default="desktop", choices=sorted(targets.SIZES),
                   help="desktop 1920x1080 or phone 1080x1920")
    p.add_argument("--tts", default="openai", choices=["openai", "elevenlabs"])
    p.add_argument("--voice", default="", help="override the voice for the active provider")
    p.add_argument("--work", default="", help="build directory (default: 'build' beside --out)")
    p.add_argument("--lang", default="", help="language for the runtime estimate")
    p.add_argument("--only", default="", help="build just these segment ids")
    p.add_argument("--no-tts", action="store_true", help="cached audio only; never synthesise")
    p.add_argument("--estimate", action="store_true", help="word count and runtime, then stop")
    p.add_argument("--publish", action="store_true",
                   help="this render is for publication: production placeholders are an error")
    p.add_argument("--card-advice", type=int, default=3,
                   help="report when a script has more title cards than this")
    p.add_argument("--words-policy", default="default",
                   help="which of the brand's word policies applies to this script")
    p.add_argument("--loudness", type=float, default=None,
                   help="level the finished file to this LUFS (try -14 for YouTube). Off by "
                        "default, so a render stays byte-for-byte reproducible")
    p.set_defaults(run=_tutorial)


def _tutorial(args):
    from .generate import script as scriptlib
    from .generate.assemble import Build, assemble, write_timings
    from .narrate import Voice, cache_path, cached

    brand = brandlib.get(args.brand, args.brands or None)
    target = targets.target("tutorial-phone" if args.size == "phone" else "tutorial-desktop")

    shots = Path(args.shots).resolve()
    if not shots.is_dir():
        raise SystemExit("--shots is not a directory: %s" % shots)
    doc = scriptlib.parse(Path(args.script).resolve())

    problems = scriptlib.lint(doc, str(shots), brand, card_advice=args.card_advice,
                              publish=args.publish, words_policy=args.words_policy)
    if problems:
        print(scriptlib.format_problems(problems))
        if scriptlib.has_errors(problems):
            raise SystemExit("the script has errors; nothing was synthesised or rendered")
        print("")

    if args.only:
        wanted = {s.strip().zfill(3) for s in args.only.split(",")}
        doc.segments = [s for s in doc.segments if s.id in wanted]
        if not doc.segments:
            raise SystemExit("--only matched no segments")

    lang = scriptlib.language_of(args.script, args.lang)
    seconds, words, cjk = scriptlib.estimate(doc, lang)
    seconds += (0.4 + 0.7) * len(doc.segments)
    print("estimate: %s, %d words%s, %d segments -> %02d:%02d"
          % (lang, words, (" + %d CJK characters" % cjk) if cjk else "",
             len(doc.segments), int(seconds // 60), int(seconds) % 60))
    if args.estimate:
        return 0

    out = Path(args.out).resolve()
    build = Build(Path(args.work).resolve() if args.work else out.parent / "build")
    voice = Voice(brand, args.tts, args.voice or None)

    todo = sum(1 for s in doc.segments
               if not cached(cache_path(build.audio, s.id, s.text, voice)))
    print("voice   : %s" % voice.describe())
    print("brand   : %s, %s %dx%d" % (args.brand, args.size, target["width"], target["height"]))
    print("script  : %d segments, %d needing voice" % (len(doc.segments), todo))

    def report(segment, secs, label, synthesised):
        print("  %s  %5.1fs  %s%s" % (segment.id, secs, label,
                                      "  [voiced]" if synthesised else ""))

    timings, total = assemble(doc, str(shots), out, brand, target, voice, build,
                              allow_synthesis=not args.no_tts, on_segment=report)
    if args.loudness is not None:
        from .generate.assemble import normalise_loudness
        from .shell import duration as measure
        normalise_loudness(out, args.loudness, target["encode"])
        total = measure(out)
        print("  levelled to %.1f LUFS" % args.loudness)
    timings_path = write_timings(timings, out.parent / ("%s-timings.txt" % out.stem))
    print("\noutput  : %s" % out)
    print("timings : %s" % timings_path)
    print("length  : %02d:%02d (%.1f seconds)" % (int(total // 60), int(total) % 60, total))
    return 0


def _add_lint(sub):
    p = sub.add_parser("lint", help="check a script without spending voice or render time")
    p.add_argument("--script", required=True)
    p.add_argument("--shots", required=True)
    p.add_argument("--brand", default="default")
    p.add_argument("--brands", default="")
    p.add_argument("--publish", action="store_true")
    p.add_argument("--card-advice", type=int, default=3)
    p.add_argument("--words-policy", default="default")
    p.set_defaults(run=_lint)


def _lint(args):
    from .generate import script as scriptlib
    brand = brandlib.get(args.brand, args.brands or None)
    doc = scriptlib.parse(Path(args.script).resolve())
    problems = scriptlib.lint(doc, str(Path(args.shots).resolve()), brand,
                              card_advice=args.card_advice, publish=args.publish,
                              words_policy=args.words_policy,
                         fade=args.fade)
    if not problems:
        print("OK %s: %d segments, nothing to report" % (args.script, len(doc.segments)))
        return 0
    print(scriptlib.format_problems(problems))
    return 1 if scriptlib.has_errors(problems) else 0


# ------------------------------------------------------------------ shared

def _add_transcribe(sub):
    p = sub.add_parser("transcribe", help="word-timed transcript for a media file")
    p.add_argument("media", nargs="+")
    p.add_argument("--out-dir", default="", help="where the words files go (default: beside)")
    p.add_argument("--model", default="base.en")
    p.add_argument("--language", default="en")
    p.add_argument("--force", action="store_true")
    p.set_defaults(run=_transcribe)


def _transcribe(args):
    from .transcribe import transcribe
    for media in args.media:
        path = transcribe(media, out_dir=args.out_dir or None, model_name=args.model,
                          language=args.language, force=args.force)
        print("OK %s" % path)
    return 0


# ------------------------------------------------------------------ edit

def _add_edit(sub):
    p = sub.add_parser("import", help="bring a take into an edit project")
    p.add_argument("source", help="an AgentEyes recording directory, or any video file")
    p.add_argument("--project", required=True)
    p.add_argument("--name", default="", help="what to call this take")
    p.add_argument("--model", default="base.en")
    p.set_defaults(run=_import)

    p = sub.add_parser("transcript", help="the project's takes as text with word timings")
    p.add_argument("--project", required=True)
    p.add_argument("--take", default="")
    p.set_defaults(run=_transcript)

    p = sub.add_parser("cut", help="cut by text: keep the sentences you name")
    p.add_argument("--project", required=True)
    p.add_argument("--take", default="")
    p.add_argument("--keep", action="append", default=[],
                   help="a phrase to keep; repeat for each. Cut lands on word boundaries.")
    p.add_argument("--window", action="append", default=[],
                   help="start-end in seconds, snapped to sentence boundaries")
    p.add_argument("--append", action="store_true", help="add to the timeline, do not replace")
    p.add_argument("--crop", default="", help="x,y,w,h in SOURCE pixels: the region worth "
                                              "showing. Without one a wide screen recording "
                                              "is unreadable in a phone frame.")
    p.set_defaults(run=_cut)

    p = sub.add_parser("crop", help="set the visible region on clips already in the timeline")
    p.add_argument("--project", required=True)
    p.add_argument("--clip", action="append", type=int, default=[],
                   help="clip number; repeat for several. Omit for every clip.")
    p.add_argument("--crop", default="", help="x,y,w,h in source pixels; empty clears it")
    p.set_defaults(run=_crop)

    p = sub.add_parser("trim-silence", help="drop dead air longer than a stated threshold")
    p.add_argument("--project", required=True)
    p.add_argument("--over", type=float, default=0.9, help="seconds of silence to cut past")
    p.add_argument("--leave", type=float, default=0.25, help="seconds of breath to leave")
    p.set_defaults(run=_trim_silence)

    p = sub.add_parser("timeline", help="show the timeline as it stands")
    p.add_argument("--project", required=True)
    p.set_defaults(run=_timeline)

    p = sub.add_parser("render", help="render the timeline to a target")
    p.add_argument("--project", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--target", default="youtube", choices=sorted(targets.TARGETS))
    p.add_argument("--captions", default="", choices=["", "centre", "strip", "full", "none"])
    p.add_argument("--hook", default="",
                   help="one or two lines separated by '|', burned across the top from the "
                        "FIRST frame. A platform uses that frame as the thumbnail, so this is "
                        "the thumbnail. The footage is inset below it.")
    p.add_argument("--brand", default="default", help="palette and product name for the hook")
    p.add_argument("--brands", default="")
    p.add_argument("--footer", default="", help="the line along the bottom of a hook layout")
    p.add_argument("--gap", type=float, default=1.0,
                   help="narration target only: seconds of silence where one take ends and "
                        "the next begins - a new chapter")
    p.set_defaults(run=_render)


def _import(args):
    from .edit.project import Project
    project = Project.open_or_create(args.project)
    take = project.add_take(args.source, name=args.name or None, model=args.model)
    print("OK take %s: %s, %.1fs, %d words"
          % (take["name"], take["video"], take["duration"], take["word_count"]))
    return 0


def _transcript(args):
    from .edit.project import Project
    project = Project.open(args.project)
    for line in project.transcript_lines(args.take or None):
        print(line)
    return 0


def _cut(args):
    from .edit.project import Project
    project = Project.open(args.project)
    clips = project.cut(take=args.take or None, keep=args.keep, windows=args.window,
                        append=args.append, crop=_crop_spec(args.crop))
    for clip in clips:
        print("  %-16s %7.2f - %7.2f  %s" % (clip["take"], clip["start"], clip["end"],
                                             clip["text"][:70]))
        for note in clip.get("notes", []):
            if note.startswith("EDGE:"):
                print("      %s" % note)
    print("timeline: %d clips, %.1fs" % (len(project.timeline), project.timeline_seconds()))
    return 0


def _crop_spec(text):
    if not text:
        return None
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4 or not all(p.lstrip("-").isdigit() for p in parts):
        raise SystemExit("a crop looks like x,y,w,h in whole source pixels, not %r" % text)
    return [int(p) for p in parts]


def _crop(args):
    from .edit.project import Project
    project = Project.open(args.project)
    n = project.set_crop(_crop_spec(args.crop), args.clip or None)
    print("OK crop %s on %d clip(s)" % (args.crop or "cleared", n))
    return 0


def _trim_silence(args):
    from .edit.project import Project
    project = Project.open(args.project)
    before = project.timeline_seconds()
    removed = project.trim_silence(over=args.over, leave=args.leave)
    print("removed %d stretches of dead air over %.2fs: %.1fs -> %.1fs"
          % (removed, args.over, before, project.timeline_seconds()))
    return 0


def _timeline(args):
    from .edit.project import Project
    project = Project.open(args.project)
    at = 0.0
    for i, clip in enumerate(project.timeline):
        length = clip["end"] - clip["start"]
        print("%3d  %-16s %7.2f - %7.2f  (%5.2fs at %7.2f)  %s"
              % (i, clip["take"], clip["start"], clip["end"], length, at, clip["text"][:60]))
        at += length
    print("%d clips, %.1fs" % (len(project.timeline), at))
    return 0


def _render(args):
    from .edit.project import Project
    from .edit.render import render_timeline
    project = Project.open(args.project)
    target = targets.target(args.target)
    brand = brandlib.get(args.brand, args.brands or None) if args.hook else None
    captions = args.captions or ("strip" if args.hook else target["captions"])
    out = render_timeline(project, Path(args.out).resolve(), target,
                          captions=captions, hook=args.hook, brand=brand,
                          footer=args.footer, gap=args.gap)
    print("OK %s" % out)
    return 0


# ------------------------------------------------------------------ qa

def _add_qa(sub):
    p = sub.add_parser("qa", help="the machine gate a rendered file must pass")
    p.add_argument("video")
    p.add_argument("--target", default="", choices=[""] + sorted(targets.TARGETS))
    p.add_argument("--script", default="", help="the script that made it, for the hear-back")
    p.add_argument("--brand", default="", help="whose word rules to apply to what is heard")
    p.add_argument("--brands", default="")
    p.add_argument("--no-speech", action="store_true",
                   help="skip the hear-back. It is the check that matters; skipping it is "
                        "for a fast local loop, never for a release")
    p.add_argument("--model", default="base.en")
    p.add_argument("--words-policy", default="default")
    p.add_argument("--fade", type=float, default=None,
                   help="the fade length the file was rendered with, in seconds. TAIL needs "
                        "it: the trailing silence must be at least this long or the fade ran "
                        "over speech. Defaults to this library's own join fade.")
    p.set_defaults(run=_qa)


def _qa(args):
    from .qa.run import run_checks
    brand = brandlib.get(args.brand, args.brands or None) if args.brand else None
    results = run_checks(args.video,
                         target=targets.target(args.target) if args.target else None,
                         script_path=args.script or None,
                         brand=brand,
                         speech=not args.no_speech,
                         model=args.model,
                         words_policy=args.words_policy,
                         fade=args.fade)
    for row in results:
        print("%-4s %-9s %s" % (row["result"], row["check"], row["detail"]))
    fails = [r for r in results if r["result"] == "FAIL"]
    warns = [r for r in results if r["result"] == "WARN"]
    skips = [r for r in results if r["result"] == "SKIP"]
    print("%s: %d FAIL, %d WARN, %d SKIP" % (args.video, len(fails), len(warns), len(skips)))
    if skips:
        print("NOT CLEARED: %s did not run, so nothing here covers what they check."
              % ", ".join(r["check"] for r in skips))
    return 1 if fails else 0


def _add_sheet(sub):
    p = sub.add_parser("sheet", help="a labelled contact sheet, so frames get read")
    p.add_argument("video", nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--per-video", type=int, default=3)
    p.add_argument("--columns", type=int, default=4)
    p.set_defaults(run=_sheet)


def _sheet(args):
    from .qa.sheet import build
    out, tiles = build(args.video, Path(args.out).resolve(), args.per_video, args.columns)
    print("OK %s  %d frames from %d file(s)" % (out, tiles, len(args.video)))
    print("Open it and READ it. Nothing else in this package looks at what a card SAYS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ------------------------------------------------------------------ illustrate

def _add_illustrate(sub):
    p = sub.add_parser("illustrate",
                       help="animate a scene list over a recorded narration, word by word")
    p.add_argument("--audio", required=True, help="the narration (ccvideo render --target narration)")
    p.add_argument("--words", required=True, help="word timings of that narration (ccvideo transcribe)")
    p.add_argument("--scenes", required=True, help="the scene list, anchored to spoken words")
    p.add_argument("--out", required=True)
    p.add_argument("--start", type=float, default=0.0, help="seconds into the narration")
    p.add_argument("--end", type=float, required=True, help="seconds into the narration")
    p.add_argument("--brand", default="devthrottle")
    p.add_argument("--brands", default="")
    p.add_argument("--workers", type=int, default=1,
                   help="render in this many processes side by side; the result is identical. "
                        "Capped at half the cores, whatever is asked")
    p.set_defaults(run=_illustrate)


def _illustrate(args):
    from .illustrate import render as illus, scenes as scenelib
    brand = brandlib.get(args.brand, args.brands or None)
    scenes = scenelib.load(args.scenes, args.words, start=args.start, end=args.end)
    for s in scenes:
        print("  scene %6.2f - %6.2f  %d elements" % (s["t0"], s["t1"], len(s["elements"])))
    out = illus.render(scenes, brand, args.audio, args.out, args.start, args.end,
                       workers=args.workers)
    print("OK %s" % out)
    return 0


def _add_illustrate_check(sub):
    p = sub.add_parser("illustrate-check",
                       help="score a scene list's picture BEFORE rendering: frames are drawn "
                            "and measured exactly as 'ccvideo score' measures the file")
    p.add_argument("--words", required=True)
    p.add_argument("--scenes", required=True)
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--end", type=float, required=True)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--json", default="")
    p.add_argument("--fail-under", type=float, default=None)
    p.set_defaults(run=_illustrate_check)


def _illustrate_check(args):
    import json as _json
    from .illustrate import render as illus, scenes as scenelib
    from .qa import picture
    scenes = scenelib.load(args.scenes, args.words, start=args.start, end=args.end)
    busy, diff = illus.sample(scenes, args.start, args.end, workers=args.workers)
    result = picture.score(busy, diff)
    result["scenes"] = str(args.scenes)
    # Name the scene each empty stretch falls in, so the fix goes to the right place.
    for run in result["empty"] + result["thin"]:
        t = args.start + run["at"]
        run["at"] = round(t, 1)
        run["scene"] = next((n for n, s in enumerate(scenes) if s["t0"] <= t < s["t1"]), None)
    if args.json:
        Path(args.json).write_text(_json.dumps(result, indent=1), encoding="utf-8")
    print(picture.report(result))
    if args.fail_under is not None and result["clean_percent"] < args.fail_under:
        print("FAIL  clean %.1f%% is under %.1f%%" % (result["clean_percent"], args.fail_under))
        return 1
    return 0


# ------------------------------------------------------------------ photo

def _add_photo(sub):
    p = sub.add_parser("photo", help="real photographs from Wikimedia Commons, free licenses "
                                     "only, each saved with its credit")
    ps = p.add_subparsers(dest="photo_cmd", required=True)
    s = ps.add_parser("search", help="list candidate photographs; nothing is downloaded")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=12)
    f = ps.add_parser("fetch", help="download one file and write its credit beside it")
    f.add_argument("title", help='the Commons file name, e.g. "File:Lettvin Pitts.jpg"')
    f.add_argument("--out", required=True)
    f.add_argument("--width", type=int, default=2400)
    c = ps.add_parser("credits", help="the credits of every photograph in a folder")
    c.add_argument("folder")
    p.set_defaults(run=_photo)


def _ascii(text):
    """Commons names are full of accents; this tool prints ASCII only."""
    return str(text).encode("ascii", "replace").decode()


def _photo(args):
    from . import photos
    if args.photo_cmd == "search":
        for f in photos.search(args.query, args.limit):
            print(_ascii("%s %-60s %5sx%-5s %-14s %s" % ("FREE " if f["free"] else "NOT  ",
                  f["title"][:60], f["width"], f["height"], f["license"][:14], f["author"][:40])))
            if f["description"]:
                print(_ascii("      %s" % f["description"][:110]))
        return 0
    if args.photo_cmd == "fetch":
        info = photos.fetch(args.title, args.out, args.width)
        print("OK %s" % args.out)
        print(_ascii("   %s" % info["credit"]))
        return 0
    for line in photos.credits(args.folder):
        print(_ascii(line))
    return 0


# ------------------------------------------------------------------ score

def _add_score(sub):
    p = sub.add_parser("score",
                       help="score a rendered video's picture: empty screen, thin screen, "
                            "nothing moving - pixel arithmetic, no model")
    p.add_argument("video")
    p.add_argument("--json", default="", help="also write the full result here")
    p.add_argument("--fail-under", type=float, default=None,
                   help="exit 1 when the clean percentage is below this")
    p.set_defaults(run=_score)


def _score(args):
    from .qa import picture
    result = picture.run(args.video, args.json or None)
    print(picture.report(result))
    if args.fail_under is not None and result["clean_percent"] < args.fail_under:
        print("FAIL  clean %.1f%% is under %.1f%%" % (result["clean_percent"], args.fail_under))
        return 1
    return 0


# ------------------------------------------------------------------ image

def _add_image(sub):
    p = sub.add_parser("image", help="generate a scene image (moods and places, not real faces)")
    p.add_argument("--prompt", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--size", default="1792x1024")
    p.set_defaults(run=_image)


def _image(args):
    from .imagegen import generate
    print("OK %s" % generate(args.prompt, args.out, args.size))
    return 0
