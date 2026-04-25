"""Motion simulator driver — emits TSS1 attitude from a sea-state model.

Wraps a :class:`MotionModel` (sea-state-driven multi-sine roll/pitch/heave
generator) and emits one TSS1 sentence per tick. TSS1 is the format
Qinsy expects from a conventional MRU (Applanix POS MV, Kongsberg
Seapath in TSS1 mode, Norwegian Subsea).

The motion driver does not own heading — that is the heading driver's
job, and the two streams are reconciled by Qinsy's template. This is
why we emit TSS1 (heading-free) rather than PSXN20 (which folds heading
in and would need cross-driver coordination to keep the two streams
consistent).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .._core.channel import OutputChannel
from .._core.formatters.tss1 import build_tss1
from .._core.motion_model import MotionModel
from .._core.state.motion_state import MotionState


@dataclass
class MotionDriver:
    """Tick a :class:`MotionModel` and emit one TSS1 message per tick."""

    state: MotionState
    channel: OutputChannel
    model: MotionModel

    def tick(self, dt_seconds: float) -> List[bytes]:
        if dt_seconds < 0:
            raise ValueError("dt_seconds must be non-negative")

        roll, pitch, heave = self.model.update(dt_seconds, self.state.sea_state)
        self.state.roll_deg = roll
        self.state.pitch_deg = pitch
        self.state.heave_m = heave

        # build_tss1 returns bytes already terminated with \r\n.
        data = build_tss1(heave_m=heave, roll_deg=roll, pitch_deg=pitch)
        self.channel.write(data)
        return [data]
