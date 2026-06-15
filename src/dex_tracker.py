"""Decide which species spawn at the current location that you still need.

Pure/injectable: the matching and missing-list logic take plain data so they are
unit-tested without files or screen capture. EncounterData wraps the vendored
`encounters.json` (built by scripts/update_data.py) plus the legendary exclusion
list and exposes the two operations the app needs:

- match_location(hud_name, region): map the OCR'd HUD location to a data key.
  The HUD shows only the bare name, but "Route 5" exists in several regions, so a
  region hint disambiguates; without one an ambiguous name returns None.
- missing_here(key, period, season, caught): the spawn list for that location at
  the given time/season, minus legendaries and minus what you've already caught,
  deduped by species and sorted by National Dex id (the display order).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process

# Same channel-suffix strip used for the cave heuristic, kept local to avoid a
# circular import with location_reader.
_CH_SUFFIX = re.compile(r"\s*ch\.?\s*\d+.*$", re.IGNORECASE)
# Default fuzzy threshold (rapidfuzz ratio 0-100) for tolerating OCR noise.
MATCH_THRESHOLD = 82.0


@dataclass(frozen=True)
class DexEntry:
    id: int  # National Dex id (sort key / display order)
    name: str
    ways: tuple[str, ...]  # how to encounter it here; empty = plain grass/cave walking
    rarity: str  # the rarest rarity among this species' active encounters here
    caught: bool  # already OT-caught on the active account


# Rarity ordering (higher = rarer) for picking a species' headline rarity and for
# ranking the "rarest already-caught" entries the hybrid list pads with.
_RARITY_RANK = {
    "Very Rare": 6,
    "Rare": 5,
    "Special": 4,
    "Lure": 3,
    "Uncommon": 2,
    "Horde": 1,
    "Common": 1,
    "Very Common": 0,
}
# Once everything common is caught, the list pads its tail with caught species of
# these "notable" rarities (user choice) so the rares stay visible.
PAD_RARITIES = frozenset({"Lure", "Rare", "Very Rare"})


def _normalize(name: str) -> str:
    """Lowercase, drop the channel suffix and punctuation, collapse whitespace.
    'Viridian Forest Ch. 2' / 'VIRIDIAN FOREST' both -> 'viridian forest'."""
    s = _CH_SUFFIX.sub("", name.strip().lower())
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _digits(name: str) -> tuple[str, ...]:
    """The number tokens in a name. 'route 5' -> ('5',). Used to keep fuzzy
    matching from collapsing 'Route 5' into 'Route 35' (a substring win)."""
    return tuple(re.findall(r"\d+", name))


# A "Special"-rarity encounter is a roaming phenomenon; label it by its method.
_PHENO = {
    "Grass": "Grass Pheno",
    "Water": "Water Pheno",
    "Shadow": "Shadow Pheno",
    "Dust Cloud": "Dust Pheno",
    "Fishing": "Fishing Pheno",
}
# Ambient walking encounters at normal rarity -- the default, so no tag.
_WALK = {"Grass", "Cave", "Inside"}


def encounter_tag(method: str, rarity: str) -> str:
    """A short label for HOW to find a species via one encounter, or "" for the
    default (walking in plain grass/cave). Phenomena (Special rarity) read as
    "<Pheno>", Lure spawns as "Lure", and everything non-walking (surf Water,
    fishing rods, Headbutt, Rocks, Honey Tree, Dark Grass, Shadow) as the method."""
    if rarity == "Special":
        return _PHENO.get(method, f"{method} Pheno")
    if rarity == "Lure":
        return "Lure"
    if method in _WALK:
        return ""
    return method


def available_here(encounters: list[dict], period: str, season: int) -> list[dict]:
    """The encounters active at this period AND season."""
    return [
        e for e in encounters if period in e["periods"] and season in e["seasons"]
    ]


def location_entries(
    encounters: list[dict],
    period: str,
    season: int,
    caught: set[int],
    legendaries: set[int],
) -> list[DexEntry]:
    """All non-legendary species available now (caught AND uncaught), deduped by
    id with their encounter ways and headline (rarest) rarity, sorted by dex id.
    The caught flag lets the display show a to-do list and pad it with rares."""
    by_id: dict[int, dict] = {}
    for e in available_here(encounters, period, season):
        pid = e["id"]
        if pid in legendaries:
            continue
        slot = by_id.setdefault(pid, {"name": e["name"], "ways": set(), "rarities": set()})
        tag = encounter_tag(e["method"], e["rarity"])
        if tag:
            slot["ways"].add(tag)
        slot["rarities"].add(e["rarity"])
    entries = []
    for pid, slot in sorted(by_id.items()):
        rarity = max(slot["rarities"], key=lambda r: _RARITY_RANK.get(r, 0))
        entries.append(
            DexEntry(pid, slot["name"], tuple(sorted(slot["ways"])), rarity, pid in caught)
        )
    return entries


def select_display(entries: list[DexEntry], limit: int) -> tuple[list[DexEntry], int]:
    """Pick the rows to show and how many uncaught are hidden ("+X").

    Uncaught first, in dex order (the to-do list). If they all fit and leave room,
    pad the tail with the rarest already-caught species of PAD_RARITIES so the
    notable rares stay visible even once caught. Returns (rows, hidden_uncaught)."""
    uncaught = [e for e in entries if not e.caught]
    rows = uncaught[:limit]
    hidden = len(uncaught) - len(rows)
    if hidden == 0 and len(rows) < limit:
        rares = sorted(
            (e for e in entries if e.caught and e.rarity in PAD_RARITIES),
            key=lambda e: (-_RARITY_RANK[e.rarity], e.id),
        )
        rows = rows + rares[: limit - len(rows)]
    return rows, hidden


class EncounterData:
    """Loads the vendored encounter + legendary data and answers location/missing
    queries. Read-only; safe to share."""

    def __init__(self, locations: dict[str, dict], legendaries: set[int]) -> None:
        self._locations = locations
        self._legendaries = legendaries
        # normalized name -> [keys] (a name can repeat across regions)
        self._by_norm: dict[str, list[str]] = {}
        for key, loc in locations.items():
            self._by_norm.setdefault(_normalize(loc["name"]), []).append(key)

    @classmethod
    def load(cls, encounters_path: Path | str, legendaries_path: Path | str) -> EncounterData:
        enc = json.loads(Path(encounters_path).read_text("utf-8"))["locations"]
        leg = set(json.loads(Path(legendaries_path).read_text("utf-8"))["ids"])
        return cls(enc, leg)

    def location_name(self, key: str) -> str:
        return self._locations[key]["name"]

    def location_for_key(self, key: str) -> dict:
        """The full location record (name, region, encounters) for a key."""
        return self._locations[key]

    def _candidate_keys(self, norm: str, region_u: str | None) -> list[str]:
        """All location keys matching a normalized name (region-filtered): exact
        normalized matches if any, else the best fuzzy match for OCR noise. The
        number tokens must match exactly so fuzzy can't turn "Route 5" into
        "Route 35"; only the word part is matched fuzzily."""

        def in_region(key: str) -> bool:
            return region_u is None or self._locations[key]["region"].upper() == region_u

        exact = [k for k in self._by_norm.get(norm, []) if in_region(k)]
        if exact:
            return exact
        qd = _digits(norm)
        candidates = {
            n: keys
            for n, keys in self._by_norm.items()
            if _digits(n) == qd and any(in_region(k) for k in keys)
        }
        if not candidates:
            return []
        best = process.extractOne(norm, candidates.keys(), scorer=fuzz.WRatio)
        if best is None or best[1] < MATCH_THRESHOLD:
            return []
        return [k for k in candidates[best[0]] if in_region(k)]

    def match_location(self, hud_name: str, region: str | None = None) -> str | None:
        """Resolve an OCR'd HUD location name to a data key.

        With a region hint, only that region's locations are considered (so the
        shared "Route 5" name is unambiguous). Returns None if nothing clears the
        threshold or the name is ambiguous across regions with no usable hint.
        """
        norm = _normalize(hud_name)
        if not norm:
            return None
        keys = self._candidate_keys(norm, region.upper() if region else None)
        return keys[0] if len(keys) == 1 else None

    def regions_for_name(self, hud_name: str) -> set[str]:
        """The set of regions a HUD location name could belong to (ignoring any
        hint). A single-element set means the name pins the region down."""
        norm = _normalize(hud_name)
        if not norm:
            return set()
        return {self._locations[k]["region"] for k in self._candidate_keys(norm, None)}

    def entries_here(
        self, key: str, period: str, season: int, caught: set[int]
    ) -> list[DexEntry]:
        """All non-legendary species available now at a location (caught + uncaught)."""
        loc = self._locations.get(key)
        if loc is None:
            return []
        return location_entries(loc["encounters"], period, season, caught, self._legendaries)

    def missing_here(
        self, key: str, period: str, season: int, caught: set[int]
    ) -> list[DexEntry]:
        """Just the uncaught entries (convenience for the dev scripts)."""
        return [e for e in self.entries_here(key, period, season, caught) if not e.caught]


class RegionResolver:
    """Tracks the current region so ambiguous location names ("Route 5" exists in
    Kanto and Unova) resolve correctly, with no manual region input.

    The HUD shows only the bare location name. As soon as a name pins the region
    (it exists in exactly one region -- forests, caves, most named places), we
    adopt it; switching regions means passing through such a place (e.g. the
    harbour town you arrive in), so the region is taken over automatically.
    Ambiguous names then resolve against the remembered region. Encounter-less
    towns aren't in the data and simply don't change the region.
    """

    def __init__(self, data: EncounterData) -> None:
        self._data = data
        self.region: str | None = None

    def reset(self) -> None:
        self.region = None

    def resolve(self, hud_name: str) -> str | None:
        """Location key for the current HUD name, updating the tracked region when
        the name determines it. Returns None if unmatched or still ambiguous."""
        regions = self._data.regions_for_name(hud_name)
        if len(regions) == 1:
            self.region = next(iter(regions))  # name pins the region -> adopt/switch
        return self._data.match_location(hud_name, self.region)
