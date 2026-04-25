"""Environmental state — water temperature and generic XDR channels.

Used by the env driver to emit MTW (water temperature) and XDR
(generic transducer measurements: pressure, salinity, etc.). The XDR
quad list is operator-defined in YAML; the dataclass holds the live
values that get formatted on each tick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..formatters.nmea_xdr import XdrMeasurement


@dataclass
class EnvState:
    """One tick of environmental state."""

    # Water temperature in degrees Celsius (MTW).
    water_temp_c: float = 18.0

    # Atmospheric pressure in bar (XDR ``P`` quad).
    pressure_bar: float = 1.013

    # Relative humidity, percent (XDR ``H`` quad).
    humidity_pct: float = 65.0

    # Operator-extensible XDR quads. Driver tick rebuilds this list
    # from the scalars above plus any extra YAML-configured channels.
    xdr_quads: List[XdrMeasurement] = field(default_factory=list)

    # Talker IDs.
    mtw_talker_id: str = "YX"
    xdr_talker_id: str = "YX"
