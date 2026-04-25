"""Shared state for a motion simulator driver (MRU, Seapath, motion sensor).

Tracks the last-emitted attitude and the sea-state forcing that the
:class:`MotionModel` uses to scale amplitudes. Separate from
:class:`GnssState` because an MRU is conceptually its own sensor — the
drivers may be co-located on a real Seapath but they can also be
distinct boxes on a real ship, and modelling them as distinct states
avoids cross-coupling bugs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MotionState:
    """Attitude + forcing for a motion (TSS1) driver."""

    # Heading is supplied externally — a real Seapath derives it from the
    # INS, not from the MRU. Drivers that want a wandering heading drive
    # this field themselves (e.g. from a HeadingDriver running alongside).
    heading_true_deg: float = 0.0

    # Latest MotionModel output, cached here so readers (dashboards,
    # subscribers) can pick up the last-emitted value without re-running
    # the model.
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    heave_m: float = 0.0

    # Sea-state forcing, clamped to [0, 5] by the model on update.
    sea_state: int = 2
