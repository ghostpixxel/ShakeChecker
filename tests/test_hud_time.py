from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from battle_reader import load_calibration
from game_time import is_dusk_ball_night
from location_reader import _CLOCK, read_game_clock

ROOT = Path(__file__).parent.parent
CAL = load_calibration(ROOT / "calibration.toml")


def test_clock_regex_parses_and_rejects():
    assert _CLOCK.search("Saturday, 22:41").group(0) == "22:41"
    assert _CLOCK.search("Wednesday, 09:27").group(0) == "09:27"
    assert _CLOCK.search("10.43 with an OCR dot").group(0) == "10.43"
    assert _CLOCK.search("$90,128") is None  # money is not a time
    assert _CLOCK.search("25:99") is None  # out of range


# (fixture name -> expected HH:MM on the HUD clock)
CLOCKS = {
    "red_health_no_status_cave.png": (10, 23),
    "full_health_water.png": (11, 47),
    "overworld_city_running.png": (9, 27),
    "two_third_green_health_cave.png": (10, 0),
    "1_HP_red_health_sleeping_cave.png": (10, 43),
}


@pytest.mark.parametrize(("name", "hm"), CLOCKS.items())
def test_reads_hud_clock(name, hm):
    img = cv2.imread(str(ROOT / "fixtures" / name))
    minute = read_game_clock(img, CAL.hud_time)
    assert minute == hm[0] * 60 + hm[1]


def test_dusk_ball_night_window_is_2100_to_0759():
    # The Dusk Ball window matches the in-game dark filter: 21:00 -> 07:59,
    # wider than the Night spawn period because it stays dark into early morning.
    def night(h, m=0):
        return is_dusk_ball_night(h * 60 + m)

    assert night(21, 0) and night(23, 59) and night(0, 0) and night(3, 59)
    assert night(4, 0) and night(7, 59)  # still dark in the early morning
    assert not night(8, 0) and not night(10, 23) and not night(20, 59)
