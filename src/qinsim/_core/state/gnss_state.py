"""Plain dataclass carrying the GNSS scalars that feed NMEA sentence builders.

Ports ``GNSSSimulatorState`` from Meridian's ``gnss_simulator.py`` into a
pure-Python dataclass. The Qt signals, threading helpers, and path-stepping
logic are intentionally left behind — those belong in the driver that
orchestrates the state, not in the state itself.

Every field's type/default comes from VERIFIED observation of Meridian's
runtime behaviour (the generator functions in ``nmea_gnss`` read these
attribute names and expect these Python types).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

# GST noise profile presets keyed by NMEA fix quality.
# Structure: (std_major_m, std_minor_m, orientation_deg, sigma_lat_m,
#             sigma_lon_m, sigma_alt_m, _unused). Ported verbatim from
# Meridian's ``GST_PROFILES`` — values reflect plausible RTK/DGPS/SPS
# uncertainty bands.
GST_PROFILES: dict[int, tuple[float, float, float, float, float, float, float]] = {
    0: (99.9, 99.9, 99.9, 0.0, 99.9, 99.9, 99.9),
    1: (10.0, 8.0, 6.0, 45.0, 7.5, 5.5, 12.0),
    2: (2.5, 2.0, 1.5, 30.0, 1.8, 1.2, 3.0),
    4: (0.05, 0.03, 0.02, 90.0, 0.025, 0.015, 0.04),
    5: (0.8, 0.6, 0.4, 60.0, 0.5, 0.3, 1.0),
}


def _default_time() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _default_prns() -> list[str]:
    return ["01", "04", "07", "10", "13", "16", "19", "22"]


@dataclass
class GnssState:
    """Scalars read by every NMEA sentence builder.

    The driver mutates these fields each simulation step; the formatter
    functions in ``formatters/nmea_gnss.py`` read them without mutating.
    """

    # Position / attitude
    current_time_utc: datetime.datetime = field(default_factory=_default_time)
    latitude: float = 51.5074
    longitude: float = -0.1278
    altitude_msl: float = 50.0
    geoid_separation: float = -5.3
    speed_knots: float = 0.0
    heading_true: float = 0.0

    # Quality / DOP
    fix_quality: int = 1
    num_satellites: int = 8
    hdop: float = 1.0
    vdop: float = 1.5
    pdop: float = 1.8

    # GSA / GST
    mode_gsa_1: str = "A"
    satellite_prns: list[str] = field(default_factory=_default_prns)
    gst_profile: tuple[float, float, float, float, float, float, float] = GST_PROFILES[1]

    # Set by ``FOLLOW_REPLAYER`` mode when the latest poll of the
    # replayer's /position/current endpoint returned no fresh data
    # (session not running, no #SPO yet, HTTP timeout, …). The driver
    # still emits NMEA from the last-known position; the flag propagates
    # to the QRG so the operator knows the number is frozen.
    follow_stale: bool = False

    # Two-character NMEA 0183 talker ID prefixed to every sentence this
    # state emits. ``None`` means "use the builder's historical default"
    # (``GP``) — preserves wire-level compatibility for callers that
    # never touched this field. Qinsy filter rules often key on talker
    # ID (``GN`` for multi-constellation, ``IN`` for integrated nav),
    # so operators override per-device when deploying against a real
    # template.
    talker_id: str | None = None
