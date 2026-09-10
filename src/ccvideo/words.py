"""The word-timed transcript: the one thing every cut in this library is made on.

A words file is JSON:

    {"source": "...", "duration": 123.4,
     "segments": [{"start": 0.0, "end": 3.2, "text": "...",
                   "words": [{"start": 0.0, "end": 0.3, "word": "So"}, ...]}]}

Every tool this library replaces produced or consumed some version of that, and three of them
carried their own copy of the code below. There is one copy here.

Two rules live in this module because they are properties of the transcript, not of any one
renderer:

* A fragment token is glued to the word before it. Whisper emits the tail of a hyphenated,
  contracted or punctuated word as its own token - "well" + "-trained", "100" + ",000" - and a
  plain space join puts WELL -TRAINED and 100 ,000 on screen, words nobody said. Worse, a cue
  break can fall BETWEEN them, so a caption opens on ",000". Gluing before anything else is
  built means a break can never land inside a word.
* A sentence boundary is not only a full stop. Run-on speakers get almost no punctuation from
  a transcriber, so a real pause and a capitalised sentence opener count too.
"""

import json
from pathlib import Path

# A token opening with one of these is the back half of the word before it, never a word.
FRAGMENT = ("-", "'", ",", ".", "!", "?", ";", ":", "%")

SENTENCE_END = (".", "!", "?")

# Whisper capitalises the first word of a sentence even when it drops the full stop, so a
# capitalised opener marks a boundary before it. This is the default list; a caller with a
# different speaker or language passes its own.
DEFAULT_OPENERS = frozenset("""
So And But Now Then All Alright Okay Let's This That Once Here Instead If When Because You We
It It's The There These Those I I'm I'll I've As For In On After Before First Next Finally
What Which Keep Make Go Just Right Yeah Yes No Oh Also Again Everything Anyways Hey Hi Hello
Today To With By Of Every Each One Two
""".split())


def load(path):
    """Read a words file. Returns the parsed document."""
    path = Path(path)
    if not path.exists():
        raise SystemExit(
            "no words file at %s - run 'ccvideo transcribe' on the media first. "
            "Nothing in this library cuts on an estimate." % path
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    if "segments" not in doc:
        raise SystemExit("%s is not a words file: it has no 'segments'" % path)
    return doc


def all_words(source):
    """Every word in order, from a words file path or an already-loaded document."""
    doc = source if isinstance(source, dict) else load(source)
    words = [w for s in doc["segments"] for w in s.get("words", [])]
    if not words:
        raise SystemExit(
            "the transcript has segments but no word timings. Re-run 'ccvideo transcribe' - "
            "a segment-level transcript cannot be cut on word boundaries."
        )
    return words


def window(source, start, end, rebase=True):
    """The words inside [start, end).

    rebase=True returns times relative to `start`, which is what a renderer wants for a clip it
    is about to cut. rebase=False keeps source times, which is what an editor wants.
    """
    out = []
    for w in all_words(source):
        if w["end"] <= start or w["start"] >= end:
            continue
        if rebase:
            out.append({"start": max(0.0, w["start"] - start),
                        "end": min(end - start, w["end"] - start),
                        "word": (w["word"] or "").strip()})
        else:
            out.append({"start": w["start"], "end": w["end"], "word": (w["word"] or "").strip()})
    return out


def glue_fragments(words):
    """Merge each fragment token onto the word before it. See the module docstring."""
    out = []
    for w in words:
        token = (w.get("word") or "").strip()
        if not token:
            continue
        if out and token.startswith(FRAGMENT):
            out[-1] = {"start": out[-1]["start"], "end": w["end"],
                       "word": out[-1]["word"].rstrip() + token}
            continue
        out.append({"start": w["start"], "end": w["end"], "word": token})
    return out


def join_tokens(tokens, rendered):
    """Join tokens with spaces, except a fragment, which is glued to what came before.

    `rendered` is what to actually emit for each token - the same strings, or the same strings
    with colour markup around one of them. The join rule reads the raw token, so markup can
    never change where a space goes.
    """
    out = ""
    for token, text in zip(tokens, rendered):
        if out and not token.startswith(FRAGMENT):
            out += " "
        out += text
    return out


def join_pair(left, right, right_token):
    """The same join rule where two finished strings are merged."""
    if left and right_token.startswith(FRAGMENT):
        return left + right
    return (left + " " + right) if left else right


def is_boundary(words, i, pause=0.35, opener_pause=0.12, openers=DEFAULT_OPENERS):
    """Word i closes a sentence.

    Three ways, and the last two exist because a run-on speaker gets almost no punctuation
    from a transcriber: the word ends in . ! ?, the speaker pauses after it, or the next word
    is a capitalised sentence opener AND there is at least a breath between them.

    THE BREATH IS NOT OPTIONAL. Half the useful openers - "I", "It", "We", "The", "That" - are
    also ordinary mid-sentence words, and a transcriber capitalises the pronoun "I" wherever it
    falls. Without the gap requirement, "What am I working on here" contains a sentence
    boundary after "am", and a cut asked to end on the previous sentence ends on the words
    "What am" instead. That was measured on real footage, not imagined. A real sentence break
    in speech always carries at least a short gap; a pronoun mid-clause carries none at all.
    """
    token = (words[i]["word"] or "").strip()
    if token.endswith(SENTENCE_END):
        return True
    if i + 1 >= len(words):
        return False
    gap = words[i + 1]["start"] - words[i]["end"]
    if gap >= pause:
        return True
    return (gap >= opener_pause
            and (words[i + 1]["word"] or "").strip().rstrip(",") in openers)


def sentence_starts(words, **kw):
    if not words:
        return []
    return [words[0]["start"]] + [words[i + 1]["start"]
                                  for i in range(len(words) - 1) if is_boundary(words, i, **kw)]


def sentence_ends(words, **kw):
    return [words[i]["end"] for i in range(len(words)) if is_boundary(words, i, **kw)]


def text_of(words):
    """The words as a readable line, with the fragment rule applied."""
    glued = glue_fragments(words)
    tokens = [w["word"] for w in glued]
    return join_tokens(tokens, tokens)
