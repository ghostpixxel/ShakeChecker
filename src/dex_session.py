"""Tie the dex-tracker pieces together for live use.

DexSession is the stateful glue between the screen readers and the data layer:
it remembers the current region (RegionResolver), knows the active account's
caught set (CaughtStore), and turns a HUD location name into the "still needed
here" view, filtered by the current in-game time/season. It also records a
species as caught when the in-battle OT ball icon is seen.

Pure of screen capture: callers feed it the OCR'd location name and the resolved
enemy species id; this module never touches the screen. Time defaults to the
real UTC clock but is injectable for tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from account_store import CaughtStore
from dex_tracker import DexEntry, EncounterData, RegionResolver
from game_time import Period, current_period, current_season


@dataclass(frozen=True)
class LocationView:
    """What to show for the current location."""

    route: str  # display name, shown as the panel header
    region: str
    period: Period
    season: int
    entries: list[DexEntry]  # all available species (caught + uncaught), dex-sorted


class DexSession:
    def __init__(
        self,
        data: EncounterData,
        caught: CaughtStore,
        period_fn: Callable[[], Period] = current_period,
        season_fn: Callable[[], int] = current_season,
    ) -> None:
        self._data = data
        self._caught = caught
        self._resolver = RegionResolver(data)
        self._period_fn = period_fn
        self._season_fn = season_fn

    @property
    def region(self) -> str | None:
        return self._resolver.region

    def seed_region(self, region: str | None) -> None:
        """Pre-set the tracked region (a manual override / starting hint). A later
        region-unique location still takes over automatically."""
        self._resolver.region = region.upper() if region else None

    def on_location(self, hud_name: str) -> LocationView | None:
        """Resolve the HUD location (updating the tracked region) and build the
        missing-here view for the current time/season. None if the location can't
        be matched yet (unknown name, or an ambiguous one before a region is known)."""
        key = self._resolver.resolve(hud_name)
        if key is None:
            return None
        period = self._period_fn()
        season = self._season_fn()
        entries = self._data.entries_here(key, period.value, season, self._caught.caught)
        loc = self._data.location_for_key(key)
        return LocationView(loc["name"], loc["region"], period, season, entries)

    def record_caught(self, species_id: int) -> bool:
        """Mark a species OT-caught (call when the OT ball icon is seen). Returns
        True if it was newly recorded, so the caller can log it once."""
        return self._caught.add(species_id)
