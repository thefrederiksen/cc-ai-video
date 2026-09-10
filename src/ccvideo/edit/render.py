"""Render an edit timeline to a finished file.

Each clip is cut, fitted to the target and LEVELLED ON ITS OWN before anything is joined.
That per-clip pass is not cosmetic and it is the thing most easily dropped as redundant: takes
recorded on different days, on different microphones, or pulled from archive footage arrive at
wildly different levels, and a single normalisation at the end sets one level for the loudest
of them and leaves the rest inaudible. Measured on a video mixing archive radio with a studio
microphone, only eight percent of the quiet passage's words could be heard back out of the
finished render; levelling each piece first is what let both sit in the same video.

Captions are built from the same word timings the cuts were made on, so a caption can never
disagree with the cut it sits under.
"""

from pathlib import Path

from .. import cues as cuelib
from .. import subtitles, targets, words as wordlib
from ..shell import duration, run


def crop_filter(crop, source_w, source_h):
    """The crop chain for a clip, and the source size that follows it.

    A crop is the single biggest lever on whether a screen recording is READABLE on a phone.
    A 1920x1080 capture fitted whole into a 1080x1920 frame is a band 607 pixels tall, and
    interface text in it cannot be read at arm's length. Cropping to the panel that matters and
    scaling THAT up is a one and a half to three times blow-up, and the words fill the screen.
    """
    if not crop:
        return "", source_w, source_h
    x, y, w, h = (int(v) for v in crop)
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > source_w or y + h > source_h:
        raise SystemExit("crop %s falls outside the %dx%d source"
                         % (list(crop), source_w, source_h))
    return "crop=%d:%d:%d:%d," % (w, h, x, y), w, h


def fit_filter(source_w, source_h, width, height, blur_fill=True):
    """Fit source video into the target frame.

    When the aspect ratios differ - 16:9 footage going to a 9:16 phone frame - the gap is
    filled with the same frame, scaled up, blurred and darkened. Bars are dead pixels on a
    phone; the blurred fill reads as depth and costs nothing. When the aspects match, the
    frame is scaled and padded and nothing else happens to it.
    """
    source_aspect = source_w / float(source_h)
    target_aspect = width / float(height)
    if abs(source_aspect - target_aspect) < 0.01 or not blur_fill:
        return ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
                % (width, height, width, height))
    return (
        "split=2[bg][fg];"
        "[bg]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"
        "gblur=sigma=28,eq=brightness=-0.25:saturation=1.1[bgb];"
        "[fg]scale=%d:%d:force_original_aspect_ratio=decrease,setsar=1[fgs];"
        "[bgb][fgs]overlay=x=(W-w)/2:y=(H-h)/2"
        % (width, height, width, height, width, height))


def render_clip(project, clip, index, out_path, target, level_db=-16.0, frame=None,
                fade_in=True, fade_out=True):
    """Cut one clip, fit it, level it, encode it.

    `frame` is the size to fit into: the whole target frame normally, and the inset box
    when a hook layout is being built.
    """
    from ..shell import probe

    take = project.take(clip["take"])
    encode = target["encode"]
    seconds = clip["end"] - clip["start"]
    source = probe(take["video"])
    frame_w, frame_h = frame if frame else (target["width"], target["height"])
    if not source["audio"]:
        raise SystemExit(
            "%s carries no audio track, so nothing in it can be cut on a word boundary "
            "or heard back afterwards" % take["video"])

    pre, view_w, view_h = crop_filter(clip.get("crop"), source["width"], source["height"])
    video_filter = ("[0:v]" + pre
                    + fit_filter(view_w, view_h, frame_w, frame_h,
                                 blur_fill=frame is None) + "[v]")
    # A fade hides the click at a SEAM. The first and last edges of the finished film are
    # not seams, and fading them costs real things: the opening frame is the one a platform
    # turns into a thumbnail, and it came out empty, while the closing fade is precisely
    # what once ate a short's final word. So fade the joins, not the ends.
    fade = min(targets.JOIN_FADE, seconds / 6.0)
    chain = ["aresample=%s" % encode["sample_rate"],
             "loudnorm=I=%.1f:TP=-1.5:LRA=11" % level_db]
    if fade_in:
        chain.append("afade=t=in:st=0:d=%.3f" % fade)
    if fade_out:
        chain.append("afade=t=out:st=%.3f:d=%.3f" % (max(seconds - fade, 0.0), fade))
    audio_filter = "[0:a]" + ",".join(chain) + "[a]"

    run(["ffmpeg", "-y", "-loglevel", "error",
         "-ss", "%.3f" % clip["start"], "-t", "%.3f" % seconds, "-i", str(take["video"]),
         "-filter_complex", video_filter + ";" + audio_filter,
         "-map", "[v]", "-map", "[a]"]
        + targets.video_args(encode) + targets.audio_args(encode)
        + ["-t", "%.3f" % seconds, str(out_path)])
    return duration(out_path)


# Below this, the picture has been shrunk enough that interface text stops being readable at
# phone size. Measured against the renderer this replaces, which graded the same ratio.
LEGIBLE = 0.8


def blow_up(project, clip, target):
    """How much the visible region is scaled by on its way into the frame. Under 1.0 is a
    shrink; a screen recording that has been shrunk is a screen recording nobody can read."""
    from ..shell import probe
    take = project.take(clip["take"])
    source = probe(take["video"])
    _, view_w, view_h = crop_filter(clip.get("crop"), source["width"], source["height"])
    scale = min(target["width"] / float(view_w), target["height"] / float(view_h))
    return scale


def timeline_cues(project, timeline, lengths, style_name, corrections=()):
    """Cues for the whole timeline, in finished-file time.

    Each clip's words are windowed out of its own take and shifted by where that clip actually
    landed - using the MEASURED length of the rendered piece, not the requested one, because
    a frame-aligned encode rounds and a few of those accumulate into visible caption drift.
    """
    all_cues, at = [], 0.0
    for clip, length in zip(timeline, lengths):
        take = project.take(clip["take"])
        words = project.words_of(take)
        inside = wordlib.window({"segments": [{"words": words}]},
                                clip["start"], clip["end"], rebase=True)
        all_cues.extend(cuelib.build(inside, style_name, offset=at, corrections=corrections))
        at += length
    return all_cues, at


def render_timeline(project, out_path, target, captions="full", corrections=(), hook="",
                    brand=None, footer=""):
    """Render the project's timeline. Returns the output path.

    With `hook`, the frame becomes the hook layout: the headline burned across the top from
    the first frame - a platform takes that frame as the thumbnail - and the footage inset
    below it rather than filling the frame.
    """
    if not project.timeline:
        raise SystemExit(
            "this project's timeline is empty. Cut something into it first: "
            "ccvideo cut --project %s --keep \"...\"" % project.root)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = project.root / "build"
    work.mkdir(parents=True, exist_ok=True)

    box = plate = None
    if hook:
        from .. import layout
        from ..shell import probe as probe_media
        if brand is None:
            raise SystemExit("a hook needs a brand for its palette and product name")
        first = project.take(project.timeline[0]["take"])
        shape = probe_media(first["video"])
        _, view_w, view_h = crop_filter(project.timeline[0].get("crop"),
                                        shape["width"], shape["height"])
        box = layout.hook_box(target["width"], target["height"], view_w, view_h)
        plate = layout.build_hook_plate(brand, target["width"], target["height"],
                                        layout.parse_hook(hook), footer,
                                        box, work / "hook.png")

    pieces, lengths, small = [], [], []
    for i, clip in enumerate(project.timeline):
        piece = work / ("clip-%03d.mp4" % i)
        length = render_clip(project, clip, i, piece, target,
                             frame=(box[0], box[1]) if box else None,
                             fade_in=i > 0,
                             fade_out=i < len(project.timeline) - 1)
        pieces.append(piece)
        lengths.append(length)
        factor = blow_up(project, clip, target)
        print("  %03d  %-16s %6.2fs  x%.2f  %s"
              % (i, clip["take"], length, factor, clip["text"][:52]), flush=True)
        if factor < LEGIBLE:
            small.append((i, factor))
    if small:
        # Reported, never silently corrected. Which part of a screen matters is a judgment
        # about the content, and a tool that guessed it would crop away the thing being shown.
        print("  %d clip(s) are under x%.2f and interface text in them will not read on a "
              "phone: %s. Crop them to the panel that matters: ccvideo crop --project %s "
              "--clip <n> --crop x,y,w,h"
              % (len(small), LEGIBLE, ", ".join("%03d (x%.2f)" % s for s in small),
                 project.root), flush=True)

    listing = work / "concat.txt"
    listing.write_text("".join("file '%s'\n" % p.as_posix() for p in pieces), encoding="utf-8")
    joined = work / "joined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(joined)])
    total = duration(joined)

    if target["max_seconds"] and total > target["max_seconds"]:
        raise SystemExit(
            "the timeline is %.1fs and the %s target takes at most %.0fs. Cut something out "
            "rather than shipping a file the platform will refuse or truncate."
            % (total, target["name"], target["max_seconds"]))

    if captions in ("", "none"):
        _finish(joined, out_path, target, total, None, plate, box)
    else:
        cues, _ = timeline_cues(project, project.timeline, lengths, captions, corrections)
        cues, dropped = cuelib.hold_into_gaps(cues, until=total)
        for cue in dropped:
            print("  dropped an unreadable fragment at %.2fs: %r"
                  % (cue["start"], cue["plain"]), flush=True)
        if plate:
            from .. import layout
            baseline = layout.caption_baseline(target["height"])
        elif captions == "centre":
            baseline = int(target["height"] * 0.555)
        else:
            baseline = int(target["height"] * 0.86)
        ass = subtitles.write_ass(cues, work / "captions.ass",
                                  target["width"], target["height"],
                                  size=80 if captions == "centre" else 56,
                                  baseline=baseline,
                                  glow=captions == "centre" and not plate)
        # The sidecar is an accessibility track and is built separately, in "full" style. The
        # burned-in cues are a design - two words at a time, upper case - and shipping those as
        # an SRT gives a screen reader a shouted fragment every second.
        sidecar, _ = timeline_cues(project, project.timeline, lengths, "full", corrections)
        subtitles.write_srt(sidecar, out_path.with_suffix(".srt"))
        _finish(joined, out_path, target, total, ass, plate, box)
        print("  %d cues, sidecar %s" % (len(cues), out_path.with_suffix(".srt").name))

    return out_path


def _finish(joined, out_path, target, total, ass, plate=None, box=None):
    """The last pass: the hook plate if there is one, captions, loudness, faststart."""
    encode = target["encode"]
    if plate:
        # The plate is input 0 and is looped, so the hook is on screen from the FIRST frame -
        # which is the frame a platform turns into the thumbnail.
        inputs = ["-loop", "1", "-i", str(plate), "-i", str(joined)]
        # Both streams are rebased to zero before the overlay. The joined file inherits a
        # start timestamp from the concat, so without this the compositor emits ONE frame of
        # bare plate before the footage arrives - and that one frame is the frame a platform
        # turns into the thumbnail. It showed an empty box.
        stage = ("[1:v]setpts=PTS-STARTPTS[fg];[0:v]setpts=PTS-STARTPTS[bg];"
                 "[bg][fg]overlay=%d:%d:shortest=1[base]" % (box[2], box[3]))
        audio_in = "[1:a]"
    else:
        inputs = ["-i", str(joined)]
        stage = "[0:v]null[base]"
        audio_in = "[0:a]"
    if ass:
        stage += ";[base]subtitles='%s'[v]" % subtitles.ass_path_for_filter(ass)
    else:
        stage += ";[base]null[v]"
    if target["loudness"] is not None:
        audio = "%sloudnorm=I=%.1f:TP=-1.5:LRA=11[a]" % (audio_in, target["loudness"])
    else:
        audio = "%sanull[a]" % audio_in
    run(["ffmpeg", "-y", "-loglevel", "error"] + inputs
        + ["-filter_complex", stage + ";" + audio, "-map", "[v]", "-map", "[a]"]
        + targets.video_args(encode) + targets.audio_args(encode)
        + ["-t", "%.3f" % total, "-movflags", "+faststart", str(out_path)])
