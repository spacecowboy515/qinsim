"""Rich Live panel + Windows keypress reader for the operator TUI.

Two modes:

* **list** — drivers table + scenario picker, the default. Keys: number
  to load a scenario, ``r`` restart, ``q`` quit, ``↑/↓`` move the driver
  cursor, ``enter`` open config for the highlighted driver.
* **config** — drilled into one driver. Keys: ``↑/↓`` move field cursor,
  ``←/→`` (or ``+/-``) adjust rate, ``space`` toggle the highlighted
  sentence, ``esc`` / ``enter`` return to list mode. Edits apply live
  via :meth:`ThreadedRegistry.swap`.

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

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import Config, Destination, DriverSpec, ScenarioEntry
from .runtime import ThreadedRegistry

# Update cadence — fast enough for "live" feel, slow enough that
# rendering doesn't fight the driver threads for the GIL.
_REFRESH_HZ = 5.0


# Per-driver-kind list of every NMEA sentence the driver supports. The
# config panel uses this to render the toggle list. Motion is TSS1-only
# at the formatter layer — there is no toggle for it, so it's omitted
# here and the config panel shows "(fixed: TSS1)" instead.
SENTENCE_CATALOGUE: dict[str, tuple[str, ...]] = {
    "gnss": ("GGA", "RMC", "VTG", "GLL", "GSA", "GSV", "GST", "ZDA", "HDT"),
    "heading": ("HDT", "HDM", "HDG"),
    "depth": ("DPT", "DBT"),
    "env": ("MTW", "XDR"),
}

# Default sentence sets used when a driver spec doesn't list any —
# matches the per-driver factory defaults in :mod:`qinsim.drivers`.
_DEFAULT_SENTENCES: dict[str, tuple[str, ...]] = {
    "gnss": ("GGA", "RMC", "VTG"),
    "heading": ("HDT",),
    "depth": ("DPT", "DBT"),
    "env": ("MTW", "XDR"),
}


@dataclass
class UIState:
    """In-memory state for the TUI's two modes."""

    mode: str = "list"  # 'list' or 'config'
    driver_idx: int = 0
    # In config mode: 0 = rate, 1..N = sentence toggles. Constrained
    # by the active driver's catalogue at navigation time.
    field_idx: int = 0


@dataclass(frozen=True)
class KeyEvent:
    """One keypress from the TUI.

    ``key`` is one of:

    * ``q``, ``r`` — original quit/restart commands
    * ``1``..``9`` — scenario picker
    * ``up``, ``down``, ``left``, ``right`` — arrow navigation
    * ``enter``, ``esc``, ``space`` — mode and toggle controls
    * ``+``, ``-`` — rate nudges (alternative to left/right)
    """

    key: str


def start_keypress_thread(events: queue.Queue[KeyEvent]) -> threading.Event:
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
        import msvcrt

        # Special keys (arrows etc.) arrive as a two-char sequence: a
        # \x00 or \xe0 lead byte, then a discriminator. Reading them
        # in one place keeps the dispatch table tight.
        arrow_keys = {"H": "up", "P": "down", "K": "left", "M": "right"}

        while not stop.is_set():
            if not msvcrt.kbhit():
                time.sleep(0.02)
                continue
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                # Two-char extended sequence — the lead byte guarantees
                # a discriminator follows in the same input event, so
                # read it unconditionally. (An earlier kbhit() guard
                # raced with the console buffer and silently dropped
                # arrow presses.) Unknown extended keys are dropped.
                disc = msvcrt.getwch()
                mapped = arrow_keys.get(disc)
                if mapped is not None:
                    events.put(KeyEvent(key=mapped))
                continue
            if ch == "\r":
                events.put(KeyEvent(key="enter"))
                continue
            if ch == "\x1b":
                events.put(KeyEvent(key="esc"))
                continue
            if ch == " ":
                events.put(KeyEvent(key="space"))
                continue
            if ch in ("+", "="):
                # ``=`` is the unshifted ``+`` on US keyboards; treat
                # both as "nudge up" so the operator doesn't need to
                # hold shift.
                events.put(KeyEvent(key="+"))
                continue
            if ch in ("-", "_"):
                events.put(KeyEvent(key="-"))
                continue
            if ch in ("q", "Q", "r", "R") or (ch.isdigit() and ch != "0"):
                events.put(KeyEvent(key=ch.lower()))

    threading.Thread(target=_run, name="qinsim-keypress", daemon=True).start()
    return stop


# ---------------------------------------------------------------------
# Sentence helpers
# ---------------------------------------------------------------------


def sentences_for(spec: DriverSpec) -> list[str]:
    """Return the active sentence list for a driver spec, with defaults."""
    raw = spec.state.get("sentences")
    if isinstance(raw, list) and raw:
        return [str(s) for s in raw]
    return list(_DEFAULT_SENTENCES.get(spec.kind, ()))


def toggle_sentence(spec: DriverSpec, sentence: str) -> None:
    """Add/remove ``sentence`` on ``spec.state['sentences']`` in place.

    Refuses to remove the last sentence — the driver factories raise
    if the list is empty. The operator can still drop the rate to 0
    via the rate nudges if they want the lane silent.
    """
    current = sentences_for(spec)
    if sentence in current:
        if len(current) <= 1:
            return
        current.remove(sentence)
    else:
        current.append(sentence)
    spec.state["sentences"] = current


RATE_MIN_HZ = 1.0
RATE_MAX_HZ = 25.0


def adjust_rate(spec: DriverSpec, delta_hz: float) -> None:
    """Bump ``spec.rate_hz`` by ``delta_hz``, clamped to 1-25 Hz.

    Lower bound keeps the period calculation safe and matches Qinsy's
    typical 1 Hz minimum input expectation; upper bound caps at 25 Hz
    because no NMEA driver in this sim has a sensible reason to go
    faster and one fat-thumb keystroke shouldn't hammer the consumer.
    """
    new_rate = max(RATE_MIN_HZ, min(RATE_MAX_HZ, spec.rate_hz + delta_hz))
    # ``DriverSpec`` is a regular dataclass (mutable) so direct
    # assignment is fine — and far simpler than constructing a fresh
    # one. The handle gets rebuilt via swap() right after.
    spec.rate_hz = round(new_rate)


def adjust_all_rates(config: Config, delta_hz: float) -> None:
    """Bump every driver's rate by ``delta_hz`` (same clamp as ``adjust_rate``)."""
    for spec in config.drivers:
        adjust_rate(spec, delta_hz)


# ---------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------


def _format_handle_destinations(destinations: list[Destination]) -> str:
    """Return ``"host:port (UDP)"`` (with ``+N`` if more than one)."""
    if not destinations:
        return "—"
    primary = destinations[0]
    base = f"{primary.host}:{primary.port} (UDP)"
    if len(destinations) > 1:
        base += f" +{len(destinations) - 1}"
    return base


def render(
    registry: ThreadedRegistry,
    scenarios: list[ScenarioEntry],
    active_scenario: Path | None,
    started_at: float,
    ui: UIState,
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
    drivers.add_column(" ", width=2)
    drivers.add_column("name")
    drivers.add_column("kind")
    drivers.add_column("destination", overflow="fold")
    drivers.add_column("rate", justify="right")
    drivers.add_column("lines/s", justify="right")
    drivers.add_column("slip ms", justify="right")
    drivers.add_column("dropped", justify="right")
    drivers.add_column("last sentence", overflow="ellipsis", no_wrap=True, max_width=48)

    for idx, h in enumerate(handles):
        snap = h.channel.metrics.snapshot()
        last = h.last_emit[-1].rstrip().decode("ascii", errors="replace") if h.last_emit else ""
        slip_style = "red" if h.slip_ms > 50.0 else "dim"
        is_cursor = idx == ui.driver_idx
        marker = "▶" if is_cursor else " "
        row_style = "bold cyan" if is_cursor else ""
        drivers.add_row(
            Text(marker, style=row_style),
            Text(h.name, style=row_style),
            h.kind,
            _format_handle_destinations(h.destinations),
            f"{h.rate_hz:.1f}",
            f"{float(snap['lines_per_sec']):.1f}",
            Text(f"{h.slip_ms:.1f}", style=slip_style),
            f"{int(snap['dropped_by_effect']):d}",
            last,
        )

    if ui.mode == "config":
        body = Group(header, drivers, _render_config_panel(config, handles, ui), _config_footer())
    else:
        body = Group(header, drivers, _render_picker(scenarios, active_scenario), _list_footer())
    return body


def _render_picker(scenarios: list[ScenarioEntry], active: Path | None) -> Table:
    picker = Table(title="scenarios", expand=True, header_style="bold yellow")
    picker.add_column("#", justify="right")
    picker.add_column("name")
    picker.add_column("path")
    for i, sc in enumerate(scenarios, start=1):
        if i > 9:
            break
        marker = "▶" if active == sc.path else " "
        style = "bold green" if active == sc.path else ""
        picker.add_row(
            Text(f"{marker} {i}", style=style),
            Text(sc.name, style=style),
            Text(str(sc.path), style="dim"),
        )
    return picker


def _render_config_panel(
    config: Config | None,
    handles: list,  # type: ignore[type-arg]
    ui: UIState,
) -> Panel:
    """Per-driver edit panel: rate field + sentence toggles."""
    if config is None or not config.drivers or ui.driver_idx >= len(config.drivers):
        return Panel(Text("no driver selected", style="dim"), title="configure", border_style="yellow")

    spec = config.drivers[ui.driver_idx]
    table = Table.grid(padding=(0, 2))
    table.add_column(" ", width=2)
    table.add_column("field", style="bold")
    table.add_column("value")

    cursor_style = "bold cyan"

    # Field 0: rate
    is_rate = ui.field_idx == 0
    table.add_row(
        Text("▶" if is_rate else " ", style=cursor_style if is_rate else ""),
        Text("rate (Hz)", style=cursor_style if is_rate else ""),
        Text(f"{spec.rate_hz:.1f}", style=cursor_style if is_rate else ""),
    )

    # Fields 1..N: sentence toggles
    catalogue = SENTENCE_CATALOGUE.get(spec.kind, ())
    if not catalogue:
        # Motion driver — TSS1 is hardcoded in the formatter, so there
        # are no toggles to offer. Show the locked sentence so the
        # operator knows what's on the wire.
        table.add_row("", Text("sentences", style="dim"), Text("TSS1 (fixed)", style="dim"))
    else:
        active_set = set(sentences_for(spec))
        for i, sentence in enumerate(catalogue, start=1):
            is_cursor = ui.field_idx == i
            on = sentence in active_set
            checkbox = "[x]" if on else "[ ]"
            value_style = "green" if on else "dim"
            table.add_row(
                Text("▶" if is_cursor else " ", style=cursor_style if is_cursor else ""),
                Text(checkbox, style=cursor_style if is_cursor else value_style),
                Text(sentence, style=cursor_style if is_cursor else value_style),
            )

    title = f"configure · {spec.name} ({spec.kind})"
    return Panel(table, title=title, border_style="yellow")


def _list_footer() -> Text:
    return Text.assemble(
        ("press ", "dim"),
        ("1", "bold"),
        ("-", "dim"),
        ("9", "bold"),
        (" load · ", "dim"),
        ("↑↓", "bold"),
        (" select driver · ", "dim"),
        ("enter", "bold"),
        (" configure · ", "dim"),
        ("+/-", "bold"),
        (" all rates · ", "dim"),
        ("r", "bold"),
        (" restart · ", "dim"),
        ("q", "bold"),
        (" quit", "dim"),
    )


def _config_footer() -> Text:
    return Text.assemble(
        ("press ", "dim"),
        ("↑↓", "bold"),
        (" field · ", "dim"),
        ("←→", "bold"),
        (" / ", "dim"),
        ("+/-", "bold"),
        (" rate · ", "dim"),
        ("space", "bold"),
        (" toggle sentence · ", "dim"),
        ("esc", "bold"),
        (" / ", "dim"),
        ("enter", "bold"),
        (" back", "dim"),
    )


def field_count_for(spec: DriverSpec) -> int:
    """Number of navigable fields in config mode for this driver.

    Always at least 1 (the rate row). Drivers without a sentence
    catalogue (motion) stop there; the rest add one row per sentence.
    """
    return 1 + len(SENTENCE_CATALOGUE.get(spec.kind, ()))


def make_live() -> Live:
    """Construct a :class:`rich.live.Live` with our refresh rate."""
    return Live(refresh_per_second=_REFRESH_HZ, screen=False)
