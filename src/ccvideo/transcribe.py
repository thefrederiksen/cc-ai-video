"""Word-timed transcripts. Every cut this library makes is made on one of these.

There is no estimate-based path anywhere in this package, and that is on purpose: hand-read
times are never exact. Of the first fifty cuts made from a hand-timed segment list, forty-four
opened or closed inside a word. A cut through a word is the worst kind of cut, and nobody
notices in the timings - only in the finished file.

The output is cached beside the media (or in `out_dir`) as `<stem>.words.json`, plus a
readable `<stem>.txt` for a human or an agent to read. Transcribing is the slow step, so it is
done once per file and never repeated unless asked.
"""

import json
from pathlib import Path

from .shell import probe

MODEL_CACHE = {}


def _model(name):
    """One model instance per name per process. Loading it takes seconds and a batch of takes
    would otherwise pay that cost for every file."""
    if name not in MODEL_CACHE:
        from faster_whisper import WhisperModel
        MODEL_CACHE[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return MODEL_CACHE[name]


def transcribe_file(path, model_name="base.en", language="en"):
    """Word-timed segments for one media file. Returns the segment list."""
    model = _model(model_name)
    segments, _ = model.transcribe(str(path), language=language, word_timestamps=True,
                                   beam_size=5, vad_filter=True)
    out = []
    for s in segments:
        words = [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word}
                 for w in (s.words or [])]
        out.append({"start": round(s.start, 3), "end": round(s.end, 3),
                    "text": s.text.strip(), "words": words})
    if not out:
        raise SystemExit(
            "no speech was found in %s. A transcript with no words cannot be cut on word "
            "boundaries, so this stops here rather than producing an empty timeline." % path)
    return out


def words_path(media, out_dir=None):
    media = Path(media)
    directory = Path(out_dir) if out_dir else media.parent
    return directory / (media.stem + ".words.json")


def transcribe(media, out_dir=None, model_name="base.en", language="en", force=False):
    """Transcribe `media` to a words file, cached. Returns the words file path."""
    media = Path(media)
    if not media.exists():
        raise SystemExit("no such media file: %s" % media)
    out = words_path(media, out_dir)
    if out.exists() and not force:
        return out

    out.parent.mkdir(parents=True, exist_ok=True)
    print("transcribing %s ..." % media.name, flush=True)
    segments = transcribe_file(media, model_name, language)
    document = {"source": str(media), "model": model_name, "language": language,
                "duration": probe(media)["duration"], "segments": segments}
    out.write_text(json.dumps(document, indent=1), encoding="utf-8")

    readable = out.with_suffix("").with_suffix(".txt") if out.suffix == ".json" else None
    readable = out.parent / (media.stem + ".txt")
    with readable.open("w", encoding="utf-8") as fh:
        for s in segments:
            fh.write("[%7.1f -%7.1f] %s\n" % (s["start"], s["end"], s["text"]))
    words = sum(len(s["words"]) for s in segments)
    print("  %d segments, %d words, %.0fs -> %s"
          % (len(segments), words, document["duration"], out.name), flush=True)
    return out


def hear(media, model_name="base.en", language="en"):
    """What a finished file SAYS, as one string. Used by the hear-back gate, which is the
    check that actually proves a render carries its own audio."""
    return " ".join(s["text"] for s in transcribe_file(media, model_name, language)).strip()
