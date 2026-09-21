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
5. scenes         a builder script writes scenes.json
6. anchors        every `at` must resolve - fix them all in one pass, not one per run
7. stills         draw the last frame of every scene, as contact sheets, and LOOK at them
8. check          ccvideo illustrate-check ... --fail-under <n>       (5 min for 28 min)
9. render         ccvideo illustrate ... --workers 22                 (32 min for 28 min)
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

## Pictures

- **Generated backdrops read as generic stock.** The owner's verdict on version 1: "the images
  are generic". FLUX-schnell gives a plausible mood picture of anything, which is exactly why it
  says nothing specific. A picture has to show THE thing being talked about, or not be there.
- Never generate a face of a real person. Real people get a name card (initials tile, name,
  one line) - or a real archive photograph, with its source.
- Text over a photograph needs a scrim: the renderer darkens the left and top of every picture,
  where the words sit. Without it, heavy white type vanished into bright windows and lamps.
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

- Replace generic backdrops with specific pictures: archive photographs of the real places,
  machines and papers, with credits - or drop the picture and draw the idea instead.
- Fix every empty stretch the score lists, then re-score. Target: no EMPTY stretch of 1 s or more.
