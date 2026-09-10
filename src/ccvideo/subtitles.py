"""Writing cues out: burned-in ASS for a short, and a sidecar SRT for a long video.

WHY THE SRT IS GENERATED AND NOT LEFT TO THE PLATFORM

YouTube's own transcription writes our product name three different wrong ways on our own
channel, and a competitor's product name for another. We hold the exact words that were
spoken - either because we wrote them, or because we transcribed the source ourselves - so
the caption track is generated from what we know rather than guessed from the audio a second
time by somebody else's model.
"""

from pathlib import Path

# ASS colours are BGR, not RGB. Every one of these was arrived at by writing the hex backwards
# once and wondering why the highlight was orange.
HIGHLIGHT = {
    "cyan": "&H00FFE600",
    "green": "&H0060FF60",
    "orange": "&H0030A0FF",
    "yellow": "&H0000E6FF",
    "red": "&H004040FF",
    "blue": "&H00B86600",
}


def ass_time(t):
    centiseconds = int(round(t * 100))
    h, centiseconds = divmod(centiseconds, 360000)
    m, centiseconds = divmod(centiseconds, 6000)
    s, centiseconds = divmod(centiseconds, 100)
    return "%d:%02d:%02d.%02d" % (h, m, s, centiseconds)


def srt_time(t):
    milliseconds = int(round(t * 1000))
    h, milliseconds = divmod(milliseconds, 3600000)
    m, milliseconds = divmod(milliseconds, 60000)
    s, milliseconds = divmod(milliseconds, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, milliseconds)


def write_ass(cues, path, width, height, font="Segoe UI Black", size=80,
              baseline=None, glow=True):
    """Burned-in captions.

    Two layers when `glow`: a heavily blurred copy of the same text underneath, then the text
    itself. That is what keeps a white caption readable over a bright frame without a box
    behind it, which would cover the picture.
    """
    path = Path(path)
    baseline = baseline if baseline is not None else int(height * 0.555)
    margin_v = height - baseline
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, "
        "Encoding\n"
        "Style: Glow,%s,%d,&H00FFFFFF,&H000000FF,&H60FFFFFF,&H00000000,-1,0,0,0,100,100,1,0,"
        "1,10,0,2,40,40,%d,1\n"
        "Style: Cap,%s,%d,&H00FFFFFF,&H000000FF,&H00101010,&H00000000,-1,0,0,0,100,100,1,0,"
        "1,2,0,2,40,40,%d,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, "
        "Text\n"
        % (width, height, font, size, margin_v, font, size, margin_v))

    import re
    lines = [head]
    for cue in cues:
        if glow:
            bare = re.sub(r"\{\\c[^}]*\}", "", cue["text"])
            lines.append("Dialogue: 0,%s,%s,Glow,,0,0,0,,{\\blur14\\1a&HFF&}%s"
                         % (ass_time(cue["start"]), ass_time(cue["end"]), bare))
        lines.append("Dialogue: 1,%s,%s,Cap,,0,0,0,,%s"
                     % (ass_time(cue["start"]), ass_time(cue["end"]), cue["text"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_srt(cues, path, wrap_at=42):
    """A sidecar caption track. Two lines at most, broken on a space."""
    path = Path(path)
    out = []
    for i, cue in enumerate(cues, 1):
        out.append(str(i))
        out.append("%s --> %s" % (srt_time(cue["start"]), srt_time(cue["end"])))
        out.append(_wrap_two(cue.get("plain") or cue["text"], wrap_at))
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8", newline="\n")
    return path


def _wrap_two(text, wrap_at):
    if len(text) <= wrap_at:
        return text
    cut = text.rfind(" ", 0, wrap_at + 2)
    if cut < 15:
        return text
    return text[:cut] + "\n" + text[cut + 1:]


def ass_path_for_filter(path):
    """An .ass path as ffmpeg's subtitles filter wants it: forward slashes, escaped colon.

    A Windows drive letter is the single most common reason a caption track silently does not
    appear - the filter reads the colon as its own option separator and finds no file.
    """
    return Path(path).as_posix().replace(":", "\\:")
