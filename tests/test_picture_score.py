"""The picture score: an empty screen is measured, never judged."""

import numpy as np

from ccvideo.qa import picture


def grey(fill=10):
    return np.full((picture.H, picture.W), fill, np.int16)


def test_an_empty_dark_frame_and_an_empty_pale_frame_both_read_zero():
    assert picture.frame_busy(grey(10))[0] == 0.0
    assert picture.frame_busy(grey(190))[0] == 0.0


def test_a_headline_reads_busy_and_a_tiny_label_reads_empty():
    headline = grey(10)
    headline[20:50, 20:220] = 240          # a heavy line of type
    label = grey(10)
    label[5:9, 5:40] = 120                 # a small grey chapter label
    assert picture.frame_busy(headline)[0] > picture.THIN
    assert picture.frame_busy(label)[0] < picture.EMPTY


def test_diff_measures_movement_between_frames():
    a, b = grey(10), grey(10)
    b[0:90, :] = 200
    assert picture.frame_busy(a, a)[1] == 0.0
    assert picture.frame_busy(b, a)[1] > 1.0


def test_a_stretch_is_a_run_long_enough_not_a_single_frame():
    r = picture.RATE
    mask = [False] * r + [True] * 2 + [False] * r + [True] * (3 * r) + [False]
    runs = picture.stretches(mask, 1.0)
    assert runs == [((r + 2 + r) / r, 3.0)]


def test_score_counts_empty_time_and_names_the_stretch():
    r = picture.RATE
    busy = np.array([0.2] * (10 * r) + [0.0] * (4 * r) + [0.2] * (6 * r))
    diff = np.array([0.5] * len(busy))
    result = picture.score(busy, diff)
    assert result["empty"] == [{"at": 10.0, "seconds": 4.0}]
    assert result["empty_seconds_total"] == 4.0
    assert result["clean_percent"] == 80.0
    assert result["still"] == []


def test_a_frozen_but_full_screen_is_a_still_defect():
    r = picture.RATE
    busy = np.array([0.2] * (20 * r))
    diff = np.array([0.5] * (5 * r) + [0.0] * (8 * r) + [0.5] * (7 * r))
    result = picture.score(busy, diff)
    assert result["empty"] == []
    assert result["still"] == [{"at": 5.0, "seconds": 8.0}]
