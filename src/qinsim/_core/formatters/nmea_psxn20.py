"""$PSXN,20 — Kongsberg Seapath proprietary motion sentence.

.. deprecated:: 2026-04-23
   The native motion driver (``simulators/native/motion/driver.py``) now
   emits **TSS1** via :mod:`~native._core.formatters.tss1` — Qinsy's
   motion template is wired for TSS1, which is what real MRUs on the
   survey fleet output. This PSXN,20 builder is kept intact as a
   reference and as a drop-in alternative if a future driver variant
   needs Seapath-native framing, but it has no live callers and does
   not reach the wire in the shipped configuration. **Do not re-enable
   it for the motion lane without coordinating with Sam** — changing
   the on-wire format mid-flight breaks the Qinsy template.

Ports Meridian's ``motion_simulator.format_psxn20`` to a pure-Python, scalar-
parameter builder. Motion drivers call this with the current attitude; the
GnssState object is deliberately NOT used here because an MRU is conceptually
a different sensor from a GNSS receiver (even when physically integrated).

Wire format VERIFIED against Meridian's MotionSim:

    $PSXN,20,hhmmss.ss,heading,roll,pitch,heave*CS

Fields are two-decimal degrees / metres. Heading is whatever the caller
passes — MotionSim treats it as a static value while real Seapaths derive
it from the GNSS INS solution.
"""

from __future__ import annotations

import datetime

from ..checksum import nmea_encode
from ..time_fmt import format_time_hhmmss_ss


def build_psxn20(
    heading_deg: float,
    roll_deg: float,
    pitch_deg: float,
    heave_m: float,
    utc_now: datetime.datetime | None = None,
) -> str:
    """Return ``$PSXN,20,...*CS`` for the given attitude scalars.

    ``utc_now`` defaults to ``datetime.utcnow()`` so unit tests can inject a
    fixed timestamp. Drivers should pass their own simulation clock rather
    than relying on the default.
    """
    if utc_now is None:
        utc_now = datetime.datetime.now(datetime.UTC)
    body = (
        "PSXN,20,"
        f"{format_time_hhmmss_ss(utc_now)},"
        f"{heading_deg:.2f},"
        f"{roll_deg:.2f},"
        f"{pitch_deg:.2f},"
        f"{heave_m:.2f}"
    )
    return nmea_encode(body)
