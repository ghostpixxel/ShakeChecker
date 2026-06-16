"""Tests for the pure battle-loop decisions extracted from the LiveLoop. These
lock the exact turn-correction and battle-end-grace behaviour that previously
lived inline in app.py and could only be checked by playing the game."""

from battle_logic import apply_chat_turn, battle_end_grace, is_in_battle
from battle_reader import BattleState, BattleText
from turn_tracker import TurnTracker

MENU = BattleText(menu_present=True, caught=False, action=False)
EMPTY = BattleText(menu_present=False, caught=False, action=False)
ACTION = BattleText(menu_present=False, caught=False, action=True)
CAUGHT = BattleText(menu_present=False, caught=True, action=False)


# --- is_in_battle ---------------------------------------------------------


def test_in_battle_from_each_signal():
    assert is_in_battle(BattleState.SINGLE, EMPTY) is True  # enemy bar
    assert is_in_battle(BattleState.MULTI, EMPTY) is True  # horde bars
    assert is_in_battle(BattleState.NO_BATTLE, MENU) is True  # command menu
    assert is_in_battle(BattleState.NO_BATTLE, ACTION) is True  # "X used Y!"
    assert is_in_battle(BattleState.NO_BATTLE, CAUGHT) is True  # catch banner


def test_not_in_battle_when_no_signal():
    assert is_in_battle(BattleState.NO_BATTLE, EMPTY) is False


# --- battle_end_grace -----------------------------------------------------


def test_grace_trainer_beats_everything():
    assert battle_end_grace(True, True, trainer_s=6.0, anim_s=4.0, normal_s=1.0) == 6.0
    assert battle_end_grace(True, False, trainer_s=6.0, anim_s=4.0, normal_s=1.0) == 6.0


def test_grace_animation_when_panel_up():
    assert battle_end_grace(False, True, trainer_s=6.0, anim_s=4.0, normal_s=1.0) == 4.0


def test_grace_short_when_back_in_overworld():
    assert battle_end_grace(False, False, trainer_s=6.0, anim_s=4.0, normal_s=1.0) == 1.0


# --- apply_chat_turn ------------------------------------------------------


def _tracker(turns_completed: int) -> TurnTracker:
    t = TurnTracker()
    # simulate having reached `turns_completed` via the menu path
    for _ in range(turns_completed):
        t.observe_menu(True, False)  # menu up (turn prompt)
        t.observe_menu(False, True)  # action committed
        t.observe_menu(True, False)  # menu returns -> a turn counts
    assert t.turns_completed == turns_completed
    return t


def test_no_reading_is_noop():
    t = _tracker(3)
    out = apply_chat_turn(t, None, asleep=False, now=10.0, last_advance=0.0, down_guard_s=3.0)
    assert out == "none"
    assert t.turns_completed == 3


def test_ignored_during_turn_one():
    # THE battle-boundary bug: a stale "Turn 11" from the previous battle arrives
    # while the new battle is still on turn 1. It must NOT move the counter.
    t = _tracker(0)
    out = apply_chat_turn(t, 11, asleep=False, now=10.0, last_advance=0.0, down_guard_s=3.0)
    assert out == "start"
    assert t.turns_completed == 0  # untouched -- stays at turn 1


def test_up_correction_for_missed_turn():
    # menu missed a turn (e.g. a 2-turn move): chat says turn 4 -> 3 completed.
    t = _tracker(2)  # counter at turn 3
    out = apply_chat_turn(t, 4, asleep=False, now=10.0, last_advance=0.0, down_guard_s=3.0)
    assert out == "up"
    assert t.turns_completed == 3


def test_equal_reading_only_resyncs_sleep():
    t = _tracker(3)  # counter at turn 4
    out = apply_chat_turn(t, 4, asleep=False, now=10.0, last_advance=0.0, down_guard_s=3.0)
    assert out == "equal"
    assert t.turns_completed == 3


def test_down_correction_after_guard_elapsed():
    # menu over-counted to turn 6; chat says turn 4 and the menu has been quiet
    # longer than the guard -> trust the lower chat value.
    t = _tracker(5)  # counter at turn 6
    out = apply_chat_turn(t, 4, asleep=False, now=10.0, last_advance=0.0, down_guard_s=3.0)
    assert out == "down"
    assert t.turns_completed == 3


def test_down_correction_blocked_within_guard():
    # the same low read right after a real advance must NOT drag the count down.
    t = _tracker(5)  # counter at turn 6
    out = apply_chat_turn(t, 4, asleep=False, now=10.5, last_advance=10.0, down_guard_s=3.0)
    assert out == "equal"  # not down; observe() can't lower, leaves it as-is
    assert t.turns_completed == 5


def test_sleep_turns_accrue_while_asleep():
    # Dream Ball needs the consecutive-sleep counter to advance with the chat turns.
    t = _tracker(2)  # counter at turn 3
    apply_chat_turn(t, 3, asleep=True, now=10.0, last_advance=0.0, down_guard_s=3.0)  # sync
    before = t.turns_asleep
    apply_chat_turn(t, 4, asleep=True, now=10.0, last_advance=0.0, down_guard_s=3.0)  # +1 turn
    assert t.turns_asleep == before + 1


def test_sleep_resets_when_awake():
    t = _tracker(2)
    apply_chat_turn(t, 4, asleep=True, now=10.0, last_advance=0.0, down_guard_s=3.0)
    assert t.turns_asleep > 0
    apply_chat_turn(t, 5, asleep=False, now=10.0, last_advance=0.0, down_guard_s=3.0)
    assert t.turns_asleep == 0  # awake -> Dream Ball back to x1 immediately
