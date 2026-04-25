"""Single-beam echosounder state — the depth driver's tick state.

Carries the most recent depth reading plus the static install metadata
(transducer offset, max range, talker ID). The driver advances ``depth_m``
between ticks via a slow random walk plus optional sea-floor profile;
this dataclass is just the snapshot a formatter consumes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DepthState:
    """One tick of single-beam depth state."""

    # Current depth below transducer, metres, positive-down.
    depth_m: float = 25.0

    # Distance waterline → transducer face, metres, positive-down.
    transducer_offset_m: float = 0.5

    # Reported max range. Qinsy uses this for plot-axis defaults.
    max_range_m: float = 200.0

    # NMEA talker ID (``SD`` = sounder/depth; some sounders use ``EC``).
    talker_id: str = "SD"
