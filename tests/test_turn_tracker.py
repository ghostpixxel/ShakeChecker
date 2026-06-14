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


def test_template_no_false_catch_on_switch_or_run():
    # "Gotcha!" must not be confused with "Go! Y!" (switch) or "Got away!" (run);
    # a false catch would latch the overlay and never end the battle.
    assert _bt("batle_action_pokemon_switch_2_new_pokemon_enters.png").caught is False
    assert _bt("batle_action_run_selected.png").caught is False


def test_template_detects_committed_action():
    # a committed action ("X used Y!" / "Go! Y!") -> a real turn ran
    assert _bt("batle_action_attack_selected.png").action is True  # "used"
    assert _bt("batle_action_item_selected.png").action is True  # "used"
    assert _bt("batle_action_pokemon_switch_2_new_pokemon_enters.png").action is True  # "Go!"
    # not actions: command menu, move submenu, and look-alikes "Got away"/"Gotcha"
    assert _bt("full_health_no_status.png").action is False
    assert _bt("two_third_green_health_cave.png").action is False
    assert _bt("batle_action_run_selected.png").action is False  # "Got away" != "Go!"
    assert _bt("batle_action_pokemon_catched_text_after pokeball_disapeared.png").action is False


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


# --- chat-independent command-menu turn counter (action-gated) ---


def menu(t: TurnTracker, present: bool, action: bool = False) -> None:
    t.observe_menu(present, action)


def start_battle(t: TurnTracker) -> None:
    """Battle intro: menu absent (sending out the Pokemon, switch-in abilities),
    then it appears for turn 1. That first appearance is turn 1, never a count."""
    for _ in range(5):
        menu(t, False)
    menu(t, True)


def commit_turn(t: TurnTracker) -> None:
    """One committed turn: the menu is gone, a committed action ("X used Y!") is
    seen, then the menu returns for the next turn's prompt."""
    menu(t, False)
    menu(t, False, action=True)
    menu(t, True)


def browse_cancel(t: TurnTracker) -> None:
    """Open a submenu (menu gone) with NO action, then cancel back to the menu."""
    for _ in range(8):
        menu(t, False)
    menu(t, True)


def test_first_menu_appearance_after_long_intro_is_turn_one():
    t = TurnTracker()
    start_battle(t)
    assert t.turns_completed == 0


def test_menu_present_whole_time_counts_nothing():
    t = TurnTracker()
    start_battle(t)
    for _ in range(20):
        menu(t, True)
    assert t.turns_completed == 0


def test_menu_counts_each_committed_turn():
    t = TurnTracker()
    start_battle(t)
    assert t.turns_completed == 0
    commit_turn(t)
    assert t.turns_completed == 1
    commit_turn(t)
    assert t.turns_completed == 2


def test_browse_cancel_without_action_does_not_count():
    # the reported bug: a long bag/Pokemon browse then cancel makes the menu
    # vanish and return with NO action -> the turn must NOT advance.
    t = TurnTracker()
    start_battle(t)
    browse_cancel(t)
    assert t.turns_completed == 0
    # but a real action right after still counts
    commit_turn(t)
    assert t.turns_completed == 1


def test_chat_overrides_menu_count_upward():
    t = TurnTracker()
    start_battle(t)
    commit_turn(t)  # menu count -> 1
    t.observe(5, enemy_asleep=False)  # chat is authoritative
    assert t.turns_completed == 4


def test_menu_resumes_from_chat_value():
    # after the chat jumps the turn (e.g. it tracked a horde while menu counting
    # was paused), the menu must continue from there, not from its own stale count.
    t = TurnTracker()
    start_battle(t)
    commit_turn(t)
    assert t.turns_completed == 1
    t.observe(5, enemy_asleep=False)  # chat jumps to turn 5
    assert t.turns_completed == 4
    commit_turn(t)  # next real turn continues from 4 -> 5, not back to 2
    assert t.turns_completed == 5


def test_menu_count_survives_reset():
    t = TurnTracker()
    start_battle(t)
    commit_turn(t)
    assert t.turns_completed == 1
    t.reset()
    start_battle(t)
    for _ in range(10):
        menu(t, True)
    assert t.turns_completed == 0
