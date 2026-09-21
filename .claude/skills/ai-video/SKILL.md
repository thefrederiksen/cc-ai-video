---
name: ai-video
description: Make AI videos from a script, and cut real recorded footage together, with the ccvideo CLI. Narrated tutorials and walkthroughs, vertical shorts, cutting takes by text on real word boundaries, captions, and a QA gate that must pass before anything ships. Triggers on "/ai-video", "make a video", "narrated video", "tutorial video", "cut this recording", "edit my footage", "make a short from this", "clip this video", "add captions", "check this video".
---

# ai-video

You drive `ccvideo`. It makes videos two ways, and knowing which one you are doing changes
everything about how you work.

**GENERATE** makes an AI video from a script you write. It is synthetic and we say so - do not
try to make it sound human, and do not spend effort on that. Judge it on whether it is clear,
correct, and cheap to change.

**EDIT** cuts real footage the owner recorded. Judge it on speed and on not ruining good takes.

## Running it

```
ccvideo --help
```

If `ccvideo` is not found, **use `python -m ccvideo` instead** - same tool, and it always
works because it goes through the interpreter that installed the package. The console script
lands in a Scripts directory that is not always on PATH: a per-user install puts it somewhere
PATH does not cover, and a shell that was already open will not see a PATH change made after
it started. Neither means the install is broken.

Do not guess at flags; read the help.

`ffmpeg` and `ffprobe` must be on PATH. If they are not, say so and stop - there is no path
through this tool without them.

## GENERATE: script, lint, render, gate

```
ccvideo lint     --script s.txt --shots frames/ --brand <brand>
ccvideo tutorial --script s.txt --shots frames/ --out out.mp4 --brand <brand> [--size phone]
ccvideo qa       out.mp4 --target youtube --script s.txt --brand <brand>
```

**Lint before you render, and read what it says.** It costs nothing and it runs before a
single second of voice is bought.

**Estimate before you commit.** `--estimate` prints the projected runtime and stops. Narrating
a written page verbatim once produced a 41-minute video that was rejected outright. Check the
number is the length that was asked for.

**The audio cache is the reason this is cheap.** Each segment is cached against its exact
spoken text plus a fingerprint of the voice. Editing one paragraph re-synthesises one
paragraph. So: iterate freely on wording, but NEVER change the brand's narration
`instructions`, the voice or the provider casually - that misses every cached segment and
re-buys the whole video. On a long script that is thousands of words of speech.

`--no-tts` renders from cache only and fails if anything is missing. Use it whenever you are
testing the assembly rather than the writing.

### The three segment kinds

`IMAGE: <file>` a real frame. `IMAGE: CARD` a card for the viewer. `IMAGE: SHOT` a placeholder
for footage nobody has filmed yet, carrying a `SHOT:` line describing what to capture.

Use `SHOT` for anything you are standing in for. Do not fake it with a `CARD` - a walkthrough
once shipped 53 percent placeholder cards because nothing could tell the two apart. Pass
`--publish` on any render meant for an audience; it refuses to contain a `SHOT`.

### Cards

The title-card cap is advice, not a law. If a deck-style video genuinely wants twelve cards,
raise `--card-advice` and say why. Do not work around it by generating cards outside the tool.

## EDIT: import, read, cut, crop, render

```
ccvideo import       <recording-dir | video file> --project P
ccvideo transcript   --project P
ccvideo cut          --project P --keep "..." --keep "..."
ccvideo crop         --project P --crop x,y,w,h
ccvideo trim-silence --project P --over 0.9
ccvideo timeline     --project P
ccvideo render       --project P --out out.mp4 --target shorts
ccvideo render       --project P --out short.mp4 --target shorts \
                     --hook "Six agents.|One person." --brand <brand> --footer example.com
```

### The hook IS the thumbnail

A platform takes a Short's FIRST FRAME as its thumbnail, so `--hook` is not decoration on top
of the video - it is the whole of what somebody sees before deciding whether to stop scrolling.
One or two lines, burned from frame one, with the footage inset below it and the captions in
their own strip rather than over the picture.

Write it as a claim somebody would stop for, keep each line to about 34 characters, and let the
tool refuse anything that will not read at browse size rather than shrinking it to fit.

**READ THE TRANSCRIPT BEFORE YOU CUT.** Picking which moments are worth keeping is your job
and no algorithm does it well. The tool's job is to make the cut land cleanly once you have
chosen.

**Cut by text.** `--keep` takes words the speaker actually said. Quote them as the transcript
has them. A phrase that is not there is an error rather than a nearest guess, which is
deliberate - a silently wrong cut is worse than a stop.

**Be hard about boundaries.** Open on a complete sentence that is a real hook, never on the
dangling tail of the previous topic. End on a sentence that RESOLVES the idea, never on a
fragment or the lead-in to the next topic. Be willing to end earlier to land clean: a tight
thirty seconds that resolves beats thirty-six that dribbles on.

**Crop, or nobody can read it.** A 1920x1080 capture in a 1080x1920 frame is a 607-pixel band
and its interface text is illegible on a phone. The renderer prints a blow-up factor per clip
and warns under x0.80. When it does, look at a frame, decide which panel carries the meaning,
and crop to it. The tool will not choose for you and should not.

**Check the timeline before rendering.** `ccvideo timeline` shows exactly what will be cut and
what each clip says.

## ILLUSTRATE: pictures over a human narration

For a long narration a person recorded (voice only). Stitch the takes with
`ccvideo render --project P --target narration --out n.wav`, transcribe it, then draw pictures
that arrive ON the words:

```
ccvideo illustrate --audio n.wav --words n.words.json --scenes scenes.json \
                   --out video.mp4 --start 0 --end <seconds> --workers 20
cc-secrets run deepinfra-api-key -- ccvideo image --prompt "..." --out img/x.png
```

A scene list is JSON: each scene and each element has an `at` that is a phrase the speaker
says, quoted as the transcript has it (hyphenated words are split: "British born"). Anchors
search forward from the scene start; a phrase that is not there stops the build. Elements:
text, box, flow, bars, counter, ruler, stack, strike, chat, person, paper, image.

* Generated images are for places and moods only. A real person gets a `person` card - never a
  generated face.
* Render a still of every scene before the full render, and LOOK at it. Overlaps and wrapped
  lines are only visible there.
* `--workers` splits the frames across processes; each encoder gets its share of the cores. A
  28-minute video takes about 30 minutes on 24 cores.

## The gate - nothing ships without it

```
ccvideo qa <file> --target <target> --script <script> --brand <brand>
ccvideo sheet <files...> --out sheet.png
```

* A `SKIP` is not a pass. If SPEECH skipped, the file is NOT cleared - say so plainly rather
  than reporting green.
* **The hear-back is the check that matters.** Frames prove text was drawn and prove nothing
  about the audio being present, correct or in sync. A desynced video shipped once having been
  frame-checked.
* Never skip the hear-back on anything headed for an audience. `--no-speech` is for a fast
  local loop only.

**TAIL is separate from the percentage, and it has to be.** The hear-back scores the whole
file, so one lost word out of ninety passes any threshold comfortably. A short shipped ending
on "the most lines of" - the word "code" was in the source, the cut landed on a correct word
boundary, and a fade-out longer than that 0.24 second word deleted it. THE FADE IS PART OF
THE CUT.

TAIL decides on the TRAILING SILENCE, from the audio: at least a fade of silence at the end
means the fade lay in silence and cannot have taken anything. It makes no claim about which
word survived, because measured against 66 finished shorts it cannot - a transcriber returned
"habit" for "hacker", "process" for "project", "write" for "respond", and one product name in
place of another, in endings where the word was plainly audible. Pass `--fade` to match the
renderer that made the file. A WARN means listen to the last second; only a silent ending
fails.

**Then LOOK at it.** No check in this tool reads what a card SAYS. Open a contact sheet for
many frames; open a single frame at FULL SIZE when checking one thing. A thumbnail has twice
hidden a real defect in this work.

## Rules

* **ASCII only** in scripts, cards, titles, descriptions and anything you write.
* **No fallbacks.** If the tool stops, fix the cause. Do not work around it.
* **Never upload.** This tool makes files. Publishing is a separate tool and a separate
  decision, and the human makes the final irreversible click.
* If a render is for an audience, pass `--publish` and let it refuse what it should refuse.
