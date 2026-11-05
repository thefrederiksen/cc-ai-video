from ccvideo import targets


def test_youtube_master_is_1080p_at_master_quality():
    t = targets.target("youtube-master")
    assert t["size"] == "desktop"
    assert t["profile"] == "master"
    # The LEVEL is not this target's business and is deliberately not a literal here. It was
    # -14.0 when this target was added; the owner then set one level for the whole factory
    # (5 Nov 2026) and pinning a literal per target is exactly how four of them got loose.
    assert t["loudness"] == targets.PUBLISHED_LUFS
