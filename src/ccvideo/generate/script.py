"""The segment script: parsing, and the lint that runs before a single second is synthesised.

A script is one image or card plus one block of spoken text, repeated:

    # TITLE: the name embedded in the mp4
    # FOOTER: the line along the bottom of every card

    === SEG 001
    IMAGE: CARD
    CARD: kicker|title|subtitle
    CHAPTER: What this is
    TEXT:
    The words the narrator says.

    === SEG 002
    IMAGE: a-real-screenshot.png
    TEXT:
    More words.

THREE SEGMENT KINDS, and the third is why this module exists
    IMAGE: <file>   a real frame
    IMAGE: CARD     a title card drawn for the VIEWER
    IMAGE: SHOT     a placeholder for footage not yet captured, drawn for the PERSON HOLDING
                    THE CAMERA, carrying a SHOT: line describing what to film

The third kind is not a nicety. A twenty-minute walkthrough was assembled at 53 percent
production placeholders - frames reading "SHOT TO CAPTURE: the hygiene skill actually running",
two of them carrying internal warnings - and it rendered clean and passed every machine check,
because nothing in the pipeline could tell a card meant for a viewer from a card meant for the
crew. It was caught by a human looking at a contact sheet. Now the machine can tell: a shot
card is its own kind, it is always reported, and a build marked for publication refuses to
contain one.

LINT, NOT REFUSAL
The assembler this replaces hard-refused more than three title cards. That is the right advice
for a tutorial and the wrong law for a deck-style video, and it was worked around by generating
the cards as images outside the tool - which is worse than the thing it prevented. Here the cap
is a lint rule with a number the caller sets, so it advises by default and binds when asked.
"""

import os
import re

from ..brand import banned_hits

# What must never reach the voice: markup and filenames. The narrator will read them aloud.
NARRATABLE = re.compile(r"[*#`|_\[\]]|\.png|\.md|\.jpg")

# Projected speaking rate for the runtime estimate, per language. One global rate was wrong in
# the dangerous direction - it over-predicted English and UNDER-predicted German and Turkish by
# a quarter, so a script could look inside its cap and then render well past it. Each of these
# is that language's SLOWEST measured rate, so an estimate errs long and never short.
WPM_BY_LANG = {"en": 150.0, "de": 118.0, "es": 132.0, "fr": 132.0,
               "nl": 125.0, "tr": 97.0, "da": 130.0, "ja": 150.0}
WPM_UNKNOWN = 115.0
# Japanese has no spaces, so counting words called a three and a half minute video 23 seconds.
# Measured 266-323 CJK characters a minute; the low end is used.
CJK_CPM = 265.0

DEFAULT_CARD_ADVICE = 3     # cards above this are reported: they are runtime with no product


class Segment:
    def __init__(self, sid):
        self.id = sid
        self.kind = None            # "image" | "card" | "shot"
        self.image = None           # the file, for kind == "image"
        self.card = None            # [kicker, title, sub], for kind == "card"
        self.shot = None            # what to film, for kind == "shot"
        self.chapter = None
        self.lines = []
        self.text = ""

    def __repr__(self):
        return "<Segment %s %s>" % (self.id, self.kind)


class Script:
    def __init__(self, path, segments, title, footer):
        self.path = path
        self.segments = segments
        self.title = title
        self.footer = footer

    @property
    def cards(self):
        return [s for s in self.segments if s.kind == "card"]

    @property
    def shots(self):
        return [s for s in self.segments if s.kind == "shot"]


def parse(path):
    """Read a script into segments. Raises on anything structurally wrong; editorial problems
    are left to lint(), so a caller can see every one of them at once."""
    segments, cur, mode = [], None, None
    title = footer = ""
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if cur is None and line.startswith("# FOOTER:"):
                footer = line.split(":", 1)[1].strip()
                continue
            if cur is None and line.startswith("# TITLE:"):
                title = line.split(":", 1)[1].strip()
                continue
            if line.startswith("=== SEG"):
                if cur:
                    segments.append(cur)
                parts = line.split()
                if len(parts) < 3:
                    raise SystemExit("malformed segment header: %r" % line)
                cur = Segment(parts[2])
                mode = None
                continue
            if cur is None:
                continue                                  # header comments
            if line.startswith("IMAGE:"):
                value = line.split(":", 1)[1].strip()
                if value == "CARD":
                    cur.kind = "card"
                elif value == "SHOT":
                    cur.kind = "shot"
                else:
                    cur.kind, cur.image = "image", value
                mode = None
            elif line.startswith("CARD:"):
                parts = line.split(":", 1)[1].split("|")
                parts += [""] * (3 - len(parts))
                cur.card = [p.strip() for p in parts[:3]]
                mode = None
            elif line.startswith("SHOT:"):
                cur.shot = line.split(":", 1)[1].strip()
                mode = None
            elif line.startswith("CHAPTER:"):
                cur.chapter = line.split(":", 1)[1].strip()
                mode = None
            elif line.startswith("TEXT:"):
                mode = "text"
            elif mode == "text":
                cur.lines.append(line)
    if cur:
        segments.append(cur)

    if not segments:
        raise SystemExit("script has no '=== SEG' segments: %s" % path)
    for seg in segments:
        seg.text = " ".join(" ".join(seg.lines).split())
    return Script(path, segments, title, footer)


def lint(script, shots_dir, brand, card_advice=DEFAULT_CARD_ADVICE, publish=False,
         words_policy="default"):
    """Everything wrong with this script, as a list of (level, segment id, message).

    level is "error" - the build cannot proceed - or "warn" - the build proceeds and a human
    is told. `publish=True` promotes the things that are only acceptable in a work in progress.
    """
    problems = []

    def error(sid, msg):
        problems.append(("error", sid, msg))

    def warn(sid, msg):
        problems.append(("warn", sid, msg))

    if not script.title:
        error("-", "no '# TITLE:' line - the mp4 needs an embedded name")
    if not script.footer:
        error("-", "no '# FOOTER:' line - the title cards need one")

    seen = set()
    for seg in script.segments:
        if seg.id in seen:
            error(seg.id, "duplicate segment id")
        seen.add(seg.id)

        if not seg.kind:
            error(seg.id, "no IMAGE line")
        if not seg.text:
            error(seg.id, "no narration text")
        else:
            hit = NARRATABLE.search(seg.text)
            if hit:
                error(seg.id, "would speak markup or a filename: %r"
                      % seg.text[max(0, hit.start() - 40):hit.end() + 40])
            hits = banned_hits(brand, seg.text, words_policy)
            if hits:
                warn(seg.id, "narration trips the %r word policy: %s"
                     % (words_policy, ", ".join(hits)))

        if seg.kind == "image":
            if not os.path.exists(os.path.join(shots_dir, seg.image)):
                error(seg.id, "no such frame %r in %s" % (seg.image, shots_dir))
        elif seg.kind == "card":
            if not seg.card:
                error(seg.id, "is a CARD with no 'CARD:' line")
        elif seg.kind == "shot":
            if not seg.shot:
                error(seg.id, "is a SHOT with no 'SHOT:' line saying what to film")
            message = ("is a production placeholder for footage not yet captured: %r"
                       % (seg.shot or ""))
            if publish:
                error(seg.id, message + " - a video for publication cannot contain one")
            else:
                warn(seg.id, message)

    cards = len(script.cards)
    if cards > card_advice:
        warn("-", "%d title cards (advice is %d) - a card is runtime with no product on screen"
             % (cards, card_advice))

    return problems


def format_problems(problems):
    return "\n".join("%-5s %-5s %s" % (level.upper(), sid, msg) for level, sid, msg in problems)


def has_errors(problems):
    return any(level == "error" for level, _, _ in problems)


def language_of(script_path, override=""):
    """The trailing '-de' or '-ja' on a script filename is its language, which is how whole
    script libraries are named. An explicit override always wins."""
    if override:
        return override
    stem = os.path.splitext(os.path.basename(str(script_path)))[0]
    tail = stem.rsplit("-", 1)[-1].lower()
    return tail if tail in WPM_BY_LANG else "en"


def speech_seconds(text, wpm):
    """Projected speaking time. CJK is counted by character and everything else by word, and
    both are added, so a Japanese script quoting English interface names is estimated sanely."""
    from ..draw import CJK
    cjk = len(CJK.findall(text))
    words = len(CJK.sub(" ", text).split())
    return cjk / CJK_CPM * 60.0 + words / wpm * 60.0


def estimate(script, lang):
    """(seconds, words, cjk characters) for the whole script, before anything is spent.

    Narrating a written page verbatim once produced a 41 minute video that was rejected
    outright. This is the cheapest possible place to find that out.
    """
    from ..draw import CJK
    wpm = WPM_BY_LANG.get(lang, WPM_UNKNOWN)
    seconds = sum(speech_seconds(s.text, wpm) for s in script.segments)
    words = sum(len(CJK.sub(" ", s.text).split()) for s in script.segments)
    cjk = sum(len(CJK.findall(s.text)) for s in script.segments)
    return seconds, words, cjk
