"""Debounce the enemy status reading.

The status badge is re-read every frame. During the ball-throw / catch animation
the thrown ball flashes blue/cyan over the badge region, which the hue classifier
briefly misreads (e.g. as FRZ). Real status changes persist for many frames, so
we only switch the reported status once a new value has been seen for a few
consecutive frames; short animation blips are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StatusSettler:
    # consecutive frames a *different* status must hold before we switch to it.
    # At ~0.2 s/frame this rides out the ~0.5 s ball-flash; a real status change
    # lasts far longer and still gets through.
    stable_needed: int = 4

    _committed: str | None = None
    _candidate: str | None = None
    _count: int = 0

    def update(self, status: str) -> str:
        """Fold in this frame's raw status; return the settled status to show."""
        if self._committed is None:
            self._committed = status  # first reading shows immediately
            self._candidate = status
            self._count = 0
            return self._committed
        if status == self._committed:
            self._candidate = status
            self._count = 0
            return self._committed
        # differs from the committed status: needs to persist to take over
        if status == self._candidate:
            self._count += 1
        else:
            self._candidate = status
            self._count = 1
        if self._count >= self.stable_needed:
            self._committed = status
            self._count = 0
        return self._committed

    def reset(self) -> None:
        """Call at the start of each battle."""
        self._committed = None
        self._candidate = None
        self._count = 0
