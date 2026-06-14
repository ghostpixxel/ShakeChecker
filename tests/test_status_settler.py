from __future__ import annotations

from status_settler import StatusSettler


def test_first_reading_commits_immediately():
    s = StatusSettler(stable_needed=4)
    assert s.update("psn") == "psn"


def test_brief_blip_is_ignored():
    s = StatusSettler(stable_needed=4)
    s.update("psn")
    # a few FRZ frames from the catch animation, fewer than the threshold
    assert s.update("frz") == "psn"
    assert s.update("frz") == "psn"
    assert s.update("frz") == "psn"
    # back to the real status
    assert s.update("psn") == "psn"


def test_sustained_change_takes_over():
    s = StatusSettler(stable_needed=4)
    s.update("none")
    for _ in range(3):
        assert s.update("psn") == "none"  # not yet
    assert s.update("psn") == "psn"  # 4th consecutive -> switch


def test_interrupted_candidate_resets():
    s = StatusSettler(stable_needed=3)
    s.update("psn")
    s.update("frz")
    s.update("frz")
    s.update("psn")  # interruption resets the frz run
    assert s.update("frz") == "psn"
    assert s.update("frz") == "psn"
    assert s.update("frz") == "frz"  # needs 3 fresh consecutive again


def test_reset_clears_state():
    s = StatusSettler(stable_needed=2)
    s.update("psn")
    s.reset()
    assert s.update("slp") == "slp"  # next battle commits fresh
