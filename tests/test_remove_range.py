"""remove-range: a time range out of a finished video, at flat memory (cc-ai-video#28).

The bug this guards against: a hand-written trim/concat on ONE input held every decoded frame
of the second piece in memory and reached 34 GB on an 872 s 1080p film, killing the Director it
ran under (devthrottle_internal#3627). The long test below renders a 10-minute 1080p file
through the real command and measures ffmpeg's peak RSS the way the issue does: the children's
maxrss, which is what /usr/bin/time -v reports.

The long test takes a few minutes. It is skipped only when ffmpeg is missing, never silently:
a skip is printed as one.
"""

import resource
import shutil
import subprocess
import sys

import pytest

from ccvideo import remove_range as rr
from ccvideo.shell import probe

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

ONE_GB_KB = 1024 * 1024


# ------------------------------------------------------------------ the arithmetic

def test_pieces_around_one_range():
    assert rr.kept_pieces([(113.9, 115.0)], 872.0) == [(0.0, 113.9), (115.0, 872.0)]


def test_pieces_around_several_ranges_and_both_ends():
    assert rr.kept_pieces([(0.0, 2.0), (10.0, 12.0), (95.0, 100.0)], 100.0) == \
        [(2.0, 10.0), (12.0, 95.0)]


@pytest.mark.parametrize("ranges", [
    [(5.0, 4.0)],                     # backwards
    [(-1.0, 2.0)],                    # before the start
    [(90.0, 101.0)],                  # past the end
    [(10.0, 20.0), (15.0, 30.0)],     # overlapping
    [(30.0, 40.0), (10.0, 20.0)],     # out of order
    [(0.0, 100.0)],                   # the whole file
    [],                               # nothing
])
def test_bad_ranges_are_refused_not_corrected(ranges):
    with pytest.raises(SystemExit):
        rr.kept_pieces(ranges, 100.0)


@pytest.mark.parametrize("text", ["113.9", "a:b", "1-2"])
def test_bad_range_text_is_refused(text):
    with pytest.raises(SystemExit):
        rr.parse_range(text)


def test_each_piece_is_its_own_input():
    """The fix itself: one -i per kept piece, never one input split into two branches."""
    args = rr._inputs("in.mp4", [(0.0, 113.9), (115.0, 872.0)], 872.0)
    assert args.count("-i") == 2
    assert args == ["-threads", "2", "-to", "113.900000", "-i", "in.mp4",
                    "-threads", "2", "-ss", "115.000000", "-i", "in.mp4"]
    graph = rr._audio_graph([(0.0, 113.9), (115.0, 872.0)], 872.0)
    assert "[0:a]" in graph and "[1:a]" in graph and "asplit" not in graph


def test_chapters_move_with_the_cut():
    ranges = [(113.9, 115.0)]
    chapters = [(0.0, 60.685, "Mister"), (60.685, 115.049, "The puzzle"),
                (115.049, 184.538, "The father's book"), (184.538, 872.097, "Last")]
    moved = rr.moved_chapters(chapters, ranges, 870.9)
    assert [c[2] for c in moved] == ["Mister", "The puzzle", "The father's book", "Last"]
    assert moved[1][1] == pytest.approx(113.949)       # 115.049 less the 1.1 s removed
    assert moved[2][0] == pytest.approx(113.949)
    assert moved[3][0] == pytest.approx(183.438)
    assert moved[3][1] == pytest.approx(870.9)         # never past the new length


def test_a_chapter_wholly_inside_a_removed_range_is_dropped():
    moved = rr.moved_chapters([(0.0, 10.0, "a"), (10.0, 12.0, "gone"), (12.0, 20.0, "b")],
                              [(9.5, 12.5)], 17.0)
    assert [c[2] for c in moved] == ["a", "b"]
    assert moved[1][0] == pytest.approx(9.5)


def test_bounds_are_put_on_the_frame_grid():
    assert rr.snap([(5.013, 6.52)], 30) == [(5.0, 196 / 30)]
    assert rr.snap([(113.9, 115.0)], 30) == [(113.9, 115.0)]


@pytest.mark.parametrize("end", [10.043991, 10.0667, 10.044])
def test_a_cut_to_the_end_of_a_file_that_overhangs_its_last_frame_reaches_the_end(end):
    """A 10.044 s container whose last frame starts at 10.0 (30 fps). Each of these means 'to the end'."""
    ranges = rr.snap([(5.0, end)], 30, total=10.044)
    assert ranges == [(5.0, 10.044)]
    assert rr.kept_pieces(ranges, 10.044, min_piece=1 / 30) == [(0.0, 5.0)]


def test_a_range_from_the_start_keeps_the_rest():
    ranges = rr.snap([(0.004, 2.0)], 30, total=10.044)
    assert ranges == [(0.0, 2.0)]
    assert rr.kept_pieces(ranges, 10.044, min_piece=1 / 30) == [(2.0, 10.044)]


def test_a_range_well_past_the_end_is_still_refused():
    with pytest.raises(SystemExit):
        rr.kept_pieces(rr.snap([(5.0, 10.2)], 30, total=10.044), 10.044, min_piece=1 / 30)


def test_a_range_shorter_than_a_frame_is_refused():
    with pytest.raises(SystemExit):
        rr.kept_pieces(rr.snap([(5.001, 5.010)], 30), 100.0, min_piece=1 / 30)


def test_a_sliver_left_at_either_end_is_refused():
    with pytest.raises(SystemExit):
        rr.kept_pieces([(0.02, 5.0)], 100.0, min_piece=1 / 30)
    with pytest.raises(SystemExit):
        rr.kept_pieces([(90.0, 99.99)], 100.0, min_piece=1 / 30)


def test_a_failed_render_leaves_no_file_at_the_output(tmp_path, monkeypatch):
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(rr, "probe", lambda p: {"duration": 20.0, "video": True, "audio": True,
                                               "fps": 30.0})

    def half_written(*args):
        args[-1].write_bytes(b"half")            # the partial file, as a killed ffmpeg leaves it
        raise RuntimeError("ffmpeg was killed")
    monkeypatch.setattr(rr, "_render", half_written)
    with pytest.raises(RuntimeError):
        rr.remove_range(src, [(5.0, 6.0)], out, "youtube")
    assert not out.exists()
    assert list(tmp_path.iterdir()) == [src]


def test_chapter_titles_keep_their_letters(tmp_path):
    meta = tmp_path / "m.txt"
    rr.write_ffmetadata([(0.0, 1.0, "Caf\u00e9 = 1; #2")], meta)
    text = meta.read_text(encoding="utf-8")
    assert "title=Caf\u00e9 \\= 1\\; \\#2" in text


def test_a_time_inside_a_range_lands_on_its_start():
    assert rr.shift(114.5, [(113.9, 115.0)]) == pytest.approx(113.9)
    assert rr.shift(50.0, [(113.9, 115.0)]) == pytest.approx(50.0)


# ------------------------------------------------------------------ the real thing

def _make(path, seconds, size="1920x1080"):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=%s:rate=30:duration=%d" % (size, seconds),
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=%d" % seconds,
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-g", "60",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-shortest",
         str(path)], check=True)


def _cli_peak_kb(args):
    """Run the real CLI in a child and return the largest RSS of any of ITS children (ffmpeg)."""
    code = ("import resource, sys; from ccvideo.cli import main; rc = main(sys.argv[1:]); "
            "print('PEAK_KB', resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss); "
            "sys.exit(rc)")
    p = subprocess.run([sys.executable, "-c", code] + args, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("PEAK_KB")]
    assert line, "no peak was reported - the measurement did not run:\n" + p.stdout
    return int(line[0].split()[1]), p.stdout


@needs_ffmpeg
def test_short_cut_keeps_length_level_and_faststart(tmp_path):
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    _make(src, 20, size="640x360")
    peak, stdout = _cli_peak_kb(["remove-range", str(src), "--range", "5.0:6.5",
                                 "--range", "12.0:13.0", "--out", str(out),
                                 "--target", "youtube"])
    assert "OK" in stdout
    shape = probe(out)
    assert shape["video"] and shape["audio"]
    assert abs(shape["duration"] - 17.5) <= 1 / 30 + 0.03   # one frame, plus AAC priming
    with open(out, "rb") as f:
        head = f.read(64 * 1024)
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    assert moov != -1 and (mdat == -1 or moov < mdat), "moov is not at the front: not faststart"


def _onsets(path):
    """(picture onset, sound onset): when the black picture turns white, and when the tone starts."""
    log = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path),
                          "-vf", "blackdetect=d=0.5:pix_th=0.1",
                          "-af", "silencedetect=n=-30dB:d=0.5", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    picture = [float(x.split()[0]) for x in log.split("black_end:")[1:]]
    sound = [float(x.split()[0]) for x in log.split("silence_end: ")[1:]]
    assert picture and sound, "no onset was measured - the instrument did not run:\n" + log[-2000:]
    return picture[0], sound[0]


@needs_ffmpeg
@pytest.mark.parametrize("ranges,removed", [
    (["5.0:6.5"], 1.5),
    # Off the frame grid: 5.013 -> 5.0 and 6.52 -> 6.5333 (frames 150 and 196 at 30 fps).
    (["5.013:6.52"], 196 / 30 - 5.0),
    (["1.013:1.52", "3.007:4.49", "6.52:7.013"], (46 - 30) / 30 + (135 - 90) / 30 + (210 - 196) / 30),
], ids=["on the grid", "off the grid", "three cuts off the grid"])
def test_sound_and_picture_stay_together_through_the_cut(tmp_path, ranges, removed):
    """The picture turns from black to white at 10.0 s and a tone starts at the same moment. After the
    cut both must still start together - within 1 ms of how they started in the source - and at
    10.0 s less what was removed. Before the bounds were put on frames, an off-grid cut moved the
    sound 13 ms against the picture, and three cuts 73 ms (review of cc-ai-video#29)."""
    src, out = tmp_path / "onset.mp4", tmp_path / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i",
         "color=c=white:s=320x240:r=30:d=16,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='lt(t,10)'",
         "-f", "lavfi", "-i", "aevalsrc='if(gte(t,10),0.5*sin(2*PI*440*t),0)':s=48000:d=16",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", str(src)], check=True)
    src_picture, src_sound = _onsets(src)
    args = ["remove-range", str(src), "--out", str(out), "--target", "youtube"]
    for r in ranges:
        args += ["--range", r]
    _cli_peak_kb(args)
    picture, sound = _onsets(out)
    print("source picture %.4f sound %.4f; cut picture %.4f sound %.4f"
          % (src_picture, src_sound, picture, sound))
    assert picture == pytest.approx(10.0 - removed, abs=0.001)
    assert (sound - picture) == pytest.approx(src_sound - src_picture, abs=0.001)


@needs_ffmpeg
def test_cutting_to_the_end_of_remove_ranges_own_output_works(tmp_path):
    """remove-range's own output overhangs its last frame (AAC padding). Cutting that file to its end,
    by the end time ffprobe reports and by the start of its last frame, must both work."""
    src, first = tmp_path / "in.mp4", tmp_path / "first.mp4"
    _make(src, 12, size="320x240")
    _cli_peak_kb(["remove-range", str(src), "--range", "3.0:4.0", "--out", str(first),
                  "--target", "youtube"])
    total = probe(first)["duration"]
    last_frame = int(total * 30 - 1e-6) / 30
    print("container %.4f, last frame starts %.4f" % (total, last_frame))
    assert total > last_frame + 1e-3, "the file does not overhang its last frame - nothing is tested"
    for i, end in enumerate((total, last_frame, last_frame + 0.011)):
        out = tmp_path / ("to-end-%d.mp4" % i)
        _cli_peak_kb(["remove-range", str(first), "--range", "6.0:%.6f" % end, "--out", str(out),
                      "--target", "youtube"])
        assert abs(probe(out)["duration"] - 6.0) <= 1 / 30 + 0.03


@needs_ffmpeg
def test_chapters_in_the_file_are_moved(tmp_path):
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    raw = tmp_path / "raw.mp4"
    _make(raw, 20, size="320x240")
    meta = tmp_path / "meta.txt"
    rr.write_ffmetadata([(0.0, 8.0, "one"), (8.0, 20.0, "two")], meta)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-f", "ffmetadata",
                    "-i", str(meta), "-map", "0", "-map_chapters", "1", "-c", "copy", str(src)],
                   check=True)
    _cli_peak_kb(["remove-range", str(src), "--range", "2.0:5.0", "--out", str(out),
                  "--target", "youtube"])
    chapters = rr._chapters(out)
    assert [c[2] for c in chapters] == ["one", "two"]
    assert chapters[1][0] == pytest.approx(5.0, abs=0.002)


@needs_ffmpeg
def test_ten_minutes_of_1080p_stays_under_one_gigabyte(tmp_path):
    """The acceptance in cc-ai-video#28: 10 minutes of 1080p, peak RSS under 1 GB."""
    src, out = tmp_path / "long.mp4", tmp_path / "cut.mp4"
    _make(src, 600)
    # youtube is the fastest real target to encode; the memory shape is the same for every
    # target because it comes from the decoders, not the encoder settings.
    peak, _ = _cli_peak_kb(["remove-range", str(src), "--range", "113.9:115.0",
                            "--out", str(out), "--target", "youtube"])
    print("peak RSS %.0f MB" % (peak / 1024))
    assert peak < ONE_GB_KB, "peak RSS %.0f MB - the cut is holding frames" % (peak / 1024)
    assert abs(probe(out)["duration"] - 598.9) <= 1 / 30 + 0.03
