from ccvideo import targets


def test_youtube_master_is_1080p_at_master_quality():
    t = targets.target("youtube-master")
    assert t["size"] == "desktop"
    assert t["profile"] == "master"
    assert t["loudness"] == -14.0
