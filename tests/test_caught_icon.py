from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from battle_reader import load_calibration, read_battle, read_caught_icon

ROOT = Path(__file__).parent.parent
CAL = load_calibration(ROOT / "calibration.toml")


def first_bar(name: str):
    img = cv2.imread(str(ROOT / "fixtures" / name))
    reading = read_battle(img, CAL)
    assert reading.bars, f"no bar in {name}"
    return img, reading.bars[0]


# Fixtures whose enemy shows the red/white Poke Ball = OT caught.
OT_CAUGHT = [
    "full_health_water.png",  # Mantine
    "full_health_no_status.png",  # Cascoon
    "red_health_no_status_cave.png",  # Onix
    "full_health_paralyzed.png",
    "full_health_sleeping.png",
]


@pytest.mark.parametrize("name", OT_CAUGHT)
def test_detects_ot_caught_ball(name):
    img, bar = first_bar(name)
    assert read_caught_icon(img, bar, CAL.caught_icon) is True


def test_uncaught_has_no_ball():
    # Pachirisu: no ball, only a pink female symbol (must not read as red)
    img, bar = first_bar("full_health_trainer_battle_poisoned.png")
    assert read_caught_icon(img, bar, CAL.caught_icon) is False


def test_premier_ball_is_not_ot_caught():
    # Synthesize the non-OT case: whiten the red ball (a Premier Ball is mostly
    # white) -- the OT signal must disappear.
    img, bar = first_bar("full_health_water.png")
    assert read_caught_icon(img, bar, CAL.caught_icon) is True  # baseline: OT ball
    c = CAL.caught_icon
    h, w = img.shape[:2]
    x0, x1 = max(0, bar.x + c.dx0), min(w, bar.x + c.dx1)
    y0, y1 = max(0, bar.y + c.dy0), min(h, bar.y + c.dy1)
    band = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    red = ((hue <= c.red_h_low) | (hue >= c.red_h_high)) & (sat >= c.sat_min) & (val >= c.val_min)
    band[red] = (240, 240, 240)  # paint the red ball white
    assert read_caught_icon(img, bar, c) is False
