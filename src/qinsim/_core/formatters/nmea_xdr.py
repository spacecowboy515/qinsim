"""XDR — generic NMEA 0183 transducer-measurement sentence.

Qinsy ships a generic XDR driver that accepts any transducer reading
tagged by NMEA 0183 §6.2.7 transducer type codes. Adding build_xdr here
lets aqps' vendor-specific drivers (miniSVS, SVXCast) emit their raw
values as XDR alongside their native ASCII format — an operator can
then consume the same instrument via Qinsy's generic XDR driver without
configuring a Valeport-specific parser.

Wire format is ``$YXXDR,<type>,<value>,<unit>,<id>,...*CS`` where each
measurement occupies a four-field quad. A sentence may carry up to
four quads by convention; this builder accepts an arbitrary number and
the caller is responsible for keeping the line under the NMEA 82-char
recommendation if strict compatibility is required.

Talker ID is ``YX`` (generic transducer) by default, matching most
real-world XDR emitters. Callers can override for rare setups where
Qinsy's filter rules demand a different talker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..checksum import nmea_encode


@dataclass(frozen=True)
class XdrMeasurement:
    """One transducer reading — one XDR quad on the wire.

    ``type_code`` follows NMEA 0183 §6.2.7: ``C`` (temperature), ``P``
    (pressure), ``D`` (depth), ``A`` (angular displacement), ``H``
    (humidity), ``F`` (frequency), ``G`` (generic), etc. ``unit`` is
    the SI unit string Qinsy expects (``C``=°C, ``B``=bar, ``M``=metres,
    ``D``=degrees, ``H``=hertz). ``identifier`` is an operator-visible
    label — Qinsy surfaces it on dashboards so keep it short.
    """

    type_code: str
    value: float
    unit: str
    identifier: str

    def __post_init__(self) -> None:
        # NMEA 0183 §6.2.7 type codes are single characters; anything
        # longer is a configuration error, not a runtime condition.
        if len(self.type_code) != 1:
            raise ValueError(f"type_code must be a single character, got {self.type_code!r}")
        if not self.identifier:
            raise ValueError("identifier must be non-empty — Qinsy filters on it")
        if "," in self.identifier or "*" in self.identifier:
            raise ValueError(f"identifier must not contain NMEA delimiters: {self.identifier!r}")


def build_xdr(
    measurements: Sequence[XdrMeasurement],
    *,
    talker_id: str = "YX",
    value_decimals: int = 2,
) -> str:
    """Return ``$<talker>XDR,type,value,unit,id,...*CS``.

    ``value_decimals`` controls the per-quad number format. Two decimals
    matches what Qinsy's XDR driver expects for temperature and pressure
    in the default configuration; callers that need more precision can
    bump it, at the cost of eating into the 82-char NMEA recommendation.

    Empty ``measurements`` raises — an XDR sentence with no quads is
    not legal NMEA and would confuse downstream parsers more than a
    silent drop.
    """
    if not measurements:
        raise ValueError("build_xdr requires at least one measurement")
    if len(talker_id) != 2:
        raise ValueError(f"talker_id must be 2 characters, got {talker_id!r}")
    parts = [f"{talker_id}XDR"]
    for m in measurements:
        parts.append(m.type_code)
        parts.append(f"{m.value:.{value_decimals}f}")
        parts.append(m.unit)
        parts.append(m.identifier)
    return nmea_encode(",".join(parts))


def build_mtw(temperature_c: float, *, talker_id: str = "YX") -> str:
    """Return ``$<talker>MTW,tt.t,C*CS`` — water temperature in degrees Celsius.

    NMEA 0183 §6.3.18. Single-field sentence used by sounders and CTDs.
    Talker ``YX`` (generic transducer) by default, matching most field
    devices; some Qinsy templates expect ``II`` (integrated instrument).
    """
    return nmea_encode(f"{talker_id}MTW,{temperature_c:.1f},C")
