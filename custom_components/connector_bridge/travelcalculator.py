"""Time-based position estimation for stateless (unidirectional) blinds.

The Dooya gateway does not report a real position for open/close-only
motors, so we estimate one from the elapsed travel time. Positions use the
Home Assistant convention: 0 = closed, 100 = open.
"""

from __future__ import annotations

import time


def _clamp(value: float) -> float:
    """Constrain a position to the valid 0-100 range."""
    return max(0.0, min(100.0, value))


class TravelCalculator:
    """Estimate a cover's position based on how long it has been moving."""

    def __init__(self, travel_time_full: float) -> None:
        # Seconds for a full 0 -> 100 (or 100 -> 0) travel.
        self._travel_time_full = max(float(travel_time_full), 0.1)
        self._position: float = 0.0
        self._target: float | None = None
        self._start_position: float = 0.0
        self._start_time: float | None = None

    def set_position(self, position: float) -> None:
        """Record a known position and cancel any travel in progress."""
        self._position = _clamp(position)
        self._target = None
        self._start_time = None

    def start_travel(self, target: float) -> None:
        """Begin travelling from the current position toward ``target``."""
        self._position = self.current_position()
        self._start_position = self._position
        self._target = _clamp(target)
        self._start_time = time.monotonic()

    def stop(self) -> None:
        """Freeze at the current (estimated) position."""
        self._position = self.current_position()
        self._target = None
        self._start_time = None

    def current_position(self) -> float:
        """Return the current estimated position."""
        if self._start_time is None or self._target is None:
            return self._position
        elapsed = time.monotonic() - self._start_time
        traveled = (elapsed / self._travel_time_full) * 100.0
        distance = self._target - self._start_position
        if distance >= 0:
            return _clamp(self._start_position + min(traveled, distance))
        return _clamp(self._start_position - min(traveled, -distance))

    def is_traveling(self) -> bool:
        """Return True while the cover is still estimated to be moving."""
        if self._start_time is None or self._target is None:
            return False
        return abs(self.current_position() - self._target) > 0.5

    def travel_direction(self) -> int | None:
        """Return 1 while opening, -1 while closing, None when idle."""
        if not self.is_traveling() or self._target is None:
            return None
        return 1 if self._target >= self._start_position else -1

    def finalize_if_arrived(self) -> bool:
        """Snap to the target if travel is complete. Return True if arrived."""
        if self._target is not None and not self.is_traveling():
            self._position = self._target
            self._target = None
            self._start_time = None
            return True
        return False
