"""GNSS NMEA 0183 sentence builders.

Ports the eight sentence generators from Meridian's ``gnss_simulator.py``
(``create_gpgga`` through ``create_gpzda``). Signatures match the original
state-object pattern: each builder takes a :class:`GnssState` and returns a
complete ``$...*CS`` sentence string (no CRLF — transport layer appends).

Formatting rules are VERIFIED against Meridian's validated output; fix_quality
→ mode-indicator mapping comes from the same source. Lat/lon formatting is
factored into shared helpers because GGA, RMC, and GLL all use it.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Union

from ..checksum import nmea_encode
from ..state.gnss_state import GnssState
from ..time_fmt import format_date_ddmmyy, format_time_hhmmss_ss
from .nmea_hdt import build_hdt as _build_hdt_scalar


def format_lat_nmea(latitude: float) -> str:
    """Return ``ddmm.mmmm,H`` for a signed decimal latitude in degrees.

    ``None`` is not accepted — pass ``0.0`` if you mean the equator.
    Empty-field handling belongs in the caller if it's ever needed.
    """
    hemisphere = "N" if latitude >= 0 else "S"
    mag = abs(latitude)
    degrees = int(mag)
    minutes = (mag - degrees) * 60
    return f"{degrees:02d}{minutes:07.4f},{hemisphere}"


def format_lon_nmea(longitude: float) -> str:
    """Return ``dddmm.mmmm,H`` for a signed decimal longitude in degrees."""
    hemisphere = "E" if longitude >= 0 else "W"
    mag = abs(longitude)
    degrees = int(mag)
    minutes = (mag - degrees) * 60
    return f"{degrees:03d}{minutes:07.4f},{hemisphere}"


def _mode_indicator(fix_quality: int) -> str:
    """Map GGA fix_quality to the single-char mode indicator used by RMC/VTG/GLL.

    Per Meridian's validated mapping: 0→N, 1→A (SPS), 2→D (DGPS/SBAS),
    4/5→A (RTK fixed/float, most receivers quote 'A' here).
    """
    if fix_quality == 1:
        return "A"
    if fix_quality == 2:
        return "D"
    if fix_quality >= 4:
        return "A"
    return "N"


def _talker(state: GnssState) -> str:
    """Return the two-character talker ID for sentences built from ``state``.

    Falls back to ``GP`` when the state did not override it — preserves
    wire-level compatibility for callers that predate the talker_id
    feature and keeps every existing golden-string test green.
    """
    return state.talker_id or "GP"


def build_gga(state: GnssState) -> str:
    body = (
        f"{_talker(state)}GGA,"
        f"{format_time_hhmmss_ss(state.current_time_utc)},"
        f"{format_lat_nmea(state.latitude)},"
        f"{format_lon_nmea(state.longitude)},"
        f"{state.fix_quality},"
        f"{state.num_satellites:02d},"
        f"{state.hdop:.1f},"
        f"{state.altitude_msl:.1f},M,"
        f"{state.geoid_separation:.1f},M,"
        ","  # Age DGPS
    )
    return nmea_encode(body)


def build_hdt(state: GnssState) -> str:
    return _build_hdt_scalar(state.heading_true, talker_id=_talker(state))


def build_rmc(state: GnssState) -> str:
    status = "A" if state.fix_quality > 0 else "V"
    body = (
        f"{_talker(state)}RMC,"
        f"{format_time_hhmmss_ss(state.current_time_utc)},"
        f"{status},"
        f"{format_lat_nmea(state.latitude)},"
        f"{format_lon_nmea(state.longitude)},"
        f"{state.speed_knots:.2f},"
        f"{state.heading_true:.2f},"
        f"{format_date_ddmmyy(state.current_time_utc)},"
        ",,"  # Mag variation value and direction
        f"{_mode_indicator(state.fix_quality)}"
    )
    return nmea_encode(body)


def build_vtg(state: GnssState) -> str:
    speed_kmh = state.speed_knots * 1.852
    body = (
        f"{_talker(state)}VTG,"
        f"{state.heading_true:.2f},T,"
        ",M,"
        f"{state.speed_knots:.2f},N,"
        f"{speed_kmh:.2f},K,"
        f"{_mode_indicator(state.fix_quality)}"
    )
    return nmea_encode(body)


def build_gll(state: GnssState) -> str:
    status = "A" if state.fix_quality > 0 else "V"
    body = (
        f"{_talker(state)}GLL,"
        f"{format_lat_nmea(state.latitude)},"
        f"{format_lon_nmea(state.longitude)},"
        f"{format_time_hhmmss_ss(state.current_time_utc)},"
        f"{status},"
        f"{_mode_indicator(state.fix_quality)}"
    )
    return nmea_encode(body)


def build_gsa(state: GnssState) -> str:
    # Mode2: 1=no fix, 2=2D, 3=3D. Derive from fix_quality and sat count.
    if state.fix_quality <= 0:
        mode2 = 1
    elif state.num_satellites >= 4:
        mode2 = 3
    elif state.num_satellites >= 3:
        mode2 = 2
    else:
        mode2 = 2  # Meridian's rule: if fix reported, at least 2D
    sats = state.satellite_prns[:12]
    sat_fields = ",".join(sats[i] if i < len(sats) else "" for i in range(12))
    body = (
        f"{_talker(state)}GSA,"
        f"{state.mode_gsa_1},"
        f"{mode2},"
        f"{sat_fields},"
        f"{state.pdop:.1f},{state.hdop:.1f},{state.vdop:.1f}"
    )
    return nmea_encode(body)


def build_gst(state: GnssState) -> str:
    t = format_time_hhmmss_ss(state.current_time_utc)
    talker = _talker(state)
    if state.fix_quality == 0:
        body = f"{talker}GST,{t},,,,,,,"
    else:
        p = state.gst_profile
        body = (
            f"{talker}GST,{t},"
            f"{p[0]:.2f},{p[1]:.2f},{p[2]:.2f},"
            f"{p[3]:.1f},"
            f"{p[4]:.2f},{p[5]:.2f},{p[6]:.2f}"
        )
    return nmea_encode(body)


def build_gsv(state: GnssState) -> List[str]:
    """Build one or more GSV sentences listing satellites in view.

    GSV is the only multi-line NMEA sentence in aqps: up to four
    satellites are reported per sentence, with ``total_msgs`` and
    ``msg_num`` signalling the batch. The driver emits each sentence in
    the returned list as a separate line so a downstream parser can
    recover the full sky plot by concatenating them.

    Elevation / azimuth / SNR are derived deterministically from the PRN
    so the output is stable across ticks when the sat list is stable.
    That is not a fidelity claim — we are not modelling satellite motion
    — but it gives Qinsy's sky-plot panel non-empty values to draw
    without needing a real orbit model.
    """
    prns = list(state.satellite_prns)
    talker = _talker(state)
    if not prns:
        # Zero-satellites-in-view still produces a single GSV with
        # ``total=1,msg=1,in_view=0`` to signal "no sats" rather than
        # suppressing the sentence entirely (Qinsy treats absence as
        # "stream died").
        body = f"{talker}GSV,1,1,00,"
        return [nmea_encode(body)]

    # Chunk sats into groups of 4; that is the GSV per-sentence cap.
    groups: List[List[str]] = [prns[i:i + 4] for i in range(0, len(prns), 4)]
    total = len(groups)
    in_view = len(prns)
    out: List[str] = []
    for idx, group in enumerate(groups, start=1):
        parts: List[str] = []
        for prn in group:
            # Cheap deterministic sky placement: elevation 15..85, azimuth
            # 0..355 in 20-degree bins, SNR 35..48. All values are well
            # within Qinsy's validation ranges.
            try:
                n = int(prn)
            except ValueError:
                n = 0
            elev = 15 + (n * 7) % 70
            azim = (n * 20) % 360
            snr = 35 + (n * 3) % 14
            parts.append(f"{prn},{elev:02d},{azim:03d},{snr:02d}")
        # Pad to four sat slots so the sentence is always 19 fields wide
        # (total/msg/in_view + 4 × {prn,elev,azim,snr}). An empty slot is
        # four empty commas.
        while len(parts) < 4:
            parts.append(",,,")
        body = f"{talker}GSV,{total},{idx},{in_view:02d}," + ",".join(parts)
        out.append(nmea_encode(body))
    return out


def build_zda(state: GnssState) -> str:
    dt = state.current_time_utc
    body = (
        f"{_talker(state)}ZDA,"
        f"{format_time_hhmmss_ss(dt)},"
        f"{dt.day:02d},"
        f"{dt.month:02d},"
        f"{dt.year:04d},"
        "00,00"
    )
    return nmea_encode(body)


# Dispatch table mirroring Meridian's ``NMEA_GENERATORS``. Drivers pick a
# subset by sentence-name key.
# Builder return type is a single sentence or a list of sentences — GSV is
# the only current multi-line builder, but the union keeps the door open
# for future batched sentences (e.g. multi-constellation GSA variants)
# without another dispatch change.
NMEA_BUILDERS: Dict[str, Callable[[GnssState], Union[str, List[str]]]] = {
    "GGA": build_gga,
    "HDT": build_hdt,
    "RMC": build_rmc,
    "VTG": build_vtg,
    "GLL": build_gll,
    "GSA": build_gsa,
    "GSV": build_gsv,
    "GST": build_gst,
    "ZDA": build_zda,
}
