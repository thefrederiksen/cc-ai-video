"""Contact sheets: many frames on one page, so they get READ rather than assumed.

A card is rendered pixels and silent. No transcription reaches it, no hear-back covers it, and
every automated check in this package will pass a video whose cards say the wrong thing
entirely. A twenty-minute walkthrough was once assembled at 53 percent production placeholder
cards - frames written for the person holding the camera, two of them carrying internal
warnings - and it passed every machine check there was. A human reading a contact sheet caught
it.

So this exists, and it is not a decoration.

EVERY TILE IS LABELLED with its file and its timestamp. An unlabelled sheet tells a reader
that something is wrong somewhere, which is barely better than not looking. A labelled one
tells them where.

A SHORT GRID IS PADDED WITH BLACK, never with a repeat of the last frame. The version this
replaces duplicated the final tile to fill the grid, and two identical tiles on a contact
sheet is indistinguishable from a real defect where a video repeats itself.

This is for reviewing MANY frames at once. It is not a substitute for opening one frame at
full size when a single thing needs checking.
"""

import tempfile
from pathlib import Path

from ..shell import probe, run

TILE_W = 360
COLUMNS = 4
LABEL_H = 22


def grab(video, at, out, width=TILE_W):
    run(["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % at, "-i", str(video),
         "-frames:v", "1", "-vf", "scale=%d:-2" % width, str(out)])
    return out


def sample_times(seconds, count):
    """`count` timestamps spread across a video, avoiding both ends.

    The first and last half second of a file are where fades live, and a fade sampled as a
    representative frame is a black tile that says nothing.
    """
    if count < 1:
        raise SystemExit("a contact sheet of %d frames shows nothing" % count)
    if count == 1:
        return [seconds * 0.5]
    edge = min(0.5, seconds * 0.05)
    span = seconds - 2 * edge
    return [edge + span * i / float(count - 1) for i in range(count)]


def build(videos, out_path, per_video=3, columns=COLUMNS):
    """One sheet covering every video. Returns the path written."""
    from PIL import Image, ImageDraw

    from ..draw import load_font

    videos = [Path(v) for v in videos]
    for video in videos:
        if not video.exists():
            raise SystemExit("no such file: %s" % video)

    tiles = []
    with tempfile.TemporaryDirectory(prefix="ccvideo-sheet-") as temp:
        temp = Path(temp)
        for video in videos:
            seconds = probe(video)["duration"]
            for i, at in enumerate(sample_times(seconds, per_video)):
                frame = grab(video, at, temp / ("%s-%02d.png" % (video.stem, i)))
                tiles.append((Image.open(frame).convert("RGB"), video.name, at))

        if not tiles:
            raise SystemExit("nothing to put on a contact sheet")

        cell_w = max(t[0].width for t in tiles)
        cell_h = max(t[0].height for t in tiles) + LABEL_H
        rows = (len(tiles) + columns - 1) // columns
        sheet = Image.new("RGB", (cell_w * columns, cell_h * rows), (0, 0, 0))
        draw = ImageDraw.Draw(sheet)
        font = load_font(13, bold=False)

        for index, (image, name, at) in enumerate(tiles):
            col, row = index % columns, index // columns
            x, y = col * cell_w, row * cell_h
            sheet.paste(image, (x, y))
            label = "%s  %02d:%02d" % (name, int(at // 60), int(at) % 60)
            draw.rectangle([x, y + image.height, x + cell_w, y + cell_h], fill=(16, 16, 16))
            draw.text((x + 6, y + image.height + 4), label, font=font, fill=(200, 210, 224))

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out_path)

    return out_path, len(tiles)
