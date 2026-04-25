"""NMEA 0183 checksum primitives.

A single source of truth for the XOR-over-payload checksum used by every
NMEA-style sentence aqps emits (GGA/HDT/RMC/VTG/GLL/GSA/GST/ZDA/PSXN/VX2).
Ports match Meridian's duplicated implementations (gnss, heading, motion,
svx_cast) — DOCUMENTED against NMEA 0183 v4.10 §5.3.1.
"""

from __future__ import annotations


def nmea_checksum(payload: str) -> str:
    """Return the two-character hex NMEA checksum for ``payload``.

    Input may include a leading ``$`` and/or a trailing ``*CS`` — both are
    stripped before computing the XOR. Output is always uppercase two-char
    hex, e.g. ``"6A"``.
    """
    s = payload[1:] if payload.startswith("$") else payload
    if "*" in s:
        s = s.split("*", 1)[0]
    cs = 0
    for ch in s:
        cs ^= ord(ch)
    return format(cs, "02X")


def nmea_encode(body: str) -> str:
    """Wrap a payload body as a complete NMEA sentence ``$body*CS``.

    ``body`` must NOT include the leading ``$`` or the trailing ``*CS``; this
    function adds both. No CRLF is appended — line termination is the
    transport layer's job (OutputChannel applies CRLF when configured).
    """
    return f"${body}*{nmea_checksum(body)}"
