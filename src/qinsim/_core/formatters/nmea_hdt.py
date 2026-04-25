"""GPHDT — true-heading NMEA sentence (scalar builder).

Factored out of ``nmea_gnss`` so that heading-only devices (the HeadingSim
port; compass/gyro drivers) can build the sentence without fabricating a full
``GnssState``. The state-object wrapper in ``nmea_gnss.build_hdt`` delegates
here so there is a single source of truth for the wire format.

VERIFIED against Meridian's ``heading_simulator.format_hdt`` — same field
order, same two-decimal heading, same ``T`` (true) reference indicator.
"""

from __future__ import annotations

from ..checksum import nmea_encode


def build_hdt(heading_deg: float, *, talker_id: str = "GP") -> str:
    """Return ``$<talker>HDT,hhh.hh,T*CS`` for a true-heading value in degrees.

    Callers are responsible for wrapping the value into ``[0, 360)`` if
    required — this builder formats whatever it is given. ``talker_id``
    defaults to ``GP`` matching Meridian's historical output; real gyros
    often emit ``HE`` or ``IN`` and Qinsy's filter rules key on it.
    """
    return nmea_encode(f"{talker_id}HDT,{heading_deg:.2f},T")


def build_hdm(heading_deg: float, *, talker_id: str = "HC") -> str:
    """Return ``$<talker>HDM,hhh.hh,M*CS`` for a magnetic-heading value.

    Default talker is ``HC`` (heading compass) per NMEA 0183; Qinsy
    filters on talker ID and rejects HDM sentences that advertise ``GP``.
    The ``M`` reference indicator distinguishes this from HDT.
    """
    return nmea_encode(f"{talker_id}HDM,{heading_deg:.2f},M")


def build_hdg(
    heading_deg: float,
    deviation_deg: float,
    variation_deg: float,
    *,
    talker_id: str = "HC",
) -> str:
    """Return ``$<talker>HDG,hhh.hh,dd.d,E|W,vv.v,E|W*CS``.

    HDG carries magnetic heading plus per-ship deviation and per-location
    variation. The direction fields are always present in the wire format
    even when the magnitude is zero — NMEA parsers that expect a full
    five-field sentence reject the shorter variant.
    """
    dev_dir = "E" if deviation_deg >= 0 else "W"
    var_dir = "E" if variation_deg >= 0 else "W"
    return nmea_encode(
        f"{talker_id}HDG,{heading_deg:.2f},"
        f"{abs(deviation_deg):.1f},{dev_dir},"
        f"{abs(variation_deg):.1f},{var_dir}"
    )
