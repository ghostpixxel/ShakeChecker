"""Track the battle turn count and the enemy's accumulated sleep turns.

Pure/stateful (no I/O). Fed observations each frame; produces the numbers the
conditional balls need: turns_completed (Quick/Timer) and turns_asleep (Dream).

Turn numbers are 1-based as PokeMMO reports them ("Turn N started!"); a turn
that has fully begun N means N-1 turns completed. Two signals funnel in:

- observe(): the chat's exact "Turn N started!" — authoritative when visible.
- observe_menu(): chat-independent. The command menu (FIGHT/BAG/POKEMON/RUN) is
  shown only while waiting for the player's action, i.e. once per turn. A turn is
  counted when the menu RETURNS *and a committed action ran while it was gone*
  (the "X used Y!" narration was seen). Requiring the action text is what makes
  it robust: opening BAG/POKEMON and cancelling makes the menu disappear and
  reappear with no action, so it must NOT advance the turn.

The enemy HP bar is deliberately NOT a turn signal: it blinks off/on during
many in-battle animations (a confirmed culprit: the opponent's Intimidate
lowering Attack at switch-in), so keying turns on it produced phantom turns.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TurnTracker:
    turns_completed: int = 0
    turns_asleep: int = 0
    _turn_number: int = 1  # highest 1-based turn seen this battle
    _menu_turns: int = 0  # turns counted from command-menu reappearances
    _menu_seen: bool = False  # has the command menu appeared yet this battle
    _action_since_menu: bool = False  # a committed action ran since the menu was up

    def reset(self) -> None:
        """Call at the start of each battle."""
        self.turns_completed = 0
        self.turns_asleep = 0
        self._turn_number = 1
        self._menu_turns = 0
        self._menu_seen = False
        self._action_since_menu = False

    def observe(self, turn_number: int | None, enemy_asleep: bool) -> None:
        """Fold in one frame's chat observation.

        `turn_number` is the latest turn read from chat OCR, or None if unknown
        this frame. `enemy_asleep` is the current sleep status. This is the
        authoritative, exact source when the chat is visible.
        """
        # Sleep resets the moment the enemy is awake; it only accrues per turn
        # while asleep (so Dream Ball is x1 again immediately on wake-up).
        if not enemy_asleep:
            self.turns_asleep = 0

        if turn_number is not None and turn_number > self._turn_number:
            if enemy_asleep:
                self.turns_asleep += turn_number - self._turn_number
            self._turn_number = turn_number
            self.turns_completed = max(self.turns_completed, turn_number - 1)
            # keep the menu counter in step with the chat so it continues from the
            # corrected value (e.g. after a horde, where menu counting is paused)
            self._menu_turns = max(self._menu_turns, self.turns_completed)

    def observe_menu(self, menu_present: bool, action_seen: bool) -> None:
        """Chat-independent turn count from the command menu (see module docstring).

        `action_seen` is True on a frame where a committed-action narration ("X
        used Y!") is visible. A turn is counted when the menu RETURNS and such an
        action ran while it was gone; this only raises turns_completed (the exact
        chat value still overrides it upward).

        The menu's FIRST appearance is turn 1 and never counts (the battle intro /
        switch-in abilities precede it). Opening and cancelling a submenu makes
        the menu vanish/return with no action, so it does not advance the turn.
        """
        if menu_present:
            if not self._menu_seen:
                self._menu_seen = True  # turn 1 prompt; no action yet
            elif self._action_since_menu:
                self._menu_turns += 1
                self.turns_completed = max(self.turns_completed, self._menu_turns)
            self._action_since_menu = False
        elif action_seen:
            self._action_since_menu = True
