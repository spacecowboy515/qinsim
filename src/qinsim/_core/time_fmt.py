"""UTC time / date formatting helpers for NMEA-family sentences.

Three formats cover everything aqps currently emits:

- ``hhmmss.ss`` — GNSS NMEA sentences (GGA/RMC/GLL/GST/ZDA) AND $PSXN,20
  (Meridian's MotionSim emits PSXN,20 with centisecond precision, verified
  against ``motion_simulator.format_time_hhmmssss``).
- ``hhmmss.sss`` — $VX2 (SVX Cast), which specifies millisecond precision.
- ``ddmmyy`` — GPRMC date field.

Callers are expected to pass an aware or naive UTC ``datetime``; we do not
convert between time zones. Driver code is responsible for calling
``datetime.datetime.utcnow()`` or equivalent before formatting.
"""

from __future__ import annotations

import datetime


def format_time_hhmmss_ss(dt: datetime.datetime) -> str:
    """Format ``dt`` as ``hhmmss.ss`` (centisecond precision).

    Used by GPGGA, GPRMC, GPGLL, GPGST, GPZDA. Rounds the seconds field to
    two decimals by construction (``{:05.2f}``), matching Meridian's
    established GNSS output.
    """
    seconds = dt.second + dt.microsecond / 1_000_000.0
    return dt.strftime("%H%M") + f"{seconds:05.2f}"


def format_time_hhmmss_sss(dt: datetime.datetime) -> str:
    """Format ``dt`` as ``hhmmss.sss`` (millisecond precision).

    Used by $VX2 — Valeport MIDAS SVX2 quotes three decimals on the time
    field.
    """
    seconds = dt.second + dt.microsecond / 1_000_000.0
    return dt.strftime("%H%M") + f"{seconds:06.3f}"


def format_date_ddmmyy(dt: datetime.datetime) -> str:
    """Format ``dt`` as ``ddmmyy`` — the GPRMC date field format."""
    return dt.strftime("%d%m%y")
