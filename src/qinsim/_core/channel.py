"""Per-device output tee — UDP send + in-process subscribers.

Ported from Meridian's ``UnifiedSim.core.pipeline.OutputChannel``. The tee
guarantees byte fidelity: whatever the formatter hands to :meth:`write`
is what lands on the wire AND what every subscriber observes. Subscribers
exist so the React dashboard (or a future pytest capture) can watch the
exact bytes a device emits without tapping the network.

Threading model:
- :meth:`write` is thread-safe and non-blocking (enqueue-and-return).
- A single daemon worker drains the queue, sends via UDP, then notifies
  subscribers on its own thread. Subscribers must forward to their own
  thread (e.g. a Qt signal or an asyncio loop) if they need it.
- :meth:`close` stops the worker and is idempotent.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

from .effects import ChannelEffect, EmitContext
from .transports.udp import UdpTransport


# Subscribers are plain callables; there is no need for a Protocol class. A
# type alias makes the signature explicit at the edges where it matters.
OutputSubscriber = Callable[[bytes, float], None]


@dataclass
class OutputMetrics:
    """Rolling-window counters for a single output channel.

    Totals are lifetime; the rolling events deque backs the per-window
    throughput reported by :meth:`snapshot`.
    """

    window_seconds: float = 5.0
    total_bytes: int = 0
    total_lines: int = 0
    # Lines the effects chain swallowed before they reached the wire. Kept
    # separate from total_lines so a fault-injection run still reports
    # accurate on-the-wire traffic.
    dropped_by_effect: int = 0
    last_error: Optional[str] = None
    last_sent_ts: float = 0.0
    _events: Deque[Tuple[float, int, int]] = field(default_factory=deque)

    def record_event(self, now: float, nbytes: int, nlines: int) -> None:
        self.total_bytes += nbytes
        self.total_lines += nlines
        self.last_sent_ts = now
        self._events.append((now, nbytes, nlines))
        self._trim(now)

    def record_error(self, err: BaseException) -> None:
        self.last_error = str(err)

    def clear_error(self) -> None:
        self.last_error = None

    def _trim(self, now: float) -> None:
        horizon = now - self.window_seconds
        while self._events and self._events[0][0] < horizon:
            self._events.popleft()

    def snapshot(self) -> Dict[str, object]:
        """Return a presentation-ready dict of the current metrics."""
        now = time.monotonic()
        self._trim(now)
        bytes_sum = sum(e[1] for e in self._events)
        lines_sum = sum(e[2] for e in self._events)
        # Use the observed span of events for the rate denominator when it
        # is smaller than the configured window — avoids reporting a low
        # rate during the first fraction of a second of running.
        if len(self._events) > 1:
            span = self._events[-1][0] - self._events[0][0]
            window = max(0.001, min(self.window_seconds, span))
        else:
            window = self.window_seconds
        return {
            "bytes_per_sec": bytes_sum / window if window > 0 else 0.0,
            "lines_per_sec": lines_sum / window if window > 0 else 0.0,
            "total_bytes": self.total_bytes,
            "total_lines": self.total_lines,
            "dropped_by_effect": self.dropped_by_effect,
            "last_error": self.last_error,
            "last_sent_ts": self.last_sent_ts,
        }


class OutputChannel:
    """Per-device tee: enqueue bytes, drain to UDP + subscribers asynchronously."""

    # Queue size tuned to match Meridian's original — big enough to absorb a
    # UI pause without dropping lines, small enough that a genuinely stuck
    # worker makes its presence felt via dropped writes.
    _DEFAULT_QUEUE_SIZE = 10_000

    def __init__(
        self,
        transport: UdpTransport,
        dest_ip: str,
        dest_port: int,
        queue_size: int = _DEFAULT_QUEUE_SIZE,
    ) -> None:
        self._transport = transport
        self._dest_ip = dest_ip
        self._dest_port = dest_port

        self._queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=queue_size)
        self._subs: List[OutputSubscriber] = []
        self._subs_lock = threading.Lock()

        self._effects: List[ChannelEffect] = []
        self._effects_lock = threading.Lock()

        self.metrics = OutputMetrics()
        self._running = True
        self._worker = threading.Thread(
            target=self._run, name="OutputChannelWorker", daemon=True
        )
        self._worker.start()

    def configure_destination(self, dest_ip: str, dest_port: int) -> None:
        # Writes are still serialised through the queue, so reconfiguring
        # mid-stream is safe — the next dequeued item picks up the new dest.
        self._dest_ip = dest_ip
        self._dest_port = dest_port

    def add_subscriber(self, sub: OutputSubscriber) -> None:
        with self._subs_lock:
            if sub not in self._subs:
                self._subs.append(sub)

    def remove_subscriber(self, sub: OutputSubscriber) -> None:
        with self._subs_lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    def add_effect(self, effect: ChannelEffect) -> None:
        """Append ``effect`` to the channel's effects chain.

        Effects run in insertion order on the worker thread. An effect
        returning ``None`` stops the chain for that line and drops it.
        """
        with self._effects_lock:
            self._effects.append(effect)

    def remove_effect(self, effect: ChannelEffect) -> None:
        with self._effects_lock:
            try:
                self._effects.remove(effect)
            except ValueError:
                pass

    def clear_effects(self) -> None:
        with self._effects_lock:
            self._effects.clear()

    def effects(self) -> List[ChannelEffect]:
        """Return a shallow copy of the current effects chain."""
        with self._effects_lock:
            return list(self._effects)

    def write(self, data: bytes) -> None:
        """Enqueue ``data`` for the worker. Non-blocking; drops on full queue.

        Dropped writes are recorded via ``metrics.last_error`` but are NOT
        counted in ``total_lines`` — the totals always reflect bytes that
        actually made it onto the wire.
        """
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            self.metrics.record_error(RuntimeError("Output queue full; dropping line"))

    def close(self) -> None:
        """Stop the worker thread. Idempotent; safe to call after close."""
        if not self._running:
            return
        self._running = False
        # None is the wake-up sentinel; the worker exits its loop on seeing
        # it (or on the next get() timeout, whichever comes first).
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker.is_alive():
            self._worker.join(timeout=1.0)

    def _run(self) -> None:
        while self._running:
            try:
                data = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if data is None:
                # Sentinel from close(); drain-and-exit. Any items still in
                # the queue are intentionally discarded — a closed channel
                # should not keep sending.
                break

            now = time.monotonic()

            # Effects sit between the queue and the transport. A chain that
            # returns None drops the line entirely — nothing reaches the
            # wire or subscribers, which preserves the channel's byte-
            # fidelity contract (wire == subscriber view) even under fault
            # injection.
            with self._effects_lock:
                effects = list(self._effects)
            if effects:
                ctx = EmitContext(
                    emitted_ts=now,
                    dest_ip=self._dest_ip,
                    dest_port=self._dest_port,
                )
                payload: Optional[bytes] = data
                for eff in effects:
                    try:
                        payload = eff.apply(payload, ctx)
                    except Exception as exc:
                        # A broken effect must not take down the channel.
                        # Log via metrics and pass the bytes through
                        # unchanged — preferable to dropping a live stream
                        # because a fault helper raised.
                        self.metrics.record_error(exc)
                        break
                    if payload is None:
                        break
                if payload is None:
                    self.metrics.dropped_by_effect += 1
                    continue
                data = payload

            try:
                self._transport.send(self._dest_ip, self._dest_port, data)
                self.metrics.clear_error()
            except Exception as exc:  # pragma: no cover - rare socket error
                self.metrics.record_error(exc)

            with self._subs_lock:
                subs = list(self._subs)
            for sub in subs:
                try:
                    sub(data, now)
                except Exception:
                    # Subscriber errors are isolated — a broken terminal
                    # must not take down the pipeline or other subscribers.
                    pass

            self.metrics.record_event(now, len(data), 1)
