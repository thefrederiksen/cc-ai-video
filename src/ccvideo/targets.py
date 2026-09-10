"""Output targets: a frame size, an encode profile, and a caption style.

These were three sets of magic numbers spread across four renderers. They differ for real
reasons, and naming those reasons is the whole value of this module.

FRAME SIZES are where the video is going. Nothing else about them is interesting.

ENCODE PROFILES differ because the PICTURE differs:

  stills   A narrated slideshow of screenshots. The picture barely moves, so 15 fps and a high
           CRF keep the file small without the slow zoom juddering, and speech is mono so the
           audio track is mono. Anything faster is bytes spent on a still image.
  footage  Real recorded video. 30 fps and a much lower CRF, because motion at CRF 30 smears,
           and stereo because the source is stereo.
  motion   Cut footage with a music bed under it. Like footage, but a lower CRF and a higher
           audio bitrate: a bed under speech is where compression artefacts are audible.

CAPTION STYLES differ because the READER differs, and all three are deliberate:

  centre   2 to 4 words at the vertical centre, one word coloured. For a short that is read
           rather than heard, scrolling past at arm's length.
  strip    Up to 7 words in two lines above a footer. For a short where the footage is the
           subject and the captions support it.
  full     Whole transcript segments. For a long video where the captions are an accessibility
           track, not a design element.
"""

SIZES = {
    "desktop": (1920, 1080),
    "phone": (1080, 1920),
}

PROFILES = {
    "stills": {
        "fps": 15,
        "crf": "30",
        "preset": "slow",
        "tune": "stillimage",
        "audio_kbps": "72k",
        "sample_rate": "44100",
        "channels": "1",
    },
    "footage": {
        "fps": 30,
        "crf": "20",
        "preset": "medium",
        "tune": None,
        "audio_kbps": "160k",
        "sample_rate": "44100",
        "channels": "2",
    },
    "motion": {
        "fps": 30,
        "crf": "19",
        "preset": "medium",
        "tune": None,
        "audio_kbps": "192k",
        "sample_rate": "48000",
        "channels": "2",
    },
}

# Where a finished file is going, and what that place expects.
TARGETS = {
    "youtube": {"size": "desktop", "profile": "footage", "captions": "full",
                "loudness": -14.0, "max_seconds": None},
    "shorts": {"size": "phone", "profile": "footage", "captions": "centre",
               "loudness": -14.0, "max_seconds": 60.0},
    "linkedin": {"size": "phone", "profile": "footage", "captions": "strip",
                 "loudness": -14.0, "max_seconds": 600.0},
    # The narrated slideshow, in both shapes. These two are what the assembler builds.
    "tutorial-desktop": {"size": "desktop", "profile": "stills", "captions": "full",
                         "loudness": None, "max_seconds": None},
    "tutorial-phone": {"size": "phone", "profile": "stills", "captions": "full",
                       "loudness": None, "max_seconds": None},
}


def size(name):
    if name not in SIZES:
        raise SystemExit("unknown size %r - have %s" % (name, ", ".join(sorted(SIZES))))
    return SIZES[name]


def profile(name):
    if name not in PROFILES:
        raise SystemExit("unknown encode profile %r - have %s" % (name, ", ".join(sorted(PROFILES))))
    return PROFILES[name]


def target(name):
    if name not in TARGETS:
        raise SystemExit("unknown target %r - have %s" % (name, ", ".join(sorted(TARGETS))))
    row = dict(TARGETS[name])
    row["name"] = name
    row["width"], row["height"] = size(row["size"])
    row["encode"] = profile(row["profile"])
    return row


def video_args(prof):
    """The libx264 arguments for an encode profile."""
    args = ["-c:v", "libx264", "-preset", prof["preset"]]
    if prof["tune"]:
        args += ["-tune", prof["tune"]]
    args += ["-crf", prof["crf"], "-pix_fmt", "yuv420p", "-r", str(prof["fps"])]
    return args


def audio_args(prof):
    return ["-c:a", "aac", "-b:a", prof["audio_kbps"],
            "-ar", prof["sample_rate"], "-ac", prof["channels"]]
