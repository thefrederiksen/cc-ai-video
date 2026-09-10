"""Brands: a palette, the name on a card footer, how the narrator is told to read, and the
words this brand will not say.

Adding a product means adding a row - never a new copy of a renderer. Two things about this
module are load-bearing and easy to break by tidying:

* **`instructions` is part of the voice cache key.** The narration cache is keyed on the exact
  spoken text plus a fingerprint of the voice, and that fingerprint includes this string. Edit
  it and every cached segment for that brand misses and is re-synthesised - which is the right
  behaviour when the voice really changed, and an expensive accident when someone reflowed a
  sentence. That is one of the two reasons real brands live in a JSON file rather than in this
  source: a row nobody here can edit cannot be reflowed by accident, so an existing project
  keeps re-rendering from its cache, free and byte-identical.
* **Brands and their word rules are configuration, not code.** A palette, a pronunciation and
  an editorial policy belong to whoever owns the brand. One neutral row ships; real ones come
  from `--brands` or CCVIDEO_BRANDS, and a caller picks WHICH of a brand's word policies
  applies to the script in hand.
"""

import json
import os
import re
from pathlib import Path

# Words a brand refuses to say. Self-congratulation ("honestly", "genuinely") reads as an
# advertisement, and "free" as a headline is an absence-sell.
DEFAULT_BANNED = [
    r"\bhonest(ly|y)?\b", r"\bgenuinely\b", r"\bactually\b", r"\bdeliberately\b",
    r"\bfrankly\b", r"\bfree\b",
    r"\bno sign.?up\b", r"\bno account\b", r"\bno credit card\b",
]

# WHY THERE IS MORE THAN ONE LIST
#
# A rival's name in a video that positions against it is a rule worth having. The same name in
# a tutorial that lists which tools the product supports is the product's own feature list, and
# banning it there is the rule misfiring on its own documentation. The first run of this gate
# failed a shipped video for saying "Cursor" in a sentence naming the agents DevThrottle runs.
#
# So a policy is chosen per script, not per brand. "default" is what a product video uses;
# "positioning" adds the names a video arguing against them must not hand free attention to.
COMPETITOR_NAMES = [r"\bcursor\b", r"\bwindsurf\b", r"\bcline\b", r"\baider\b"]

# ONE NEUTRAL BRAND SHIPS WITH THE LIBRARY, and it is not anybody's.
#
# A brand carries a company's palette, the way its name is pronounced and how its narrator is
# directed. Those belong to whoever owns the brand, not in a public package. Real ones are
# supplied as a JSON file - `--brands`, or the CCVIDEO_BRANDS environment variable - kept
# wherever that company keeps its configuration.
#
# There is a second reason to keep them out, and it is expensive to get wrong: `instructions`
# is part of the voice cache key. A brand row that lives in this repository could be reflowed
# by anyone tidying a docstring, and every cached narration line for that brand would miss.
# Held as configuration, it changes only when its owner changes it.
BUILTIN = {
    "default": {
        "bg": (18, 18, 22),
        "bg2": (32, 34, 42),
        "accent": (90, 140, 220),
        "rule": (56, 60, 72),
        "pronunciation": [],
        "product": "",
        "instructions": (
            "Calm, confident product-tutorial narrator. Measured pace, clear articulation, "
            "warm but professional. Slight emphasis on UI element names."
        ),
        "word_policies": {"default": DEFAULT_BANNED,
                          "positioning": DEFAULT_BANNED + COMPETITOR_NAMES},
    },
}

# Card text colours. Shared by every brand; the ground and accent are what differ.
TITLE_COLOUR = (255, 255, 255)
SUB_COLOUR = (169, 182, 204)
FOOT_COLOUR = (104, 121, 148)


BRANDS_ENV = "CCVIDEO_BRANDS"


def load_brands(path=None):
    """The brand table, optionally overlaid with a JSON file of the same shape.

    An overlay row REPLACES a built-in row of the same name rather than merging into it, so a
    caller can never end up with half of our palette and half of theirs and no way to tell
    which is on screen.
    """
    table = {name: dict(row) for name, row in BUILTIN.items()}
    path = path or os.environ.get(BRANDS_ENV, "").strip() or None
    if path:
        path = Path(path)
        if not path.exists():
            raise SystemExit("no brand file at %s" % path)
        extra = json.loads(path.read_text(encoding="utf-8"))
        for name, row in extra.items():
            missing = [k for k in ("bg", "bg2", "accent", "rule", "product", "instructions")
                       if k not in row]
            if missing:
                raise SystemExit("brand %r in %s is missing %s" % (name, path, ", ".join(missing)))
            row = dict(row)
            for key in ("bg", "bg2", "accent", "rule"):
                row[key] = tuple(row[key])
            row.setdefault("pronunciation", [])
            row.setdefault("word_policies", {"default": DEFAULT_BANNED})
            table[name] = row
    return table


def get(name, path=None):
    table = load_brands(path)
    if name not in table:
        raise SystemExit("unknown brand %r - have %s" % (name, ", ".join(sorted(table))))
    return table[name]


def spoken(brand, text):
    """The text as the narrator should SAY it, with this brand's pronunciation applied.

    Part of the voice cache key. Never change what this returns for existing text without
    accepting that every cached segment re-synthesises.
    """
    out = text
    for written, said in brand.get("pronunciation", []):
        out = re.sub(re.escape(written), said, out, flags=re.IGNORECASE)
    return out


def word_policy(brand, policy="default"):
    policies = brand.get("word_policies") or {"default": DEFAULT_BANNED}
    if policy not in policies:
        raise SystemExit("brand %r has no word policy %r - have %s"
                         % (brand.get("product"), policy, ", ".join(sorted(policies))))
    return policies[policy]


def banned_hits(brand, text, policy="default"):
    """Which of this brand's banned patterns the text trips. Empty means clean."""
    return [p for p in word_policy(brand, policy) if re.search(p, text, re.I)]
