from __future__ import annotations

from app import dex_panel_text
from dex_session import LocationView
from dex_tracker import DexEntry
from game_time import Period


def entry(id, name, rarity="Common", ways=(), caught=False):
    return DexEntry(id, name, ways, rarity, caught)


def view(entries):
    return LocationView(
        route="VIRIDIAN FOREST",
        region="KANTO",
        period=Period.DAY,
        season=0,
        entries=entries,
    )


def test_none_view_is_empty():
    assert dex_panel_text(None) == ""


def test_all_caught_message():
    # everything caught and nothing rare enough to pad -> "all caught here"
    text = dex_panel_text(view([entry(1, "Caterpie", caught=True)]))
    assert "VIRIDIAN FOREST (KANTO) DAY S0 — 0 needed" in text
    assert "all caught here" in text


def test_header_counts_only_uncaught():
    text = dex_panel_text(view([entry(1, "A"), entry(2, "B"), entry(3, "C", caught=True)]))
    assert "— 2 needed" in text


def test_rarity_label_and_ways_shown():
    text = dex_panel_text(view([entry(131, "Lapras", rarity="Very Rare", ways=("Water",))]))
    assert "#131  Lapras [Very Rare] (Water)" in text


def test_plain_grass_has_no_parens():
    text = dex_panel_text(view([entry(10, "Caterpie", rarity="Common")]))
    assert "Caterpie [Common]" in text
    assert "(" not in text.split("needed")[1]


def test_caps_uncaught_at_five_with_overflow():
    entries = [entry(i, f"Mon{i}") for i in range(1, 9)]  # 8 uncaught
    lines = dex_panel_text(view(entries)).splitlines()
    assert lines[0].endswith("8 needed")
    assert len(lines) == 1 + 5 + 1  # header + 5 + "+3"
    assert lines[-1] == "  +3"


def test_pads_tail_with_rarest_caught_marked():
    entries = [
        entry(1, "A"),  # uncaught
        entry(2, "B"),  # uncaught
        entry(10, "Caught Common", rarity="Common", caught=True),  # too common to pad
        entry(11, "Caught Rare", rarity="Rare", caught=True),
        entry(12, "Caught VeryRare", rarity="Very Rare", caught=True),
    ]
    text = dex_panel_text(view(entries))
    assert "Caught VeryRare [Very Rare] ✓" in text
    assert "Caught Rare [Rare] ✓" in text
    assert "Caught Common" not in text  # common caught is not padded in
    assert "+" not in text  # no uncaught overflow
