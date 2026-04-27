"""Scalar value profile — Static / Ramp / Random generator.

Captures a pattern that recurs across several Meridian device simulators
(MiniSVS, SVXCast, future CTD ports): a single scalar output whose value
over time is controlled by one of three modes — hold at a constant,
sweep linearly between two endpoints over a duration, or emit uniform
random values inside a range.

Lifted into ``_core`` so every such driver consumes the same profile
type, is tested once, and composes predictably with a seeded
:class:`random.Random` for reproducibility. The profile does not own
time — callers advance it per tick by passing ``dt_seconds`` — which
matches the tick-based driver pattern used by GNSS / Heading / Motion.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum


class ChannelMode(StrEnum):
    """How a scalar channel decides its next value.

    ``STATIC``  — always returns :attr:`ChannelProfile.static_value`.
    ``RAMP``    — linearly sweeps ``ramp_start → ramp_end`` over
                  ``ramp_duration_s``. Clamps to ``ramp_end`` once
                  duration elapses. Duration ≤ 0 snaps to ``ramp_end``.
    ``RANDOM``  — draws ``rng.uniform(min_value, max_value)`` each call.
                  Callers inject the ``random.Random`` so tests can seed it.
    """

    STATIC = "static"
    RAMP = "ramp"
    RANDOM = "random"


@dataclass
class ChannelProfile:
    """Config + internal elapsed-time state for one scalar channel."""

    mode: ChannelMode = ChannelMode.STATIC

    # Used by STATIC.
    static_value: float = 0.0

    # Used by RANDOM.
    min_value: float = 0.0
    max_value: float = 0.0

    # Used by RAMP.
    ramp_start: float = 0.0
    ramp_end: float = 0.0
    ramp_duration_s: float = 60.0

    # Elapsed seconds since the profile started. Advanced by ``tick``
    # even in modes that don't use it so switching modes mid-run does
    # not look like a fresh start.
    _elapsed_s: float = 0.0

    def reset(self) -> None:
        """Zero the internal elapsed-time counter (re-arms a RAMP from zero)."""
        self._elapsed_s = 0.0

    def tick(self, dt_seconds: float, rng: random.Random) -> float:
        """Advance elapsed time by ``dt_seconds`` and return the next value."""
        if dt_seconds < 0:
            raise ValueError("dt_seconds must be non-negative")
        self._elapsed_s += dt_seconds

        if self.mode is ChannelMode.STATIC:
            return self.static_value

        if self.mode is ChannelMode.RANDOM:
            return rng.uniform(self.min_value, self.max_value)

        # RAMP: duration ≤ 0 or fully elapsed -> hold at endpoint.
        if self.ramp_duration_s <= 0 or self._elapsed_s >= self.ramp_duration_s:
            return self.ramp_end
        progress = self._elapsed_s / self.ramp_duration_s
        return self.ramp_start + progress * (self.ramp_end - self.ramp_start)
