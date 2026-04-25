"""Threaded registry — instantiates drivers from a Config and runs them.

Each driver runs in its own daemon thread with its own
:class:`OutputChannel` and a per-tick effect chain. Threads share a
single :class:`UdpTransport` (one socket per process; sends are
thread-safe at the transport layer). Shutdown / scenario-swap is
signalled via a :class:`threading.Event`.

The thread loop is the simplest possible monotonic-clock pacer:

::

    next_due = monotonic()
    while not stop:
        now = monotonic()
        if now >= next_due:
            buf = driver.tick(period)
            next_due += period
            if now > next_due + period:
                next_due = now + period   # slipped, reset
        else:
            stop.wait(timeout=next_due - now)

Slip is reported back to the registry so the TUI can show "this
driver fell 80 ms behind real time" — usually a sign the operator
turned on a high-rate scenario on a sleep-throttled VM.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ._core.channel import OutputChannel
from ._core.effects import effect_from_dict
from ._core.motion_model import MotionModel
from ._core.state.depth_state import DepthState
from ._core.state.env_state import EnvState
from ._core.state.gnss_state import GnssState
from ._core.state.heading_state import HeadingState
from ._core.state.motion_state import MotionState
from ._core.transports.udp import UdpTransport
from .config import Config, Destination, DriverSpec, EffectSpec
from .drivers import DepthDriver, EnvDriver, GnssDriver, HeadingDriver, MotionDriver
from .drivers.base import Driver

log = logging.getLogger(__name__)


@dataclass
class DriverHandle:
    """Live handle for one running driver — used by the status TUI."""

    name: str
    kind: str
    rate_hz: float
    driver: Driver
    channel: OutputChannel
    thread: threading.Thread
    stop_event: threading.Event
    last_emit: List[bytes] = field(default_factory=list)
    last_emit_ts: float = 0.0
    slip_ms: float = 0.0


class ThreadedRegistry:
    """Owns the running drivers, their channels, and the shared transport."""

    def __init__(self) -> None:
        self._transport: Optional[UdpTransport] = None
        self._handles: List[DriverHandle] = []
        self._lock = threading.Lock()
        self._config: Optional[Config] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, config: Config) -> None:
        """Spin up every driver in ``config``. Idempotent — calls stop() first."""
        self.stop()
        with self._lock:
            self._transport = UdpTransport()
            self._handles = [
                self._spawn(spec, config.destinations, self._transport)
                for spec in config.drivers
            ]
            self._config = config

    def stop(self) -> None:
        """Tear down every driver thread, channel, and the transport."""
        with self._lock:
            for h in self._handles:
                h.stop_event.set()
            for h in self._handles:
                h.thread.join(timeout=1.0)
            for h in self._handles:
                try:
                    h.channel.close()
                except Exception as exc:  # pragma: no cover — best-effort cleanup
                    log.debug("channel close failed for %s: %s", h.name, exc)
            self._handles = []
            if self._transport is not None:
                try:
                    self._transport.close()
                except Exception as exc:  # pragma: no cover
                    log.debug("transport close failed: %s", exc)
            self._transport = None
            self._config = None

    def swap(self, config: Config) -> None:
        """Stop current drivers, start the new config. Used by the scenario picker."""
        self.start(config)

    def handles(self) -> List[DriverHandle]:
        """Return a shallow copy of live handles for the status panel."""
        with self._lock:
            return list(self._handles)

    @property
    def config(self) -> Optional[Config]:
        return self._config

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _spawn(
        self,
        spec: DriverSpec,
        destinations: Sequence[Destination],
        transport: UdpTransport,
    ) -> DriverHandle:
        # One channel per (driver, destination) pair so each Qinsy box
        # receives an independent stream — this matches the way real
        # vessel networks are typically wired (one source, many sinks)
        # and keeps the effects chain attached to the wire-side, not
        # the driver-side, of the fan-out.
        primary = destinations[0]
        channel = OutputChannel(transport, primary.host, primary.port)
        for fx_spec in spec.effects:
            channel.add_effect(_build_effect(fx_spec))

        # Additional destinations get fan-out subscribers — same bytes,
        # multiple wires. Honest single-source semantics; if the
        # operator wants per-destination effects they should run two
        # qinsim instances.
        for extra in destinations[1:]:
            host, port = extra.host, extra.port

            def _fanout(data: bytes, _ts: float, host: str = host, port: int = port) -> None:
                transport.send(host, port, data)

            channel.add_subscriber(_fanout)

        driver = _build_driver(spec, channel)
        stop_event = threading.Event()
        period = 1.0 / spec.rate_hz

        handle = DriverHandle(
            name=spec.name,
            kind=spec.kind,
            rate_hz=spec.rate_hz,
            driver=driver,
            channel=channel,
            thread=threading.Thread(),  # placeholder, replaced below
            stop_event=stop_event,
        )

        def _loop() -> None:
            next_due = time.monotonic()
            while not stop_event.is_set():
                now = time.monotonic()
                if now >= next_due:
                    try:
                        emitted = driver.tick(period)
                    except Exception as exc:
                        log.exception("driver %s tick failed: %s", spec.name, exc)
                        # Don't kill the thread — scenarios with
                        # transient state errors should self-heal as
                        # the driver advances. A persistent fault
                        # surfaces in the status panel via repeated
                        # log lines.
                        next_due = now + period
                        continue
                    handle.last_emit = emitted
                    handle.last_emit_ts = now
                    next_due += period
                    handle.slip_ms = max(0.0, (now - next_due + period) * 1000.0)
                    if now > next_due + period:
                        # Slipped a full period; reset to avoid a
                        # cascading catch-up burst that would saturate
                        # the channel queue.
                        next_due = now + period
                else:
                    stop_event.wait(timeout=max(0.0, next_due - now))

        thread = threading.Thread(
            target=_loop, name=f"qinsim-driver-{spec.name}", daemon=True
        )
        handle.thread = thread
        thread.start()
        return handle


# ---------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------


def _build_effect(spec: EffectSpec) -> Any:
    return effect_from_dict(spec.to_dict())


def _build_driver(spec: DriverSpec, channel: OutputChannel) -> Driver:
    if spec.kind == "gnss":
        state = _coerce_state(GnssState, spec.state)
        sentences = spec.state.get("sentences", ["GGA", "RMC", "VTG"])
        return GnssDriver(state=state, channel=channel, sentences=sentences)
    if spec.kind == "heading":
        state = _coerce_state(HeadingState, spec.state)
        sentences = spec.state.get("sentences", ["HDT"])
        return HeadingDriver(state=state, channel=channel, sentences=sentences)
    if spec.kind == "motion":
        import random as _random

        state = _coerce_state(MotionState, spec.state)
        # MotionModel takes an injected Random — pin a seed via
        # ``motion_seed`` for reproducible scenarios; otherwise system
        # entropy gives fresh phases per run.
        seed = spec.state.get("motion_seed")
        rng = _random.Random(seed) if seed is not None else _random.Random()
        return MotionDriver(state=state, channel=channel, model=MotionModel(rng=rng))
    if spec.kind == "depth":
        state = _coerce_state(DepthState, spec.state)
        return DepthDriver(
            state=state,
            channel=channel,
            walk_rate_m_per_s=float(spec.state.get("walk_rate_m_per_s", 0.05)),
            seed=spec.state.get("seed"),
        )
    if spec.kind == "env":
        state = _coerce_env_state(spec.state)
        return EnvDriver(
            state=state,
            channel=channel,
            temp_walk_c_per_s=float(spec.state.get("temp_walk_c_per_s", 0.02)),
            seed=spec.state.get("seed"),
        )
    raise ValueError(f"unknown driver kind: {spec.kind}")


def _coerce_state(cls: type, raw: Dict[str, Any]) -> Any:
    """Build ``cls(...)`` from ``raw``, ignoring keys the dataclass doesn't know.

    The state YAML carries some operator-side keys (``sentences``,
    ``walk_rate_m_per_s``, ``seed``) that aren't dataclass fields —
    those are consumed by the driver factory above. Filtering keeps
    the dataclass constructor strict without forcing the YAML to fan
    out into nested sections.
    """
    fields = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    filtered = {k: v for k, v in raw.items() if k in fields}
    return cls(**filtered)


def _coerce_env_state(raw: Dict[str, Any]) -> EnvState:
    """EnvState needs xdr_quads coerced from list-of-dict to list-of-XdrMeasurement."""
    from ._core.formatters.nmea_xdr import XdrMeasurement

    fields = {f for f in EnvState.__dataclass_fields__}
    filtered: Dict[str, Any] = {k: v for k, v in raw.items() if k in fields}
    quads_raw = filtered.get("xdr_quads", [])
    if not isinstance(quads_raw, list):
        raise ValueError("env.state.xdr_quads must be a list")
    quads: List[XdrMeasurement] = []
    for i, entry in enumerate(quads_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"env.state.xdr_quads[{i}] must be a mapping")
        quads.append(
            XdrMeasurement(
                type_code=str(entry["type_code"]),
                value=float(entry["value"]),
                unit=str(entry["unit"]),
                identifier=str(entry["identifier"]),
            )
        )
    filtered["xdr_quads"] = quads
    return EnvState(**filtered)
