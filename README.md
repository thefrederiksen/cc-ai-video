# cc-ai-video

Make AI videos, and cut real footage together. One Python library, one CLI, one Claude Code
skill.

The split between the two halves is a product decision, not a technical one.

**GENERATE** makes **AI videos**. They are synthetic and there is no attempt to hide it: no
manufactured breath, no fake hesitation, no effort spent trying to pass as a person. The goal
is a video that is clear, correct, fast to produce and cheap to change. A script and a folder
of frames go in; a narrated, chaptered mp4 comes out.

**EDIT** strings **real recorded footage** together. It is saving the cost of an editor and
moving faster, and nobody needs to care who did it. Every cut it makes lands on a real word
boundary.

Both halves finish at the same QA gate.

## Install

```
pip install cc-ai-video
```

Needs `ffmpeg` and `ffprobe` on PATH. Windows fonts are used for cards; on other platforms
point the library at your own.

## Generate a narrated video

```
ccvideo lint     --script script.txt --shots frames/ --brand devthrottle
ccvideo tutorial --script script.txt --shots frames/ --out out.mp4 --brand devthrottle
ccvideo qa       out.mp4 --target youtube --script script.txt
```

A script is one picture plus one block of spoken text, repeated:

```
# TITLE: the name embedded in the mp4
# FOOTER: the line along the bottom of every card

=== SEG 001
IMAGE: CARD
CARD: kicker|A title|A subtitle
CHAPTER: Opening
TEXT:
The words the narrator says.

=== SEG 002
IMAGE: a-real-screenshot.png
TEXT:
More words.
```

### The audio cache is why this is cheap to iterate

Each segment's narration is cached on disk, keyed on the exact spoken text **plus a
fingerprint of the voice**. Edit one paragraph and one paragraph is re-synthesised. Change the
provider, the model, the voice or the brand's narration direction and the whole cache misses
**on purpose**, because a video narrated half in one voice and half in another must never be
able to ship.

That key is a compatibility surface. A project with a warm cache re-renders free, instantly,
and **byte for byte identical** - which is what makes it possible to prove that a change to
this library changed nothing. There is a frozen test guarding it.

### Three kinds of segment, and the third is the point

| `IMAGE:` | what it is |
|---|---|
| `a-frame.png` | a real captured frame |
| `CARD` | a title card drawn for the **viewer** |
| `SHOT` | a placeholder for footage not yet captured, drawn for the **person holding the camera**, carrying a `SHOT:` line saying what to film |

A twenty-minute walkthrough was once assembled at 53 percent production placeholders - cards
reading "SHOT TO CAPTURE: ...", two of them carrying internal notes - and it rendered clean and
passed every machine check, because nothing in the pipeline could tell a card meant for a
viewer from a card meant for the crew. A human reading a contact sheet caught it.

Now the machine can tell. A shot card is its own kind, it is always reported, and
`--publish` refuses to render one.

## Cut real footage together

```
ccvideo import     recording-dir-or-video.mp4 --project my-edit
ccvideo transcript --project my-edit                       # read it before cutting
ccvideo cut        --project my-edit --keep "the sentence you want to keep"
ccvideo crop       --project my-edit --crop 400,130,1000,560
ccvideo trim-silence --project my-edit --over 0.9
ccvideo timeline   --project my-edit
ccvideo render     --project my-edit --out short.mp4 --target shorts
```

`import` takes a screen-recorder directory (a `recording.mp4` beside its artifacts) or any
plain video file, so a phone clip or a downloaded talk needs no special handling. **The
footage is never copied into the project.** A take records where its video lives, and nothing
more.

The recorder's own transcript is never used for timings. Those are segment-level, and one
opened while this library was designed carried 0.0 to 0.0 on every segment and the wrong
language entirely - which would have produced a timeline of zero-length cuts that looked
perfectly fine in the JSON. Word timings are always derived here, from the audio.

### Cut by text, not by timestamp

`--keep` matches words the speaker actually said, then snaps the window onto real sentence
boundaries. A phrase that is not in the take is an error, never a nearest guess: silently
cutting the wrong sentence is worse than stopping.

Of the first fifty cuts made from hand-read times without this, **forty-four opened or closed
inside a word**. Nobody notices in the numbers. Everybody notices in the finished file.

### Crop, or nobody can read it

A 1920x1080 screen capture fitted whole into a 1080x1920 phone frame is a band 607 pixels
tall, and interface text in it cannot be read at arm's length. Crop to the panel that matters
and that region is scaled **up** instead. The renderer measures the blow-up for every clip and
tells you which are too small - it never guesses a crop for you, because which part of a
screen matters is a judgment about the content.

## The QA gate

```
ccvideo qa out.mp4 --target shorts --script script.txt --brand devthrottle
ccvideo sheet out*.mp4 --out sheet.png
```

Every check is a **presence the file must show**, never an absence. A file that cannot be
checked FAILS, and a check that did not run reports `SKIP` - never `PASS`.

**The hear-back is the check that matters.** It transcribes the finished mp4 and asks how much
of what should have been said comes back out of it. Pulling frames out of a render proves text
was drawn and proves nothing at all about whether the audio is present, correct, or in sync -
a desynced video shipped once having been frame-checked and passed.

`ccvideo sheet` builds a labelled contact sheet, because a card is rendered pixels and silent
and no automated check in this package reads what one says. Every tile carries its file and
timestamp, and a short grid is padded with black rather than a repeat of the last frame - two
identical tiles on a contact sheet are indistinguishable from a real defect.

## Configuration

A brand is a palette, the name on a card footer, how the narrator is told to read, and the
words that brand will not say. Pass `--brands your-brands.json` to add or replace one.

Word rules are **configuration, and chosen per script**. A rival's name in a video positioning
against it is a rule worth having; the same name in a tutorial listing which tools the product
supports is the product's own feature list. `--words-policy` picks which applies.

API keys are read at the moment of use, from the environment or a `.env`-shaped file named by
`CCVIDEO_CREDENTIALS`. No key is ever stored, printed, or written into an output.

## Design rules

* **No fallbacks.** A missing file, font, key, crop or transcript stops the run and says how to
  fix it. Nothing degrades quietly.
* **Never cut on an estimate.** Every cut lands on a word boundary from a real transcript.
* **ASCII only** in all code, comments, output and documentation.
* **Look at the frames.** Full size when checking one thing; a contact sheet when reading many.

## Contributing to this repository

It is public and must stay public, so **run the safety scan before every commit**:

```
python tools/scan_public.py
python -m pytest tests -q
```

The scan refuses footage, credentials, personal details, internal paths and machine names,
assistant attribution, and any non-ASCII byte. It exits non-zero on any finding, and it exits
non-zero when its site-specific denylist is missing - because an unchecked run is a broken
instrument, not a clean result. Create `.denylist` (gitignored) or point `CCVIDEO_DENYLIST` at
one.

A finding is fixed by removing the thing, not by widening a pattern.

## Licence

MIT. See LICENSE.
