"""Spherical-earth geodesy primitives for simulator drivers.

Ports the inline helpers from Meridian's ``gnss_simulator`` and ``heading_
simulator`` into one source of truth. The formulas use a sphere of radius
``EARTH_RADIUS_METERS`` — good enough for a simulator whose outputs we
compare against other great-circle computations, not against WGS-84 or a
real ellipsoidal geoid. Do not use these for survey-grade geodesy.

Functions take and return degrees (lat/lon) and metres (distance). Headings
and bearings are degrees clockwise from true north, in ``[0, 360)``.
"""

from __future__ import annotations

import math
from typing import Tuple

# DOCUMENTED: mean Earth radius per IUGG 1980 (6 371 008.8 m, rounded to
# the integer value Meridian's simulators use). Any nearby value is fine for
# a simulator — the constant is shared so every computation agrees.
EARTH_RADIUS_METERS: float = 6_371_000.0

# DOCUMENTED: international knot = 1852 m/h exactly (since 1929).
KNOTS_TO_MPS: float = 1852.0 / 3600.0


def haversine_distance(
    lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float
) -> float:
    """Great-circle distance between two lat/lon points, in metres."""
    lat1 = math.radians(lat1_deg)
    lat2 = math.radians(lat2_deg)
    dlat = lat2 - lat1
    dlon = math.radians(lon2_deg - lon1_deg)
    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_METERS * math.asin(math.sqrt(a))


def bearing_to_waypoint(
    from_lat_deg: float, from_lon_deg: float,
    to_lat_deg: float, to_lon_deg: float,
) -> float:
    """Initial true bearing from ``from`` to ``to``, in degrees ``[0, 360)``."""
    lat1 = math.radians(from_lat_deg)
    lat2 = math.radians(to_lat_deg)
    dlon = math.radians(to_lon_deg - from_lon_deg)
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def forward_project(
    lat_deg: float, lon_deg: float, bearing_deg: float, distance_m: float,
) -> Tuple[float, float]:
    """Return ``(lat, lon)`` after travelling ``distance_m`` on ``bearing_deg``.

    The returned longitude is normalised into ``[-180, 180)`` so downstream
    NMEA formatters do not see a wrap-induced jump.
    """
    br = math.radians(bearing_deg)
    lat1 = math.radians(lat_deg)
    lon1 = math.radians(lon_deg)
    dr = distance_m / EARTH_RADIUS_METERS
    lat2 = math.asin(
        math.sin(lat1) * math.cos(dr) + math.cos(lat1) * math.sin(dr) * math.cos(br)
    )
    lon2 = lon1 + math.atan2(
        math.sin(br) * math.sin(dr) * math.cos(lat1),
        math.cos(dr) - math.sin(lat1) * math.sin(lat2),
    )
    # Normalise into [-pi, pi) before converting back to degrees.
    lon2 = (lon2 + math.pi) % (2.0 * math.pi) - math.pi
    return math.degrees(lat2), math.degrees(lon2)


def normalize_angle_diff(diff_deg: float) -> float:
    """Fold a signed heading difference into ``(-180, 180]``.

    Used by turn-rate controllers to pick the shorter direction when a
    target heading is on the other side of 0/360.
    """
    d = diff_deg
    while d <= -180.0:
        d += 360.0
    while d > 180.0:
        d -= 360.0
    return d
