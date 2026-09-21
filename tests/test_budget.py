"""This library never takes the whole machine: half the cores, lower priority."""

import os
import subprocess
import sys

from ccvideo import budget


def test_cores_is_half_the_machine():
    assert budget.cores() == max(1, (os.cpu_count() or 2) // 2)


def test_workers_are_capped_at_half_the_cores(capsys):
    assert budget.workers(10 ** 6) == budget.cores()
    assert "capped" in capsys.readouterr().out


def test_a_modest_request_is_kept():
    assert budget.workers(1) == 1


def test_encoders_share_the_budget_rather_than_each_taking_it():
    n = budget.cores()
    assert budget.encoder_threads(n) * n <= n
    assert budget.encoder_threads(1) == n


def test_every_ffmpeg_is_capped_before_its_output():
    cmd = budget.ffmpeg(["ffmpeg", "-i", "in.mp4", "-c:v", "libx264", "out.mp4"])
    assert cmd[-3:] == ["-threads", str(budget.cores()), "out.mp4"]


def test_a_command_that_names_its_threads_keeps_them_and_others_are_untouched():
    own = ["ffmpeg", "-i", "a", "-threads", "2", "b"]
    assert budget.ffmpeg(own) == own
    assert budget.ffmpeg(["ffprobe", "x"]) == ["ffprobe", "x"]


def test_lowering_priority_really_lowers_it_and_children_inherit():
    """Measured on a real child process, not assumed from the call returning."""
    report = (
        "import sys, os\n"
        "if sys.platform == 'win32':\n"
        "    import ctypes\n"
        "    from ctypes import wintypes\n"
        "    k = ctypes.windll.kernel32\n"
        "    k.GetCurrentProcess.restype = wintypes.HANDLE\n"
        "    k.GetPriorityClass.argtypes = [wintypes.HANDLE]\n"
        "    print(hex(k.GetPriorityClass(k.GetCurrentProcess())))\n"
        "else:\n"
        "    print(os.nice(0))\n"
    )
    # The child lowers itself, reports, then starts a grandchild that reports too - the
    # grandchild stands in for a worker process or an ffmpeg the library launches.
    probe = (
        "import subprocess, sys\n"
        "from ccvideo import budget\n"
        "budget.lower_priority()\n"
        "exec(%r)\n"
        "sys.stdout.flush()\n"
        "subprocess.run([sys.executable, '-c', %r])\n" % (report, report)
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         env=env, check=True).stdout.split()
    assert len(out) == 2, out
    for level in out:
        if sys.platform == "win32":
            assert level == hex(budget.BELOW_NORMAL)
        else:
            assert int(level) >= budget.NICENESS
