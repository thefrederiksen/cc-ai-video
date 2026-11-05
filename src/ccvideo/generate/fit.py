"""Fitting a recorded clip to the narration over it.

The narration is the master and is never cut to fit the picture. The picture bends instead,
by three rules, applied in order:

  1. The clip is SHORTER than the segment: play it at its own speed, then hold its last frame
     until the segment ends. No loop - a pointer that jumps back to where it started reads as
     a glitch.
  2. The clip is LONGER: play it faster, by just enough to land on the segment's end, up to
     `max_speed` (2x by default). A screen recording sped up a little reads as brisk; past
     about 2x the pointer becomes hard to follow.
  3. Still longer at `max_speed`: play it at `max_speed` and TRIM THE END. That loses
     picture, so it is always reported - the fix is a CLIP-END, or more narration.

`CLIP-START` / `CLIP-END` pick the part of the clip that matters before any of this applies.
"""

DEFAULT_MAX_SPEED = 2.0


class Fit:
    """How one clip is played under one segment."""

    def __init__(self, start, used, speed, hold, trimmed):
        self.start = start          # seconds into the source where playback begins
        self.used = used            # seconds of SOURCE that are played
        self.speed = speed          # playback rate: 1.0 is real time
        self.hold = hold            # seconds the last frame is held after playback
        self.trimmed = trimmed      # seconds of the chosen source range that are NOT shown

    @property
    def shown(self):
        """Seconds of the segment the playback covers, hold included."""
        return self.used / self.speed + self.hold

    def describe(self):
        parts = ["x%.2f" % self.speed]
        if self.hold > 0.005:
            parts.append("hold %.1fs" % self.hold)
        if self.trimmed > 0.005:
            parts.append("TRIMMED %.1fs" % self.trimmed)
        return " ".join(parts)

    def __repr__(self):
        return "<Fit start=%.2f used=%.2f speed=%.3f hold=%.2f trimmed=%.2f>" % (
            self.start, self.used, self.speed, self.hold, self.trimmed)


def fit(clip_length, segment_seconds, clip_start=None, clip_end=None,
        max_speed=DEFAULT_MAX_SPEED):
    """The Fit of a clip `clip_length` seconds long under a segment `segment_seconds` long."""
    if max_speed < 1.0:
        raise SystemExit("the clip speed cap is %.2f; it must be at least 1 (never slow a "
                         "clip down to fill time - hold its last frame instead)" % max_speed)
    if segment_seconds <= 0:
        raise SystemExit("a segment of %.3fs has no time to show a clip in" % segment_seconds)
    start = clip_start or 0.0
    end = clip_end if clip_end is not None else clip_length
    end = min(end, clip_length)
    if end <= start:
        raise SystemExit("the clip range %.2f-%.2f is empty (clip is %.2fs)"
                         % (start, end, clip_length))
    chosen = end - start

    if chosen <= segment_seconds:
        return Fit(start, chosen, 1.0, segment_seconds - chosen, 0.0)
    speed = chosen / segment_seconds
    if speed <= max_speed:
        return Fit(start, chosen, speed, 0.0, 0.0)
    used = segment_seconds * max_speed
    return Fit(start, used, max_speed, 0.0, chosen - used)
