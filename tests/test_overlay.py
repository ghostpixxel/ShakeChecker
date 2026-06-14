from __future__ import annotations

import pytest

from overlay import Overlay, phys_to_logical, prob_color_hex, status_badge, subheader_text

# --- pure helpers (no Qt) ---


def test_prob_color_thresholds():
    assert prob_color_hex(0.0) == "#ff5555"  # red
    assert prob_color_hex(0.34) == "#ff5555"
    assert prob_color_hex(0.35) == "#ffcc44"  # yellow
    assert prob_color_hex(0.65) == "#ffcc44"
    assert prob_color_hex(0.66) == "#55dd66"  # green
    assert prob_color_hex(1.0) == "#55dd66"


def test_subheader_text():
    assert subheader_text(75, 2) == "Rate: 75  ·  Turn 2"


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
    ov.show_battle(
        419, "Floatzel", 75, 2, {"Poké Ball": 0.098, "Great Ball": 0.60, "Quick Ball": 0.80}
    )
    assert ov._name.text() == "Floatzel"
    assert ov._sub.text() == subheader_text(75, 2)
    assert ov._pct_labels["Poké Ball"].text().strip() == "9.8%"
    assert "ff5555" in ov._pct_labels["Poké Ball"].styleSheet()  # red, <50%
    assert "ffcc44" in ov._pct_labels["Great Ball"].styleSheet()  # yellow, 60%
    assert "55dd66" in ov._pct_labels["Quick Ball"].styleSheet()  # green, 80%


def test_level_rendered_next_to_name(qt_app):
    ov = Overlay(BALLS)
    ov.show_battle(66, "Machop", 180, 2, {}, level=6)
    text = ov._name.text()
    assert "Machop" in text and "Lv.6" in text


def test_no_level_renders_plain_name(qt_app):
    ov = Overlay(BALLS)
    ov.show_battle(66, "Machop", 180, 2, {})  # no level
    assert ov._name.text() == "Machop"


def test_status_badge_mapping():
    assert status_badge("psn") == ("PSN", "#9b4dca")
    assert status_badge("PSN")[0] == "PSN"  # case-insensitive
    assert status_badge("none") is None
    assert status_badge(None) is None
    assert status_badge("") is None


def test_hp_line_rendered(qt_app):
    ov = Overlay(BALLS)
    ov.show_battle(66, "Machop", 180, 2, {}, hp_pct=58.4)
    assert ov._hp.text() == "HP: 58%"
    ov.show_battle(66, "Machop", 180, 2, {})  # no hp -> blank
    assert ov._hp.text() == ""


def test_status_badge_shown_and_hidden(qt_app):
    ov = Overlay(BALLS)
    ov.show_battle(66, "Machop", 180, 2, {}, status="psn")
    assert ov._status.isVisibleTo(ov) is True
    assert "PSN" in ov._status.text()
    ov.show_battle(66, "Machop", 180, 3, {}, status="none")
    assert ov._status.isVisibleTo(ov) is False


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


def test_phys_to_logical_scales_by_dpr(qt_app):
    dpr = qt_app.primaryScreen().devicePixelRatio()
    lx, ly = phys_to_logical(1000, 600)
    assert (lx, ly) == (round(1000 / dpr), round(600 / dpr))
