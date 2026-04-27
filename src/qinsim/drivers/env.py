"""Environmental driver — emits MTW (water temperature) and XDR.

Two sentence streams from one driver because they share a tick cadence
in real installs: a CTD or sounder typically reports water temperature
via MTW and a small bag of additional measurements (atmospheric
pressure, humidity, vendor-specific channels) via XDR at the same rate.

The driver applies a small bounded random walk to keep the temperature
trace alive between scenarios. XDR quads are rebuilt from the dataclass
scalars on every tick — operators extend the set by hand-editing the
``xdr_quads`` list in the YAML scenario, and the driver carries them
through unchanged.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from .._core.channel import OutputChannel
from .._core.formatters.nmea_xdr import XdrMeasurement, build_mtw, build_xdr
from .._core.state.env_state import EnvState

_LINE_TERMINATOR = b"\r\n"

# Random-walk magnitude on water temperature. 0.02°C/s is realistic for
# a moored sensor in a calm body of water — the trace breathes without
# wandering far over a multi-hour run.
_DEFAULT_TEMP_WALK_C_PER_S: float = 0.02


_ENV_SENTENCES = ("MTW", "XDR")


@dataclass
class EnvDriver:
    """Drives an :class:`EnvState` forward and emits MTW and XDR."""

    state: EnvState
    channel: OutputChannel
    sentences: Sequence[str] = field(default_factory=lambda: list(_ENV_SENTENCES))
    temp_walk_c_per_s: float = _DEFAULT_TEMP_WALK_C_PER_S
    seed: int | None = None
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        unknown = [s for s in self.sentences if s not in _ENV_SENTENCES]
        if unknown:
            raise ValueError(
                f"Unknown env sentence keys: {unknown!r}; supported: {list(_ENV_SENTENCES)}"
            )
        self._rng = random.Random(self.seed)

    def tick(self, dt_seconds: float) -> list[bytes]:
        if dt_seconds < 0:
            raise ValueError("dt_seconds must be non-negative")

        if self.temp_walk_c_per_s > 0.0 and dt_seconds > 0.0:
            step = self._rng.uniform(-1.0, 1.0) * self.temp_walk_c_per_s * dt_seconds
            self.state.water_temp_c += step

        # Rebuild XDR quads from the live scalars + any extra channels
        # the operator pinned in YAML. Pinned quads are appended after
        # the standard P/H pair so dashboards see a stable column order.
        base_quads: list[XdrMeasurement] = [
            XdrMeasurement(
                type_code="P",
                value=self.state.pressure_bar,
                unit="B",
                identifier="BARO",
            ),
            XdrMeasurement(
                type_code="H",
                value=self.state.humidity_pct,
                unit="P",
                identifier="HUM",
            ),
        ]
        quads = base_quads + list(self.state.xdr_quads)

        emitted: list[bytes] = []
        for key in self.sentences:
            if key == "MTW":
                sentence = build_mtw(
                    self.state.water_temp_c, talker_id=self.state.mtw_talker_id
                )
            else:
                sentence = build_xdr(quads, talker_id=self.state.xdr_talker_id)
            data = sentence.encode("ascii") + _LINE_TERMINATOR
            self.channel.write(data)
            emitted.append(data)
        return emitted
