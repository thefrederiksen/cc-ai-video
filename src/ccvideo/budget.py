"""How much of the machine this library may use: never more than HALF the cores, always at a
lower priority than whatever else is running.

This runs on the owner's own workstation, next to his work and a fleet of other sessions.
On 2026-09-21 a render with 22 workers - each drawing frames and each running an x264 encoder -
took every core and ~30 GB, and halted the machine until it was killed. Two limits, enforced
here so no caller has to remember them:

  HALF THE CORES. Worker processes are capped at half the logical cores, and every encoder's
      threads come out of that same half. Asking for more is not an error; it is capped, and
      the cap is printed so nobody wonders why.
  LOWER PRIORITY. The process lowers its own priority once, before it starts anything. Worker
      processes and every ffmpeg it launches inherit it (Windows: BELOW_NORMAL passes to
      children; POSIX: niceness is inherited), so anything interactive wins the CPU first.
"""

import os
import sys

BELOW_NORMAL = 0x00004000   # Windows priority class
NICENESS = 10               # POSIX

_lowered = False


def cores():
    """The cores this library may use: half the machine's logical cores, at least one."""
    return max(1, (os.cpu_count() or 2) // 2)


def workers(requested):
    """`requested` worker processes, capped at cores()."""
    allowed = cores()
    wanted = max(1, int(requested or 1))
    if wanted > allowed:
        print("  --workers %d capped at %d: this library never uses more than half the %d "
              "cores" % (wanted, allowed, os.cpu_count() or 2), flush=True)
    return min(wanted, allowed)


def encoder_threads(worker_count=1):
    """Threads for each encoder when `worker_count` encoders run side by side."""
    return max(1, cores() // max(1, worker_count))


def lower_priority():
    """Drop this process (and so everything it starts) below normal priority. Once."""
    global _lowered
    if _lowered:
        return
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        # The process handle is pointer-sized; left to ctypes' default int it is cut to 32
        # bits and the call fails with "invalid handle".
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetPriorityClass.restype = wintypes.BOOL
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), BELOW_NORMAL):
            raise OSError("could not lower the process priority (SetPriorityClass failed, "
                          "error %d)" % kernel.GetLastError())
    else:
        current = os.nice(0)
        if current < NICENESS:
            os.nice(NICENESS - current)
    _lowered = True


def ffmpeg(cmd):
    """An ffmpeg command limited to cores() threads. The limit goes just before the output
    file, where ffmpeg applies it to the encoders; a command that already names its own
    -threads keeps them."""
    cmd = [str(c) for c in cmd]
    if not cmd or os.path.basename(cmd[0]).lower() not in ("ffmpeg", "ffmpeg.exe"):
        return cmd
    if "-threads" in cmd:
        return cmd
    return cmd[:-1] + ["-threads", str(cores()), cmd[-1]]
