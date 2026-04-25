"""Channel effects — fault-injection seam on ``OutputChannel``.

Every line a driver writes passes through the channel worker before it
reaches the wire. An :class:`OutputChannel` keeps an ordered list of
effects; each one is a :class:`ChannelEffect` that can pass the line
through, replace its bytes, or drop it entirely. Effects live on the
channel rather than inside drivers so the same fault library applies
uniformly to every sensor without per-driver glue.

The ``None``-means-drop convention keeps the protocol minimal: a
dropout is a one-line lambda, a bit-flipper is a couple more. Effects
run on the channel's worker thread, so they must be fast and must not
hold the GIL — any effect that wants to *delay* (e.g. jitter) should
``time.sleep`` inside ``apply``; the tick scheduler already copes with
that by advancing the clock at read-time.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


@dataclass(frozen=True)
class EmitContext:
    """Context handed to every effect at apply-time.

    Monotonic only — effects that schedule fault windows anchor against
    ``emitted_ts`` so wall-clock drift can't shift the window. ``dest_ip``
    and ``dest_port`` let a single shared effect decide per destination
    (e.g. only fault the starboard GNSS without fault-ing the crane).
    """

    emitted_ts: float
    dest_ip: str
    dest_port: int


class ChannelEffect(Protocol):
    """Transform or filter applied between the channel queue and the wire.

    Returning ``None`` drops the line — nothing reaches the transport or
    any subscriber, and the channel increments its dropped-by-effect
    counter. Returning bytes replaces the emitted payload (identity is
    the no-op case). Effects run in insertion order; later effects see
    whatever the earlier effects emitted.
    """

    def apply(self, data: bytes, ctx: EmitContext) -> Optional[bytes]:
        ...


@dataclass
class DropoutEffect:
    """Drop each line with independent probability ``prob``.

    Models a flaky cable or a lossy UDP link at the simplest level.
    Correlated burst outages need :class:`BurstDropoutEffect`.
    The RNG is a field so tests can pin the seed.
    """

    prob: float
    rng: random.Random = field(default_factory=random.Random)

    TYPE_NAME = "dropout"

    def apply(self, data: bytes, ctx: EmitContext) -> Optional[bytes]:
        return None if self.rng.random() < self.prob else data

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.TYPE_NAME, "prob": self.prob}


@dataclass
class BurstDropoutEffect:
    """Alternate open windows with ``duration_s`` blackout windows.

    The cycle length is ``interval_s``; the blackout is the first
    ``duration_s`` of every cycle. Anchored at ``None`` until the first
    call — that way a restart doesn't phase-shift the pattern against
    wall clock but against the stream itself, which matches operator
    intuition (`"blackouts every 30 s"` means from now).
    """

    duration_s: float
    interval_s: float
    _anchor: Optional[float] = None

    TYPE_NAME = "burst_dropout"

    def apply(self, data: bytes, ctx: EmitContext) -> Optional[bytes]:
        if self._anchor is None:
            self._anchor = ctx.emitted_ts
        # Guard against a zero-interval misconfig — treat as no-op rather
        # than divide-by-zero.
        if self.interval_s <= 0:
            return data
        phase = (ctx.emitted_ts - self._anchor) % self.interval_s
        return None if phase < self.duration_s else data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.TYPE_NAME,
            "duration_s": self.duration_s,
            "interval_s": self.interval_s,
        }


@dataclass
class JitterEffect:
    """Sleep a random slice of ``[0, max_delay_ms]`` before releasing the line.

    Runs on the channel's worker thread, so the delay shifts the wire
    arrival time without affecting driver tick scheduling. Models a
    serial buffer that drains unevenly or a congested UDP path.
    """

    max_delay_ms: float
    rng: random.Random = field(default_factory=random.Random)

    TYPE_NAME = "jitter"

    def apply(self, data: bytes, ctx: EmitContext) -> Optional[bytes]:
        if self.max_delay_ms > 0:
            time.sleep(self.rng.uniform(0, self.max_delay_ms / 1000.0))
        return data

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.TYPE_NAME, "max_delay_ms": self.max_delay_ms}


@dataclass
class CorruptEffect:
    """Corrupt each line with probability ``prob``.

    Modes:
      * ``bitflip`` — flip one random bit in the payload.
      * ``truncate`` — drop the tail, keeping between 1 byte and ``len-1`` bytes.
      * ``checksum`` — for NMEA-style ``$...*XX\\r\\n`` lines, flip the
        low nibble of the final checksum hex digit; for payloads that
        don't end in a checksum this mode passes through unchanged.

    Useful for exercising Qinsy's NMEA-parser error paths: the
    ``checksum`` mode is what you want for "sensor says NO_CHECKSUM_MATCH"
    alarms; ``truncate`` for "sensor says short frame"; ``bitflip`` for a
    noisier failure surface.
    """

    prob: float
    mode: str = "bitflip"
    rng: random.Random = field(default_factory=random.Random)

    TYPE_NAME = "corrupt"
    _MODES = ("bitflip", "truncate", "checksum")

    def __post_init__(self) -> None:
        if self.mode not in self._MODES:
            raise ValueError(
                f"unknown corruption mode {self.mode!r}; "
                f"supported: {self._MODES}"
            )

    def apply(self, data: bytes, ctx: EmitContext) -> Optional[bytes]:
        if not data or self.rng.random() >= self.prob:
            return data
        if self.mode == "bitflip":
            idx = self.rng.randrange(len(data))
            bit = 1 << self.rng.randrange(8)
            return data[:idx] + bytes([data[idx] ^ bit]) + data[idx + 1:]
        if self.mode == "truncate":
            keep = self.rng.randrange(1, len(data)) if len(data) > 1 else 1
            return data[:keep]
        if self.mode == "checksum":
            return self._corrupt_nmea_checksum(data)
        return data  # pragma: no cover — guarded by __post_init__

    def _corrupt_nmea_checksum(self, data: bytes) -> bytes:
        # Strip an optional CRLF/LF trailer so we can find the '*XX' checksum.
        trailer_len = 0
        if data.endswith(b"\r\n"):
            trailer_len = 2
        elif data.endswith(b"\n") or data.endswith(b"\r"):
            trailer_len = 1
        body = data[: len(data) - trailer_len]
        if len(body) < 3 or body[-3:-2] != b"*":
            return data  # not NMEA-shaped; pass through
        # Flip the low nibble of the last hex digit — guaranteed to change
        # the checksum and stay inside valid hex, so Qinsy's parser sees
        # "wrong checksum" not "malformed sentence".
        last = body[-1]
        if 0x30 <= last <= 0x39:  # '0'..'9'
            new = 0x30 + ((last - 0x30) ^ 0x1)
        elif 0x41 <= last <= 0x46:  # 'A'..'F'
            new = 0x41 + ((last - 0x41) ^ 0x1) if last != 0x46 else 0x45
        elif 0x61 <= last <= 0x66:  # 'a'..'f'
            new = 0x61 + ((last - 0x61) ^ 0x1) if last != 0x66 else 0x65
        else:
            return data  # not hex; pass through
        return body[:-1] + bytes([new]) + data[len(data) - trailer_len:]

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.TYPE_NAME, "prob": self.prob, "mode": self.mode}


@dataclass
class StuckValueEffect:
    """Freeze the stream at the first line for ``duration_s`` seconds.

    Captures the first line seen and returns it verbatim for every
    subsequent line in the window, then releases and passes the next
    line through (and re-arms). Models a sensor whose output is frozen
    at its last-reported value — useful because Qinsy's alarms for
    "stuck sensor" are distinct from "no data".
    """

    duration_s: float
    _frozen: Optional[bytes] = None
    _frozen_until: Optional[float] = None

    TYPE_NAME = "stuck_value"

    def apply(self, data: bytes, ctx: EmitContext) -> Optional[bytes]:
        if self._frozen is None or self._frozen_until is None or ctx.emitted_ts >= self._frozen_until:
            # Fresh arm: this line becomes the frozen value and starts a
            # new window. Lines that arrive during the window get the
            # frozen value; the line that ends a window passes through
            # AND becomes the new frozen value — models a sensor whose
            # value refreshes periodically and then sticks again.
            self._frozen = data
            self._frozen_until = ctx.emitted_ts + self.duration_s
            return data
        return self._frozen

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.TYPE_NAME, "duration_s": self.duration_s}


# ---------------------------------------------------------------------------
# Serialization factory
# ---------------------------------------------------------------------------


def effect_from_dict(data: Dict[str, Any]) -> ChannelEffect:
    """Build an effect from its JSON-shaped dict.

    Raises ``ValueError`` for an unknown ``type`` or a missing required
    field; the control-server layer turns that into a 400. A ``seed``
    key, when present, seeds the effect's RNG — useful for reproducible
    fault scenarios.
    """
    if not isinstance(data, dict):
        raise ValueError(f"effect spec must be a dict, got {type(data).__name__}")
    t = data.get("type")
    seed = data.get("seed")

    def _rng() -> random.Random:
        return random.Random(seed) if seed is not None else random.Random()

    if t == DropoutEffect.TYPE_NAME:
        return DropoutEffect(prob=float(data["prob"]), rng=_rng())
    if t == BurstDropoutEffect.TYPE_NAME:
        return BurstDropoutEffect(
            duration_s=float(data["duration_s"]),
            interval_s=float(data["interval_s"]),
        )
    if t == JitterEffect.TYPE_NAME:
        return JitterEffect(max_delay_ms=float(data["max_delay_ms"]), rng=_rng())
    if t == CorruptEffect.TYPE_NAME:
        return CorruptEffect(
            prob=float(data["prob"]),
            mode=str(data.get("mode", "bitflip")),
            rng=_rng(),
        )
    if t == StuckValueEffect.TYPE_NAME:
        return StuckValueEffect(duration_s=float(data["duration_s"]))
    raise ValueError(f"unknown effect type {t!r}")


def effect_to_dict(effect: ChannelEffect) -> Dict[str, Any]:
    """Inverse of :func:`effect_from_dict` for any built-in effect."""
    hook = getattr(effect, "to_dict", None)
    if callable(hook):
        return hook()
    # Fallback for third-party effects that don't implement to_dict — we
    # surface at least the class name so the manifest isn't opaque.
    return {"type": effect.__class__.__name__.lower()}
