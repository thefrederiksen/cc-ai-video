# Porting: what has moved, what has not, and how each move is proven

This library replaces a set of tools that grew separately and drifted. Nothing is ripped out.
Each pipeline moves one at a time, the old entry point keeps working, and a move is only
finished when its output has been proven identical.

## The bar for "proven"

Better than the bar that was set. The assembler is deterministic: given the same script, the
same frames and a warm voice cache, it produces a **byte-identical** file. So a port is proven
by rendering the same input through the new library and comparing checksums, not by comparing
durations and looking at a frame.

Two proofs done, both against files that had already shipped:

| Input | Shape | Result |
|---|---|---|
| A 3-segment vertical short (one generated card, two real frames) | 1080x1920, 37.4s | byte-identical, md5 matched |
| A 62-segment desktop walkthrough with 25 chapters | 1920x1080, 1200.9s, 42 MB | byte-identical, md5 matched, per-segment timings table identical |

The second is a published video, so the bar there was "indistinguishable from what shipped".
It is the same file.

Both were rendered with `--no-tts`, so the voice cache was reused and nothing was billed. That
is the point of the cache key being a compatibility surface, and there is a frozen test
guarding it.

## Order, and why

Driven by risk, not by size.

1. **The editor.** New, nothing depends on it, cannot break anything, and it is the half that
   was actually asked for. It also forces the shared primitives - word timings, snapping,
   targets, the ffmpeg plumbing - into existence against a real user rather than a guess.
   **Done.**
2. **The assembler** (narrated tutorials and walkthroughs). On no timer, and its per-segment
   audio cache makes the proof free. **Done, byte-identical on two files.**
3. **Creator shorts.** On no timer. The two live scheduled tasks around them are YouTube
   housekeeping that runs after upload and never touches a renderer. Proof: re-render an
   existing short from its own cut list and compare to the file on disk.
4. **The motivation shorts, last.** Seven videos a week, a live weekly schedule, uploads and
   scheduling downstream. Port it, leave the existing entry point in place calling into this
   library, and prove it by re-rendering an already-published file and running the original QA
   script against old and new.

## Kept, and where it went

| Idea | Where it came from | Where it is now |
|---|---|---|
| Cut only on real word boundaries | a shorts factory `snap.py` | `edit/snap.py`, and nothing in the library cuts any other way |
| Per-segment voice cache keyed on text plus voice | the narrated assembler | `narrate.py`, key frozen by test |
| Caption cues: a few words, one emphasised | three separate renderers | `cues.py`, as three named styles |
| Plate and card compositing | two renderers | `draw.py` |
| Levelling each piece BEFORE the mix | the motivation renderer | `edit/render.py` |
| A QA gate that exits non-zero | two QA scripts | `qa/run.py` |
| Transcribe the finished file and match it to its script | a hear-back script | `qa/run.py`, the SPEECH check |
| Contact sheets so frames get read | a review script | `qa/sheet.py`, now labelled |
| Generated captions rather than the platform's | an SRT script | `subtitles.py` |

## Changed on purpose

**The title-card cap is advice, not a refusal.** The hard limit was right for a tutorial and
wrong for a deck-style video, and it was worked around by generating cards outside the tool -
worse than the thing it prevented. It is now a lint rule with a number the caller sets.

**A production placeholder is its own segment kind.** Cards written for the person holding the
camera are now `IMAGE: SHOT`, are always reported, and are refused by `--publish`. Previously
nothing in the pipeline could tell them from a viewer's card, and a walkthrough was assembled
at 53 percent placeholders and passed every machine check.

**Banned words are configuration, chosen per script.** The first run of the gate against a
shipped video failed it for saying a rival's name - in a sentence listing the agents the
product supports. That is the product's own feature list. `--words-policy` picks which set
applies.

**A skipped check reports SKIP, never PASS.** A skipped check reported as a pass certifies
something nobody looked at, and reads as green in a summary line.

**Narration timings are never re-transcribed from our own audio.** One pipeline synthesised
narration and then ran the mp3 back through a transcriber to find the words. It mishears: a
caption read "NOBODY HE BUILDS SOMETHING" where the narrator said "nobody who builds something
new", and a whole QA check had to be added to catch words the narrator never said. We always
know the text of our own narration, so it is never guessed back out of the audio.

**Narration chunks are never joined with a stream copy.** Encoder padding accumulates at every
seam and captions drift further the longer the video runs. The join re-encodes and asserts the
result against the sum of its parts.

## Two defects found and fixed on the way

**A sentence-opener list was ending sentences mid-clause.** Half the useful openers - "I",
"It", "We", "The", "That" - are also ordinary mid-sentence words, and a transcriber
capitalises the pronoun "I" wherever it falls. So "What am I working on here" contained a
sentence boundary after "am", and a cut asked to end on the previous sentence ended on the
words "What am" instead. A boundary now needs a measurable breath as well as a capital.
Measured on real footage; the timings are in the test.

**A three-second floor was swallowing the sentence after the one requested.** It came from a
Shorts renderer where a clip must run at least three seconds, and it applied to the snap
itself, so a cut named by two seconds of words absorbed the next sentence to reach the floor.
A target's limits belong to the target and are enforced when the timeline is rendered.

## Deliberately left out

**Publishing.** Uploading, pinning, commenting, scheduling and channel statistics are not video
tooling. They carry a channel's credentials and its business rules, and a public library has no
business holding either. This library makes files and never uploads one.

**A superseded animation layer.** Around 1,600 lines of animated interface and conversation
generators from an earlier generation, replaced by the factory that came after. Left alone
rather than carried forward; worth a separate decision.

**A fourth renderer.** One tool's remaining value was two ideas - assert a voice provider's
alignment reconstructs exactly the text that was sent, and emit sample frames alongside what is
spoken at each so a person has to look. Both ideas are in the library. Carrying the renderer
to keep them would have defeated the point.

## A check that could not fail

Found while auditing the batch this library's boundary fix affected, and it is the most
important thing in this document.

The old `qa_short.py` imported `is_boundary` and `snap` **from the same module that produced
the cut**. So its BOUNDS check asked whether a cut matched the definition that had just made
it. The answer is yes by construction. A defect in the boundary rule was invisible to that
check, and its "0 FAIL" was not evidence of anything.

Re-grading the same 66 cuts with the corrected rule, against the same transcripts, and
re-deriving the window the renderer actually used: **16 of them end inside continuous speech**,
every one with a gap of 0.000 seconds at the cut. All 66 had been cleared.

Two things follow, and both are in the library.

**A check must not import its oracle from the thing it checks.** `clean_edge` in
`edit/project.py` shares no code with the boundary rule. It asks the audio and the word
timings whether there is a real pause where the cut lands, and it has a test asserting that
`is_boundary` appears nowhere in it. Words choose the boundary; audio confirms it; a
disagreement is reported rather than quietly resolved in favour of either.

**A check that did not run says SKIP, never PASS.** Same failure mode, different dress: a
green line that certifies nothing anybody looked at.
