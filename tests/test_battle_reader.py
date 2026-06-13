import json
from pathlib import Path

import cv2
import pytest

from battle_reader import BattleState, is_battle_ui_present, load_calibration, read_battle

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


BATTLE_SCENES = {"wild_single", "wild_double", "trainer"}


@pytest.mark.parametrize(("name", "exp"), CASES, ids=IDS)
def test_battle_ui_presence(name, exp):
    # The command panel marks a battle; overworld and login screen have none.
    img = cv2.imread(str(FIXTURES / name))
    expected_present = exp["scene"] in BATTLE_SCENES
    assert is_battle_ui_present(img, CAL.battle_ui) is expected_present
