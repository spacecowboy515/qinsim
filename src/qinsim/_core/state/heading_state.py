"""Shared state for a heading-only driver (gyro, autopilot, fluxgate).

Kept deliberately minimal: the HeadingDriver emits a single NMEA sentence
(HDT) and steers one scalar — the current true heading — toward a target.
Position is carried here only so the driver can advance a ``PathCursor``
when running in PATH mode; it is never formatted into the heading output.

Separate from :class:`GnssState` so a vessel with independent gyro and GNSS
feeds (the common case on a real ship) can run two drivers with two states
without fabricating irrelevant GNSS fields on the gyro side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class HeadingState:
    """Minimal state for a rate-limited heading driver."""

    # Current true heading, degrees clockwise from north, folded to [0, 360).
    current_heading_deg: float = 0.0

    # Manual target heading. ``None`` means "hold current" — the driver does
    # not try to steer. The MANUAL mode of HeadingDriver consumes this field;
    # PATH mode ignores it and uses the cursor's lookahead bearing instead.
    target_heading_deg: Optional[float] = None

    # Vessel speed, knots. Only used in PATH mode to advance the cursor.
    speed_knots: float = 0.0

    # Maximum yaw rate the autopilot will command, deg/s. This is what
    # keeps the heading from snapping at path corners — realistic vessels
    # have a bounded turn rate and sensors like gyros see that curve.
    turn_rate_dps: float = 5.0

    # Position is only needed when the driver is advancing a PathCursor;
    # it is not part of the HDT wire format. Default to (0, 0) since many
    # tests don't care about it.
    latitude: float = 0.0
    longitude: float = 0.0

    # Magnetic variation / deviation, degrees. Used by the HDG builder only —
    # HDT and HDM do not report these. Defaults to 0.0 (no correction) so a
    # state built with only ``current_heading_deg`` still emits a valid HDG
    # sentence. Qinsy drivers that consume HDG typically treat 0.0 variation
    # as "no declination applied", which is the correct behaviour when the
    # operator has not entered a magnetic model.
    deviation_deg: float = 0.0
    variation_deg: float = 0.0

    # Optional talker-ID override. ``None`` keeps each heading sentence on
    # its historical default (``GP`` for HDT, ``HC`` for HDM/HDG). When
    # set, applies uniformly to every sentence this state's driver emits.
    # Real-world integrations vary: a gyrocompass sends ``$HEHDT``, an
    # INS sends ``$INHDT``, a fluxgate sends ``$HCHDM``. Operators
    # override to match the Qinsy template filter.
    talker_id: Optional[str] = None
