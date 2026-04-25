"""Depth NMEA 0183 sentence builders — DBT and DPT.

Qinsy's single-beam echosounder driver accepts either DBT (depth below
transducer in feet/metres/fathoms) or DPT (depth + transducer offset).
Most modern installs prefer DPT because the offset field lets Qinsy do
its own waterline computation without a per-vessel calibration.

VERIFIED against NMEA 0183 v4.10 §6.3.5 (DBT) and §6.3.6 (DPT).
"""

from __future__ import annotations

from ..checksum import nmea_encode


def build_dbt(depth_m: float, *, talker_id: str = "SD") -> str:
    """Return ``$<talker>DBT,ff.f,f,mm.m,M,FF.F,F*CS`` — depth below transducer.

    Three units shipped per spec: feet, metres, fathoms. Talker ``SD``
    (sounder, depth) is the conventional default. Qinsy filters on it.
    """
    depth_ft = depth_m * 3.2808399
    depth_fath = depth_m * 0.5468066
    return nmea_encode(
        f"{talker_id}DBT,"
        f"{depth_ft:.1f},f,"
        f"{depth_m:.1f},M,"
        f"{depth_fath:.1f},F"
    )


def build_dpt(
    depth_m: float,
    offset_m: float = 0.0,
    *,
    talker_id: str = "SD",
) -> str:
    """Return ``$<talker>DPT,dd.d,oo.o,mm.m*CS`` — depth + transducer offset.

    ``offset_m`` is the distance from the waterline to the transducer
    face: positive when the transducer sits below the waterline (typical
    keel-mounted unit). Some templates carry a third "max depth range"
    field; included as a constant 200 m here per the broadly compatible
    Qinsy default. The driver state can override if a specific install
    needs the exact transducer max range advertised.
    """
    return nmea_encode(
        f"{talker_id}DPT,"
        f"{depth_m:.1f},"
        f"{offset_m:.1f},"
        "200.0"
    )
