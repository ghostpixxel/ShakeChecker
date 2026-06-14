from __future__ import annotations

from pathlib import Path

import cv2

from battle_log import parse_chat, parse_turn_number, read_chat, read_turn_number
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


def test_parse_chat_detects_catch():
    assert parse_chat(["[Battle] Gotcha! Rhyhorn was caught!"]).caught is True
    assert parse_chat(["[Battle] Geodude was caught!"]).caught is True
    assert parse_chat(["[Battle] Turn 2 started!", "Cascoon used Tackle!"]).caught is False
    assert parse_chat([]).caught is False


def test_read_chat_detects_catch_fixture():
    img = cv2.imread(
        str(ROOT / "fixtures" / "batle_action_pokemon_catched_text_after pokeball_disapeared.png")
    )
    assert read_chat(img, CAL.chat).caught is True


def test_read_chat_no_catch_in_normal_battle():
    img = cv2.imread(str(ROOT / "fixtures" / "full_health_no_status.png"))
    assert read_chat(img, CAL.chat).caught is False


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


# --- chat-independent HP-bar-cycle fallback ---


def feed_bar(t: TurnTracker, presence: list[bool]) -> None:
    for p in presence:
        t.observe_bar(p)


def test_bar_present_throughout_turn_one_stays_zero():
    t = TurnTracker()
    feed_bar(t, [True, True, True])  # no animation yet
    assert t.turns_completed == 0


def test_bar_vanish_and_return_marks_past_turn_one():
    t = TurnTracker()
    feed_bar(t, [True, True, False, False, True])  # action animation cycle
    assert t.turns_completed == 1


def test_bar_cycle_only_raises_a_floor_not_a_count():
    t = TurnTracker()
    # several animation cycles must not push turns beyond 1 on their own
    feed_bar(t, [True, False, True, False, True, False, True])
    assert t.turns_completed == 1


def test_chat_overrides_bar_floor_upward():
    t = TurnTracker()
    feed_bar(t, [True, False, True])  # floor -> 1
    t.observe(5, enemy_asleep=False)  # chat is authoritative
    assert t.turns_completed == 4


def test_bar_floor_never_lowers_chat_value():
    t = TurnTracker()
    t.observe(5, enemy_asleep=False)
    feed_bar(t, [True, False, True])  # floor 1 must not reduce 4
    assert t.turns_completed == 4
