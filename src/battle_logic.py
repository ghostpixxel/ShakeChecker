"""Pure battle-loop decisions, lifted out of the LiveLoop so the logic that drives
the live turn count and the battle-end timing is unit-testable without the game, a
window, or Qt. These functions own no state beyond the TurnTracker passed in; the
LiveLoop keeps the I/O (capture, overlay, logging). Behaviour is identical to the
inline code it replaced -- the move exists purely to make it testable.
"""

from __future__ import annotations

from battle_reader import BattleState, BattleText
from turn_tracker import TurnTracker


def is_in_battle(state: BattleState, bt: BattleText) -> bool:
    """True if THIS frame shows a battle, from battle-specific signals only: an
    enemy HP bar, or the command menu / committed-action text / catch banner.
    (The dark command panel is deliberately NOT used -- it false-positives in a
    dark cave overworld; it only tunes the end grace.)"""
    has_bar = state in (BattleState.SINGLE, BattleState.MULTI)
    return has_bar or bt.menu_present or bt.action or bt.caught


def debounce_menu(
    present: bool, raw: bool, streak: int, stable: bool, *, threshold: int
) -> tuple[bool, int, bool]:
    """Debounce the command-menu present/absent signal, returning the new
    (raw, streak, stable) state.

    The menu template can flicker during the busy multi-target (horde/double)
    animation, which would look like the menu reappearing and over-count turns.
    So a present/absent change is only accepted into `stable` once the raw signal
    has held for `threshold` consecutive frames. `stable` is the debounced value
    the turn counter consumes."""
    if present == raw:
        streak += 1
    else:
        raw = present
        streak = 1
    if streak >= threshold:
        stable = raw
    return raw, streak, stable


def is_horde_remnant(was_horde: bool, x_frac: float, remnant_x_frac: float) -> bool:
    """True if a lone enemy HP bar is actually the leftover of a horde, so the
    trainer check must be skipped (a horde is always wild).

    When a horde narrows to one bar, the party-icon strip below it catches the
    other fainted mons + scene and false-reads as a trainer party. Two
    resolution-independent signals mark it as a remnant: we saw the spread pack
    earlier this battle (`was_horde`), or the bar sits right of the canonical
    single-enemy slot (`x_frac` past `remnant_x_frac`)."""
    return was_horde or x_frac > remnant_x_frac


def battle_end_grace(
    is_trainer: bool, ui_present: bool, *, trainer_s: float, anim_s: float, normal_s: float
) -> float:
    """How long all battle signals must stay gone before the battle ends.

    Trainer battles need the longest (a Pokemon swap between faints leaves a gap);
    if the dark command panel is still up we're mid-battle in an animation (a
    2-turn move hides the bar with no menu) so don't end early; otherwise short,
    so the catch overlay clears promptly once we're truly back in the overworld."""
    if is_trainer:
        return trainer_s
    if ui_present:
        return anim_s
    return normal_s


def dex_panel_action(matched: bool, miss_streak: int, *, hide_after: int) -> tuple[str, int]:
    """Decide what to do with the overworld dex panel after one location read, and
    return (action, new_miss_streak).

    A single garbled location OCR -- or a screen transition where the HUD is briefly
    unreadable -- must NOT hide the panel; that made it vanish mid-route and never
    come back. So a miss keeps the last good panel up until enough misses pile up:

    - matched read                         -> ("show", 0)
    - unmatched, still under the threshold  -> ("keep", miss_streak + 1)
    - unmatched, hit `hide_after` in a row  -> ("hide", miss_streak + 1)
      (genuinely left to an area not in the index, e.g. a house/cave)
    """
    if matched:
        return "show", 0
    streak = miss_streak + 1
    return ("hide" if streak >= hide_after else "keep"), streak


def apply_chat_turn(
    tracker: TurnTracker,
    chat_turn: int | None,
    *,
    asleep: bool,
    now: float,
    last_advance: float,
    down_guard_s: float,
) -> str:
    """Fold one chat-OCR turn reading into `tracker`; return what happened, for the
    caller's debug log:

    - "none"  -- no reading this frame.
    - "start" -- ignored because it's still turn 1. At battle start the PREVIOUS
      battle's higher "Turn N" still lingers in the chat and the async OCR lags a
      frame or two, so a read can predate the new battle's "Turn 1 started!". Turn
      1 is the battle-start default and needs no correction.
    - "down"  -- corrected the count DOWN (the menu over-counted and the menu has
      been quiet for `down_guard_s`, so the chat is trusted to lower it).
    - "up"    -- corrected the count UP (a missed turn, e.g. a 2-turn move).
    - "equal" -- the read matched the count; only the consecutive-sleep counter was
      re-synced (which the Dream Ball needs).
    """
    if chat_turn is None:
        return "none"
    if tracker.turns_completed == 0:
        return "start"
    completed = chat_turn - 1
    if completed < tracker.turns_completed and now - last_advance > down_guard_s:
        tracker.set_turn(chat_turn)  # down: a menu over-count, menu now quiet
        return "down"
    before = tracker.turns_completed
    # up OR equal: observe() raises the count for a missed turn (no-op when already
    # equal) AND keeps the consecutive-sleep counter in sync for the Dream Ball.
    tracker.observe(chat_turn, asleep)
    return "up" if tracker.turns_completed > before else "equal"
