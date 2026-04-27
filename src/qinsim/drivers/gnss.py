"""GNSS simulator driver — emits NMEA sentences from a :class:`GnssState`.

Ported from aqps's native GNSS driver, simplified for qinsim's narrower
brief: only ``MANUAL`` and ``PATH`` modes survive (the
``FOLLOW_REPLAYER`` / ``FOLLOW_DRIVER`` modes existed to colocate aqps
with its KMALL replayer and a parent vessel antenna — neither applies
here, where qinsim is the whole world). No wiretap, no QRG manifest,
no runtime-config polling.

Two modes:

* ``MANUAL`` — the caller seeds ``state.heading_true`` /
  ``state.speed_knots`` / ``state.latitude`` / ``state.longitude`` and
  the driver integrates position forward by ``speed * dt`` along the
  current heading on every tick.
* ``PATH`` — a :class:`PathCursor` dictates position and heading; the
  cursor advances by ``speed * dt`` and the driver reads back its
  bearing (with a 5 m lookahead) for ``state.heading_true``.

Sentences are selected by name from the catalogue exported by
:mod:`qinsim._core.formatters.nmea_gnss` (``GGA``, ``RMC``, ``VTG``,
``GLL``, ``GSA``, ``GSV``, ``GST``, ``ZDA`` — and ``HDT`` for the rare
GNSS-derived heading case, though the dedicated heading driver is the
usual home for that).
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from .._core.channel import OutputChannel
from .._core.formatters.nmea_gnss import NMEA_BUILDERS
from .._core.geo import KNOTS_TO_MPS, forward_project
from .._core.path_cursor import PathCursor
from .._core.state.gnss_state import GST_PROFILES, GnssState


class GnssMode(StrEnum):
    """How the driver decides position and heading each tick."""

    MANUAL = "manual"
    PATH = "path"


# NMEA 0183 §5.3.2 — every sentence is terminated with <CR><LF>. Module
# constant so a future bare-newline transport could override without
# touching the driver body.
_LINE_TERMINATOR = b"\r\n"


@dataclass
class GnssDriver:
    """Drives a :class:`GnssState` forward and emits the configured sentences."""

    state: GnssState
    channel: OutputChannel
    sentences: Sequence[str] = field(default_factory=lambda: ["GGA", "RMC", "VTG"])
    mode: GnssMode = GnssMode.MANUAL
    path_cursor: PathCursor | None = None

    def __post_init__(self) -> None:
        unknown = [s for s in self.sentences if s not in NMEA_BUILDERS]
        if unknown:
            raise ValueError(
                f"Unknown NMEA sentence keys: {unknown!r}. "
                f"Supported: {sorted(NMEA_BUILDERS)}"
            )
        if self.mode is GnssMode.PATH and (
            self.path_cursor is None or not self.path_cursor.has_path()
        ):
            raise ValueError("PATH mode requires a loaded PathCursor")

    def tick(self, dt_seconds: float) -> list[bytes]:
        """Advance state by ``dt_seconds`` and emit one tick of sentences."""
        if dt_seconds < 0:
            raise ValueError("dt_seconds must be non-negative")

        self.state.current_time_utc = datetime.datetime.now(datetime.UTC)

        if self.mode is GnssMode.PATH:
            self._advance_on_path(dt_seconds)
        else:
            self._advance_manual(dt_seconds)

        # Refresh derived state before formatting so the GST noise band
        # tracks fix_quality and the visible-satellite list tracks
        # num_satellites — both can change at runtime under fault
        # injection (fix-quality downgrade scheduler).
        self.state.gst_profile = GST_PROFILES.get(
            self.state.fix_quality, GST_PROFILES[0]
        )
        self.state.satellite_prns = [
            f"{i:02d}"
            for i in range(1, max(1, self.state.num_satellites) + 1)
        ][: self.state.num_satellites]

        emitted: list[bytes] = []
        for key in self.sentences:
            result = NMEA_BUILDERS[key](self.state)
            # GSV is multi-line — the builder returns a list. Treating
            # both paths uniformly keeps the loop symmetric for any
            # future batched sentences.
            sentences = result if isinstance(result, list) else [result]
            for sentence in sentences:
                data = sentence.encode("ascii") + _LINE_TERMINATOR
                self.channel.write(data)
                emitted.append(data)
        return emitted

    def _advance_manual(self, dt_seconds: float) -> None:
        distance_m = self.state.speed_knots * KNOTS_TO_MPS * dt_seconds
        if distance_m <= 0.0:
            return
        lat, lon = forward_project(
            self.state.latitude,
            self.state.longitude,
            self.state.heading_true,
            distance_m,
        )
        self.state.latitude = lat
        self.state.longitude = lon

    def _advance_on_path(self, dt_seconds: float) -> None:
        assert self.path_cursor is not None  # enforced by __post_init__
        distance_m = self.state.speed_knots * KNOTS_TO_MPS * dt_seconds
        self.path_cursor.step(distance_m)
        lat, lon = self.path_cursor.current_position()
        self.state.latitude = lat
        self.state.longitude = lon
        # 5 m lookahead — turns into corners rather than overshooting.
        self.state.heading_true = self.path_cursor.target_bearing(lookahead_m=5.0)
