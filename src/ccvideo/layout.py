"""The hook layout: a vertical short with its headline burned across the top.

WHY THE HOOK IS NOT A CAPTION

A platform takes a Short's FIRST FRAME as its thumbnail. So the hook is not decoration on top
of the video - it IS the thumbnail, and it is the whole of what somebody sees before deciding
whether to stop scrolling. It has to be there from frame one, it has to be readable at browse
size, and it has to be the same text for the whole clip.

That is why it is a plate rather than a subtitle cue. Cues come and go; this does not.

THE LAYOUT, top to bottom

    brand bar        the product name, and a rule under it
    hook             one or two lines, as large as will fit the space
    footage          inset, with a rule around it
    caption strip    the running captions, clear of the picture
    footer           where to go next

The footage is INSET rather than full-frame, which is the point: a full-frame 16:9 recording
in a 9:16 phone frame leaves the picture 607 pixels tall with the hook sitting on top of it.
Inset, the hook has its own space and the picture keeps all of its own.

Every metric scales with the frame width, so this is not a 1080-wide design stretched.
"""

from .draw import load_font

# Proportions, measured against a 1080x1920 frame and scaled from there.
BRAND_BAND = 140 / 1920.0
FOOTER_BAND = 200 / 1920.0
CAPTION_BAND = 200 / 1920.0
BOX_WIDTH = 1040 / 1080.0
BOX_MAX_HEIGHT = 1150 / 1920.0
# Below this fraction of the frame width the picture is a sliver with a hook above it, which is
# a worse video than no hook at all. A very tall source hits it: capped by height, the width
# collapses. Refused rather than rendered, because nothing downstream would notice.
BOX_MIN_WIDTH = 0.45


def hook_box(width, height, source_w, source_h):
    """Where the footage sits, and how big. Returns (box_w, box_h, box_x, box_y)."""
    box_w = int(width * BOX_WIDTH)
    box_h = int(round(box_w * source_h / float(source_w)))
    limit = int(height * BOX_MAX_HEIGHT)
    if box_h > limit:
        box_h = limit
        box_w = int(round(limit * source_w / float(source_h)))
    box_w -= box_w % 2
    box_h -= box_h % 2
    if box_w < width * BOX_MIN_WIDTH:
        raise SystemExit(
            "a %dx%d source fits the hook layout only as a %dpx-wide sliver in a %dpx frame. "
            "That is not watchable. Crop it to a wider region, or render without a hook."
            % (source_w, source_h, box_w, width))
    box_x = (width - box_w) // 2
    box_y = height - int(height * FOOTER_BAND) - int(height * CAPTION_BAND) - box_h
    if box_y < int(height * BRAND_BAND):
        raise SystemExit(
            "the footage does not fit above the caption strip in a %dx%d frame. Crop it to a "
            "wider, shorter region, or render without a hook." % (width, height))
    return box_w, box_h, box_x, box_y


def fit_hook(draw, lines, zone_w, zone_h, high, low):
    """The largest font at which the hook fits its space. Refuses to shrink past `low`.

    A hook that only fits at eight points is not a hook anybody reads at browse size, so this
    stops and says to shorten the text rather than rendering something illegible.
    """
    for size in range(high, low, -2):
        font = load_font(size, bold=True)
        ascent, descent = font.getmetrics()
        line_h = int((ascent + descent) * 1.18)
        widths = [font.getbbox(line)[2] - font.getbbox(line)[0] for line in lines]
        if max(widths) <= zone_w and line_h * len(lines) <= zone_h:
            return font, line_h
    raise SystemExit(
        "the hook does not fit even at %dpx - shorten it. At browse size a hook this long is "
        "not read: %r" % (low, " | ".join(lines)))


def build_hook_plate(brand, width, height, hook_lines, footer, box, path):
    """Draw the static plate the footage is laid onto. Returns the path."""
    from PIL import Image, ImageDraw

    from .brand import FOOT_COLOUR, SUB_COLOUR, TITLE_COLOUR
    from .draw import gradient

    scale = width / 1080.0
    box_w, box_h, box_x, box_y = box
    accent = brand["accent"]

    image = gradient(brand, width, height)
    draw = ImageDraw.Draw(image)

    brand_h = int(height * BRAND_BAND)
    product = brand.get("product") or ""
    if product:
        font = load_font(max(16, int(50 * scale)), bold=True)
        w = draw.textlength(product, font=font)
        draw.text(((width - w) / 2, int(44 * scale)), product, font=font, fill=accent)
    draw.line([(int(70 * scale), brand_h), (width - int(70 * scale), brand_h)],
              fill=brand["rule"], width=max(2, int(3 * scale)))

    pad = int(30 * scale)
    top, bottom = brand_h + pad, box_y - pad
    font, line_h = fit_hook(draw, hook_lines, width - int(140 * scale), bottom - top,
                            max(24, int(96 * scale)), max(16, int(34 * scale)))
    y = top + ((bottom - top) - line_h * len(hook_lines)) / 2
    for line in hook_lines:
        w = draw.textlength(line, font=font)
        draw.text(((width - w) / 2, y), line, font=font, fill=TITLE_COLOUR)
        y += line_h

    radius = max(6, int(14 * scale))
    inset = max(4, int(7 * scale))
    draw.rounded_rectangle(
        [box_x - inset, box_y - inset, box_x + box_w + inset, box_y + box_h + inset],
        radius=radius, outline=accent, width=max(2, int(4 * scale)))

    if footer:
        font = load_font(max(14, int(40 * scale)), bold=False)
        w = draw.textlength(footer, font=font)
        draw.text(((width - w) / 2, height - int(height * FOOTER_BAND) + int(40 * scale)),
                  footer, font=font, fill=SUB_COLOUR if product else FOOT_COLOUR)

    image.save(path)
    return path


def parse_hook(text):
    """A hook is one or two lines separated by '|'."""
    lines = [part.strip() for part in text.split("|") if part.strip()]
    if not lines:
        raise SystemExit("--hook is empty")
    if len(lines) > 2:
        raise SystemExit(
            "a hook is at most two lines and this has %d. More than two cannot be read at "
            "browse size, which is the only size that matters for a thumbnail." % len(lines))
    for line in lines:
        if any(ord(ch) > 126 for ch in line):
            raise SystemExit("the hook must be ASCII: %r" % line)
    return lines


def caption_baseline(height):
    """Where captions sit in a hook layout: in their own strip, never over the picture."""
    return height - int(height * FOOTER_BAND) - int(height * CAPTION_BAND / 2)
