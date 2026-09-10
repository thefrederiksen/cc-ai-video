"""Drawing: title cards, and the text helpers every card and plate needs.

Card metrics scale with the frame width, so a phone card is a card designed for a phone and
not a desktop card squeezed sideways. At 1920 wide the scale factor is 1.0 and the output is
pixel-identical to the assembler this replaces.

The glow behind a card falls back to the flat background on ALL FOUR edges. Every segment is
fitted inside a letterbox padded with that flat colour, so a card whose edge is the lighter
tone draws a visible seam against the pad - obvious at phone size, faint but present on
desktop.
"""

import os
import re

from .brand import FOOT_COLOUR, SUB_COLOUR, TITLE_COLOUR

# Hiragana and katakana, CJK ideographs including extension A, compatibility ideographs and
# halfwidth katakana. Built from code points so this file holds no non-ASCII byte.
CJK_RANGES = ((0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
              (0xF900, 0xFAFF), (0xFF66, 0xFF9F))
CJK = re.compile("[%s]" % "".join("%s-%s" % (chr(a), chr(b)) for a, b in CJK_RANGES))

# Segoe UI carries no CJK glyphs, so a Japanese title card silently drew a row of empty boxes.
# Card text is matched to a font that can actually draw it, and a missing CJK font is an error
# rather than a page of tofu nobody notices until it is published.
LATIN_FONTS = {True: ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"],
               False: ["segoeui.ttf", "arial.ttf"]}
CJK_FONTS = {True: ["YuGothB.ttc", "meiryob.ttc", "msgothic.ttc"],
             False: ["YuGothM.ttc", "YuGothR.ttc", "meiryo.ttc", "msgothic.ttc"]}


def font_dir():
    return os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def load_font(size, bold=False, cjk=False):
    from PIL import ImageFont
    names = (CJK_FONTS if cjk else LATIN_FONTS)[bool(bold)]
    for name in names:
        path = os.path.join(font_dir(), name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    if cjk:
        raise SystemExit(
            "card text contains Japanese but none of %s is installed - rendering it with a "
            "Latin font draws empty boxes, so the build stops here" % ", ".join(names))
    return ImageFont.load_default()


def wrap(draw, text, font, max_w):
    """Break text into lines that fit `max_w`. A single word wider than the box gets its own
    line rather than being dropped or clipped."""
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def draw_tracked(draw, xy, text, font, fill, tracking):
    """Letter-spaced text, drawn one glyph at a time. PIL has no tracking of its own."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def gradient(brand, w, h):
    """The brand ground: the base colour with a soft round glow, built small and resized.

    A per-pixel loop over two million pixels is not worth it, and a 64x64 source resized with
    bicubic is indistinguishable from one.
    """
    from PIL import Image
    bg, bg2 = brand["bg"], brand["bg2"]
    size = 64
    grad = Image.new("RGB", (size, size))
    px = grad.load()
    for j in range(size):
        ny = 2.0 * (j / float(size - 1)) - 1.0
        for i in range(size):
            nx = 2.0 * (i / float(size - 1)) - 1.0
            r = min(1.0, (nx * nx + ny * ny) ** 0.5)     # radial, so the glow is round
            t = 1.0 - r
            t = t * t * (3.0 - 2.0 * t)                  # smoothstep - no hard falloff line
            px[i, j] = tuple(int(bg[k] + (bg2[k] - bg[k]) * t) for k in range(3))
    return grad.resize((w, h), Image.BICUBIC)


def render_card(brand, w, h, kicker, title, sub, footer, path):
    """One title card, saved to `path`."""
    from PIL import ImageDraw

    s = w / 1920.0
    accent = brand["accent"]
    cjk = bool(CJK.search((kicker or "") + (title or "") + (sub or "")))
    cjk_foot = bool(CJK.search((footer or "") + brand["product"]))

    img = gradient(brand, w, h)
    d = ImageDraw.Draw(img)

    left, right = int(180 * s), w - int(180 * s)
    max_w = right - left
    f_kick = load_font(max(14, int(30 * s)), True, cjk)
    f_title = load_font(max(30, int(88 * s)), True, cjk)
    f_sub = load_font(max(18, int(40 * s)), False, cjk)
    f_foot = load_font(max(12, int(24 * s)), False, cjk_foot)

    title_lines = wrap(d, title, f_title, max_w)
    while len(title_lines) > 3 and f_title.size > int(54 * s):
        f_title = load_font(f_title.size - max(2, int(8 * s)), True, cjk)
        title_lines = wrap(d, title, f_title, max_w)
    sub_lines = wrap(d, sub, f_sub, max_w) if sub else []

    title_h = len(title_lines) * int(f_title.size * 1.22)
    sub_h = len(sub_lines) * int(f_sub.size * 1.35)
    kick_h = int(74 * s) if kicker else 0
    gap = int(34 * s)
    y = (h - (kick_h + title_h + (gap if sub_lines else 0) + sub_h)) // 2

    d.rectangle([left - int(40 * s), y + int(6 * s),
                 left - int(30 * s), y + kick_h + title_h + sub_h + int(30 * s)], fill=accent)

    if kicker:
        # Letter-spaced, but NOT upper-cased: a brand whose name is lower case stays lower case.
        draw_tracked(d, (left, y), kicker, f_kick, accent, 3.2 * s)
        y += kick_h
    for line in title_lines:
        d.text((left, y), line, font=f_title, fill=TITLE_COLOUR)
        y += int(f_title.size * 1.22)
    if sub_lines:
        y += gap
        for line in sub_lines:
            d.text((left, y), line, font=f_sub, fill=SUB_COLOUR)
            y += int(f_sub.size * 1.35)

    rule_y, foot_y = h - int(118 * s), h - int(96 * s)
    d.line([(left, rule_y), (right, rule_y)], fill=brand["rule"], width=max(1, int(2 * s)))
    d.text((left, foot_y), brand["product"], font=f_foot, fill=FOOT_COLOUR)
    d.text((right - d.textlength(footer, font=f_foot), foot_y), footer,
           font=f_foot, fill=FOOT_COLOUR)
    img.save(path)
    return path
