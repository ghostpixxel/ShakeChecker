from __future__ import annotations

from pathlib import Path

import cv2

from battle_log import (
    has_command_menu,
    is_catch_banner,
    parse_turn_number,
    read_battle_text,
    read_turn_number,
)
from battle_reader import load_calibration
from turn_tracker import TurnTracker

ROOT = Path(__file__).parent.parent
CAL = load_calibration(ROOT / "calibration.toml")


def test_read_turn_number_from_chat_fixture():
    # full_health_no_status.png's chat shows "[Battle] Turn 2 started!"
    img = cv2.imread(str(ROOT / "fixtures" / "full_health_no_status.png"))
    assert read_turn_number(img, CAL.chat) == 2


def test_read_turn_number_none_in_overworld():
    img = cv2.imread(str(ROOT / "fixtures" / "overworld_city_running.png"))
    assert read_turn_number(img, CAL.chat) is None


def test_is_catch_banner_keywords():
    # OCR mangles "Gotcha"->"Gotoha" and splits/drops "was"; detection keys on
    # the surviving "caught"/"gotcha" tokens, not the exact phrase.
    assert is_catch_banner(["Gotoha!", "Rhyhorn", "sEm", "Caught!"]) is True
    assert is_catch_banner(["Gotcha!", "Shellos was caught!"]) is True
    # a faint (the other reason the bar vanishes) must NOT read as a catch
    assert is_catch_banner(["Cascoon", "fainted!"]) is False
    assert is_catch_banner(["It's super effective!"]) is False
    assert is_catch_banner([]) is False


def test_has_command_menu_keywords():
    assert has_command_menu(["FIGHT", "BAG", "POKEMON", "RUN"], 2) is True
    assert has_command_menu(["fight", "bag"], 2) is True
    assert has_command_menu(["RUN"], 2) is False  # one stray word is not the menu
    # move submenu / narration / chat-style lines are not the command menu
    assert has_command_menu(["False Swipe", "Spore", "Soak"], 2) is False
    assert has_command_menu(["Monferno used Ember!"], 2) is False
    assert has_command_menu([], 2) is False


def test_read_battle_text_detects_command_menu():
    # command menu visible at two window aspect ratios
    for name in ("full_health_no_status.png", "1920x1080_resolution.png"):
        img = cv2.imread(str(ROOT / "fixtures" / name))
        assert read_battle_text(img, CAL.battle_text).menu_present is True, name


def test_read_battle_text_no_menu_during_action_or_submenu():
    for name in (
        "batle_action_attack_selected.png",  # action textbox
        "two_third_green_health_cave.png",  # move submenu
        "overworld_city_running.png",  # not in battle
    ):
        img = cv2.imread(str(ROOT / "fixtures" / name))
        assert read_battle_text(img, CAL.battle_text).menu_present is False, name


def test_read_battle_text_detects_catch_fixture():
    img = cv2.imread(
        str(ROOT / "fixtures" / "batle_action_pokemon_catched_text_after pokeball_disapeared.png")
    )
    bt = read_battle_text(img, CAL.battle_text)
    assert bt.caught is True
    # crucially, the catch is read from the in-viewport box, NOT the chat log's
    # stale "Geodude was caught!" line, which sits below this band.
    assert bt.menu_present is False


def test_read_battle_text_false_before_catch_text_appears():
    img = cv2.imread(str(ROOT / "fixtures" / "batle_action_pokemon_catched_dark_pokeballpng.png"))
    assert read_battle_text(img, CAL.battle_text).caught is False


def test_read_battle_text_no_catch_in_normal_battle():
    img = cv2.imread(str(ROOT / "fixtures" / "full_health_no_status.png"))
    assert read_battle_text(img, CAL.battle_text).caught is False


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


def commit_turn(t: TurnTracker) -> None:
    """Simulate one committed turn: the menu is gone for a sustained action
    animation (>= the gate), then returns for the next turn's prompt."""
    feed_menu(t, [False] * GATE + [True])


def test_menu_present_whole_time_counts_nothing():
    # player sits at the menu and does nothing: turn must NOT advance
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    feed_menu(t, [True] * 20)
    assert t.turns_completed == 0


def test_menu_counts_each_committed_turn():
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    t.observe_menu(True)  # turn 1 prompt
    assert t.turns_completed == 0
    commit_turn(t)  # turn 1 -> committed, turn 2 prompt
    assert t.turns_completed == 1
    commit_turn(t)  # turn 2 -> committed, turn 3 prompt
    assert t.turns_completed == 2


def test_brief_menu_absence_does_not_count():
    # OCR flicker / sprite-animation frame / chat toggle: menu absent only briefly
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    feed_menu(t, [True, False, True])  # 1 absent sample
    feed_menu(t, [True, False, False, True])  # 2 absent samples
    feed_menu(t, [True] + [False] * (GATE - 1) + [True])  # just under the gate
    assert t.turns_completed == 0


def test_absence_must_be_contiguous():
    # a flicker back to "present" resets the run, so scattered absences don't add up
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    feed_menu(t, [False, False, True, False, False, True])
    assert t.turns_completed == 0


def test_chat_overrides_menu_count_upward():
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    commit_turn(t)  # menu count -> 1
    t.observe(5, enemy_asleep=False)  # chat is authoritative
    assert t.turns_completed == 4


def test_menu_count_never_lowers_chat_value():
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    t.observe(5, enemy_asleep=False)
    commit_turn(t)  # would be turn 2 from menu, must not reduce 4
    assert t.turns_completed == 4


def test_menu_count_survives_reset():
    t = TurnTracker(menu_absent_samples_for_turn=GATE)
    commit_turn(t)
    assert t.turns_completed == 1
    t.reset()
    feed_menu(t, [True] * 10)  # fresh battle, sitting at the menu
    assert t.turns_completed == 0
