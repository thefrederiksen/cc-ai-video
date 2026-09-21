"""Voice takes with no picture, and the narration target they render to."""

import pytest

from ccvideo import targets
from ccvideo.edit.project import resolve_source


def test_an_audio_only_recording_directory_imports_its_audio(tmp_path):
    (tmp_path / "audio.wav").write_bytes(b"")
    (tmp_path / "transcript.json").write_text("[]")
    media, kind = resolve_source(tmp_path)
    assert media == tmp_path / "audio.wav"
    assert kind == "recording-dir"


def test_a_recording_directory_prefers_its_video_over_its_audio(tmp_path):
    (tmp_path / "recording.mp4").write_bytes(b"")
    (tmp_path / "audio.wav").write_bytes(b"")
    media, _ = resolve_source(tmp_path)
    assert media.name == "recording.mp4"


@pytest.mark.parametrize("name", ["take.wav", "take.mp3", "take.m4a", "take.flac"])
def test_a_plain_audio_file_imports(tmp_path, name):
    (tmp_path / name).write_bytes(b"")
    media, kind = resolve_source(tmp_path / name)
    assert media.name == name and kind == "file"


def test_a_file_that_is_neither_video_nor_audio_is_refused(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(SystemExit):
        resolve_source(tmp_path / "notes.txt")


def test_a_directory_with_neither_video_nor_audio_is_refused(tmp_path):
    (tmp_path / "transcript.json").write_text("[]")
    with pytest.raises(SystemExit):
        resolve_source(tmp_path)


def test_the_narration_target_has_no_picture_and_a_voice_profile():
    row = targets.target("narration")
    assert row["audio_only"] is True
    assert (row["width"], row["height"]) == (0, 0)
    assert row["encode"]["sample_rate"] == "48000"
    assert row["encode"]["channels"] == "1"


def test_video_targets_are_not_audio_only():
    for name in ("youtube", "shorts", "linkedin", "tutorial-desktop", "tutorial-phone"):
        assert targets.target(name)["audio_only"] is False
