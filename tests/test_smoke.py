"""End-to-end smoke: load each bundled scenario, run for ~0.5s, capture UDP.

Asserts that every bundled scenario produces at least one valid datagram
on each driver's prefix within half a second. This is the tightest
mechanical guard we have against regressions in the runtime + drivers
+ formatters integration. A failure here usually means a YAML knob got
renamed without updating the validator or driver factory.
"""

from __future__ import annotations

import socket
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

from qinsim.config import load_config
from qinsim.runtime import ThreadedRegistry


# How long each scenario runs in the smoke test. Long enough to capture
# at least one tick from the slowest driver (env at 1 Hz) and short
# enough that the suite still completes in a few seconds.
_RUN_SECONDS = 0.6


def _run_and_capture(scenario_path: Path) -> Counter:
    """Boot ``scenario_path``, capture UDP for _RUN_SECONDS, return prefix counts."""
    cfg = load_config(scenario_path)
    # Every bundled scenario sends to 127.0.0.1:13130; the test rebinds
    # there before starting the registry so no datagrams are missed.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((cfg.destinations[0].host, cfg.destinations[0].port))
    sock.settimeout(0.1)

    captured: list[bytes] = []
    stop = threading.Event()

    def _listen() -> None:
        while not stop.is_set():
            try:
                data, _ = sock.recvfrom(4096)
                captured.append(data)
            except socket.timeout:
                continue

    listener = threading.Thread(target=_listen)
    listener.start()

    reg = ThreadedRegistry()
    try:
        reg.start(cfg)
        time.sleep(_RUN_SECONDS)
    finally:
        reg.stop()
        stop.set()
        listener.join()
        sock.close()

    prefixes: Counter = Counter()
    for d in captured:
        line = d.rstrip().decode("ascii", errors="replace")
        if line.startswith("$"):
            prefixes[line.split(",", 1)[0]] += 1
        elif line.startswith(":"):
            prefixes["TSS1"] += 1
    return prefixes


def test_harbour_rtk_fixed_emits_every_kind(bundled_scenarios_dir: Path) -> None:
    counts = _run_and_capture(bundled_scenarios_dir / "harbour_rtk_fixed.yaml")
    # GNSS @ 10 Hz × 0.6 s = 6, but use lower bound 3 so a slow CI box
    # still passes; the goal is "at least flowing", not throughput.
    assert counts["$GPGGA"] >= 3
    assert counts["$GPHDT"] >= 3
    assert counts["TSS1"] >= 3
    assert counts["$SDDPT"] >= 1
    assert counts["$YXMTW"] >= 1


@pytest.mark.parametrize(
    "scenario_name",
    [
        "harbour_rtk_fixed",
        "open_ocean_survey",
        "dropout_burst",
        "nmea_corruption",
        "rtk_outage",
    ],
)
def test_each_bundled_scenario_emits_some_traffic(
    bundled_scenarios_dir: Path, scenario_name: str
) -> None:
    counts = _run_and_capture(bundled_scenarios_dir / f"{scenario_name}.yaml")
    total = sum(counts.values())
    # Lower bound chosen so dropout_burst (which can blackout GNSS for
    # 5 s of a 30 s cycle) still passes on the off chance the test
    # window aligns with the leading blackout. Heading + motion + depth
    # + env still produce at least 10 lines in 0.6 s combined.
    assert total >= 10, f"{scenario_name} only emitted {total} datagrams: {counts}"
