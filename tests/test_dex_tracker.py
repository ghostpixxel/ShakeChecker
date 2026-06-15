from __future__ import annotations

from pathlib import Path

import pytest

from dex_tracker import (
    DexEntry,
    EncounterData,
    RegionResolver,
    location_entries,
    select_display,
)

ROOT = Path(__file__).parent.parent
ENCOUNTERS = ROOT / "src" / "data" / "encounters.json"
LEGENDARIES = ROOT / "src" / "data" / "legendaries.json"


def enc(
    id,
    name,
    method="Grass",
    rarity="Common",
    periods=("MORNING", "DAY", "NIGHT"),
    seasons=(0, 1, 2, 3),
):
    return {
        "id": id,
        "name": name,
        "method": method,
        "rarity": rarity,
        "min_level": 5,
        "max_level": 5,
        "periods": list(periods),
        "seasons": list(seasons),
    }


# --- location_entries (pure) ---


def uncaught(entries):
    return [e for e in entries if not e.caught]


def test_filters_by_period_and_season():
    encs = [
        enc(1, "Bulbasaur", periods=("NIGHT",)),
        enc(2, "Ivysaur", seasons=(1,)),
        enc(3, "Venusaur"),  # all times/seasons
    ]
    entries = location_entries(encs, "DAY", 0, caught=set(), legendaries=set())
    assert [e.id for e in entries] == [3]  # only the all-times one is active now


def test_excludes_legendaries_and_flags_caught():
    encs = [enc(1, "Bulbasaur"), enc(150, "Mewtwo"), enc(16, "Pidgey")]
    entries = location_entries(encs, "DAY", 0, caught={1}, legendaries={150})
    assert [e.id for e in entries] == [1, 16]  # legendary dropped, caught kept (flagged)
    assert next(e for e in entries if e.id == 1).caught is True
    assert next(e for e in entries if e.id == 16).caught is False


def test_dedupes_by_species_and_collects_ways_sorted_by_id():
    encs = [
        enc(16, "Pidgey", method="Grass"),  # default walking -> no tag
        enc(16, "Pidgey", method="Dark Grass"),
        enc(1, "Bulbasaur", method="Grass"),
    ]
    entries = location_entries(encs, "DAY", 0, caught=set(), legendaries=set())
    assert [e.id for e in entries] == [1, 16]  # dex order
    assert next(e for e in entries if e.id == 1).ways == ()  # plain grass
    assert next(e for e in entries if e.id == 16).ways == ("Dark Grass",)


def test_headline_rarity_is_the_rarest_among_encounters():
    encs = [
        enc(1, "Bulbasaur", rarity="Very Common"),
        enc(1, "Bulbasaur", rarity="Rare"),  # rarest wins
    ]
    assert location_entries(encs, "DAY", 0, set(), set())[0].rarity == "Rare"


def test_ways_label_special_pheno_rods_and_lure_is_not_a_way():
    encs = [
        enc(1, "Bulbasaur", method="Grass", rarity="Lure"),  # lure in grass -> no way tag
        enc(6, "Chinchou", method="Water", rarity="Lure"),  # lure via surf -> way is the method
        enc(2, "Surskit", method="Grass"),  # plain grass -> no tag
        enc(2, "Surskit", method="Water"),  # surf
        enc(3, "Magikarp", method="Old Rod", rarity="Very Common"),
        enc(4, "Audino", method="Grass", rarity="Special"),  # rustling-grass pheno
        enc(5, "Drilbur", method="Dust Cloud", rarity="Special"),
    ]
    by = {e.id: e.ways for e in location_entries(encs, "DAY", 0, set(), set())}
    assert by[1] == ()  # Lure shown via rarity, grass is the default way
    assert by[6] == ("Water",)  # Lure via surf -> the method is the way
    assert by[2] == ("Water",)  # grass dropped, surf kept
    assert by[3] == ("Old Rod",)
    assert by[4] == ("Grass Pheno",)
    assert by[5] == ("Dust Pheno",)


def test_all_caught_yields_no_uncaught():
    encs = [enc(1, "Bulbasaur"), enc(16, "Pidgey")]
    entries = location_entries(encs, "DAY", 0, caught={1, 16}, legendaries=set())
    assert uncaught(entries) == []
    assert all(e.caught for e in entries)


# --- select_display (hybrid: uncaught first, pad tail with rarest caught) ---


def de(id, rarity="Common", caught=False, ways=()):
    return DexEntry(id, f"Mon{id}", ways, rarity, caught)


def test_select_shows_uncaught_first_with_plus_overflow():
    entries = [de(i) for i in range(1, 9)]  # 8 uncaught
    rows, hidden = select_display(entries, 5)
    assert [e.id for e in rows] == [1, 2, 3, 4, 5]
    assert hidden == 3


def test_select_pads_with_rarest_caught_when_room():
    entries = [
        de(1),  # uncaught
        de(2),  # uncaught
        de(10, "Common", caught=True),  # caught but too common to pad
        de(11, "Rare", caught=True),
        de(12, "Very Rare", caught=True),
        de(13, "Lure", caught=True),
    ]
    rows, hidden = select_display(entries, 5)
    assert hidden == 0
    assert [e.id for e in rows[:2]] == [1, 2]  # uncaught first
    # padded with the rarest caught of Lure/Rare/Very Rare: VeryRare, Rare, Lure
    assert [e.id for e in rows[2:]] == [12, 11, 13]
    assert all(e.caught for e in rows[2:])


def test_select_no_padding_when_uncaught_overflow():
    entries = [de(i) for i in range(1, 7)] + [de(99, "Very Rare", caught=True)]
    rows, hidden = select_display(entries, 5)
    assert all(not e.caught for e in rows)  # no room to pad
    assert hidden == 1


# --- EncounterData against the real vendored file ---


@pytest.fixture(scope="module")
def data() -> EncounterData:
    return EncounterData.load(ENCOUNTERS, LEGENDARIES)


def test_real_data_loads():
    d = EncounterData.load(ENCOUNTERS, LEGENDARIES)
    assert d.location_name("KANTO_VIRIDIAN_FOREST") == "VIRIDIAN FOREST"


def test_match_exact_name(data):
    assert data.match_location("Viridian Forest") == "KANTO_VIRIDIAN_FOREST"


def test_match_tolerates_channel_suffix_and_case(data):
    assert data.match_location("ROCK TUNNEL Ch. 2") == "KANTO_ROCK_TUNNEL"


def test_match_tolerates_ocr_noise(data):
    # a stray trailing char like OCR sometimes adds
    assert data.match_location("Viridian Forestl") == "KANTO_VIRIDIAN_FOREST"


def test_ambiguous_route_needs_region_hint(data):
    # "Route 5" exists in multiple regions -> ambiguous without a hint
    assert data.match_location("Route 5") is None
    assert data.match_location("Route 5", region="Kanto") == "KANTO_ROUTE_5"


def test_unknown_location_returns_none(data):
    assert data.match_location("Nonexistent Place 999") is None


def test_route_number_must_match_exactly_not_fuzzily(data):
    # Johto has no Route 5; it must NOT fuzzy-collapse into "Route 35".
    assert data.match_location("Route 5", region="Johto") is None
    assert data.match_location("Route 35", region="Johto") == "JOHTO_ROUTE_35"


# --- region resolution (stateful) ---


def test_regions_for_name(data):
    assert data.regions_for_name("Viridian Forest") == {"KANTO"}
    assert data.regions_for_name("Route 5") == {"KANTO", "UNOVA"}  # ambiguous
    assert data.regions_for_name("Nowhere 999") == set()


def test_resolver_pins_region_from_unique_location(data):
    r = RegionResolver(data)
    # ambiguous name before any region is known -> unresolved
    assert r.resolve("Route 5") is None
    # a region-unique location pins Kanto
    assert r.resolve("Viridian Forest") == "KANTO_VIRIDIAN_FOREST"
    assert r.region == "KANTO"
    # now the ambiguous route resolves against the remembered region
    assert r.resolve("Route 5") == "KANTO_ROUTE_5"


def test_resolver_takes_over_region_on_switch(data):
    r = RegionResolver(data)
    r.resolve("Viridian Forest")  # Kanto
    assert r.region == "KANTO"
    # arriving at a Unova-unique place switches the region (the harbour-town case)
    assert r.resolve("Pinwheel Forest") == "UNOVA_PINWHEEL_FOREST"
    assert r.region == "UNOVA"
    # the same ambiguous "Route 5" now resolves to Unova, not Kanto
    assert r.resolve("Route 5") == "UNOVA_ROUTE_5"


def test_missing_here_excludes_legendaries_and_caught(data):
    key = "KANTO_VIRIDIAN_FOREST"
    full = data.missing_here(key, "DAY", 0, caught=set())
    assert all(isinstance(m, DexEntry) and not m.caught for m in full)
    assert [m.id for m in full] == sorted(m.id for m in full)  # dex-sorted
    # catching the first removes exactly it from the missing list
    first = full[0].id
    after = data.missing_here(key, "DAY", 0, caught={first})
    assert first not in {m.id for m in after}
    assert len(after) == len(full) - 1


def test_entries_here_includes_caught_flagged(data):
    key = "KANTO_VIRIDIAN_FOREST"
    first = data.missing_here(key, "DAY", 0, caught=set())[0].id
    entries = data.entries_here(key, "DAY", 0, caught={first})
    assert next(e for e in entries if e.id == first).caught is True
