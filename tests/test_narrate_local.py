"""The local voice: its own cache key, no key needed, and a budget that stops cleanly."""
import time

import pytest

from ccvideo import narrate

BRAND = {"instructions": "x"}


def test_local_has_its_own_fingerprint_and_needs_no_key():
    local = narrate.Voice(BRAND, "local")
    assert local.voice == narrate.LOCAL_VOICE
    assert local.api_key() is None
    assert local.digest("hello") != narrate.Voice(BRAND, "elevenlabs").digest("hello")
    assert local.digest("hello") != narrate.Voice(BRAND, "openai").digest("hello")


def test_voice_budget_is_local_only():
    with pytest.raises(SystemExit):
        narrate.Voice(BRAND, "elevenlabs", budget=60)


def test_spent_budget_stops_with_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(narrate.shutil, "which", lambda c: "/bin/true")
    v = narrate.Voice(BRAND, "local", budget=60)
    v.deadline = time.time() - 1
    with pytest.raises(SystemExit) as e:
        narrate.synthesise_local("hello there.", v, tmp_path / "seg-001-abc.mp3")
    assert e.value.code == narrate.PARTIAL_EXIT


# ---------------------------------------------------------------- the rented GPU

class Seg:
    def __init__(self, id, text):
        self.id, self.text = id, text


def _fake_narrate(calls):
    """Stands in for `narrate --batch`: records the call and writes a real wav for every job."""
    import json as _json
    import subprocess as _sp

    real_run = _sp.run

    def run(cmd, *a, **k):
        if cmd and str(cmd[0]).endswith("narrate"):
            calls.append(list(map(str, cmd)))
            for job in _json.loads(open(cmd[cmd.index("--batch") + 1]).read()):
                real_run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                          "sine=frequency=220:duration=3.5", "-ar", "48000", job["out"]], check=True)
            return _sp.CompletedProcess(cmd, 0)
        return real_run(cmd, *a, **k)
    return run


def _rented(monkeypatch):
    import shutil as _sh
    real = _sh.which
    monkeypatch.setattr(narrate.shutil, "which",
                        lambda c: "/opt/x/narrate" if c == narrate.RENTED_COMMAND
                        else None if c == narrate.LOCAL_COMMAND else real(c))
    monkeypatch.delenv("CCVIDEO_VOICE_HERE", raising=False)


def test_every_missing_segment_is_voiced_on_one_pod(tmp_path, monkeypatch):
    _rented(monkeypatch)
    calls = []
    monkeypatch.setattr(narrate.subprocess, "run", _fake_narrate(calls))
    v = narrate.Voice(BRAND, "local")
    segs = [Seg("001", "One."), Seg("002", "Two."), Seg("003", "Three.")]
    assert narrate.prefetch_local(segs, v, tmp_path) == 3
    assert len(calls) == 1 and "--batch" in calls[0]
    for s in segs:
        assert narrate.cached(narrate.cache_path(tmp_path, s.id, s.text, v))
    # A re-run rents nothing: the cache is keyed by the text.
    assert narrate.prefetch_local(segs, v, tmp_path) == 0
    assert len(calls) == 1


def test_the_batch_file_keeps_one_name_so_a_partial_run_finds_its_pod(tmp_path, monkeypatch):
    _rented(monkeypatch)
    calls = []
    monkeypatch.setattr(narrate.subprocess, "run", _fake_narrate(calls))
    v = narrate.Voice(BRAND, "local")
    narrate.prefetch_local([Seg("001", "One.")], v, tmp_path)
    narrate.prefetch_local([Seg("002", "Two.")], v, tmp_path)
    assert calls[0][calls[0].index("--batch") + 1] == calls[1][calls[1].index("--batch") + 1]


def test_where_it_was_voiced_does_not_change_the_cache_key(monkeypatch):
    _rented(monkeypatch)
    rented = narrate.Voice(BRAND, "local").digest("hello")
    monkeypatch.setenv("CCVIDEO_VOICE_HERE", "1")
    assert narrate.Voice(BRAND, "local").digest("hello") == rented


def test_the_budget_reaches_the_rented_gpu(tmp_path, monkeypatch):
    _rented(monkeypatch)
    calls = []
    monkeypatch.setattr(narrate.subprocess, "run", _fake_narrate(calls))
    v = narrate.Voice(BRAND, "local", budget=300)
    narrate.prefetch_local([Seg("001", "One.")], v, tmp_path)
    assert 290 <= int(calls[0][calls[0].index("--budget") + 1]) <= 300


def test_a_partial_rented_run_stops_with_exit_3(tmp_path, monkeypatch):
    _rented(monkeypatch)
    import subprocess as _sp
    monkeypatch.setattr(narrate.subprocess, "run", lambda cmd, *a, **k: _sp.CompletedProcess(cmd, 3))
    with pytest.raises(SystemExit) as e:
        narrate.prefetch_local([Seg("001", "One.")], narrate.Voice(BRAND, "local"), tmp_path)
    assert e.value.code == narrate.PARTIAL_EXIT


@pytest.mark.parametrize("host", ["devthrottle-mac-mini.local", "Devthrottle-Mac-mini.local"])
def test_the_mac_mini_never_voices_locally(monkeypatch, host):
    monkeypatch.setattr(narrate, "RENTED_COMMAND", "narrate")
    monkeypatch.setattr(narrate.shutil, "which", lambda c: "/usr/bin/" + c if c != "narrate" else None)
    import platform
    monkeypatch.setattr(platform, "node", lambda: host)
    with pytest.raises(SystemExit, match="not allowed to voice on this machine"):
        narrate.local_voice_where()


def test_voicing_here_can_be_asked_for_where_it_is_allowed(monkeypatch):
    monkeypatch.setattr(narrate, "RENTED_COMMAND", "narrate")
    monkeypatch.setattr(narrate.shutil, "which", lambda c: "/usr/bin/" + c)
    import platform
    monkeypatch.setattr(platform, "node", lambda: "devlinux")
    monkeypatch.setenv("CCVIDEO_VOICE_HERE", "1")
    assert narrate.local_voice_where() == ("here", "/usr/bin/" + narrate.LOCAL_COMMAND)
    monkeypatch.delenv("CCVIDEO_VOICE_HERE")
    assert narrate.local_voice_where() == ("rented", "/usr/bin/narrate")


@pytest.mark.parametrize("rc", [1, 2])
def test_a_failed_rented_run_is_an_error_and_never_falls_back_here(tmp_path, monkeypatch, rc):
    _rented(monkeypatch)
    import subprocess as _sp
    seen = []

    def run(cmd, *a, **k):
        seen.append(str(cmd[0]))
        return _sp.CompletedProcess(cmd, rc)
    monkeypatch.setattr(narrate.subprocess, "run", run)
    v = narrate.Voice(BRAND, "local")
    with pytest.raises(SystemExit) as e:
        narrate.prefetch_local([Seg("001", "One.")], v, tmp_path)
    assert e.value.code not in (0, narrate.PARTIAL_EXIT)
    with pytest.raises(SystemExit):
        narrate.synthesise_local("One.", v, tmp_path / "seg-001-x.mp3")
    assert seen and all(c.endswith("narrate") for c in seen)
