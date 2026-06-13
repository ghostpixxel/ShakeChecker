from __future__ import annotations

from hp_settler import HpSettler


def test_first_reading_passes_through():
    s = HpSettler(stable_needed=2)
    assert s.update(100.0) == 100.0  # nothing settled yet -> raw value


def test_settles_after_stable_frames():
    s = HpSettler(tolerance=1.5, stable_needed=2)
    s.update(100.0)
    assert s.update(100.0) == 100.0
    assert s.settled == 100.0


def test_holds_settled_value_during_animation():
    s = HpSettler(tolerance=1.5, stable_needed=2)
    s.update(100.0)
    s.update(100.0)  # settled at 100
    # drain animation: changing values each frame must NOT be shown
    assert s.update(88.5) == 100.0
    assert s.update(60.0) == 100.0
    assert s.update(30.0) == 100.0


def test_commits_new_value_once_stable_again():
    s = HpSettler(tolerance=1.5, stable_needed=2)
    s.update(100.0)
    s.update(100.0)
    s.update(60.0)  # animation starts, still shows 100
    assert s.update(42.0) == 100.0
    # bar stops at 42
    assert s.update(42.0) == 42.0
    assert s.settled == 42.0


def test_tolerance_absorbs_jitter():
    s = HpSettler(tolerance=1.5, stable_needed=2)
    s.update(50.0)
    assert s.update(51.0) == 50.0  # within tolerance -> counts as stable
    assert s.settled == 50.0


def test_reset_clears_state():
    s = HpSettler(stable_needed=2)
    s.update(100.0)
    s.update(100.0)
    s.reset()
    assert s.settled is None
    assert s.update(42.0) == 42.0
