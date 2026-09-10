"""Move a requested cut window onto real sentence and word boundaries.

THIS IS THE CORE PRIMITIVE OF THE EDIT HALF.

A cut is requested with times a human read off a transcript, or an agent inferred from a
phrase. Those times are never exact - they land inside words and mid-sentence. Of the first
fifty hand-timed cuts made without this, forty-four opened or closed inside a word. Nobody
notices while reading the numbers; everybody notices in the finished file.

    start -> the start of the nearest SENTENCE beginning within `reach` seconds of the request,
             otherwise the nearest word start, then `pad` of silence before it
    end   -> the end of the nearest sentence ending within `reach` seconds of the request,
             otherwise the nearest word end, then `pad` after it

Returns (start, end, notes). The notes say what was snapped and whether a real sentence
boundary was found, because a snap to a mere WORD boundary is a weaker cut and the caller -
and the QA gate - must be able to see the difference. Nothing here guesses silently.
"""

from .. import words as wordlib


def snap(words, start, end, reach=4.0, pad=0.25, max_len=None, min_len=0.5):
    """Snap [start, end] onto boundaries in `words`. See the module docstring.

    `min_len` exists only to stop a degenerate window where the end snaps back behind the
    start. It is NOT a platform's minimum length. The version this was taken from used three
    seconds, because the only caller was cutting Shorts and a Short must run at least that
    long - and the effect on a cut named by its words was to swallow the sentence AFTER the
    one asked for, because the requested end was inside the floor. A target's limits are the
    target's business and are enforced when the timeline is rendered.
    """
    if not words:
        raise SystemExit("cannot snap a cut against an empty transcript")

    notes = []
    word_starts = [w["start"] for w in words]
    starts = wordlib.sentence_starts(words)
    ends = wordlib.sentence_ends(words)

    candidates = [t for t in starts if abs(t - start) <= reach]
    if candidates:
        s = min(candidates, key=lambda t: abs(t - start))
        notes.append("start snapped to sentence start %.2f" % s)
    else:
        s = min(word_starts, key=lambda t: abs(t - start))
        notes.append("start snapped to WORD start %.2f (no sentence start within %.0fs)"
                     % (s, reach))

    candidates = [t for t in ends if abs(t - end) <= reach and t > s + min_len]
    if candidates:
        e = min(candidates, key=lambda t: abs(t - end))
        notes.append("end snapped to sentence end %.2f" % e)
    else:
        tails = [w["end"] for w in words if w["end"] > s + min_len]
        if not tails:
            raise SystemExit(
                "no word ends more than %.1fs after %.2f - the requested window is shorter "
                "than the shortest thing that could be cut here" % (min_len, s))
        e = min(tails, key=lambda t: abs(t - end))
        notes.append("end snapped to WORD end %.2f (no sentence end within %.0fs)" % (e, reach))

    # Pad into silence only, never across a neighbouring word. A transcriber stretches a word
    # over the pause that follows it, so a neighbour can "end" after our first word starts;
    # the clamp below can therefore never move INTO our own words.
    first = next(i for i, w in enumerate(words) if w["start"] >= s - 0.01)
    last = max(i for i, w in enumerate(words) if w["end"] <= e + 0.01)
    previous_end = words[first - 1]["end"] if first > 0 else 0.0
    next_start = words[last + 1]["start"] if last + 1 < len(words) else e + 10

    s_word, e_word = s, e
    s = max(0.0, s_word - pad, previous_end + 0.05) if first > 0 else max(0.0, s_word - pad)
    s = min(s, s_word)
    e = max(e_word, min(e_word + pad, next_start - 0.05))

    if max_len and e - s > max_len:
        notes.append("window %.1fs exceeds the %.0fs limit for this target after snapping"
                     % (e - s, max_len))
    return round(s, 2), round(e, 2), notes


def snapped_to_sentence(notes):
    """True when BOTH edges landed on a sentence boundary. A cut where either edge fell back
    to a word boundary is weaker and the QA gate grades it separately."""
    return not any("WORD" in note for note in notes)


def find_phrase(words, phrase):
    """The window covering `phrase`, matched against the spoken words.

    Punctuation and case are ignored on both sides, because a person naming a sentence to keep
    types what they heard, not what the transcriber wrote. Returns (start, end) of the matched
    words, before snapping - the caller snaps.
    """
    import re

    def bare(token):
        return re.sub(r"[^a-z0-9]", "", token.lower())

    needle = [bare(t) for t in phrase.split() if bare(t)]
    if not needle:
        raise SystemExit("nothing to look for in %r" % phrase)
    haystack = [bare(w["word"]) for w in words]

    for i in range(len(haystack) - len(needle) + 1):
        if haystack[i:i + len(needle)] == needle:
            return words[i]["start"], words[i + len(needle) - 1]["end"]
    raise SystemExit(
        "the phrase %r is not in this take. Read the transcript with 'ccvideo transcript' and "
        "quote it as the speaker said it - this cuts what was SAID, never what was meant."
        % phrase)
