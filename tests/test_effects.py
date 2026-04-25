"""Smoke tests for the channel effects library.

Each effect is exercised at its primary contract: dropout drops with
probability, burst_dropout drops within the window and not outside,
corrupt produces different bytes, stuck_value freezes the value.
"""

from __future__ import annotations

import random

from qinsim._core.effects import (
    BurstDropoutEffect,
    CorruptEffect,
    DropoutEffect,
    EmitContext,
    JitterEffect,
    StuckValueEffect,
    effect_from_dict,
)


def _ctx(t: float = 0.0) -> EmitContext:
    return EmitContext(emitted_ts=t, dest_ip="127.0.0.1", dest_port=13130)


def test_dropout_drops_with_probability_one() -> None:
    fx = DropoutEffect(prob=1.0)
    assert fx.apply(b"$GPGGA,*00\r\n", _ctx()) is None


def test_dropout_passes_with_probability_zero() -> None:
    fx = DropoutEffect(prob=0.0)
    assert fx.apply(b"line", _ctx()) == b"line"


def test_burst_dropout_drops_inside_window_passes_outside() -> None:
    fx = BurstDropoutEffect(duration_s=2.0, interval_s=10.0)
    # First call anchors the schedule.
    assert fx.apply(b"line", _ctx(t=100.0)) is None
    # Inside the leading window of the cycle.
    assert fx.apply(b"line", _ctx(t=101.5)) is None
    # Past the window in the same cycle.
    assert fx.apply(b"line", _ctx(t=103.0)) == b"line"
    # Next cycle, inside window again.
    assert fx.apply(b"line", _ctx(t=110.5)) is None


def test_jitter_passes_payload_through_unchanged() -> None:
    # max_delay_ms=0 short-circuits the sleep so the test is fast.
    fx = JitterEffect(max_delay_ms=0.0)
    assert fx.apply(b"hello", _ctx()) == b"hello"


def test_corrupt_checksum_changes_last_hex_digit() -> None:
    fx = CorruptEffect(prob=1.0, mode="checksum", rng=random.Random(0))
    src = b"$GPGGA,123,*5C\r\n"
    out = fx.apply(src, _ctx())
    assert out is not None
    assert out != src
    # Trailer preserved, checksum digit changed.
    assert out.endswith(b"\r\n")


def test_corrupt_bitflip_returns_same_length() -> None:
    fx = CorruptEffect(prob=1.0, mode="bitflip", rng=random.Random(0))
    src = b"some-payload"
    out = fx.apply(src, _ctx())
    assert out is not None
    assert len(out) == len(src)
    assert out != src


def test_stuck_value_freezes_within_window() -> None:
    fx = StuckValueEffect(duration_s=5.0)
    # First call seeds the frozen value and passes the line.
    assert fx.apply(b"a", _ctx(t=0.0)) == b"a"
    # Inside the window: subsequent lines come back as the frozen value.
    assert fx.apply(b"b", _ctx(t=1.0)) == b"a"
    assert fx.apply(b"c", _ctx(t=4.9)) == b"a"
    # Window expired: next line passes through and becomes the new frozen.
    assert fx.apply(b"d", _ctx(t=6.0)) == b"d"
    assert fx.apply(b"e", _ctx(t=6.5)) == b"d"


def test_effect_from_dict_round_trips_each_kind() -> None:
    cases = [
        {"type": "dropout", "prob": 0.1},
        {"type": "burst_dropout", "duration_s": 1.0, "interval_s": 5.0},
        {"type": "jitter", "max_delay_ms": 25.0},
        {"type": "corrupt", "prob": 0.05, "mode": "checksum"},
        {"type": "stuck_value", "duration_s": 3.0},
    ]
    for case in cases:
        fx = effect_from_dict(case)
        assert fx is not None, case
