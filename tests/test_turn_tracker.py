from __future__ import annotations

from pathlib import Path

import cv2

from battle_log import parse_turn_number, read_turn_number
from battle_reader import BattleTextReader, load_calibration
from turn_tracker import TurnTracker

ROOT = Path(__file__).parent.parent
CAL = load_calibration(ROOT / "calibration.toml")
BTEXT = BattleTextReader(CAL.battle_text, ROOT / "src" / "data" / "templates")


def test_read_turn_number_from_chat_fixture():
    # full_health_no_status.png's chat shows "[Battle] Turn 2 started!"
    img = cv2.imread(str(ROOT / "fixtures" / "full_health_no_status.png"))
    assert read_turn_number(img, CAL.chat) == 2


def test_read_turn_number_none_in_overworld():
    img = cv2.imread(str(ROOT / "fixtures" / "overworld_city_running.png"))
    assert read_turn_number(img, CAL.chat) is None


def test_read_high_turn_number_from_live_frame():
    # real long-battle frame (chat shows "Turn 9 started!"); the chat read must
    # return 9 so the counter self-corrects instead of sticking on an old turn.
    img = cv2.imread(str(ROOT / "fixtures" / "live_turn9_gible.png"))
    assert read_turn_number(img, CAL.chat) == 9


def test_chat_corrects_an_undercounting_fallback():
    # the menu fallback under-counted (stuck at "turn 5"); a later chat reading of
    # a higher turn must correct turns_completed, not be ignored.
    t = TurnTracker()
    t.observe(5, enemy_asleep=False)
    assert t.turns_completed == 4
    t.observe(9, enemy_asleep=False)
    assert t.turns_completed == 8


def _bt(name):
    return BTEXT.read(cv2.imread(str(ROOT / "fixtures" / name)))


def test_template_detects_command_menu():
    # menu visible at two aspect ratios and with the chat minimized
    for name in (
        "full_health_no_status.png",
        "1920x1080_resolution.png",
        "batle_minimized_chat.png",
    ):
        assert _bt(name).menu_present is True, name


def test_template_no_menu_during_action_or_submenu():
    for name in (
        "batle_action_attack_selected.png",  # action textbox
        "two_third_green_health_cave.png",  # move submenu
        "overworld_city_running.png",  # not in battle
    ):
        assert _bt(name).menu_present is False, name


def test_template_detects_catch_fixture():
    bt = _bt("batle_action_pokemon_catched_text_after pokeball_disapeared.png")
    assert bt.caught is True
    assert bt.menu_present is False


def test_template_no_catch_before_text_or_in_normal_battle():
    assert _bt("batle_action_pokemon_catched_dark_pokeballpng.png").caught is False
    assert _bt("full_health_no_status.png").caught is False


def test_parse_turn_number():
    assert parse_turn_number(["[Battle] Turn 2 started!"]) == 2
    assert parse_turn_number(["[Battle] Turn2started"]) == 2  # OCR drops spaces
    assert parse_turn_number(["[Battle] The wild Cascoon woke up!", "Turn 5 started!"]) == 5
    assert parse_turn_number(["nothing here", "[Battle] Cascoon used Tackle!"]) is None
    assert parse_turn_number([]) is None


def test_parse_turn_number_takes_highest():
    assert parse_turn_number(["Turn 3 started", "Turn 4 started", "Turn 2 started"]) == 4


def test_tracker_starts_at_zero():
    t = TurnTracker()
    assert t.turns_completed == 0
    assert t.turns_asleep == 0


def test_tracker_advances_with_turn_number():
    t = TurnTracker()
    t.observe(2, enemy_asleep=False)
    assert t.turns_completed == 1
    t.observe(5, enemy_asleep=False)
    assert t.turns_completed == 4


def test_tracker_ignores_none_and_non_increasing():
    t = TurnTracker()
    t.observe(3, enemy_asleep=False)
    t.observe(None, enemy_asleep=False)
    t.observe(2, enemy_asleep=False)  # stale/lower reading
    assert t.turns_completed == 2  # stayed at turn 3 -> 2 completed


def test_sleep_turns_accumulate_while_asleep():
    t = TurnTracker()
    t.observe(2, enemy_asleep=True)
    assert t.turns_asleep == 1
    t.observe(3, enemy_asleep=True)
    assert t.turns_asleep == 2


def test_sleep_resets_on_wake():
    t = TurnTracker()
    t.observe(2, enemy_asleep=True)
    t.observe(3, enemy_asleep=True)
    assert t.turns_asleep == 2
    t.observe(4, enemy_asleep=False)  # woke up
    assert t.turns_asleep == 0


def test_reset_clears_battle_state():
    t = TurnTracker()
    t.observe(4, enemy_asleep=True)
    t.reset()
    assert t.turns_completed == 0
    assert t.turns_asleep == 0
    t.observe(2, enemy_asleep=False)  # next battle starts counting fresh
    assert t.turns_completed == 1


# --- chat-independent command-menu turn counter (duration-gated) ---

# small absent threshold keeps the tests readable; production default is larger
GATE = 4


def feed_menu(t: TurnTracker, presence: list[bool]) -> None:
    for p in presence:
        t.observe_menu(p)


def start_battle(t: TurnTracker) -> None:
    """The battle intro keeps the menu absent (sending out the Pokemon, switch-in
    abilities), then it appears for turn 1. That first appearance is turn 1."""
    feed_menu(t, [False] * 10 + [True])


def commit_turn(t: TurnTracker) -> None:
    """One committed turn: the menu is gone for a sustained action animation
    (>= the gate), then returns for the next turn's prompt."""
    feed_menu(t, [False] * GATE + [True])


def test_first_menu_appearance_after_long_intro_is_turn_one():
    # regression: the long pre-menu absence (e.g. opponent Intimidate at switch-in)
    # must NOT be counted as a completed turn -> stays turn 1, not turn 2
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    start_battle(t)
    assert t.turns_completed == 0


def test_menu_present_whole_time_counts_nothing():
    # player sits at the menu and does nothing: turn must NOT advance
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    start_battle(t)
    feed_menu(t, [True] * 20)
    assert t.turns_completed == 0


def test_menu_counts_each_committed_turn():
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    start_battle(t)  # turn 1 prompt
    assert t.turns_completed == 0
    commit_turn(t)  # turn 1 -> committed, turn 2 prompt
    assert t.turns_completed == 1
    commit_turn(t)  # turn 2 -> committed, turn 3 prompt
    assert t.turns_completed == 2


def test_brief_menu_absence_does_not_count():
    # OCR flicker / sprite-animation frame / chat toggle: menu absent only briefly
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    start_battle(t)
    feed_menu(t, [True, False, True])  # 1 absent sample
    feed_menu(t, [True, False, False, True])  # 2 absent samples
    feed_menu(t, [True] + [False] * (GATE - 1) + [True])  # just under the gate
    assert t.turns_completed == 0


def test_absence_must_be_contiguous():
    # a flicker back to "present" resets the run, so scattered absences don't add up
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    start_battle(t)
    feed_menu(t, [False, False, True, False, False, True])
    assert t.turns_completed == 0


def test_chat_overrides_menu_count_upward():
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    start_battle(t)
    commit_turn(t)  # menu count -> 1
    t.observe(5, enemy_asleep=False)  # chat is authoritative
    assert t.turns_completed == 4


def test_menu_count_never_lowers_chat_value():
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    t.observe(5, enemy_asleep=False)
    start_battle(t)
    commit_turn(t)  # would be turn 2 from menu, must not reduce 4
    assert t.turns_completed == 4


def test_menu_count_survives_reset():
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    start_battle(t)
    commit_turn(t)
    assert t.turns_completed == 1
    t.reset()
    start_battle(t)  # fresh battle: intro then turn 1 prompt
    feed_menu(t, [True] * 10)
    assert t.turns_completed == 0
