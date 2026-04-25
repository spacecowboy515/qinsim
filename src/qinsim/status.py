"""Rich Live panel + Windows keypress reader for the operator TUI.

Three regions:

1. **Header** — scenario name, uptime, total emit rate.
2. **Drivers table** — name, kind, rate, last sentence (truncated),
   slip ms, dropped-by-effect counter.
3. **Scenario picker** — numbered list of YAMLs in ``./scenarios/``,
   active row highlighted, footer cheat sheet.

Keypresses are read on a daemon thread via :mod:`msvcrt` (Windows-only,
matching the deployment target). Events go onto a :class:`queue.Queue`
that the CLI's main loop drains.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import ScenarioEntry, list_scenarios
from .runtime import ThreadedRegistry


# Update cadence — fast enough for "live" feel, slow enough that
# rendering doesn't fight the driver threads for the GIL.
_REFRESH_HZ = 5.0


@dataclass(frozen=True)
class KeyEvent:
    """One keypress from the TUI."""

    key: str  # 'q', 'r', or '1'..'9'


def start_keypress_thread(events: "queue.Queue[KeyEvent]") -> threading.Event:
    """Spawn a daemon thread that pushes keypress events.

    Returns the stop event — set it to drain and exit. The thread
    itself is daemonised so the process can exit even if the operator
    has the terminal in a state msvcrt can't poll out of.
    """
    stop = threading.Event()

    def _run() -> None:
        # msvcrt is only importable on Windows — qinsim is Windows-only
        # by design (matches aqps and kmall-replay). Importing inside
        # the function rather than at module top so the rest of the
        # module can be imported on non-Windows hosts during dev.
        import msvcrt  # noqa: PLC0415

        while not stop.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("q", "Q", "r", "R") or (ch.isdigit() and ch != "0"):
                    events.put(KeyEvent(key=ch.lower()))
            else:
                time.sleep(0.02)

    threading.Thread(target=_run, name="qinsim-keypress", daemon=True).start()
    return stop


def render(
    registry: ThreadedRegistry,
    scenarios: List[ScenarioEntry],
    active_scenario: Optional[Path],
    started_at: float,
) -> Group:
    """Build the full rich renderable for one frame."""
    config = registry.config
    name = config.name if config else "<no scenario>"
    uptime = time.monotonic() - started_at

    handles = registry.handles()
    total_lines = sum(int(h.channel.metrics.snapshot()["total_lines"]) for h in handles)
    total_lps = sum(float(h.channel.metrics.snapshot()["lines_per_sec"]) for h in handles)

    header = Panel(
        Text.assemble(
            ("scenario  ", "dim"),
            (name, "bold cyan"),
            ("    uptime  ", "dim"),
            (f"{uptime:7.1f}s", "bold"),
            ("    lines  ", "dim"),
            (f"{total_lines:>8d}", "bold"),
            ("    rate  ", "dim"),
            (f"{total_lps:5.1f}/s", "bold green"),
        ),
        title="qinsim",
        border_style="cyan",
    )

    drivers = Table(title="drivers", expand=True, header_style="bold magenta")
    drivers.add_column("name")
    drivers.add_column("kind")
    drivers.add_column("rate", justify="right")
    drivers.add_column("lines/s", justify="right")
    drivers.add_column("slip ms", justify="right")
    drivers.add_column("dropped", justify="right")
    drivers.add_column("last sentence", overflow="ellipsis", no_wrap=True, max_width=64)
    for h in handles:
        snap = h.channel.metrics.snapshot()
        last = h.last_emit[-1].rstrip().decode("ascii", errors="replace") if h.last_emit else ""
        slip_style = "red" if h.slip_ms > 50.0 else "dim"
        drivers.add_row(
            h.name,
            h.kind,
            f"{h.rate_hz:.1f}",
            f"{float(snap['lines_per_sec']):.1f}",
            Text(f"{h.slip_ms:.1f}", style=slip_style),
            f"{int(snap['dropped_by_effect']):d}",
            last,
        )

    picker = Table(title="scenarios", expand=True, header_style="bold yellow")
    picker.add_column("#", justify="right")
    picker.add_column("name")
    picker.add_column("path")
    for i, sc in enumerate(scenarios, start=1):
        if i > 9:
            break
        marker = "▶" if active_scenario == sc.path else " "
        style = "bold green" if active_scenario == sc.path else ""
        picker.add_row(
            Text(f"{marker} {i}", style=style),
            Text(sc.name, style=style),
            Text(str(sc.path), style="dim"),
        )

    footer = Text.assemble(
        ("press ", "dim"),
        ("1", "bold"),
        ("-", "dim"),
        ("9", "bold"),
        (" load · ", "dim"),
        ("r", "bold"),
        (" restart current · ", "dim"),
        ("q", "bold"),
        (" quit", "dim"),
    )

    return Group(header, drivers, picker, footer)


def make_live() -> Live:
    """Construct a :class:`rich.live.Live` with our refresh rate."""
    return Live(refresh_per_second=_REFRESH_HZ, screen=False)
