from __future__ import annotations

from app import dex_panel_text
from dex_session import LocationView
from dex_tracker import MissingEntry
from game_time import Period


def view(missing):
    return LocationView(
        route="VIRIDIAN FOREST",
        region="KANTO",
        period=Period.DAY,
        season=0,
        missing=[MissingEntry(i, n, ("Grass",)) for i, n in missing],
    )


def test_none_view_is_empty():
    assert dex_panel_text(None) == ""


def test_all_caught_message():
    text = dex_panel_text(view([]))
    assert "VIRIDIAN FOREST (KANTO) DAY S0 — 0 needed" in text
    assert "all caught here" in text


def test_lists_few_without_plus():
    text = dex_panel_text(view([(1, "Bulbasaur"), (16, "Pidgey")]))
    assert "— 2 needed" in text
    assert "#1    Bulbasaur" in text
    assert "#16   Pidgey" in text
    assert "+" not in text


def test_caps_at_five_and_collapses_rest():
    missing = [(i, f"Mon{i}") for i in range(1, 9)]  # 8 entries
    text = dex_panel_text(view(missing))
    lines = text.splitlines()
    assert lines[0].endswith("8 needed")
    assert len(lines) == 1 + 5 + 1  # header + 5 shown + "+3"
    assert lines[-1] == "  +3"
