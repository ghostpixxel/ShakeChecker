"""Tests for enemy-name OCR resolution.

The pure OCR-string -> species matching is unit-tested without OCR; a slower
integration test runs the real OCR engine on the named fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from battle_reader import load_calibration, read_enemy_bars
from name_reader import (
    ALPHA_CATCH_RATE,
    NameReader,
    clean_ocr_text,
    detect_gender,
    match_species_name,
    parse_level,
    strip_alpha_prefix,
)

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "fixtures"
SPECIES_PATH = ROOT / "src" / "data" / "species_core.json"
CAL = load_calibration(ROOT / "calibration.toml")
NAMES = [s["name"] for s in json.loads(SPECIES_PATH.read_text("utf-8"))]


def test_clean_ocr_text_strips_level_and_icons():
    assert clean_ocr_text("Cascoon Lv. 11 $ e") == "Cascoon"
    assert clean_ocr_text("Onix Lv. 43  e") == "Onix"
    assert clean_ocr_text("Mr. Mime Lv. 5") == "Mr. Mime"


def test_parse_level():
    assert parse_level("Cascoon Lv. 11 $ e") == 11
    assert parse_level("Onix Lv. 43  e") == 43
    assert parse_level("Tentacool Lv. 24 8 0") == 24
    assert parse_level("no level here") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Cascoon Lv. 11 $ e", "Cascoon"),
        ("Mantine Lv. 22  0", "Mantine"),
        ("Onix Lv. 43  e", "Onix"),
        ("Ursaring Lv. 40 9 e", "Ursaring"),
        ("Pachirisu Lv. 12 ", "Pachirisu"),
        ("Tentacool Lv. 24 8 0", "Tentacool"),
        ("0nix lv 43", "Onix"),  # OCR misreads still fuzzy-match
    ],
)
def test_match_species_name(raw, expected):
    assert match_species_name(raw, NAMES, CAL.name.min_match_score) == expected


def test_strip_alpha_prefix():
    assert strip_alpha_prefix("Alpha Noctowl Lv. 66") == (True, "Noctowl Lv. 66")
    assert strip_alpha_prefix("alpha pidgey lv 9") == (True, "pidgey lv 9")  # case-insensitive
    assert strip_alpha_prefix("Noctowl Lv. 66") == (False, "Noctowl Lv. 66")  # not an alpha
    assert strip_alpha_prefix("Alphasaur Lv. 5")[0] is False  # needs the trailing space


def test_match_rejects_garbage():
    garbage = "xqzw* ###"  # cspell:disable-line
    assert match_species_name(garbage, NAMES, CAL.name.min_match_score) is None
    assert match_species_name("", NAMES, CAL.name.min_match_score) is None


# --- OCR integration (slower: loads the ONNX model and runs inference) ---

_OCR_FIXTURES = [
    ("full_health_no_status", "Cascoon"),
    ("full_health_water", "Mantine"),
    ("two_third_green_health_cave", "Onix"),
    ("1_HP_red_health_no_status_cave", "Ursaring"),
    ("full_health_trainer_battle_poisoned", "Pachirisu"),
    ("1920x1080_resolution", "Tentacool"),
    # Gender-split pair: OCR drops the ♂/♀ glyph, so the gender icon decides.
    ("full_health_nidoran_male", "Nidoran♂"),
    ("full_health_nidoran_female", "Nidoran♀"),
]


# --- gender icon detection (no OCR: isolates the color logic) -------------

_GENDER_FIXTURES = [
    ("full_health_nidoran_male", "♂"),
    ("full_health_nidoran_female", "♀"),
]


@pytest.mark.parametrize(
    ("fixture", "expected"), _GENDER_FIXTURES, ids=[f for f, _ in _GENDER_FIXTURES]
)
def test_detect_gender_from_banner_icon(fixture, expected):
    img = cv2.imread(str(FIXTURES / f"{fixture}.png"))
    bars = read_enemy_bars(img, CAL)
    assert bars, f"no bar detected in {fixture}"
    b = bars[0]
    c = CAL.name
    crop = img[b.y + c.dy0 : b.y + c.dy1, b.x + c.dx0 : b.x + c.dx1]
    assert detect_gender(crop, c) == expected


@pytest.fixture(scope="module")
def name_reader():
    return NameReader(CAL.name, SPECIES_PATH)


@pytest.mark.parametrize(("fixture", "expected"), _OCR_FIXTURES, ids=[f for f, _ in _OCR_FIXTURES])
def test_ocr_reads_species_from_fixture(name_reader, fixture, expected):
    img = cv2.imread(str(FIXTURES / f"{fixture}.png"))
    bars = read_enemy_bars(img, CAL)
    assert bars, f"no bar detected in {fixture}"
    species = name_reader.read(img, bars[0])
    assert species is not None
    assert species["name"] == expected


# (fixture, base species name, dex id) -- the banner's "Alpha " prefix is stripped
# and the red sprite background marks the alpha instead, so the name stays base.
_ALPHA_FIXTURES = [
    ("wild_encounter_alpha_noctowl", "Noctowl", 164),
    ("wild_encounter_alpha_noctowl_slp_1hp", "Noctowl", 164),
    ("wild_encounter_alpha_houndoom", "Houndoom", 229),
    ("wild_encounter_alpha_houndoom_slp_1hp", "Houndoom", 229),
]


@pytest.mark.parametrize(
    ("fixture", "base_name", "dex_id"), _ALPHA_FIXTURES, ids=[f for f, _, _ in _ALPHA_FIXTURES]
)
def test_ocr_reads_alpha_with_fixed_rate(name_reader, fixture, base_name, dex_id):
    # base species name/id (no "Alpha" prefix), flagged alpha, and the fixed Alpha
    # catch rate of 10 regardless of the species' normal rate.
    img = cv2.imread(str(FIXTURES / f"{fixture}.png"))
    species = name_reader.read(img, read_enemy_bars(img, CAL)[0])
    assert species is not None
    assert species["name"] == base_name  # base name, no "Alpha" prefix
    assert species["id"] == dex_id  # base species drives the sprite/dex record
    assert species["alpha"] is True
    assert species["catch_rate"] == ALPHA_CATCH_RATE == 10
