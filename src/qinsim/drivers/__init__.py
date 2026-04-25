"""Per-device simulator drivers.

Each driver is a small class with a ``state`` (per-driver dataclass), a
:class:`OutputChannel` reference, and a ``tick(dt: float) -> list[bytes]``
method. The runtime drives ticks at the configured rate; the driver
itself has no timer, no async, no Qt.

Five kinds in qinsim v1: ``gnss``, ``heading``, ``motion``, ``depth``,
``env``. Each has a corresponding ``state`` dataclass under
:mod:`qinsim._core.state` and one or more formatters under
:mod:`qinsim._core.formatters`.
"""

from .depth import DepthDriver
from .env import EnvDriver
from .gnss import GnssDriver, GnssMode
from .heading import HeadingDriver, HeadingMode
from .motion import MotionDriver

__all__ = [
    "DepthDriver",
    "EnvDriver",
    "GnssDriver",
    "GnssMode",
    "HeadingDriver",
    "HeadingMode",
    "MotionDriver",
]
