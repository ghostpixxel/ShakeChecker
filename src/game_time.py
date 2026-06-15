"""Compute the in-game time period and season from the real-world UTC clock.

PokeMMO's day/night cycle is deterministic and server-anchored to UTC, so we
compute it instead of OCR-ing the HUD clock (CLAUDE.md milestone 4): one PokeMMO
day = 6 real hours, the cycle restarting at 00:00/06:00/12:00/18:00 UTC. Always
work from UTC, never local time, so daylight-saving never shifts the result.

    game_minutes = (minutes_since_utc_midnight % 360) * 4   # 0..1439

Periods (in game time):
    Morning 04:00-10:59, Day 11:00-20:59, Night 21:00-03:59

Seasons cycle monthly (4 seasons). The exact month->SEASONn mapping in the
encounter data is anchored once and verified against the game -- see SEASON_OF_MONTH.
"""

from __future__ import annotations

import datetime as _dt
import enum


class Period(enum.StrEnum):
    MORNING = "MORNING"
    DAY = "DAY"
    NIGHT = "NIGHT"


# Minutes in one in-game day, and the real-minutes:game-minutes ratio (a 6h real
# cycle maps onto a 24h game day -> 24/6 = 4 game-minutes per real-minute).
GAME_DAY_MINUTES = 1440
REAL_CYCLE_MINUTES = 360
GAME_MINUTES_PER_REAL = GAME_DAY_MINUTES // REAL_CYCLE_MINUTES  # 4

# PokeMMO seasons change every real-world MONTH, cycling Spring -> Summer ->
# Autumn -> Winter and repeating every four months (so each season recurs three
# times a year). Verified against the PokeMMO wiki and confirmed in-game (June =
# Summer). The encounter data's SEASON0..3 use the Gen 5 internal order, so:
#   Spring=0, Summer=1, Autumn=2, Winter=3
# and the index for a month is simply (month - 1) % 4 (Jan = Spring).
SEASON_NAMES = ("Spring", "Summer", "Autumn", "Winter")


def game_minute_of_day(now_utc: _dt.datetime) -> int:
    """In-game minute-of-day (0..1439) for a UTC datetime."""
    minutes_since_midnight = now_utc.hour * 60 + now_utc.minute
    return (minutes_since_midnight % REAL_CYCLE_MINUTES) * GAME_MINUTES_PER_REAL


def period_for_game_minute(game_minute: int) -> Period:
    """Map an in-game minute-of-day to its period (see module docstring)."""
    hour = game_minute // 60
    if 4 <= hour <= 10:
        return Period.MORNING
    if 11 <= hour <= 20:
        return Period.DAY
    return Period.NIGHT  # 21-23 and 0-3


def season_for_month(month: int) -> int:
    """SEASONn index (0..3) for a calendar month (1..12). Jan = Spring (0); one
    season per month, wrapping every four months."""
    return (month - 1) % 4


def season_name(index: int) -> str:
    """Human name for a SEASONn index (0..3)."""
    return SEASON_NAMES[index % 4]


# Dusk Ball boost window (game-clock minutes). The overworld stays visibly dark
# from night into the early "morning" -- confirmed in-game dark until ~08:00 with
# the sunrise marker -- so the Dusk Ball window is WIDER than the Night spawn
# period (21:00-03:59): it runs 21:00 -> 07:59. Adjust these two if in-game
# testing shows the boost cuts off elsewhere.
DUSK_NIGHT_START = 21 * 60  # 21:00
DUSK_NIGHT_END = 8 * 60  # 08:00 (exclusive)


def is_dusk_ball_night(game_minute: int) -> bool:
    """Whether the Dusk Ball gets its night boost at this game-clock minute."""
    return game_minute >= DUSK_NIGHT_START or game_minute < DUSK_NIGHT_END


def current_game_minute(now_utc: _dt.datetime | None = None) -> int:
    """The current in-game minute-of-day computed from UTC (deterministic)."""
    now = now_utc or _dt.datetime.now(_dt.UTC)
    return game_minute_of_day(now)


def current_period(now_utc: _dt.datetime | None = None) -> Period:
    now = now_utc or _dt.datetime.now(_dt.UTC)
    return period_for_game_minute(game_minute_of_day(now))


def current_season(now_utc: _dt.datetime | None = None) -> int:
    now = now_utc or _dt.datetime.now(_dt.UTC)
    return season_for_month(now.month)
