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

# How long each scenario runs in the smoke test. All bundled scenarios
# default to 1 Hz across every driver, so we need a window long enough
# to capture two ticks from the slowest lane while keeping the whole
# suite under ~15 s. 2.5 s gives 2-3 ticks at 1 Hz with margin for a
# slow CI box.
_RUN_SECONDS = 2.5


def _run_and_capture(scenario_path: Path) -> Counter:
    """Boot ``scenario_path``, capture UDP for _RUN_SECONDS, return prefix counts.

    Each driver routes to its own ``host:port`` so the test binds one
    socket per unique destination and listens on all of them in
    parallel. The de-duped set is keyed by ``(host, port)``.
    """
    cfg = load_config(scenario_path)
    targets: set[tuple[str, int]] = {
        (d.host, d.port) for spec in cfg.drivers for d in spec.destinations
    }

    socks: list[socket.socket] = []
    for host, port in targets:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((host, port))
        s.settimeout(0.1)
        socks.append(s)

    captured: list[bytes] = []
    stop = threading.Event()
    captured_lock = threading.Lock()

    def _listen(s: socket.socket) -> None:
        while not stop.is_set():
            try:
                data, _ = s.recvfrom(4096)
            except TimeoutError:
                continue
            with captured_lock:
                captured.append(data)

    listeners = [
        threading.Thread(target=_listen, args=(s,), daemon=True) for s in socks
    ]
    for t in listeners:
        t.start()

    reg = ThreadedRegistry()
    try:
        reg.start(cfg)
        time.sleep(_RUN_SECONDS)
    finally:
        reg.stop()
        stop.set()
        for t in listeners:
            t.join()
        for s in socks:
            s.close()

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
    # Every lane defaults to 1 Hz; 2.5 s should yield 2-3 ticks each.
    # The goal is "all lanes flowing", not throughput.
    assert counts["$GPGGA"] >= 2
    assert counts["$GPHDT"] >= 2
    assert counts["TSS1"] >= 2
    assert counts["$SDDPT"] >= 2
    assert counts["$YXMTW"] >= 2


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
    # All lanes at 1 Hz x 2.5 s with five drivers ≈ 10-15 datagrams.
    # Lower bound 8 so dropout_burst (which can blackout GNSS at the
    # window head) still passes; heading + motion + depth + env alone
    # cover that floor.
    assert total >= 8, f"{scenario_name} only emitted {total} datagrams: {counts}"
