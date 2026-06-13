"""Debounce the enemy HP reading until the bar stops moving.

The HP bar drains via an animation, so a frame grabbed mid-drain reports a
stale intermediate value (e.g. 88.5% while the enemy is actually being KO'd).
HpSettler only commits a new value once it has been stable for a few frames,
so probabilities are shown for the settled HP, not animation frames.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HpSettler:
    tolerance: float = 1.5  # percent; ignore sub-bar jitter
    stable_needed: int = 2  # consecutive stable reads before committing
    settled: float | None = None
    _pending: float | None = None
    _count: int = 0

    def reset(self) -> None:
        self.settled = None
        self._pending = None
        self._count = 0

    def update(self, hp_pct: float) -> float:
        """Feed one raw HP reading; return the current settled HP.

        While the bar animates (value changing each frame) the previously
        settled value is held; once readings hold steady the new value is
        committed and returned.
        """
        if self._pending is None or abs(hp_pct - self._pending) > self.tolerance:
            self._pending = hp_pct
            self._count = 1
        else:
            self._count += 1

        if self._count >= self.stable_needed:
            self.settled = self._pending
        return self.settled if self.settled is not None else hp_pct
