"""Track the battle turn count and the enemy's accumulated sleep turns.

Pure/stateful (no I/O). Fed observations each frame; produces the numbers the
conditional balls need: turns_completed (Quick/Timer) and turns_asleep (Dream).

Turn numbers are 1-based as PokeMMO reports them ("Turn N started!"); a turn
that has fully begun N means N-1 turns completed. Two signals funnel in:

- observe(): the chat's exact "Turn N started!" — authoritative when visible.
- observe_menu(): chat-independent. The command menu (FIGHT/BAG/POKEMON/RUN) is
  shown only while waiting for the player's action, i.e. once per turn. A turn
  is counted when the menu RETURNS after being gone long enough for a real
  action animation to have played. The duration gate is what makes it robust:
  it ignores the menu briefly misreading as absent (OCR flicker, a sprite
  animation frame, the player toggling the chat) and a submenu opened and
  cancelled — none of which keep the menu gone for seconds the way a committed
  action does.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TurnTracker:
    # Consecutive "menu absent" observations required before its return counts
    # as a turn. At ~0.3 s per observation this is ~1.2 s; a committed action
    # (player move + enemy move + end effects) keeps the menu gone far longer,
    # while flicker / chat-toggle / quick submenu glances are well under it.
    menu_absent_samples_for_turn: int = 4

    turns_completed: int = 0
    turns_asleep: int = 0
    _turn_number: int = 1  # highest 1-based turn seen this battle
    _menu_turns: int = 0  # turns counted from command-menu reappearances
    _menu_absent_run: int = 0  # consecutive observations with the menu absent

    def reset(self) -> None:
        """Call at the start of each battle."""
        self.turns_completed = 0
        self.turns_asleep = 0
        self._turn_number = 1
        self._menu_turns = 0
        self._menu_absent_run = 0

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

    def observe_menu(self, menu_present: bool) -> None:
        """Chat-independent turn count from the command menu (see module docstring).

        Count a turn only when the menu returns after having been absent for at
        least `menu_absent_samples_for_turn` observations in a row — long enough
        that a real action ran. This only raises turns_completed; the exact chat
        value (observe) still overrides it upward.
        """
        if menu_present:
            if self._menu_absent_run >= self.menu_absent_samples_for_turn:
                self._menu_turns += 1
                self.turns_completed = max(self.turns_completed, self._menu_turns)
            self._menu_absent_run = 0
        else:
            self._menu_absent_run += 1
