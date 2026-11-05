"""Recorded clips under a narration: the fit rules, the render, and the caption timing.

The narration is the master. A short clip HOLDS its last frame, a long one plays FASTER up to
a cap, and past the cap its END is trimmed - and each of these is asserted as something that
happened (a held frame, a speed, a trimmed length), not as the absence of an error.
"""

import subprocess

import numpy as np
import pytest

from ccvideo import targets
from ccvideo.generate import script as scriptlib
from ccvideo.generate.assemble import (LEAD_IN, TAIL, picture_box, render_clip_segment,
                                       render_segment)
from ccvideo.generate.fit import fit
from ccvideo.generate.timing import align, find_audio, speech_window
from ccvideo.shell import probe

BRAND = {"bg": (18, 18, 22)}


# ------------------------------------------------------------------ the fit rules

def test_a_short_clip_plays_at_its_own_speed_then_holds():
    f = fit(4.0, 10.0)
    assert f.speed == 1.0 and f.used == 4.0
    assert abs(f.hold - 6.0) < 1e-9 and f.trimmed == 0.0
    assert abs(f.shown - 10.0) < 1e-9


def test_a_long_clip_plays_faster_to_land_on_the_end():
    f = fit(15.0, 10.0)
    assert abs(f.speed - 1.5) < 1e-9
    assert f.used == 15.0 and f.hold == 0.0 and f.trimmed == 0.0
    assert abs(f.shown - 10.0) < 1e-9


def test_past_the_cap_the_end_is_trimmed_and_the_narration_is_not():
    f = fit(30.0, 10.0, max_speed=2.0)
    assert f.speed == 2.0
    assert abs(f.used - 20.0) < 1e-9 and abs(f.trimmed - 10.0) < 1e-9
    assert abs(f.shown - 10.0) < 1e-9          # the segment keeps its full length


def test_the_cap_is_a_flag():
    assert abs(fit(30.0, 10.0, max_speed=3.0).speed - 3.0) < 1e-9
    assert fit(30.0, 10.0, max_speed=3.0).trimmed == 0.0


def test_clip_start_and_end_pick_the_part_that_matters():
    f = fit(15.5, 10.0, clip_start=2.0, clip_end=8.0)
    assert f.start == 2.0 and f.used == 6.0 and f.speed == 1.0 and abs(f.hold - 4.0) < 1e-9


def test_a_clip_is_never_slowed_down():
    with pytest.raises(SystemExit):
        fit(5.0, 10.0, max_speed=0.5)


# ------------------------------------------------------------------ the script

def _script(tmp_path, body):
    path = tmp_path / "s.txt"
    path.write_text("# TITLE: t\n# FOOTER: f\n" + body, encoding="utf-8")
    return scriptlib.parse(path)


def test_a_still_is_still_a_still_and_a_video_is_a_clip(tmp_path):
    doc = _script(tmp_path, "=== SEG 001\nIMAGE: a.png\nTEXT:\nOne.\n"
                            "=== SEG 002\nIMAGE: b.mp4\nCLIP-START: 1.5\nCLIP-END: 9\n"
                            "AUDIO: two.wav\nTEXT:\nTwo.\n")
    first, second = doc.segments
    assert (first.kind, first.image, first.clip_start) == ("image", "a.png", None)
    assert (second.kind, second.image) == ("clip", "b.mp4")
    assert (second.clip_start, second.clip_end, second.audio) == (1.5, 9.0, "two.wav")


def test_lint_refuses_a_clip_range_past_the_end(tmp_path):
    _testsrc(tmp_path / "b.mp4", 2.0)
    doc = _script(tmp_path, "=== SEG 001\nIMAGE: b.mp4\nCLIP-END: 5\nTEXT:\nTwo words.\n")
    from ccvideo import brand as brandlib
    problems = scriptlib.lint(doc, str(tmp_path), brandlib.get("default"))
    assert any("past the end" in msg for _, _, msg in problems)


# ------------------------------------------------------------------ pre-made audio

def test_find_audio_by_segment_id_and_by_audio_line(tmp_path):
    (tmp_path / "001.wav").write_bytes(b"x")
    (tmp_path / "other.mp3").write_bytes(b"x")
    doc = _script(tmp_path, "=== SEG 001\nIMAGE: a.png\nTEXT:\nOne.\n"
                            "=== SEG 002\nIMAGE: a.png\nAUDIO: other.mp3\nTEXT:\nTwo.\n"
                            "=== SEG 003\nIMAGE: a.png\nTEXT:\nThree.\n")
    one, two, three = doc.segments
    assert find_audio(one, tmp_path).name == "001.wav"
    assert find_audio(two, tmp_path).name == "other.mp3"
    with pytest.raises(SystemExit, match="no narration file"):
        find_audio(three, tmp_path)


def test_two_files_for_one_segment_is_a_stop(tmp_path):
    (tmp_path / "001.wav").write_bytes(b"x")
    (tmp_path / "001.mp3").write_bytes(b"x")
    doc = _script(tmp_path, "=== SEG 001\nIMAGE: a.png\nTEXT:\nOne.\n")
    with pytest.raises(SystemExit, match="2 narration files"):
        find_audio(doc.segments[0], tmp_path)


def test_only_the_speech_of_a_padded_file_is_used():
    heard = [{"start": 0.9, "end": 1.2, "word": "Hello"}, {"start": 1.3, "end": 2.0, "word": "there."}]
    start, seconds = speech_window(heard, 4.5)
    assert abs(start - 0.75) < 1e-9 and abs(start + seconds - 2.35) < 1e-9


# ------------------------------------------------------------------ caption alignment

def test_captions_show_the_written_words_at_the_heard_times():
    heard = [{"start": 0.0, "end": 0.4, "word": "Upload"},
             {"start": 0.5, "end": 0.7, "word": "the"},
             {"start": 0.8, "end": 1.2, "word": "twelve"},
             {"start": 1.3, "end": 1.8, "word": "tables."}]
    words = align("Upload the 12 SAP tables.", heard)
    assert [w["word"] for w in words] == ["Upload", "the", "12", "SAP", "tables."]
    assert words[0]["start"] == 0.0 and words[-1]["start"] == 1.3
    # The two the transcriber heard differently sit, in order, inside the gap they were said in.
    assert 0.7 <= words[2]["start"] < words[3]["start"] < words[3]["end"] <= 1.3


def test_the_wrong_file_for_a_segment_is_a_stop():
    heard = [{"start": 0.0, "end": 0.4, "word": "something"},
             {"start": 0.5, "end": 0.9, "word": "else"}]
    with pytest.raises(SystemExit, match="wrong file"):
        align("Upload the twelve SAP tables now", heard, "004")


# ------------------------------------------------------------------ one continuous narration

def _heard(spec):
    """[(word, start, end), ...] as heard words."""
    return [{"word": w, "start": s, "end": e} for w, s, e in spec]


def test_one_recording_is_cut_in_the_pause_between_segments(tmp_path):
    from ccvideo.generate.timing import split_narration
    doc = _script(tmp_path, "=== SEG 001\nIMAGE: a.png\nTEXT:\nOpen the projects page.\n"
                            "=== SEG 002\nIMAGE: a.png\nTEXT:\nThen click New project.\n")
    heard = _heard([("Open", 0.5, 0.8), ("the", 0.85, 0.95), ("projects", 1.0, 1.5),
                    ("page.", 1.55, 1.9),
                    # a short breath inside nothing, then the long pause between sentences
                    ("Then", 3.1, 3.3), ("click", 3.35, 3.6), ("New", 3.65, 3.8),
                    ("project.", 3.85, 4.4)])
    one, two = split_narration(doc.segments, heard, 5.0)
    cut = (1.9 + 3.1) / 2
    assert one[0] + one[1] <= cut <= two[0]                  # the cut is in the pause
    assert one[0] < 0.5 and one[0] + one[1] > 1.9            # each keeps all its own speech
    assert two[0] < 3.1 and two[0] + two[1] > 4.4
    assert [w["word"] for w in one[2]] == ["Open", "the", "projects", "page."]
    assert [w["word"] for w in two[2]][0] == "Then"


def test_a_misheard_word_at_the_seam_still_cuts_in_the_longest_pause(tmp_path):
    from ccvideo.generate.timing import split_narration
    doc = _script(tmp_path, "=== SEG 001\nIMAGE: a.png\nTEXT:\nUpload the 12 tables.\n"
                            "=== SEG 002\nIMAGE: a.png\nTEXT:\nNow check the schema.\n")
    heard = _heard([("Upload", 0.0, 0.4), ("the", 0.45, 0.55), ("twelve", 0.6, 0.9),
                    ("tables.", 0.95, 1.4), ("Now", 2.6, 2.8), ("check", 2.85, 3.1),
                    ("the", 3.15, 3.25), ("schema.", 3.3, 3.9)])
    one, two = split_narration(doc.segments, heard, 4.2)
    assert one[0] + one[1] <= 2.0 <= two[0]


def test_a_segment_whose_text_is_not_in_the_recording_stops_the_build(tmp_path):
    from ccvideo.generate.timing import split_narration
    doc = _script(tmp_path, "=== SEG 001\nIMAGE: a.png\nTEXT:\nOpen the projects page.\n"
                            "=== SEG 002\nIMAGE: a.png\nTEXT:\nSomething never recorded at all.\n")
    heard = _heard([("Open", 0.5, 0.8), ("the", 0.85, 0.95), ("projects", 1.0, 1.5),
                    ("page.", 1.55, 1.9)])
    with pytest.raises(SystemExit, match="segment 002"):
        split_narration(doc.segments, heard, 2.5)


# ------------------------------------------------------------------ the render

TARGET = {"width": 320, "height": 180,
          "encode": dict(targets.profile("stills"), preset="ultrafast")}


def _testsrc(path, seconds, size="320x180"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=%s:rate=30:duration=%.2f" % (size, seconds),
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _tone(path, seconds):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=%.2f" % seconds, str(path)], check=True)
    return path


def _frames(path, w=320, h=180):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w).astype(np.int16)


def _changes(frames):
    """For each frame after the first: did the picture change from the one before it?"""
    return [np.abs(frames[i] - frames[i - 1]).mean() > 0.5 for i in range(1, len(frames))]


def test_a_short_clip_holds_its_last_frame_to_the_end_of_the_narration(tmp_path):
    clip, audio = _testsrc(tmp_path / "c.mp4", 1.0), _tone(tmp_path / "a.wav", 2.0)
    seconds = 2.0 + LEAD_IN + TAIL
    f = fit(probe(clip)["duration"], seconds)
    out = tmp_path / "seg.mp4"
    assert render_clip_segment(clip, f, audio, out, BRAND, TARGET) == seconds
    assert abs(probe(out)["duration"] - seconds) < 0.1
    changes = _changes(_frames(out))
    # It moves for the clip's second, then stands still for every frame after.
    assert all(changes[:25])
    assert not any(changes[32:])


def test_a_long_clip_is_sped_up_and_moves_to_the_last_frame(tmp_path):
    clip, audio = _testsrc(tmp_path / "c.mp4", 4.0), _tone(tmp_path / "a.wav", 0.9)
    seconds = 0.9 + LEAD_IN + TAIL                   # 2.0 s: the 4 s clip plays at x2
    f = fit(probe(clip)["duration"], seconds)
    assert abs(f.speed - 2.0) < 0.01 and f.trimmed == 0.0
    out = tmp_path / "seg.mp4"
    render_clip_segment(clip, f, audio, out, BRAND, TARGET)
    assert abs(probe(out)["duration"] - seconds) < 0.1
    frames = _frames(out)
    assert all(_changes(frames)[-5:])               # still moving at the very end: no hold
    assert np.abs(frames[-1] - _frames(clip)[-1]).mean() < 12   # and ends near the clip's end


def test_a_too_long_clip_is_trimmed_not_the_narration(tmp_path):
    clip, audio = _testsrc(tmp_path / "c.mp4", 8.0), _tone(tmp_path / "a.wav", 0.9)
    seconds = 0.9 + LEAD_IN + TAIL
    f = fit(probe(clip)["duration"], seconds, max_speed=2.0)
    assert f.speed == 2.0 and abs(f.trimmed - 4.0) < 0.01
    out = tmp_path / "seg.mp4"
    render_clip_segment(clip, f, audio, out, BRAND, TARGET)
    assert abs(probe(out)["duration"] - seconds) < 0.1
    assert probe(out)["audio"]


def test_with_captions_the_picture_sits_above_a_strip(tmp_path):
    w, h = picture_box({"width": 1920, "height": 1080}, True)
    assert w == 1920 and h < 1080 and (1080 - h) % 2 == 0
    assert picture_box({"width": 1920, "height": 1080}, False) == (1920, 1080)
    clip, audio = _testsrc(tmp_path / "c.mp4", 1.0), _tone(tmp_path / "a.wav", 0.5)
    box = picture_box(TARGET, True)
    f = fit(1.0, 0.5 + LEAD_IN + TAIL)
    out = tmp_path / "seg.mp4"
    render_clip_segment(clip, f, audio, out, BRAND, TARGET, box=box)
    last = _frames(out)[-1]
    strip = last[box[1]:, :]
    assert strip.std() < 1.0 and abs(strip.mean() - 19) < 4   # the strip is plain background
    assert last[: box[1], :].std() > 10                       # the picture is above it


def test_a_still_renders_the_same_with_no_box_as_before(tmp_path):
    """No captions means no box, and then the still path is the one it always was: the same
    command, so the same bytes."""
    from PIL import Image
    Image.new("RGB", (320, 180), (200, 40, 40)).save(tmp_path / "p.png")
    audio = _tone(tmp_path / "a.wav", 0.5)
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    render_segment(str(tmp_path / "p.png"), audio, 0, a, BRAND, TARGET)
    render_segment(str(tmp_path / "p.png"), audio, 0, b, BRAND, TARGET, window=None, box=(320, 180))
    assert a.read_bytes() == b.read_bytes()
