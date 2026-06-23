from __future__ import annotations

from pathlib import Path

import pytest

from account_store import CaughtStore
from dex_session import DexSession
from dex_tracker import EncounterData
from game_time import Period

ROOT = Path(__file__).parent.parent
DATA = ROOT / "src" / "data"


@pytest.fixture(scope="module")
def data() -> EncounterData:
    return EncounterData.load(DATA / "encounters.json", DATA / "legendaries.json")


def make_session(data, tmp_path, period=Period.DAY, season=0) -> DexSession:
    caught = CaughtStore.for_account(tmp_path, "Tester")
    return DexSession(data, caught, period_fn=lambda: period, season_fn=lambda: season)


def uncaught_ids(view):
    return [e.id for e in view.entries if not e.caught]


def test_on_location_builds_view(data, tmp_path):
    s = make_session(data, tmp_path, Period.DAY, 0)
    view = s.on_location("Viridian Forest")
    assert view is not None
    assert view.route == "VIRIDIAN FOREST"
    assert view.region == "KANTO"
    assert view.period is Period.DAY
    assert [e.id for e in view.entries] == sorted(e.id for e in view.entries)  # dex-sorted
    assert s.region == "KANTO"  # region got pinned


def test_ambiguous_location_resolves_after_region_known(data, tmp_path):
    s = make_session(data, tmp_path)
    res = s.on_location("Route 5")
    assert res is not None
    assert len(res.entries) == 0  # ambiguous, no region yet
    s.on_location("Viridian Forest")  # pins Kanto
    view = s.on_location("Route 5")
    assert view is not None and view.region == "KANTO"


def test_recording_caught_shrinks_the_uncaught_list(data, tmp_path):
    s = make_session(data, tmp_path, Period.DAY, 0)
    before = uncaught_ids(s.on_location("Viridian Forest"))
    target = before[0]
    assert s.record_caught(target) is True
    assert s.record_caught(target) is False  # already recorded
    after = uncaught_ids(s.on_location("Viridian Forest"))
    assert target not in after
    assert len(after) == len(before) - 1


def test_caught_persists_across_sessions(data, tmp_path):
    s1 = make_session(data, tmp_path, Period.DAY, 0)
    target = uncaught_ids(s1.on_location("Viridian Forest"))[0]
    s1.record_caught(target)
    # a fresh session for the same account reloads the caught set from disk
    s2 = make_session(data, tmp_path, Period.DAY, 0)
    assert target not in uncaught_ids(s2.on_location("Viridian Forest"))


def test_time_filtering_changes_the_view(data, tmp_path):
    # Viridian Forest has night-only bugs (Hoothoot, Spinarak): more at night
    day = uncaught_ids(make_session(data, tmp_path, Period.DAY, 0).on_location("Viridian Forest"))
    night = uncaught_ids(
        make_session(data, tmp_path, Period.NIGHT, 0).on_location("Viridian Forest")
    )
    assert len(night) > len(day)
