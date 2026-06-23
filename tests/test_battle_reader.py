import json
from pathlib import Path

import cv2
import pytest

from battle_log import read_turn_number
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


def test_horde_status_read_at_horde_offset():
    # In a spread horde the status badge sits RIGHT of the fill (not left as in a
    # single), so it's read with the horde offset. Both fixtures are all-asleep.
    for name in ("horde_triple_wild_encounter.png", "5x_horde_sleep.png"):
        reading = read(name)
        assert reading.is_horde is True, name
        assert reading.bars, name
        assert all(b.status.value == "slp" for b in reading.bars), name


def test_horde_remnant_uses_horde_offset_via_hint():
    # A horde narrowed to ONE bar reads as SINGLE, so layout can't tell it's a
    # horde; the caller's horde=True hint keeps the right-side badge offset.
    img = cv2.imread(str(FIXTURES / "horde_triple_wild_encounter.png"))
    # crop to a single horde bar (the third) so only one is detected
    one = img.copy()
    one[:, : int(img.shape[1] * 0.52)] = 0  # blank out the left two bars
    assert read_battle(one, CAL, horde=True).bars[0].status.value == "slp"


def test_horde_remnant_detected_by_position_without_hint():
    # The real live case: a horde narrowed to one remnant, no horde hint carried
    # over. The lone bar sits at the horde slot (centre), well right of the single-
    # enemy slot (~0.17), so it's still recognised as a horde -> right-side status
    # badge. This is what makes SLP read on the last Kingler of a farmed horde.
    img = cv2.imread(str(FIXTURES / "horde_triple_wild_encounter.png"))
    one = img.copy()
    one[:, : int(img.shape[1] * 0.52)] = 0  # keep only the rightmost (third) bar
    reading = read_battle(one, CAL, horde=False)  # NO hint -- position alone
    assert len(reading.bars) == 1
    assert reading.bars[0].x > one.shape[1] * 0.30  # a centre/right remnant
    assert reading.is_horde is True
    assert reading.bars[0].status.value == "slp"


def test_single_enemy_not_flagged_as_remnant():
    # A genuine single sits in the left slot (~0.17) -> NOT a horde, left-side badge
    # so SLP still reads via the normal single offset (no false horde-layout).
    reading = read("full_health_sleeping.png")
    assert reading.state == BattleState.SINGLE
    assert reading.is_horde is False
    assert reading.bars[0].status.value == "slp"


# --- real ultrawide 5x-horde fixtures (3435 px wide, farmed Kingler horde) ---


def test_ultrawide_horde_two_remnants_stacked():
    # A 5x horde narrowed to its two left-column mons: both bars stack at the SAME
    # centre x (not the left single slot), so they must still read as a MULTI horde
    # (overlay stays hidden until one remains). Locks the "any bar past the slot"
    # horde-layout rule against a real wide-window capture.
    img = cv2.imread(str(FIXTURES / "5x_horde_2leftremain_3435x1438.png"))
    reading = read_battle(img, CAL)
    assert len(reading.bars) == 2
    assert reading.state == BattleState.MULTI
    assert reading.is_horde is True
    assert all(b.x > img.shape[1] * 0.30 for b in reading.bars)  # centre, not left slot


def test_ultrawide_horde_remnant_reads_slp():
    # THE live bug: the last Kingler of a farmed 5x horde sits at the centre slot,
    # asleep. Its status badge is on the RIGHT of the fill (horde layout); position
    # alone (no hint) must pick that up so SLP reads -> Dream Ball scales correctly.
    reading = read("5x_horde_lowerleftremnant_slp_3435x1438.png")
    assert reading.state == BattleState.SINGLE
    assert reading.is_horde is True
    assert reading.bars[0].status.value == "slp"


def test_chat_turn_reads_on_ultrawide_window():
    # The chat crop is width-capped so it stays legible on very wide windows: at
    # 3435 px the old 0.40-of-width crop ballooned and OCR shrank the text until the
    # turn stopped updating live. Both ultrawide fixtures must still yield the turn.
    one = cv2.imread(str(FIXTURES / "5x_horde_lowerleftremnant_slp_3435x1438.png"))
    assert read_turn_number(one, CAL.chat) == 5
    two = cv2.imread(str(FIXTURES / "5x_horde_2leftremain_3435x1438.png"))
    assert read_turn_number(two, CAL.chat) == 2


def test_double_status_not_broken_by_horde_logic():
    # a 2x double stacks bars at the left -> single offset; status still read
    reading = read("double_battle_red_health_burn.png")
    assert reading.is_horde is False
    assert reading.bars[0].status.value == "brn"
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
        ("batle_minimized_chat.png", False),  # wild battle, chat minimized
        # rain: diagonal streaks under the bar push edge density into the trainer
        # range (0.0095), but they aren't compact icon blobs -> still wild.
        ("rain_wild_encounter.png", False),
        ("rain_wild_encounter_3.png", False),
        ("rain_wild_encounter_4.png", False),
        # KNOWN LIMITATION: rain_wild_encounter_2.png is misread as a trainer -- a
        # rain streak forms a single compact blob, and a real trainer party strip
        # also shows just one blob here, so they're indistinguishable by blob count.
        # Left out rather than tightening the gate (that would break real trainers).
    ],
)
def test_trainer_battle_detection(name, is_trainer):
    # the party-icon strip below the enemy bar marks a trainer battle
    img = cv2.imread(str(FIXTURES / name))
    bars = read_battle(img, CAL).bars
    assert bars, name
    assert is_trainer_battle(img, bars[0], CAL.trainer) is is_trainer
