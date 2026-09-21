"""A scene list, and the word times its elements are anchored to.

A scene list says WHAT appears and on WHICH WORD - never at a second somebody typed. An
element's `at` is a phrase the speaker says; it is looked up in the narration's own word
timings, searching forward from where the scene starts, so "the one after that" said twice
resolves to the right one each time. A phrase that is not there is an error, never a nearest
guess: a picture landing on the wrong sentence is worse than a build that stops.

    {"scenes": [
      {"at": "Every time", "elements": [
         {"type": "box", "text": "ChatGPT", "at": "chat", "x": 200, "y": 300, "w": 300, "h": 90},
         ...]},
      ...]}

`at` may also be a number of seconds, for a scene that opens on silence. `delay` shifts an
anchor by a stated amount, and `until` names the word an element leaves on.
"""

import json
import re
from pathlib import Path

from .. import words as wordlib


def _bare(token):
    return re.sub(r"[^a-z0-9]", "", token.lower())


class Anchors:
    def __init__(self, words):
        self.words = [w for w in words if _bare(w["word"])]
        self.bare = [_bare(w["word"]) for w in self.words]

    def find(self, phrase, after=0.0):
        """Start time of the first occurrence of `phrase` at or after `after` seconds."""
        needle = [_bare(t) for t in str(phrase).split() if _bare(t)]
        if not needle:
            raise SystemExit("an anchor must name at least one spoken word, not %r" % phrase)
        for i in range(len(self.bare) - len(needle) + 1):
            if self.words[i]["start"] + 0.001 < after:
                continue
            if self.bare[i:i + len(needle)] == needle:
                return self.words[i]["start"]
        raise SystemExit("the anchor %r is not spoken after %.2fs - quote the words as the "
                         "transcript has them (ccvideo transcribe prints them)" % (phrase, after))

    def resolve(self, spec, after, delay=0.0):
        if isinstance(spec, (int, float)):
            return float(spec) + delay
        return self.find(spec, after) + delay


def load(path, words_file, start=0.0, end=None):
    """The scene list with every anchor turned into seconds, scenes in time order."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    anchors = Anchors(wordlib.all_words(words_file))
    scenes, cursor = [], start
    for n, scene in enumerate(doc["scenes"]):
        t0 = anchors.resolve(scene["at"], cursor, scene.get("delay", 0.0))
        if scenes and t0 < scenes[-1]["t0"]:
            raise SystemExit("scene %d starts at %.2fs, before the scene ahead of it" % (n, t0))
        elements = []
        for el in scene["elements"]:
            el = dict(el)
            el["t_in"] = anchors.resolve(el.get("at", t0), t0 - 0.3, el.get("delay", 0.0)) \
                if "at" in el else t0 + el.get("delay", 0.0)
            if "until" in el:
                el["t_out"] = anchors.resolve(el["until"], el["t_in"], el.get("until_delay", 0.0))
            for part in el.get("parts", []):
                part["t_in"] = anchors.resolve(part["at"], t0 - 0.3, part.get("delay", 0.0)) \
                    if "at" in part else el["t_in"]
            for item in el.get("items", []):
                item["t_in"] = anchors.resolve(item["at"], t0 - 0.3, item.get("delay", 0.0)) \
                    if "at" in item else el["t_in"]
            for key in ("lit_at", "move_at", "stamp_at"):
                if key in el:
                    el[key.replace("_at", "_t")] = anchors.resolve(el[key], t0 - 0.3)
            elements.append(el)
        scenes.append({"t0": t0, "elements": elements, "bg": scene.get("bg", "dark")})
        cursor = t0
    _open_on_content(scenes)
    for a, b in zip(scenes, scenes[1:]):
        a["t1"] = b["t0"]
    if scenes:
        scenes[-1]["t1"] = end if end is not None else scenes[-1]["t0"] + 5.0
    return scenes


MINOR_TEXT = 48   # text smaller than this is a label, not something to look at


def is_minor(el):
    """A label ('02 / THE SWITCH', 'IDEA 04') puts pixels on screen but nothing to look at."""
    return bool(el.get("minor")) or (el["type"] == "text" and el.get("size", 90) < MINOR_TEXT)


def first_shown(el):
    """When an element first puts something on screen: a text element with its first part, a
    list or a bar chart with its first item - not the moment the empty container is anchored."""
    pieces = [x["t_in"] for x in el.get("parts", []) + el.get("items", [])]
    return min(pieces) if pieces else el["t_in"]


def _open_on_content(scenes):
    """A scene begins when its first SUBSTANTIAL element arrives, not on the word it is
    anchored to. Until then the scene before it stays on screen.

    Anchoring a scene to its first word and its elements to later words left the viewer
    looking at a bare background: 146 seconds of empty screen in 46 stretches on the first
    28-minute video. Holding the previous, fully built scene over those words reads as a
    pause; an empty screen reads as a fault. A scene never moves past the one after it."""
    for n, s in enumerate(scenes):
        shown = [first_shown(el) for el in s["elements"] if not is_minor(el)]
        if not shown or n == 0:
            continue
        target = min(shown)
        limit = scenes[n + 1]["t0"] - 0.5 if n + 1 < len(scenes) else float("inf")
        if s["t0"] < target < limit:
            s["t0"] = target
