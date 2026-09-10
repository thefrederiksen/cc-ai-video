"""Caption cues: word timings in, readable lines out.

Three styles, and all three are deliberate. They were three separate implementations in three
renderers, and the differences between them were not bugs - they answer different questions
about who is reading.

  centre  2 to 4 words, at most 30 characters, one word coloured, at the vertical centre.
          For a short that is READ rather than heard, going past at arm's length while
          scrolling. Most of these are watched muted.
  strip   Up to 7 words in at most two lines, broken at soft conjunctions. For a short where
          the footage is the subject and the captions support it.
  full    Whole sentences. For a long video where the captions are an accessibility track and
          the viewer has the audio.

TWO RULES APPLY TO ALL THREE

*A cue too brief to read is not a caption.* It is merged into its neighbour if the pair still
fits, and otherwise held longer. A caption nobody can read is the same as no caption, but it
costs a flicker.

*A cue never opens mid-word.* The fragment rule in `words.py` runs before cues are built, not
after, because a break can otherwise fall between "10" and ",000" and put ",000 PROBABILITY"
on screen - a string the speaker never uttered.
"""

import re

from .words import glue_fragments, join_pair, join_tokens

STYLES = {
    "centre": {"max_words": 4, "max_chars": 30, "min_seconds": 0.45, "upper": True,
               "emphasis": True, "break_on_punctuation": True},
    "strip": {"max_words": 7, "max_chars": 48, "min_seconds": 1.1, "upper": False,
              "emphasis": False, "break_on_punctuation": True},
    "full": {"max_words": 0, "max_chars": 84, "min_seconds": 1.0, "upper": False,
             "emphasis": False, "break_on_punctuation": False},
}

# Words that open a clause: a good place to break a long line, and a bad place to end one.
SOFT_BREAK = {"and", "but", "so", "because", "which", "then", "when", "if", "or", "while"}


def style(name):
    if name not in STYLES:
        raise SystemExit("unknown caption style %r - have %s" % (name, ", ".join(sorted(STYLES))))
    return STYLES[name]


def build(words, style_name="centre", emphasis=(), colour=None, offset=0.0, kind="clip",
          corrections=()):
    """Cues for `words`, in the named style.

    `corrections` is a list of (pattern, replacement) applied to what reaches the screen. A
    transcriber mishears a product name in a dozen ways, and a caption is the one place the
    audience reads it, so the brand's own spelling is restored here. They are applied BOTH per
    word and to the finished line: every useful correction spans two words ("cloud code"), and
    applied per word not one of them can ever match.
    """
    spec = style(style_name)
    emphasised = {e.lower().strip(".,!?") for e in emphasis}
    words = glue_fragments(words)
    cues, current = [], []

    def fix(text):
        for pattern, replacement in corrections:
            text = re.sub(pattern, replacement, text, flags=re.I)
        return text

    def fix_line(text):
        for pattern, replacement in corrections:
            replaced = replacement.upper() if spec["upper"] else replacement
            text = re.sub(pattern, replaced, text, flags=re.I)
        return text

    def flush():
        if not current:
            return
        tokens, rendered = [], []
        for word in current:
            token = fix(word["word"])
            if spec["upper"]:
                token = token.upper()
            tokens.append(token)
            bare = word["word"].lower().strip(".,!?;:\"'")
            if spec["emphasis"] and colour and (bare in emphasised
                                                or re.fullmatch(r"[\d,.]+%?", bare)):
                rendered.append("{\\c%s&}%s{\\c&HFFFFFF&}" % (colour, token))
            else:
                rendered.append(token)
        cues.append({
            "start": offset + current[0]["start"],
            "end": offset + current[-1]["end"],
            "text": fix_line(join_tokens(tokens, rendered)),
            "plain": fix_line(join_tokens(tokens, tokens)),
            "kind": kind,
        })
        current.clear()

    for i, word in enumerate(words):
        if not word["word"]:
            continue
        prospective = len(" ".join(
            (fix(w["word"]).upper() if spec["upper"] else fix(w["word"]))
            for w in current + [word]))
        if current and prospective > spec["max_chars"]:
            flush()
        current.append(word)

        token = word["word"].strip()
        if spec["break_on_punctuation"] and re.search(r"[.!?]$", token) and len(current) >= 1:
            flush()
        elif spec["break_on_punctuation"] and re.search(r"[,;:]$", token) and len(current) >= 2:
            flush()
        elif spec["max_words"] and len(current) >= spec["max_words"]:
            flush()
        elif not spec["max_words"]:
            nxt = (words[i + 1]["word"].strip().lower().strip(".,;!?")
                   if i + 1 < len(words) else "")
            span = current[-1]["end"] - current[0]["start"]
            if nxt in SOFT_BREAK and len(current) >= 4 and span >= 1.6:
                flush()
    flush()

    return _readable(cues, spec)


def _readable(cues, spec):
    """Merge a cue too brief to read into the one after it, then hold what is left."""
    merged = []
    for cue in cues:
        if merged:
            previous = merged[-1]
            pair = join_pair(previous["plain"], cue["plain"], cue["plain"]).strip()
            brief = previous["end"] - previous["start"] < spec["min_seconds"]
            fits = (len(pair) <= spec["max_chars"]
                    and (not spec["max_words"] or len(pair.split()) <= spec["max_words"]))
            if brief and fits:
                previous["text"] = join_pair(previous["text"], cue["text"], cue["plain"]).strip()
                previous["plain"] = pair
                previous["end"] = cue["end"]
                continue
        merged.append(dict(cue))

    for i, cue in enumerate(merged):
        nxt = merged[i + 1]["start"] if i + 1 < len(merged) else cue["end"] + 1.0
        if cue["end"] - cue["start"] < spec["min_seconds"]:
            cue["end"] = min(cue["start"] + spec["min_seconds"] + 0.2, nxt - 0.02)
        if cue["end"] <= cue["start"]:
            cue["end"] = cue["start"] + 0.3
    return merged


def hold_into_gaps(cues, until=None, minimum=0.45, drop_below=0.35):
    """Let each cue run into the silence after it, bounded by the next cue.

    A cue is timed inside its own clip, so the last word of a clip lands exactly on the
    boundary and would be squeezed to nothing when the clips are joined. A cue still too brief
    after that is a fragment the cut caught on its way past - half a thought with no room to
    be read - and is dropped rather than flashed. Returns (kept cues, dropped cues).
    """
    kept, dropped = [], []
    for i, cue in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else (
            until if until is not None else cue["end"] + 1.0)
        cue = dict(cue)
        cue["end"] = min(cue["end"], nxt - 0.02)
        if cue["end"] - cue["start"] < minimum:
            cue["end"] = min(cue["start"] + minimum + 0.2, nxt - 0.02)
        if cue["end"] - cue["start"] < drop_below:
            dropped.append(cue)
            continue
        kept.append(cue)
    return kept, dropped
