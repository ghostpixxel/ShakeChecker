import json
from pathlib import Path

import cv2
import pytest

from battle_reader import (
    BattleState,
    is_battle_ui_present,
    is_trainer_battle,
    load_calibration,
    read_battle,
)

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text("utf-8"))
META = EXPECTED["_meta"]
CAL = load_calibration(ROOT / "calibration.toml")

CASES = sorted(EXPECTED["fixtures"].items())
IDS = [name for name, _ in CASES]

STATE_BY_BAR_COUNT = {0: BattleState.NO_BATTLE, 1: BattleState.SINGLE, 2: BattleState.MULTI}


def read(name: str):
    img = cv2.imread(str(FIXTURES / name))
    assert img is not None, f"fixture {name} not readable"
    return read_battle(img, CAL)


@pytest.mark.parametrize(("name", "exp"), CASES, ids=IDS)
def test_bar_count(name, exp):
    reading = read(name)
    assert len(reading.bars) == len(exp["enemies"])
    assert reading.state == STATE_BY_BAR_COUNT[len(exp["enemies"])]


@pytest.mark.parametrize(
    ("name", "exp"),
    [(n, e) for n, e in CASES if e["enemies"]],
    ids=[n for n, e in CASES if e["enemies"]],
)
def test_hp_and_color(name, exp):
    reading = read(name)
    assert len(reading.bars) == len(exp["enemies"])
    for bar, enemy in zip(reading.bars, exp["enemies"], strict=True):
        assert bar.hp_pct == pytest.approx(enemy["hp_pct"], abs=META["hp_tolerance_pct"])
        assert bar.hp_pct > 0.0
        assert bar.color == enemy["hp_color"]


@pytest.mark.parametrize(
    ("name", "exp"),
    [(n, e) for n, e in CASES if e["enemies"]],
    ids=[n for n, e in CASES if e["enemies"]],
)
def test_status(name, exp):
    reading = read(name)
    assert len(reading.bars) == len(exp["enemies"])
    for bar, enemy in zip(reading.bars, exp["enemies"], strict=True):
        assert bar.status.value == enemy["status"]


CHAT_MIN = sorted(EXPECTED["chat_minimized_fixtures"].items())


@pytest.mark.parametrize(("name", "exp"), CHAT_MIN, ids=[n for n, _ in CHAT_MIN])
def test_hp_bar_visibility_after_animation(name, exp):
    # The chat-independent turn fallback relies on detecting when the enemy HP
    # bar is present vs hidden by the attack animation.
    img = cv2.imread(str(FIXTURES / name))
    assert img is not None, f"missing fixture {name}"
    assert bool(read_battle(img, CAL).bars) is exp["bar_visible"]


BATTLE_SCENES = {"wild_single", "wild_double", "trainer"}


@pytest.mark.parametrize(("name", "exp"), CASES, ids=IDS)
def test_battle_ui_presence(name, exp):
    # The command panel marks a battle; overworld and login screen have none.
    img = cv2.imread(str(FIXTURES / name))
    expected_present = exp["scene"] in BATTLE_SCENES
    assert is_battle_ui_present(img, CAL.battle_ui) is expected_present


def test_horde_counts_all_bars():
    # three wild bars across the top -> MULTI (the overlay waits for one to remain)
    reading = read("horde_triple_wild_encounter.png")
    assert len(reading.bars) == 3
    assert reading.state == BattleState.MULTI


def test_five_horde_is_multi():
    # the 5-horde's staggered two-row layout isn't fully counted, but >=2 bars are
    # found, so the state is MULTI and the overlay stays hidden during the horde.
    reading = read("battle_frame_5x_horde.png")
    assert len(reading.bars) >= 2
    assert reading.state == BattleState.MULTI


@pytest.mark.parametrize(
    ("name", "is_trainer"),
    [
        ("full_health_trainer_battle_no_status.png", True),
        ("full_health_trainer_battle_poisoned.png", True),
        ("full_health_no_status.png", False),
        ("full_health_water.png", False),
        ("two_third_green_health_cave.png", False),
        ("red_health_no_status_cave.png", False),
        ("1920x1080_resolution.png", False),
    ],
)
def test_trainer_battle_detection(name, is_trainer):
    # the party-icon strip below the enemy bar marks a trainer battle
    img = cv2.imread(str(FIXTURES / name))
    bars = read_battle(img, CAL).bars
    assert bars, name
    assert is_trainer_battle(img, bars[0], CAL.trainer) is is_trainer
