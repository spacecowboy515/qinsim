"""Heading simulator driver — emits HDT/HDM/HDG with a rate-limited autopilot.

Ports aqps's heading driver, simplified: ``MANUAL`` and ``PATH`` modes,
no wiretap, no manifest. A real gyrocompass curves at a bounded turn
rate when steered onto a new heading, and that curve is what Qinsy
sees and processes through its template's smoothing/quality logic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence

from .._core.channel import OutputChannel
from .._core.formatters.nmea_hdt import build_hdg, build_hdm, build_hdt
from .._core.geo import KNOTS_TO_MPS, normalize_angle_diff
from .._core.path_cursor import PathCursor
from .._core.state.heading_state import HeadingState


# Default talker IDs per sentence — match NMEA 0183 convention. A
# state with ``talker_id`` set overrides these uniformly.
_DEFAULT_TALKER = {"HDT": "GP", "HDM": "HC", "HDG": "HC"}


def _talker_for(state: HeadingState, sentence: str) -> str:
    return state.talker_id or _DEFAULT_TALKER[sentence]


def _magnetic_heading(state: HeadingState) -> float:
    """Return magnetic heading = true heading − variation, folded to [0, 360)."""
    return (state.current_heading_deg - state.variation_deg) % 360.0


_HEADING_BUILDERS = {
    "HDT": lambda s: build_hdt(s.current_heading_deg, talker_id=_talker_for(s, "HDT")),
    "HDM": lambda s: build_hdm(_magnetic_heading(s), talker_id=_talker_for(s, "HDM")),
    "HDG": lambda s: build_hdg(
        _magnetic_heading(s), s.deviation_deg, s.variation_deg,
        talker_id=_talker_for(s, "HDG"),
    ),
}


class HeadingMode(str, Enum):
    """How the driver picks the steering target each tick."""

    MANUAL = "manual"   # state.target_heading_deg is the steering target
    PATH = "path"       # PathCursor lookahead bearing is the steering target


_LINE_TERMINATOR = b"\r\n"


@dataclass
class HeadingDriver:
    """Drives a :class:`HeadingState` forward and emits heading sentences."""

    state: HeadingState
    channel: OutputChannel
    mode: HeadingMode = HeadingMode.MANUAL
    path_cursor: Optional[PathCursor] = None
    sentences: Sequence[str] = field(default_factory=lambda: ["HDT"])

    def __post_init__(self) -> None:
        if self.mode is HeadingMode.PATH and (
            self.path_cursor is None or not self.path_cursor.has_path()
        ):
            raise ValueError("PATH mode requires a loaded PathCursor")
        unknown = [s for s in self.sentences if s not in _HEADING_BUILDERS]
        if unknown:
            raise ValueError(
                f"Unknown heading sentence keys: {unknown!r}; "
                f"supported: {sorted(_HEADING_BUILDERS)}"
            )

    def tick(self, dt_seconds: float) -> List[bytes]:
        if dt_seconds < 0:
            raise ValueError("dt_seconds must be non-negative")

        if self.mode is HeadingMode.PATH:
            self._advance_on_path(dt_seconds)
            target = self._path_target_bearing()
        else:
            target = self.state.target_heading_deg

        if target is not None and dt_seconds > 0:
            self._steer_toward(target, dt_seconds)

        self.state.current_heading_deg = self.state.current_heading_deg % 360.0

        emitted: List[bytes] = []
        for key in self.sentences:
            sentence = _HEADING_BUILDERS[key](self.state)
            data = sentence.encode("ascii") + _LINE_TERMINATOR
            self.channel.write(data)
            emitted.append(data)
        return emitted

    def _steer_toward(self, target_deg: float, dt_seconds: float) -> None:
        """Move toward ``target`` shortest-direction, rate-limited."""
        diff = normalize_angle_diff(target_deg - self.state.current_heading_deg)
        max_delta = self.state.turn_rate_dps * dt_seconds
        if abs(diff) <= max_delta:
            self.state.current_heading_deg = target_deg
        else:
            self.state.current_heading_deg += math.copysign(max_delta, diff)

    def _advance_on_path(self, dt_seconds: float) -> None:
        assert self.path_cursor is not None  # enforced by __post_init__
        distance_m = self.state.speed_knots * KNOTS_TO_MPS * dt_seconds
        self.path_cursor.step(distance_m)
        lat, lon = self.path_cursor.current_position()
        self.state.latitude = lat
        self.state.longitude = lon

    def _path_target_bearing(self) -> float:
        assert self.path_cursor is not None
        return self.path_cursor.target_bearing(lookahead_m=5.0)
