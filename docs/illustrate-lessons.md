# Illustrated narration - what we learned making the first one

Written after "How We Got to ChatGPT" version 1 (2026-09-21): 28 minutes, Soren's own voice,
87 word-anchored scenes, rendered with `ccvideo illustrate`. Every lesson here cost time once.
Read it before starting the next one.

## The pipeline, in order

```
1. record         the human reads the script, chapter by chapter (AgentEyes, audio mode)
2. stitch         ccvideo import / cut / render --target narration   -> narration.wav + .srt
3. hear-back      ccvideo qa narration.wav --script script.txt       (speech must come back out)
4. words          ccvideo transcribe                                  -> words.json (the anchors)
5. pictures       ccvideo photo search / fetch    (real photographs, credited)
5b. scenes        a builder script writes scenes.json
6. anchors        every `at` must resolve - fix them all in one pass, not one per run
7. stills         draw the last frame of every scene, as contact sheets, and LOOK at them
8. check          ccvideo illustrate-check ... --fail-under <n>
9. render         ccvideo illustrate ... --workers 12                 (capped at half the cores)
10. score         ccvideo score video.mp4 --json score.json           (30 s)
11. watch         a human watches it. Nothing above judges whether a picture is GOOD.
```

Steps 8 and 10 are the same measurement. 8 draws the frames without encoding them, so a bad
scene list is caught before a render is spent; 10 is the proof on the file itself. On version 1
they agreed to 0.2% (90.2% vs 90.4% clean) and found the same stretches - that agreement is the
control that says the pre-render check can be trusted.

## Empty screens - the defect a viewer notices first

**Version 1 was 90.4% clean: 146 seconds of empty screen in 46 stretches, the worst 8.6 s.**
The owner saw it immediately ("areas where the screen just goes blank for quite a long time").

Cause: a scene starts ON its word, but everything IN it is anchored to later words. Between the
scene's start and its first element, the viewer looks at a bare background. A tiny grey label
("IDEA 04", "02 / THE SWITCH") does not count - the measurement reads it as empty, and so does
the eye.

Rules that follow:

- Something SUBSTANTIAL arrives within half a second of a scene's start - a headline, a card,
  a picture. Anchor it to the scene's own first word.
- Build a scene up from there; never open on nothing and wait for the payoff word.
- Measure it. `ccvideo score` and `illustrate-check` are pixel arithmetic, calibrated by eye:
  under 0.4% busy pixels is EMPTY, under 1% is THIN (one small line). No model decides.

**Version 2 fixed it in two moves, measured before rendering: 90.2% -> 97.7% -> 100% clean.**

1. The library rule (`scenes.py`, `_open_on_content`): **a scene begins when its first
   substantial element arrives**, not on the word it is anchored to. Until then the previous,
   fully built scene stays up - a held picture reads as a pause, a bare background as a fault.
   Text under 48 px is a label and does not count. A list or bar chart counts from its first
   ITEM, not the moment its empty container is anchored (missing that left 20 s unfixed).
   This alone took the empty screen from 146 s to 13 s.
2. By hand, for the last three: a scene whose first real thing is small (a tiny list, one mono
   line) needs a bigger first thing - a large label, or the headline card anchored to the
   scene's own first word.

Iterate with `illustrate-check` (5 minutes), not with renders (32 minutes).

## Pictures

- **Use real photographs: `ccvideo photo search / fetch / credits`.** Wikimedia Commons had a
  free photograph for almost everything in a 28-minute AI history: Walter Pitts at a blackboard
  in 1954, the 1958 Navy press photo announcing the perceptron, Deep Blue itself, the real
  Principia title page, 1930s Chicago, an IBM 704, and 19 of the people. The tool refuses any
  license but public domain, CC0, CC BY and CC BY-SA, and writes each photo's credit beside it;
  the renderer prints that credit on screen under the picture, and `photo credits` lists them
  all for the video description.
- For a PERSON, take the photo on their Wikipedia page first - then check it is on Commons
  (a fair-use photo lives only on Wikipedia and is refused). Plain Commons search is noisy:
  "Donald Hebb" returned Donald Rumsfeld.
- A card photo must show the FACE. A lecture photo with the person small at a podium crops to
  the lectern - use the name card instead (Hopfield, Bender in version 2). Tiny files (under
  ~300 px) look like mistakes; drop them.
- Wide photos go full-screen (`image`); portraits, documents and objects go in a frame at the
  side (`photo`), so they are not blown up or cropped away.
- **Generated backdrops read as generic stock.** The owner's verdict on version 1: "the images
  are generic". FLUX-schnell gives a plausible mood picture of anything, which is exactly why it
  says nothing specific. A picture has to show THE thing being talked about, or not be there.
- Never generate a face of a real person. Real people get a name card (initials tile, name,
  one line) - or a real archive photograph, with its source.
- Text over a photograph needs a scrim: the renderer darkens the left, top and bottom of every
  picture, where the words sit. Grey (muted) text on a photograph cannot be read at all. Without it, heavy white type vanished into bright windows and lamps.
- Captions go bottom-right, away from the text column on the left.

## Anchors

- Quote words AS THE TRANSCRIPT HAS THEM, misspellings included ("Rose and Blatt", "LeCoon",
  "Hupfield", "verbose" for Werbos). The transcript is the clock; the script is not.
- Hyphenated words are split into two tokens: "British-born" is anchored as "British born",
  "GPT-3" as "GPT 3".
- A phrase that appears twice resolves forward from the scene start. Check that every element
  lands INSIDE its own scene - a match in the next scene is a silent wrong answer.
- Run a checker that lists EVERY failing anchor with the transcript text around it. Fixing one
  per run wastes a run per anchor.

## Layout

- Look at a still of every scene before rendering. Overlaps only show there: a headline that
  wraps onto the line below it, a list colliding with its title, a counter running off the edge.
- An underline has to follow a phrase that wraps onto two lines (fixed in the renderer; it used
  to crash drawing a negative-width bar).
- On a text-only scene, keep a right-hand element clear of the left column's widest line.

## Rendering

- **Fonts ship inside the package** (`src/ccvideo/fonts`, SIL Open Font License): Inter Black
  and Inter SemiBold, and Cascadia Mono. Version 1 used Windows' Segoe UI, which cannot be
  copied to another machine and does not exist on a Mac, so the Mac Mini could not render at
  all. Now a video renders to the same pixels on any machine. Inter is a little wider than
  Segoe: re-check stills for wrapped lines after changing fonts.
- A bright photograph (a white facade) can be darkened further with `"dim": 0.6` on the image.
- **The library never takes the whole machine** (`budget.py`). It runs on the owner's own
  workstation next to his work and a fleet of other sessions; a 22-worker render took every
  core and ~30 GB and halted the machine until it was killed ("you halted the machine
  completely"). Now, whatever a caller asks for: worker processes are capped at HALF the
  logical cores, every encoder's threads come out of that same half, every ffmpeg the library
  runs gets `-threads` for that half, and the process drops to below-normal priority before it
  starts anything, so its workers and every ffmpeg inherit it. A test measures the priority on
  a real child and grandchild process - the first version of the Windows call silently did
  nothing (a 64-bit handle cut to 32 bits), and only that measurement caught it.
- 22 x264 encoders each took threads for the whole machine and ran a 64 GB box out of memory;
  some runs died a minute in. Each encoder now gets its share of the cores.
- `Pool.imap` in order hid those failures until every run ahead finished - 20 minutes of a dead
  render. Runs are now collected unordered and a failed frame stops the render at once, naming
  the frame.
- ffmpeg's concat list resolves relative names against the LIST's folder. Parts are named
  absolutely.
- A long render outlives a 10-minute tool call. Run it, then wait for it in the foreground on its
  output; never detach it.

## The voice

- The voice is always the human's. AI images are acceptable; an AI voice is not.
- Never put words in the narrator's mouth. A line invented for his personal story ("I only
  learned this putting the story together") made him sound like he did not know his own field.
  Flag every line you write inside someone's own story.
- AgentEyes recorded at 16 kHz mono (built for transcription). Fine to publish, but record at
  48 kHz next time.

## Open for the next version

- Specific pictures where Commons had none: Warren McCulloch, Donald Hebb, Ilya Sutskever (only
  a 192 px file), the Mark I Perceptron machine itself.
- The score measures emptiness and movement, not quality. Whether a picture is the RIGHT one is
  still a human watching.
