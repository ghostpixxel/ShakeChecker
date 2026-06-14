from __future__ import annotations

import pytest

from overlay import Overlay, prob_color_hex, subheader_text

# --- pure helpers (no Qt) ---


def test_prob_color_thresholds():
    assert prob_color_hex(0.0) == "#ff5555"  # red
    assert prob_color_hex(0.29) == "#ff5555"
    assert prob_color_hex(0.30) == "#ffcc44"  # yellow
    assert prob_color_hex(0.59) == "#ffcc44"
    assert prob_color_hex(0.60) == "#55dd66"  # green
    assert prob_color_hex(1.0) == "#55dd66"


def test_subheader_text():
    assert subheader_text(75, 2) == "Catch 75   ·   Turn 2"


# --- widget smoke tests (need a QApplication) ---


@pytest.fixture(scope="module")
def qt_app():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:  # pragma: no cover
        pytest.skip("PyQt6 unavailable")
    app = QApplication.instance() or QApplication([])
    yield app


BALLS = ["Poké Ball", "Great Ball", "Quick Ball"]


def test_show_battle_sets_header_and_percentages(qt_app):
    ov = Overlay(BALLS)
    ov.show_battle(419, "Floatzel", 75, 2, {"Poké Ball": 0.098, "Quick Ball": 0.49})
    assert ov._name.text() == "Floatzel"
    assert ov._sub.text() == subheader_text(75, 2)
    assert ov._pct_labels["Poké Ball"].text().strip() == "9.8%"
    assert "ff5555" in ov._pct_labels["Poké Ball"].styleSheet()  # red, <30%
    assert "ffcc44" in ov._pct_labels["Quick Ball"].styleSheet()  # yellow, 49%


def test_missing_ball_shows_dash(qt_app):
    ov = Overlay(BALLS)
    ov.show_battle(1, "Bulbasaur", 45, 1, {"Poké Ball": 0.1})  # no Great/Quick
    assert ov._pct_labels["Great Ball"].text() == "—"


def test_animated_species_gets_a_running_movie(qt_app):
    ov = Overlay(BALLS)
    ov.show_battle(1, "Bulbasaur", 45, 1, {})  # dex 1 has an animated gif
    assert ov._movie is not None
    from PyQt6.QtGui import QMovie

    assert ov._movie.state() == QMovie.MovieState.Running


def test_static_species_clears_movie(qt_app):
    ov = Overlay(BALLS)
    ov.show_battle(1, "Bulbasaur", 45, 1, {})  # animated -> movie set
    ov.show_battle(1000, "GenSix", 50, 1, {})  # static png -> movie cleared
    assert ov._movie is None
