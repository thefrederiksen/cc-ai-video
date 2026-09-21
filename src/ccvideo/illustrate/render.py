"""Draw a scene list over a narration, frame by frame, and encode it with the voice.

What makes this kind of video watchable, learned by reading a well-made one frame by frame:

  MOTION ALL THE TIME. Something moves every half second - text typing on, a line drawing,
      a marker sliding, a loader turning, a dashed flow crawling. A still frame for three
      seconds reads as a slide, and a slide is where people stop watching.
  CONTRAST. A near-black field, one bright accent and one warm marker colour. Navy on navy
      is polite and nobody remembers it.
  HEAVY TYPE. Key words are big and black-weight. Labels are small and plain.
  SETUP, THEN PAYOFF. A dashed empty slot arrives first; the thing fills it ON its word.
  GLOW AND DEPTH. Bright things bloom a little, and every scene pushes in slowly.
  VARIETY. A pale scene now and then resets the eye.

Every element still arrives ON its word - that is the whole effect. The anchors are resolved
in scenes.py; this module only draws.
"""

import math
import os
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .. import budget

W, H, FPS = 1920, 1080, 30
ENTER = 0.35    # seconds an element takes to arrive
LEAVE = 0.22    # seconds a scene takes to clear before the next one
PUSH = 0.035    # how far every scene pushes in across its length

THEMES = {
    "dark": {
        "bg": (8, 10, 14), "panel": (22, 26, 33), "rule": (52, 60, 70), "text": (242, 245, 247),
        "muted": (118, 128, 140), "accent": (32, 212, 190), "accent_fill": (12, 150, 136),
        "warm": (255, 140, 26), "card_text": (242, 245, 247), "dot": (26, 30, 38),
    },
    "light": {
        "bg": (176, 194, 194), "panel": (14, 16, 20), "rule": (120, 138, 140), "text": (14, 16, 20),
        "muted": (70, 86, 90), "accent": (0, 118, 108), "accent_fill": (0, 118, 108),
        "warm": (255, 128, 10), "card_text": (242, 245, 247), "dot": (160, 178, 178),
    },
}

# Open fonts shipped inside the package (SIL Open Font License, licenses beside them), so a
# video renders to the same pixels on Windows, macOS and Linux. The first videos used Windows'
# own Segoe UI, which cannot be copied to another machine and does not exist off Windows.
FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
FONT_FILES = {"head": "Inter-Black.ttf", "label": "Inter-SemiBold.ttf", "mono": "CascadiaMono-Regular.ttf"}


def _mix(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def ease(k):
    k = max(0.0, min(1.0, k))
    return 1 - (1 - k) ** 3


def back(k):
    """Ease out with a small overshoot: things land with a bit of weight."""
    k = max(0.0, min(1.0, k))
    c = 1.70158
    return 1 + (c + 1) * (k - 1) ** 3 + c * (k - 1) ** 2


def progress(t, t_in, length=ENTER):
    return ease((t - t_in) / length) if t >= t_in else 0.0


_SCRIM = []


def _scrim():
    """A picture is a backdrop, not the message: darken it most where the words sit - the
    left, the top and the bottom - so heavy type over a bright photograph still reads."""
    if not _SCRIM:
        xs = np.clip(np.linspace(0, 1, W) / 0.7, 0, 1)
        ys = np.clip(np.linspace(0, 1, H) / 0.35, 0, 1)
        left = 0.38 + 0.52 * xs ** 1.4
        top = 0.55 + 0.45 * ys
        bottom = 0.5 + 0.5 * np.clip((1 - np.linspace(0, 1, H)) / 0.35, 0, 1)
        rows = np.minimum(top, bottom)
        _SCRIM.append((np.minimum(left[None, :], rows[:, None]))[:, :, None].astype(np.float32))
    return _SCRIM[0]


class Canvas:
    def __init__(self, brand=None):
        self.fonts = {}
        self.images = {}
        self.bases = {name: self._background(p) for name, p in THEMES.items()}

    def font(self, size, kind="head"):
        key = (size, kind)
        if key not in self.fonts:
            path = FONT_DIR / FONT_FILES[kind]
            if not path.exists():
                raise SystemExit("font %s is missing from the package (%s) - reinstall ccvideo"
                                 % (FONT_FILES[kind], path))
            self.fonts[key] = ImageFont.truetype(path, size)
        return self.fonts[key]

    def _background(self, p):
        img = Image.new("RGB", (W, H), p["bg"])
        d = ImageDraw.Draw(img)
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                d.point((x, y), fill=p["dot"])
                d.point((x + 1, y), fill=p["dot"])
        for (cx, cy, sx, sy) in ((36, 36, 1, 1), (W - 36, 36, -1, 1),
                                 (36, H - 36, 1, -1), (W - 36, H - 36, -1, -1)):
            d.line([(cx, cy), (cx + 26 * sx, cy)], fill=p["rule"], width=2)
            d.line([(cx, cy), (cx, cy + 26 * sy)], fill=p["rule"], width=2)
        return img

    # ------------------------------------------------------------------ helpers

    def c(self, p, name, a=1.0):
        return _mix(p["bg"], p.get(name, name if isinstance(name, tuple) else p["text"]), a)

    def icon(self, d, kind, cx, cy, s, colour, bg):
        r = s / 2
        if kind == "check":
            d.line([(cx - r * .6, cy), (cx - r * .15, cy + r * .45), (cx + r * .65, cy - r * .5)], fill=colour, width=int(s / 7))
        elif kind == "cross":
            d.line([(cx - r * .5, cy - r * .5), (cx + r * .5, cy + r * .5)], fill=colour, width=int(s / 7))
            d.line([(cx - r * .5, cy + r * .5), (cx + r * .5, cy - r * .5)], fill=colour, width=int(s / 7))
        elif kind == "dot":
            d.ellipse((cx - r * .5, cy - r * .5, cx + r * .5, cy + r * .5), fill=colour)
        elif kind == "tri":
            d.polygon([(cx, cy - r * .55), (cx + r * .55, cy + r * .45), (cx - r * .55, cy + r * .45)], fill=colour)
        elif kind == "square":
            d.rectangle((cx - r * .45, cy - r * .45, cx + r * .45, cy + r * .45), fill=colour)
        elif kind == "mail":
            d.rectangle((cx - r * .7, cy - r * .45, cx + r * .7, cy + r * .45), outline=colour, width=int(s / 12))
            d.line([(cx - r * .7, cy - r * .45), (cx, cy + r * .1), (cx + r * .7, cy - r * .45)], fill=colour, width=int(s / 12))
        elif kind == "code":
            w = int(s / 10)
            d.line([(cx - r * .25, cy - r * .5), (cx - r * .7, cy), (cx - r * .25, cy + r * .5)], fill=colour, width=w)
            d.line([(cx + r * .25, cy - r * .5), (cx + r * .7, cy), (cx + r * .25, cy + r * .5)], fill=colour, width=w)
        elif kind == "chat":
            d.rounded_rectangle((cx - r * .7, cy - r * .5, cx + r * .7, cy + r * .3), radius=int(r * .2), outline=colour, width=int(s / 12))
            d.polygon([(cx - r * .35, cy + r * .28), (cx - r * .5, cy + r * .65), (cx - r * .05, cy + r * .28)], fill=colour)
        elif kind == "question":
            f = self.font(int(s * .8), "head")
            tw = d.textlength("?", font=f)
            d.text((cx - tw / 2, cy - s * .52), "?", font=f, fill=colour)

    def cover(self, path, w, h, focus_y=0.3):
        """The photograph cropped to fill w x h, keeping the point `focus_y` down the picture
        in frame - faces sit in the upper third of a portrait."""
        key = (path, int(w), int(h), focus_y)
        if key not in self.images:
            src = Image.open(path).convert("RGB")
            scale = max(w / src.width, h / src.height)
            src = src.resize((max(int(w), int(src.width * scale + 0.5)),
                              max(int(h), int(src.height * scale + 0.5))), Image.LANCZOS)
            ox = (src.width - w) / 2
            oy = min(max(0.0, src.height * focus_y - h / 2), src.height - h)
            self.images[key] = src.crop((int(ox), int(oy), int(ox) + int(w), int(oy) + int(h)))
        return self.images[key]

    def paste(self, img, picture, x, y, alpha, radius=0):
        """Put a picture on the frame at (x, y), faded by alpha, optionally with round corners."""
        mask = Image.new("L", picture.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, picture.width - 1, picture.height - 1),
                                               radius=radius, fill=int(255 * max(0.0, min(1.0, alpha))))
        img.paste(picture, (int(x), int(y)), mask)

    @staticmethod
    def credit(path):
        """'Photo: author, license' from the credit file ccvideo photo fetch wrote, or None."""
        meta = Path(str(path) + ".json")
        if not meta.exists():
            return None
        import json as _json
        info = _json.loads(meta.read_text(encoding="utf-8"))
        author = info.get("author", "unknown")
        if len(author) > 44:
            author = author[:41] + "..."
        text = "Photo: %s, %s" % (author, info.get("license", ""))
        return text.encode("ascii", "replace").decode()

    def dashed_line(self, d, a, b, colour, width=3, dash=14, gap=10, offset=0.0):
        (ax, ay), (bx, by) = a, b
        length = math.hypot(bx - ax, by - ay)
        if length < 1:
            return
        step = dash + gap
        pos = -(offset % step)
        while pos < length:
            s, e = max(0.0, pos), min(length, pos + dash)
            if e > s:
                d.line([(ax + (bx - ax) * s / length, ay + (by - ay) * s / length),
                        (ax + (bx - ax) * e / length, ay + (by - ay) * e / length)], fill=colour, width=width)
            pos += step

    def dashed_rect(self, d, box, colour, t=0.0):
        x0, y0, x1, y1 = box
        crawl = t * 30
        for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            self.dashed_line(d, a, b, colour, width=2, dash=12, gap=8, offset=crawl)

    # ------------------------------------------------------------------ elements

    def text(self, img, d, p, el, t, alpha):
        size = el.get("size", 90)
        f = self.font(size, el.get("font", "head"))
        parts = el.get("parts") or [{"text": el["text"], "colour": el.get("colour", "text"), "t_in": el["t_in"]}]
        x, y = el["x"], el["y"]
        max_w = el.get("wrap", W - x - 140)
        line_h = int(size * 1.12)
        cx, cy = x, y
        per_char = el.get("per_char", 0.028)
        for part in parts:
            if t < part["t_in"]:
                break
            k = ease((t - part["t_in"]) / 0.3)
            shown = int((t - part["t_in"]) / per_char) if el.get("mode", "type") == "type" else 10 ** 6
            colour = self.c(p, part.get("colour", "text"), alpha)
            used = 0
            lines, seg_x = [], cx
            for i, word in enumerate(part["text"].split(" ")):
                token = word + (" " if i < len(part["text"].split(" ")) - 1 else "")
                if used >= shown:
                    break
                visible = token[:max(0, shown - used)]
                used += len(token)
                if cx + d.textlength(token, font=f) > x + max_w and cx > x:
                    lines.append((seg_x, cx, cy))
                    cx, cy = x, cy + line_h
                    seg_x = x
                d.text((cx, cy + (1 - k) * 22), visible, font=f, fill=colour)
                cx += d.textlength(visible, font=f)
            if part.get("mark") == "underline" and t >= part["t_in"] + 0.25:
                m = ease((t - part["t_in"] - 0.25) / 0.4)
                for x0, x1, ly in lines + [(seg_x, cx, cy)]:
                    if x1 - x0 < 2:
                        continue
                    uy = ly + size * 1.05
                    d.rectangle((x0, uy, x0 + (x1 - x0) * m, uy + max(5, size // 14)),
                                fill=self.c(p, "warm", alpha))
        if el.get("cursor") and t >= el["t_in"] and int(t * 2.4) % 2 == 0:
            d.rectangle((cx + 8, cy + size * 0.18, cx + 8 + size * 0.09, cy + size * 1.02),
                        fill=self.c(p, "accent", alpha))

    def box(self, img, d, p, el, t, alpha):
        raw = (t - el["t_in"]) / ENTER
        if raw <= 0:
            return
        k = back(raw)
        a = alpha * min(1.0, raw * 1.6)
        x, y, w, h = el["x"], el["y"], el["w"], el["h"]
        grow = 0.82 + 0.18 * k
        cx, cy = x + w / 2, y + h / 2
        x0, y0, x1, y1 = cx - w * grow / 2, cy - h * grow / 2, cx + w * grow / 2, cy + h * grow / 2
        style = el.get("style", "solid")
        if "lit_t" in el and t >= el["lit_t"]:
            style = "accent"
        radius = el.get("radius", 10)
        text_colour = "card_text"
        if style == "dashed":
            self.dashed_rect(d, (x0, y0, x1, y1), self.c(p, "muted", a), t)
            text_colour = "muted"
        elif style == "accent":
            d.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=self.c(p, "accent_fill", a),
                                outline=self.c(p, "accent", a), width=3)
            text_colour = (255, 255, 255)
        elif style == "warm":
            d.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=self.c(p, "warm", a))
            text_colour = (14, 16, 20)
        else:
            d.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=self.c(p, "panel", a),
                                outline=self.c(p, "rule", a), width=2)
        if el.get("marker"):
            d.rectangle((x1 - 16, y0 - 8, x1 - 2, y0 + 6), fill=self.c(p, "warm", a))
        tx = x0 + 28
        icon = el.get("icon")
        if icon:
            s = min(h * 0.55, 56)
            ib = (tx, cy - s / 2, tx + s, cy + s / 2)
            chip = style in ("accent", "warm")
            d.rounded_rectangle(ib, radius=6, fill=(255, 255, 255) if not chip else self.c(p, "bg", a * .25))
            self.icon(d, icon, tx + s / 2, cy, s * 0.9, (14, 16, 20) if not chip else (255, 255, 255), p["bg"])
            tx += s + 20
        label = el.get("text")
        if label:
            size = el.get("size", 40)
            f = self.font(size, el.get("font", "label"))
            tw = d.textlength(label, font=f)
            if el.get("align", "left" if icon else "center") == "center":
                tx = cx - tw / 2
            colour = text_colour if isinstance(text_colour, tuple) else self.c(p, text_colour, a)
            if isinstance(text_colour, tuple):
                colour = _mix(p["bg"], text_colour, a)
            d.text((tx, cy - size * 0.66), label, font=f, fill=colour)
        if el.get("spinner") and t >= el["t_in"]:
            r = min(w, h) * 0.13
            sx, sy = x1 - r * 2.2, cy
            start = (t * 360 * 1.1) % 360
            d.arc((sx - r, sy - r, sx + r, sy + r), start, start + 270, fill=(255, 255, 255), width=6)

    def flow(self, img, d, p, el, t, alpha):
        """A connector that draws on, then keeps crawling - dashes moving and a dot riding it."""
        k = progress(t, el["t_in"], el.get("length", 0.45))
        if k <= 0:
            return
        (ax, ay), (bx, by) = el["from"], el["to"]
        ex, ey = ax + (bx - ax) * k, ay + (by - ay) * k
        colour = self.c(p, "accent", alpha)
        self.dashed_line(d, (ax, ay), (ex, ey), colour, width=4, dash=16, gap=10, offset=-(t - el["t_in"]) * 60)
        if k >= 1:
            ang = math.atan2(by - ay, bx - ax)
            for side in (0.55, -0.55):
                d.line([(bx, by), (bx - 24 * math.cos(ang + side), by - 24 * math.sin(ang + side))], fill=colour, width=5)
            m = ((t - el["t_in"]) * 0.9) % 1.0
            dx, dy = ax + (bx - ax) * m, ay + (by - ay) * m
            d.ellipse((dx - 8, dy - 8, dx + 8, dy + 8), fill=self.c(p, "warm", alpha))

    def bars(self, img, d, p, el, t, alpha):
        size = el.get("size", 44)
        f = self.font(size, "label")
        fm = self.font(size, "mono")
        x, y = el["x"], el["y"]
        full = el.get("w", 560)
        for i, item in enumerate(el["items"]):
            k = progress(t, item["t_in"], 0.8)
            if k <= 0:
                continue
            a = alpha * min(1.0, k * 2)
            yy = y + i * 88
            d.text((x, yy), item["label"], font=f, fill=self.c(p, "text", a))
            bx = x + el.get("label_w", 200)
            d.rounded_rectangle((bx, yy + 10, bx + full, yy + 52), radius=8, fill=self.c(p, "panel", a))
            colour = "accent" if i == 0 else "rule"
            d.rounded_rectangle((bx, yy + 10, bx + max(16, full * item["value"] * k), yy + 52), radius=8,
                                fill=self.c(p, colour, a))
            d.text((bx + full + 24, yy), "%d%%" % round(item["value"] * 100 * k), font=fm, fill=self.c(p, "muted", a))

    def counter(self, img, d, p, el, t, alpha):
        if t < el["t_in"]:
            return
        k = ease((t - el["t_in"]) / el.get("length", 1.6))
        value = el["from"] + (el["to"] - el["from"]) * k
        f = self.font(el.get("size", 200), el.get("font", "head"))
        text = el.get("format", "%d") % value
        d.text((el["x"], el["y"]), text, font=f, fill=self.c(p, el.get("colour", "accent"), alpha))
        if el.get("suffix"):
            d.text((el["x"] + d.textlength(text, font=f) + 20, el["y"] + el.get("size", 200) * 0.52),
                   el["suffix"], font=self.font(int(el.get("size", 200) * 0.3), "head"), fill=self.c(p, "text", alpha))

    def ruler(self, img, d, p, el, t, alpha):
        k = progress(t, el["t_in"], 0.7)
        if k <= 0:
            return
        x0, x1, y = el["x0"], el["x1"], el["y"]
        a = alpha * k
        rule = self.c(p, "rule", a)
        d.line([(x0, y), (x0 + (x1 - x0) * k, y)], fill=rule, width=3)
        lo, hi = el["from_year"], el["to_year"]
        f = self.font(30, "mono")
        for year in range(lo, hi + 1):
            px = x0 + (x1 - x0) * (year - lo) / float(hi - lo)
            if px > x0 + (x1 - x0) * k:
                break
            big = year % el.get("step", 10) == 0
            d.line([(px, y - (14 if big else 6)), (px, y + (14 if big else 6))], fill=rule, width=3 if big else 1)
            if big:
                d.text((px - 36, y + 26), str(year), font=f, fill=self.c(p, "muted", a))
        if "move_t" in el:
            m = ease((t - el["move_t"]) / el.get("move_length", 2.0)) if t >= el["move_t"] else 0.0
            a0 = (el.get("mark_from", lo) - lo) / float(hi - lo)
            a1 = (el.get("mark_to", hi) - lo) / float(hi - lo)
            px0 = x0 + (x1 - x0) * a0
            px = px0 + (x1 - x0) * (a1 - a0) * m
            d.rectangle((px0, y - 5, px, y + 5), fill=self.c(p, "accent", a))
            d.ellipse((px0 - 11, y - 11, px0 + 11, y + 11), fill=self.c(p, "accent", a))
            d.ellipse((px - 13, y - 13, px + 13, y + 13), fill=self.c(p, "warm", a))

    def stack(self, img, d, p, el, t, alpha):
        x, y = el["x"], el["y"]
        w, h, gap = el.get("w", 420), el.get("h", 70), 14
        fm = self.font(30, "mono")
        for i, item in enumerate(el["items"]):
            raw = (t - item["t_in"]) / 0.45
            if raw <= 0:
                continue
            k = back(raw)
            a = alpha * min(1.0, raw * 2)
            top = y - (i + 1) * (h + gap) - (1 - k) * 140
            lit = "lit_t" in el and t >= el["lit_t"] + i * 0.14
            d.rounded_rectangle((x, top, x + w, top + h), radius=8,
                                fill=self.c(p, "accent_fill" if lit else "panel", a),
                                outline=self.c(p, "accent" if lit else "rule", a), width=2)
            d.text((x + 22, top + h / 2 - 20), "%02d" % (i + 1), font=fm,
                   fill=self.c(p, "text" if lit else "muted", a))
            if item.get("label"):
                fl = self.font(el.get("size", 36), "label")
                d.text((x + 90, top + h / 2 - el.get("size", 36) * 0.66), item["label"], font=fl,
                       fill=self.c(p, "card_text", a))
            else:
                d.rectangle((x + 90, top + h / 2 - 5, x + 90 + (w - 130) * (0.35 + 0.1 * (i % 4)), top + h / 2 + 5),
                            fill=self.c(p, "rule" if not lit else "text", a))

    def strike(self, img, d, p, el, t, alpha):
        k = progress(t, el["t_in"], 0.35)
        if k <= 0:
            return
        x, y, w = el["x"], el["y"], el["w"]
        d.line([(x, y + 4), (x + w * k, y - 8 * k)], fill=self.c(p, "warm", alpha), width=8)

    def chat(self, img, d, p, el, t, alpha):
        """A chat window that types its message out while the speaker talks."""
        k = progress(t, el["t_in"], 0.4)
        if k <= 0:
            return
        a = alpha * k
        x, y, w, h = el["x"], el["y"] + (1 - k) * 40, el["w"], el["h"]
        d.rounded_rectangle((x, y, x + w, y + h), radius=14, fill=self.c(p, "panel", a), outline=self.c(p, "rule", a), width=2)
        d.rectangle((x + 2, y + 2, x + w - 2, y + 54), fill=self.c(p, "bg", a * 0.6))
        for i, col in enumerate(("warm", "accent", "muted")):
            d.ellipse((x + 24 + i * 30, y + 18, x + 42 + i * 30, y + 36), fill=self.c(p, col, a))
        f = self.font(el.get("size", 40), "label")
        msg = el["message"]
        shown = msg[:max(0, int((t - el["t_in"] - 0.3) / el.get("per_char", 0.06)))]
        d.text((x + 34, y + 90), shown, font=f, fill=self.c(p, "text", a))
        if int(t * 2.4) % 2 == 0:
            cx = x + 34 + d.textlength(shown, font=f) + 6
            d.rectangle((cx, y + 94, cx + 5, y + 94 + el.get("size", 40)), fill=self.c(p, "accent", a))
        d.rounded_rectangle((x + w - 150, y + h - 74, x + w - 28, y + h - 24), radius=8, fill=self.c(p, "accent_fill", a))
        d.text((x + w - 128, y + h - 70), "Send", font=self.font(30, "label"), fill=_mix(p["bg"], (255, 255, 255), a))

    def person(self, img, d, p, el, t, alpha):
        """A real person, named - never a made-up face. Initials tile, name, one line of who."""
        raw = (t - el["t_in"]) / 0.45
        if raw <= 0:
            return
        k = back(raw)
        a = alpha * min(1.0, raw * 1.8)
        x, y = el["x"], el["y"] + (1 - k) * 60
        w, h = el.get("w", 760), el.get("h", 220)
        d.rounded_rectangle((x, y, x + w, y + h), radius=12, fill=self.c(p, "panel", a),
                            outline=self.c(p, "rule", a), width=2)
        tile = h - 40
        if el.get("photo"):
            face = self.cover(el["photo"], tile, tile, el.get("focus_y", 0.28))
            self.paste(img, face, x + 20, y + 20, a, radius=10)
        else:
            d.rounded_rectangle((x + 20, y + 20, x + 20 + tile, y + 20 + tile), radius=10,
                                fill=self.c(p, el.get("tile", "accent_fill"), a))
            initials = "".join(w_[0] for w_ in el["name"].replace("-", " ").split()[:2]).upper()
            fi = self.font(int(tile * 0.42), "head")
            tw = d.textlength(initials, font=fi)
            d.text((x + 20 + (tile - tw) / 2, y + 20 + tile * 0.2), initials, font=fi,
                   fill=_mix(p["bg"], (255, 255, 255), a))
        tx = x + tile + 50
        d.text((tx, y + 34), el["name"], font=self.font(el.get("size", 58), "head"), fill=self.c(p, "card_text", a))
        if el.get("detail"):
            d.text((tx, y + 34 + el.get("size", 58) * 1.25), el["detail"], font=self.font(32, "mono"),
                   fill=self.c(p, "muted", a))
        d.rectangle((x + w - 18, y - 8, x + w - 2, y + 8), fill=self.c(p, "warm", a))

    def photo(self, img, d, p, el, t, alpha):
        """A real photograph in a frame, pushing in slowly, credited underneath. For portraits,
        documents and small archive pictures a full-screen backdrop would blow up or crop."""
        raw = (t - el["t_in"]) / 0.5
        if raw <= 0:
            return
        k = back(raw)
        a = alpha * min(1.0, raw * 1.6)
        x, y, w, h = el["x"], el["y"] + (1 - k) * 50, el["w"], el["h"]
        pad = 10
        d.rounded_rectangle((x - pad, y - pad, x + w + pad, y + h + pad), radius=14,
                            fill=self.c(p, "panel", a), outline=self.c(p, "rule", a), width=2)
        zoom = 1.0 + el.get("zoom", 0.06) * min(1.0, max(0.0, t - el["t_in"]) / 12.0)
        big = self.cover(el["path"], int(w * zoom), int(h * zoom), el.get("focus_y", 0.35))
        ox, oy = (big.width - w) // 2, int((big.height - h) * el.get("focus_y", 0.35))
        self.paste(img, big.crop((ox, oy, ox + int(w), oy + int(h))), x, y, a, radius=8)
        d = ImageDraw.Draw(img)
        label = el.get("caption")
        if label:
            f = self.font(30, "mono")
            tw = d.textlength(label, font=f)
            d.rectangle((x + 16, y + h - 58, x + 16 + tw + 28, y + h - 14), fill=_mix(p["bg"], (10, 12, 16), a))
            d.text((x + 30, y + h - 54), label, font=f, fill=_mix(p["bg"], (236, 240, 242), a))
        credit = self.credit(el["path"])
        if credit:
            d.text((x, y + h + pad + 8), credit, font=self.font(20, "mono"), fill=self.c(p, "muted", a))

    def paper(self, img, d, p, el, t, alpha):
        """A page: a paper, a book, a headline. The title types on and the body lines draw in."""
        raw = (t - el["t_in"]) / 0.5
        if raw <= 0:
            return
        k = ease(raw)
        a = alpha * min(1.0, raw * 1.8)
        x, y, w, h = el["x"], el["y"] + (1 - k) * 50, el.get("w", 620), el.get("h", 780)
        paper_col = _mix(p["bg"], (238, 234, 222), a)
        ink = _mix(paper_col, (20, 20, 24), 1.0)
        d.rectangle((x + 14, y + 14, x + w + 14, y + h + 14), fill=_mix(p["bg"], (0, 0, 0), a * 0.6))
        d.rectangle((x, y, x + w, y + h), fill=paper_col)
        if el.get("kicker"):
            d.text((x + 40, y + 36), el["kicker"], font=self.font(26, "mono"), fill=_mix(paper_col, (110, 110, 110), 1.0))
        f = self.font(el.get("size", 44), "head")
        words = el["title"].split(" ")
        shown = int((t - el["t_in"]) / 0.03)
        line, lines = "", []
        for word in words:
            trial = (line + " " + word).strip()
            if d.textlength(trial, font=f) > w - 80 and line:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        yy, used = y + 90, 0
        for ln in lines:
            vis = ln[:max(0, shown - used)]
            used += len(ln) + 1
            d.text((x + 40, yy), vis, font=f, fill=ink)
            yy += el.get("size", 44) * 1.2
        yy += 30
        n = int((h - (yy - y) - 40) / 34)
        for i in range(max(0, n)):
            m = ease((t - el["t_in"] - 0.6 - i * 0.05) / 0.4)
            if m <= 0:
                break
            ln_w = (w - 80) * (0.55 + 0.4 * ((i * 37) % 10) / 10.0) * m
            d.rectangle((x + 40, yy + i * 34, x + 40 + ln_w, yy + i * 34 + 10), fill=_mix(paper_col, (160, 158, 150), 1.0))
        if el.get("stamp_t") is not None and t >= el["stamp_t"]:
            m = back((t - el["stamp_t"]) / 0.35)
            f2 = self.font(int(46 * max(0.3, m)), "head")
            label = el.get("stamp", "")
            sw = d.textlength(label, font=f2)
            sx, sy = x + w - sw - 50, y + h - 120
            d.rectangle((sx - 16, sy - 8, sx + sw + 16, sy + 64), outline=self.c(p, "warm", alpha), width=5)
            d.text((sx, sy), label, font=f2, fill=self.c(p, "warm", alpha))

    def image(self, img, d, p, el, t, alpha, scene):
        path = el["path"]
        if path not in self.images:
            src = Image.open(path).convert("RGB")
            scale = max(W / src.width, H / src.height) * 1.02
            src = src.resize((int(src.width * scale), int(src.height * scale)), Image.LANCZOS)
            vig = Image.new("L", src.size, 0)
            vd = ImageDraw.Draw(vig)
            vd.ellipse((-src.width * .15, -src.height * .25, src.width * 1.15, src.height * 1.25), fill=255)
            vig = vig.filter(ImageFilter.GaussianBlur(160))
            src = Image.composite(src, Image.new("RGB", src.size, (0, 0, 0)), vig)
            self.images[path] = src
        src = self.images[path]
        k = progress(t, el["t_in"], 0.9)
        if k <= 0:
            return
        span = max(0.1, scene["t1"] - el["t_in"])
        z = 1.0 + el.get("zoom", 0.1) * min(1.0, (t - el["t_in"]) / span)
        cw, ch = W / z, H / z
        ox = (src.width - cw) * el.get("pan_x", 0.5)
        oy = (src.height - ch) * el.get("pan_y", 0.5)
        frame = src.crop((int(ox), int(oy), int(ox + cw), int(oy + ch))).resize((W, H), Image.BILINEAR)
        # `dim` darkens a bright photograph further, for type that must read over a white facade.
        frame = Image.fromarray((np.asarray(frame, dtype=np.float32) * _scrim() * el.get("dim", 1.0))
                                .astype(np.uint8))
        img.paste(Image.blend(img, frame, alpha * k))
        if el.get("caption") and t >= el.get("caption_t", el["t_in"] + 0.8):
            kc = progress(t, el.get("caption_t", el["t_in"] + 0.8), 0.4)
            f = self.font(34, "mono")
            tw = d.textlength(el["caption"], font=f)
            y0, x1 = H - 110, W - 90
            x0 = x1 - tw - 52
            d.rectangle((x1 - 12, y0, x1, y0 + 52), fill=_mix((0, 0, 0), p["warm"], alpha * kc))
            d.rectangle((x1 - 20 - (tw + 32) * kc, y0, x1 - 20, y0 + 52), fill=_mix((0, 0, 0), (10, 12, 16), alpha * kc))
            if kc > 0.9:
                d.text((x0, y0 + 6), el["caption"], font=f, fill=(236, 240, 242))
        credit = self.credit(path)
        if credit and t >= el["t_in"] + 0.8:
            f = self.font(20, "mono")
            tw = d.textlength(credit, font=f)
            d.text((W - 90 - tw, H - 52), credit, font=f, fill=_mix((0, 0, 0), (200, 206, 210), alpha))

    # ------------------------------------------------------------------ frame

    def frame(self, scenes, t):
        scene = next((s for s in scenes if s["t0"] <= t < s["t1"]), None)
        if scene is None:
            return self.bases["dark"].copy()
        p = THEMES[scene.get("bg", "dark")]
        img = self.bases[scene.get("bg", "dark")].copy()
        d = ImageDraw.Draw(img)
        alpha = 1.0
        if t > scene["t1"] - LEAVE:
            alpha = max(0.0, (scene["t1"] - t) / LEAVE)
        has_image = False
        for el in scene["elements"]:
            if t < el["t_in"] and el["type"] != "text":
                continue
            a = alpha
            if "t_out" in el and t >= el["t_out"]:
                a = alpha * max(0.0, 1 - (t - el["t_out"]) / LEAVE)
                if a <= 0:
                    continue
            if el["type"] == "image":
                self.image(img, d, p, el, t, a, scene)
                d = ImageDraw.Draw(img)
                has_image = True
            else:
                getattr(self, el["type"])(img, d, p, el, t, a)
        if not has_image:
            z = 1.0 + PUSH * min(1.0, (t - scene["t0"]) / max(0.5, scene["t1"] - scene["t0"]))
            if z > 1.001:
                cw, ch = W / z, H / z
                img = img.crop((int((W - cw) / 2), int((H - ch) / 2), int((W + cw) / 2), int((H + ch) / 2))).resize((W, H), Image.BILINEAR)
        return self.bloom(img, scene.get("bg", "dark"))

    def bloom(self, img, bg):
        """Bright things glow a little. Measured on a quarter-size copy, so it costs almost nothing."""
        small = img.resize((W // 4, H // 4), Image.BILINEAR)
        arr = np.asarray(small).astype(np.float32)
        lum = arr.max(axis=2)
        threshold = 150 if bg == "dark" else 250
        mask = np.clip((lum - threshold) / 80.0, 0, 1)[..., None]
        glow = Image.fromarray((arr * mask).astype(np.uint8)).filter(ImageFilter.GaussianBlur(7))
        glow = np.asarray(glow.resize((W, H), Image.BILINEAR)).astype(np.float32)
        out = np.asarray(img).astype(np.float32) + glow * (0.55 if bg == "dark" else 0.0)
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def _encode_chunk(job):
    """Render frames [first, last) of the job to a video-only file. Runs in a worker process."""
    scenes, start, first, last, fps, path, threads = job
    canvas = Canvas()
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H), "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-threads", str(threads), str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(first, last):
            try:
                frame = canvas.frame(scenes, start + i / float(fps))
            except Exception as err:
                # A half-written part must never pass for a whole one: say which frame broke.
                proc.kill()
                raise RuntimeError("frame %d (%.2fs) of %s failed: %r"
                                   % (i, start + i / float(fps), path.name, err)) from err
            proc.stdin.write(frame.tobytes())
    finally:
        if proc.poll() is None:
            proc.stdin.close()
        code = proc.wait()
    if code != 0:
        raise SystemExit("ffmpeg failed encoding %s (exit %d)" % (path, code))
    return path


def render(scenes, brand, audio, out, start, end, fps=FPS, workers=1):
    """Encode [start, end) of the narration with the narration under it.

    With `workers` above one, the frames are split into that many runs, each drawn and encoded
    in its own process, then joined without re-encoding. Drawing is the slow part and every
    frame depends only on its own time, so the split is exact - no seam, no drift.
    """
    import multiprocessing

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round((end - start) * fps))
    work = out.parent / (out.stem + ".parts")
    work.mkdir(exist_ok=True)
    budget.lower_priority()
    workers = budget.workers(min(workers, frames // (fps * 2) or 1))
    bounds = [frames * n // workers for n in range(workers + 1)]
    # Each encoder gets its share of the HALF of the cores this library may use. Left to itself
    # x264 takes threads for the whole machine in every process.
    threads = budget.encoder_threads(workers)
    jobs = [(scenes, start, bounds[n], bounds[n + 1], fps, work / ("part-%03d.mp4" % n), threads)
            for n in range(workers)]
    print("  %d frames in %d run(s)" % (frames, workers), flush=True)
    if workers == 1:
        parts = [_encode_chunk(jobs[0])]
    else:
        with multiprocessing.Pool(workers) as pool:
            # Unordered, so a failed run stops the render at once rather than after every run
            # ahead of it has finished.
            parts = []
            for n, part in enumerate(pool.imap_unordered(_encode_chunk, jobs)):
                parts.append(part)
                print("  run %d/%d done" % (n + 1, workers), flush=True)
            parts.sort()
    return join(parts, audio, out, start, end)


def join(parts, audio, out, start, end):
    """Join rendered parts, in order, under [start, end) of the narration - no re-encode."""
    out = Path(out)
    listing = out.parent / (out.stem + ".parts") / "concat.txt"
    # ffmpeg reads a relative name in a concat list against the LIST's folder, not the working
    # one, so every part is named absolutely.
    listing.write_text("".join("file '%s'\n" % Path(p).resolve().as_posix() for p in parts),
                       encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", str(listing),
           "-ss", "%.3f" % start, "-t", "%.3f" % (end - start), "-i", str(audio),
           "-map", "0:v", "-map", "1:a", "-c:v", "copy",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", "-shortest", str(out)]
    if subprocess.run(budget.ffmpeg(cmd)).returncode != 0:
        raise SystemExit("ffmpeg failed joining %s" % out)
    return out


def _sample_chunk(job):
    """Draw every frame the picture score would look at, shrunk the same way. Worker process."""
    from ..qa import picture
    scenes, times = job
    canvas = Canvas()
    out = []
    for t in times:
        img = canvas.frame(scenes, t).convert("L").resize((picture.W, picture.H), Image.BOX)
        out.append(np.asarray(img, dtype=np.int16))
    return out


def sample(scenes, start, end, workers=1):
    """(busy, diff) arrays for the frames of [start, end), drawn but never encoded - the picture
    score of a video before it is rendered. Same rate and same arithmetic as scoring the file."""
    import multiprocessing
    from ..qa import picture
    times = [start + i / float(picture.RATE) for i in range(int((end - start) * picture.RATE))]
    budget.lower_priority()
    workers = budget.workers(min(workers, len(times) // 50 or 1))
    bounds = [len(times) * n // workers for n in range(workers + 1)]
    jobs = [(scenes, times[bounds[n]:bounds[n + 1]]) for n in range(workers)]
    if workers == 1:
        grey = _sample_chunk(jobs[0])
    else:
        with multiprocessing.Pool(workers) as pool:
            grey = [g for part in pool.map(_sample_chunk, jobs) for g in part]
    busy, diff, prev = [], [], None
    for g in grey:
        b, d = picture.frame_busy(g, prev)
        busy.append(b)
        diff.append(d)
        prev = g
    return np.array(busy), np.array(diff)
