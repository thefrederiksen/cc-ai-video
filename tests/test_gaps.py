"""Where dead air is, and where it is safe to cut it out."""

from ccvideo.edit.project import _word_gaps


def w(start, end, word="x"):
    return {"start": start, "end": end, "word": word}


WORDS = [w(2.64, 3.28), w(3.48, 5.02), w(5.92, 7.32)]


def test_gaps_are_the_stretches_with_no_word_in_them():
    gaps = _word_gaps(WORDS, 2.39, 7.41)
    assert (3.28, 3.48) in gaps
    assert (5.02, 5.92) in gaps


def test_the_head_and_tail_of_the_clip_are_gaps_too():
    gaps = _word_gaps(WORDS, 2.39, 7.41)
    assert gaps[0] == (2.39, 2.64)
    assert gaps[-1] == (7.32, 7.41)


def test_no_gap_ever_overlaps_a_word():
    # The invariant the whole trim rests on. A silence detector at any usable threshold marks
    # the quiet onset of a word as silence, and trimming there cuts through a word - the one
    # thing this library exists to prevent. Removals are confined to these gaps.
    for gap_start, gap_end in _word_gaps(WORDS, 2.39, 7.41):
        for word in WORDS:
            assert gap_end <= word["start"] or gap_start >= word["end"]


def test_gaps_are_clamped_to_the_clip():
    for gap_start, gap_end in _word_gaps(WORDS, 3.0, 6.0):
        assert gap_start >= 3.0 and gap_end <= 6.0


def test_a_clip_with_no_words_is_one_long_gap():
    assert _word_gaps(WORDS, 20.0, 25.0) == [(20.0, 25.0)]


def test_clean_edge_accepts_a_cut_inside_a_real_pause():
    # 0.25s after the last word, inside a 0.90s pause whose silence is measured from 0.35s in.
    # Asking only "is this instant silent" called this a hard chop; it is not one.
    from ccvideo.edit.project import clean_edge
    quiet = [(5.37, 5.83)]
    assert clean_edge(WORDS, quiet, 5.27)


def test_clean_edge_rejects_a_junction_with_no_pause_in_it():
    # THE 66-SHORTS CASE. A boundary rule said this was a sentence end; the audio says there is
    # no gap at all. Sixteen published and scheduled shorts end exactly here.
    from ccvideo.edit.project import clean_edge
    words = [w(1.0, 2.0), w(2.0, 3.0)]
    assert not clean_edge(words, [(10.0, 11.0)], 2.0)


def test_clean_edge_rejects_a_cut_inside_a_word():
    from ccvideo.edit.project import clean_edge
    assert not clean_edge(WORDS, [(0.0, 20.0)], 4.0)


def test_clean_edge_shares_no_code_with_the_boundary_rule():
    # The whole point. If this check imported is_boundary it would agree with whatever made the
    # cut, and would report success in exactly the case that needed a failure.
    import inspect

    from ccvideo.edit import project
    source = inspect.getsource(project.clean_edge) + inspect.getsource(project.edge_notes)
    assert "is_boundary" not in source


def test_a_fade_longer_than_the_final_word_is_not_a_clean_edge():
    # THE SHIPPED DEFECT, with its real timings. A short ended on "the most lines of" - the
    # word "code" was in the source, the cut landed on a correct word boundary, and a 0.25s
    # fade-out over a 0.24s final word faded the whole word to silence. Word timings alone
    # call this clean, because at the word level it IS clean.
    from ccvideo.edit.project import clean_edge
    words = [w(343.90, 344.20, "lines"), w(344.20, 344.44, "code")]
    quiet = [(344.44, 346.0)]
    assert not clean_edge(words, quiet, 344.44, side="end", fade=0.25)


def test_the_same_edge_is_clean_when_the_fade_fits_the_silence():
    # The relationship is the point: shorten the fade and the same cut becomes safe.
    from ccvideo.edit.project import clean_edge
    words = [w(343.90, 344.20, "lines"), w(344.20, 344.44, "code")]
    quiet = [(344.44, 346.0)]
    assert clean_edge(words, quiet, 344.50, side="end", fade=0.05)


def test_the_edge_check_uses_the_renderers_own_fade_by_default():
    # If these ever drift apart, raising the fade for a nicer feel silently reintroduces the
    # defect above and no test notices. They are one constraint, so they are one number.
    import inspect

    from ccvideo import targets
    from ccvideo.edit import project, render
    assert "targets.JOIN_FADE" in inspect.getsource(render.render_clip)
    assert "targets.JOIN_FADE" in inspect.getsource(project.clean_edge)
    assert targets.JOIN_FADE > 0


def test_a_start_cut_checks_the_fade_in_window_not_the_fade_out():
    from ccvideo.edit.project import clean_edge
    words = [w(10.0, 10.30, "hello")]
    quiet = [(9.0, 10.0)]
    # Opening exactly on the word: the fade-IN would run over it.
    assert not clean_edge(words, quiet, 10.0, side="start", fade=0.25)
    # Ending exactly there is fine - the fade-OUT lies in the silence before it.
    assert clean_edge(words, quiet, 10.0, side="end", fade=0.25)
