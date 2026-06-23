from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from battle_reader import load_calibration
from location_reader import clean_location, is_cave_location, read_location

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "fixtures"
CAL = load_calibration(ROOT / "calibration.toml")


def test_clean_location_strips_channel_suffix():
    assert clean_location("Eterna Forest Ch. 2") == "Eterna Forest"
    assert clean_location(" Route 41 Ch.2") == "Route 41"
    assert clean_location("Wayward Cave Ch. 12") == "Wayward Cave"


def test_is_cave_by_keyword():
    assert is_cave_location("Wayward Cave") is True
    assert is_cave_location("Oreburgh Gate") is True
    assert is_cave_location("Oreburgh Mine") is True
    assert is_cave_location("Rock Tunnel") is True
    assert is_cave_location("Mt. Coronet") is True  # "coronet"


def test_is_cave_keyword_less():
    assert is_cave_location("Victory Road") is True
    assert is_cave_location("VictoryF Road") is True  # OCR noise tolerated
    assert is_cave_location("Ice Path") is True


def test_not_cave():
    assert is_cave_location("Eterna Forest") is False
    assert is_cave_location("Route 213") is False
    assert is_cave_location("Floaroma Town") is False
    assert is_cave_location("") is False


@pytest.mark.parametrize(
    ("name", "is_cave"),
    [
        ("two_third_green_health_cave.png", True),  # Victory Road
        ("red_health_no_status_cave.png", True),
        ("full_health_no_status.png", False),  # Eterna Forest
        ("full_health_water.png", False),  # Route 41
        ("overworld_city_running.png", False),  # Floaroma Town
    ],
)
def test_read_location_and_cave_on_fixtures(name, is_cave):
    img = cv2.imread(str(FIXTURES / name))
    assert is_cave_location(read_location(img, CAL.location)) is is_cave
