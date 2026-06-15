import json
from pathlib import Path

import pytest

from catch_calc import BattleContext, ball_multiplier, catch_probability, x_value

DATA = Path(__file__).parent.parent / "src" / "data"
BALL_LIST = json.loads((DATA / "balls.json").read_text("utf-8"))["balls"]
BALLS_BY_ID = {b["id"]: b for b in BALL_LIST}
# flat-rate balls only (conditional ones have no static rate)
BALLS = {b["id"]: b["rate"] for b in BALL_LIST if "rate" in b}
STATUS = json.loads((DATA / "status_rates.json").read_text("utf-8"))["rates"]


def test_bulbasaur_reference_case():
    # Bulbasaur (rate 45), 100% HP, Sleep, Poke Ball -> x = 30, P = 11.8%
    # (matches pokemmohub.com, see CLAUDE.md)
    x = x_value(1.0, 45, BALLS["poke"], STATUS["slp"])
    assert x == pytest.approx(30.0)
    p = catch_probability(1.0, 45, BALLS["poke"], STATUS["slp"])
    assert p == pytest.approx(0.118, abs=0.0005)


def test_status_rate_table():
    # Pin the catch multipliers: SLP/FRZ x2, PAR/PSN/BRN x1.5 (minor-status
    # bonus; PSN/BRN absent from Hub but confirmed x1.5 in-game), none x1.
    assert STATUS == {
        "none": 1.0,
        "slp": 2.0,
        "frz": 2.0,
        "par": 1.5,
        "psn": 1.5,
        "brn": 1.5,
    }


def test_guaranteed_catch_at_x_cap():
    # full HP contributes factor 1/3, so rate 255 with ball x3 gives x = 255 exactly -> 100%
    assert catch_probability(1.0, 255, ball_rate=3.0) == 1.0
    # anything above the cap stays 100%
    assert catch_probability(1.0, 255, ball_rate=4.0) == 1.0
    # just below the cap is < 100%
    assert catch_probability(1.0, 254, ball_rate=3.0) < 1.0


def test_lower_hp_increases_probability():
    full = catch_probability(1.0, 45)
    half = catch_probability(0.5, 45)
    sliver = catch_probability(0.01, 45)
    assert full < half < sliver


def test_one_third_hp_factor():
    # at p = 1/3 the HP factor is (3 - 2/3) / 3 = 7/9
    assert x_value(1 / 3, 90) == pytest.approx(90 * 7 / 9)


def test_status_and_ball_multipliers_compose():
    base = x_value(1.0, 45)
    assert x_value(1.0, 45, BALLS["ultra"], STATUS["par"]) == pytest.approx(base * 2.0 * 1.5)


def test_probability_bounds():
    for rate in (3, 45, 120, 200):
        for hp in (1.0, 0.5, 0.01):
            p = catch_probability(hp, rate)
            assert 0.0 < p <= 1.0


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        x_value(0.0, 45)
    with pytest.raises(ValueError):
        x_value(1.5, 45)
    with pytest.raises(ValueError):
        x_value(1.0, 0)


# --- conditional ball multipliers (ported from c4vv/CatchCalc pokeballs.js) ---


def mult(ball_id: str, ctx: BattleContext) -> float:
    return ball_multiplier(BALLS_BY_ID[ball_id], ctx)


def test_flat_balls_ignore_context():
    ctx = BattleContext(turns_completed=5, enemy_types=("water",), enemy_level=3)
    assert mult("poke", ctx) == 1.0
    assert mult("great", ctx) == 1.5
    assert mult("ultra", ctx) == 2.0
    assert mult("heal", ctx) == 1.25


def test_dream_ball_scales_with_sleep_turns():
    # PokeMMO capture calculator: 0/1/2/3 sleep turns -> 1x / 1.5x / 2.5x / 4x,
    # but ONLY while the enemy is asleep.
    def dream(n):
        return mult("dream", BattleContext(turns_asleep=n, enemy_asleep=True))

    assert dream(0) == 1.0
    assert dream(1) == 1.5
    assert dream(2) == 2.5
    assert dream(3) == 4.0
    assert dream(9) == 4.0  # capped at the 3-turn value


def test_dream_ball_requires_sleep_status():
    # awake -> no boost regardless of any leftover sleep-turn count
    assert mult("dream", BattleContext(turns_asleep=3, enemy_asleep=False)) == 1.0
    assert mult("dream", BattleContext(turns_asleep=0, enemy_asleep=False)) == 1.0


def test_luxury_is_one_not_two():
    # corrected from the Hub's 2.0; Luxury affects friendship, not catch rate
    assert mult("luxury", BattleContext()) == 1.0


def test_repeat_placeholder_is_one():
    assert mult("repeat", BattleContext(already_caught=True)) == 1.0


def test_quick_ball_first_turn_only():
    assert mult("quick", BattleContext(turns_completed=0)) == 5.0
    assert mult("quick", BattleContext(turns_completed=1)) == 1.0
    assert mult("quick", BattleContext(turns_completed=9)) == 1.0


def test_timer_ball_ramps_and_caps():
    assert mult("timer", BattleContext(turns_completed=0)) == pytest.approx(1.0)
    assert mult("timer", BattleContext(turns_completed=1)) == pytest.approx(1.3)
    assert mult("timer", BattleContext(turns_completed=5)) == pytest.approx(2.5)
    assert mult("timer", BattleContext(turns_completed=10)) == pytest.approx(4.0)  # capped
    assert mult("timer", BattleContext(turns_completed=99)) == pytest.approx(4.0)


def test_net_ball_water_or_bug_only():
    assert mult("net", BattleContext(enemy_types=("WATER", "FLYING"))) == 3.5
    assert mult("net", BattleContext(enemy_types=("Bug", "Poison"))) == 3.5
    assert mult("net", BattleContext(enemy_types=("Normal",))) == 1.0
    assert mult("net", BattleContext(enemy_types=())) == 1.0


def test_nest_ball_level_curve():
    assert mult("nest", BattleContext(enemy_level=1)) == pytest.approx(4.0)  # capped at 4
    assert mult("nest", BattleContext(enemy_level=16)) == pytest.approx(4.0)  # 7-3=4
    assert mult("nest", BattleContext(enemy_level=21)) == pytest.approx(3.0)  # 7-4=3
    assert mult("nest", BattleContext(enemy_level=40)) == pytest.approx(1.0)  # floor
    assert mult("nest", BattleContext(enemy_level=99)) == pytest.approx(1.0)


def test_dusk_ball_condition():
    assert mult("dusk", BattleContext(dusk_active=True)) == 2.5
    assert mult("dusk", BattleContext(dusk_active=False)) == 1.0


def test_unknown_rule_raises():
    with pytest.raises(ValueError):
        ball_multiplier({"id": "weird", "rule": "nonexistent"}, BattleContext())
