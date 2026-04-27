"""Single-beam echosounder driver — DBT and DPT.

Qinsy's single-beam driver accepts either DBT (depth-below-transducer
in feet/metres/fathoms) or DPT (depth + transducer offset). DPT is the
modern preference — Qinsy uses the offset field to do its own waterline
computation rather than requiring a per-vessel calibration entry.

The driver advances ``state.depth_m`` between ticks via a small bounded
random walk on top of an optional sea-floor profile from a YAML lookup.
This keeps the trace alive during long bench runs without pretending to
model real bathymetry — operators who want a specific profile load one
of the bundled scenarios (``open_ocean_survey.yaml``) or hand-edit
``depth_m`` between scenarios.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from .._core.channel import OutputChannel
from .._core.formatters.nmea_depth import build_dbt, build_dpt
from .._core.state.depth_state import DepthState

_LINE_TERMINATOR = b"\r\n"

# Random-walk step magnitude per second. 5 cm/s feels like a calm-water
# trace at 25 m without drifting unrealistically far over a long run —
# tuned by eye against archived single-beam logs.
_DEFAULT_WALK_RATE_M_PER_S: float = 0.05

# Hard floor on simulated depth — a sounder reading 0 m would tell
# Qinsy the transducer is dry, which is a fault state we have a
# dedicated effect for. The bounded walk should never wander into it.
_MIN_DEPTH_M: float = 0.5


_DEPTH_BUILDERS = {
    "DBT": lambda s: build_dbt(s.depth_m, talker_id=s.talker_id),
    "DPT": lambda s: build_dpt(s.depth_m, s.transducer_offset_m, talker_id=s.talker_id),
}


@dataclass
class DepthDriver:
    """Drives a :class:`DepthState` forward and emits depth sentences."""

    state: DepthState
    channel: OutputChannel
    sentences: Sequence[str] = field(default_factory=lambda: ["DPT", "DBT"])
    # Random-walk step magnitude. ``0.0`` freezes depth — useful for
    # deterministic test scenarios; the bundled scenarios leave the
    # default in place so the trace breathes.
    walk_rate_m_per_s: float = _DEFAULT_WALK_RATE_M_PER_S
    # Optional fixed seed for reproducible scenarios — None uses the
    # system entropy source, which is the right default for live runs
    # but produces noisy diffs in golden tests.
    seed: int | None = None
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        unknown = [s for s in self.sentences if s not in _DEPTH_BUILDERS]
        if unknown:
            raise ValueError(
                f"Unknown depth sentence keys: {unknown!r}; "
                f"supported: {sorted(_DEPTH_BUILDERS)}"
            )
        self._rng = random.Random(self.seed)

    def tick(self, dt_seconds: float) -> list[bytes]:
        if dt_seconds < 0:
            raise ValueError("dt_seconds must be non-negative")

        if self.walk_rate_m_per_s > 0.0 and dt_seconds > 0.0:
            # Symmetric uniform step — over many ticks the trace
            # diffuses but does not bias up or down. A scenario that
            # wants a long-term trend should drive ``state.depth_m``
            # directly between ticks (or, in v2, plug a profile).
            step = self._rng.uniform(-1.0, 1.0) * self.walk_rate_m_per_s * dt_seconds
            self.state.depth_m = max(_MIN_DEPTH_M, self.state.depth_m + step)

        emitted: list[bytes] = []
        for key in self.sentences:
            sentence = _DEPTH_BUILDERS[key](self.state)
            data = sentence.encode("ascii") + _LINE_TERMINATOR
            self.channel.write(data)
            emitted.append(data)
        return emitted
