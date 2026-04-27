"""Command-line entry point — boot a scenario, run the TUI, handle keypresses.

Three subcommands today:

* ``serve`` — the operator path. Loads a scenario, starts the runtime,
  shows the rich panel, and serves keypresses until ``q``.
* ``validate`` — load a YAML and print the parsed config (or the
  :class:`ConfigError` path/reason). Useful for CI and pre-flight.
* ``list`` — show bundled + local scenarios.

First run with no config copies the bundled scenarios from
``importlib.resources`` to ``./scenarios/`` next to the exe so the
operator has something to edit. The CLI never writes anything else.
"""

from __future__ import annotations

import argparse
import logging
import queue
import shutil
import signal
import sys
import time
from importlib import resources
from pathlib import Path

from .config import Config, ScenarioEntry, list_scenarios, load_config
from .runtime import ThreadedRegistry
from .status import (
    SENTENCE_CATALOGUE,
    KeyEvent,
    UIState,
    adjust_all_rates,
    adjust_rate,
    field_count_for,
    make_live,
    render,
    start_keypress_thread,
    toggle_sentence,
)

log = logging.getLogger(__name__)


# Where bundled scenarios live inside the package — `importlib.resources`
# resolves this whether qinsim is run from source or from a frozen
# PyInstaller exe with the bundled tree.
_BUNDLE_PACKAGE = "qinsim.scenarios"

# Operator-visible scenarios directory — first run copies the bundle here.
_LOCAL_SCENARIO_DIR = Path("scenarios")


def main(argv: list[str] | None = None) -> int:
    # Wrap the whole entrypoint so a startup error doesn't dump the
    # operator out of a double-clicked console window before they can
    # read it. The pause is only triggered when stdin is a real TTY —
    # piped / scripted invocations exit immediately as you'd expect.
    try:
        return _main_inner(argv)
    except SystemExit:
        # argparse exits via SystemExit on bad args; let it propagate
        # but still pause so the operator sees the error message.
        _pause_if_interactive()
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        _pause_if_interactive()
        return 1


def _main_inner(argv: list[str] | None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "list":
        return _cmd_list(args)
    raise SystemExit(f"unknown command: {args.command}")


def _pause_if_interactive() -> None:
    """Hold the console open if we're attached to a real terminal.

    A double-clicked .exe inherits a console window that closes on
    exit; without a pause the operator never sees a startup error.
    Skip the pause when stdin isn't a TTY so CI / piped invocations
    aren't blocked.
    """
    try:
        if sys.stdin.isatty():
            print("\nPress Enter to exit...", file=sys.stderr)
            sys.stdin.readline()
    except Exception:
        pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="qinsim",
        description="Minimal Qinsy NMEA simulator — single exe, UDP, fault injection.",
    )
    p.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ERROR")
    # ``required=False`` so a bare double-click with no subcommand
    # falls through to the ``serve`` default below — operators don't
    # have to know argparse to launch the TUI.
    sub = p.add_subparsers(dest="command", required=False)

    s_serve = sub.add_parser("serve", help="Run a scenario and show the live panel")
    s_serve.add_argument(
        "scenario",
        nargs="?",
        type=Path,
        help="Path to a scenario YAML. If omitted, loads the first scenario in ./scenarios/.",
    )
    s_serve.add_argument(
        "--scenarios-dir",
        type=Path,
        default=_LOCAL_SCENARIO_DIR,
        help="Directory the picker scans for scenarios. Default: ./scenarios/",
    )
    s_serve.add_argument(
        "--refresh-scenarios",
        action="store_true",
        help=(
            "Overwrite bundled scenarios on disk with the versions baked into "
            "this build. Use after upgrading the exe; hand-authored YAMLs that "
            "don't share a name with a bundled scenario are left alone."
        ),
    )

    s_val = sub.add_parser("validate", help="Validate a scenario YAML and print the parsed config")
    s_val.add_argument("scenario", type=Path)

    s_list = sub.add_parser("list", help="List scenarios in the scenarios directory")
    s_list.add_argument(
        "--scenarios-dir",
        type=Path,
        default=_LOCAL_SCENARIO_DIR,
    )
    s_list.add_argument(
        "--refresh-scenarios",
        action="store_true",
        help="Overwrite bundled scenarios on disk with this build's versions.",
    )

    args = p.parse_args(argv)
    # Default to ``serve`` so a double-clicked exe boots the TUI.
    # Filling in the scenario / scenarios-dir defaults the same way
    # the serve subparser would means the rest of the CLI doesn't
    # have to special-case this path.
    if args.command is None:
        args.command = "serve"
        if not hasattr(args, "scenario"):
            args.scenario = None
        if not hasattr(args, "scenarios_dir"):
            args.scenarios_dir = _LOCAL_SCENARIO_DIR
    return args


# ---------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------


def _cmd_serve(args: argparse.Namespace) -> int:
    _bootstrap_scenarios(
        args.scenarios_dir, refresh=getattr(args, "refresh_scenarios", False)
    )
    scenarios = list_scenarios(args.scenarios_dir)
    if not scenarios and args.scenario is None:
        print(
            f"no scenarios found in {args.scenarios_dir} and no path supplied",
            file=sys.stderr,
        )
        # Double-clicked exe with no scenarios would otherwise close
        # the console before the operator could read the message.
        _pause_if_interactive()
        return 2

    initial = args.scenario or scenarios[0].path
    try:
        config = load_config(initial)
    except Exception as exc:
        print(f"failed to load {initial}: {exc}", file=sys.stderr)
        return 1

    registry = ThreadedRegistry()
    registry.start(config)

    events: queue.Queue[KeyEvent] = queue.Queue()
    keypress_stop = start_keypress_thread(events)

    started_at = time.monotonic()
    active = initial

    # Clean Ctrl+C — set a flag the main loop polls, rather than
    # raising in the middle of a registry.swap().
    interrupted = False

    def _on_sigint(_sig: int, _frm: object) -> None:
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, _on_sigint)

    ui = UIState()

    try:
        with make_live() as live:
            while not interrupted:
                live.update(render(registry, scenarios, active, started_at, ui))
                try:
                    ev = events.get(timeout=0.2)
                except queue.Empty:
                    continue
                # ``q`` always quits regardless of mode — the operator
                # should never be trapped in the config panel.
                if ev.key == "q":
                    break
                if ui.mode == "config":
                    config = _handle_config_key(ev, registry, config, ui)
                else:
                    new_config, new_active, new_started = _handle_list_key(
                        ev, registry, scenarios, config, active, started_at, ui
                    )
                    config = new_config
                    active = new_active
                    started_at = new_started
    finally:
        keypress_stop.set()
        registry.stop()

    return 0


def _handle_list_key(
    ev: KeyEvent,
    registry: ThreadedRegistry,
    scenarios: list[ScenarioEntry],
    config: Config,
    active: Path,
    started_at: float,
    ui: UIState,
) -> tuple[Config, Path, float]:
    """Process one keypress while the TUI is in list (default) mode.

    Returns ``(config, active, started_at)`` — possibly mutated by a
    scenario load. Driver-cursor and mode transitions live on ``ui``.
    """
    if ev.key == "r":
        registry.swap(config)
        return config, active, time.monotonic()
    if ev.key == "up":
        if config.drivers:
            ui.driver_idx = (ui.driver_idx - 1) % len(config.drivers)
        return config, active, started_at
    if ev.key == "down":
        if config.drivers:
            ui.driver_idx = (ui.driver_idx + 1) % len(config.drivers)
        return config, active, started_at
    if ev.key == "enter":
        if config.drivers:
            ui.mode = "config"
            ui.field_idx = 0
        return config, active, started_at
    if ev.key in ("+", "-"):
        # Global rate nudge: bump every driver up/down by 1 Hz, clamped
        # to the same band as the per-driver adjuster. One swap rebuilds
        # all driver threads at once.
        delta = 1.0 if ev.key == "+" else -1.0
        adjust_all_rates(config, delta)
        registry.swap(config)
        return config, active, started_at
    if ev.key.isdigit():
        idx = int(ev.key) - 1
        if 0 <= idx < len(scenarios):
            target = scenarios[idx].path
            try:
                new_config = load_config(target)
            except Exception as exc:
                log.error("failed to load %s: %s", target, exc)
                return config, active, started_at
            registry.swap(new_config)
            ui.driver_idx = 0
            return new_config, target, time.monotonic()
    return config, active, started_at


def _handle_config_key(
    ev: KeyEvent,
    registry: ThreadedRegistry,
    config: Config,
    ui: UIState,
) -> Config:
    """Process one keypress while the TUI is in config mode.

    Edits mutate the in-memory ``config`` and apply via
    :meth:`ThreadedRegistry.swap` so the operator sees the new rate /
    sentence set on the next live tick. Returns the (possibly same)
    config object the main loop should keep using.
    """
    if not config.drivers:
        ui.mode = "list"
        return config
    if ui.driver_idx >= len(config.drivers):
        ui.driver_idx = 0
    spec = config.drivers[ui.driver_idx]
    n_fields = field_count_for(spec)

    if ev.key in ("esc", "enter"):
        ui.mode = "list"
        ui.field_idx = 0
        return config
    if ev.key == "up":
        ui.field_idx = (ui.field_idx - 1) % max(1, n_fields)
        return config
    if ev.key == "down":
        ui.field_idx = (ui.field_idx + 1) % max(1, n_fields)
        return config
    if ev.key in ("left", "-", "right", "+"):
        # Rate nudges only act on the rate row (field 0). On other
        # rows we silently ignore so the operator doesn't accidentally
        # rebuild the registry on every left/right.
        if ui.field_idx != 0:
            return config
        delta = 1.0 if ev.key in ("right", "+") else -1.0
        adjust_rate(spec, delta)
        registry.swap(config)
        return config
    if ev.key == "space":
        # Sentence rows are 1..N, indexing into the kind's catalogue.
        catalogue = SENTENCE_CATALOGUE.get(spec.kind, ())
        sentence_idx = ui.field_idx - 1
        if 0 <= sentence_idx < len(catalogue):
            toggle_sentence(spec, catalogue[sentence_idx])
            registry.swap(config)
        return config
    return config


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.scenario)
    except Exception as exc:
        print(f"INVALID  {args.scenario}: {exc}", file=sys.stderr)
        return 1
    print(f"OK       {args.scenario}")
    print(f"  name         {config.name}")
    if config.destinations:
        print(f"  default fallback destinations ({len(config.destinations)}):")
        for d in config.destinations:
            print(f"    - {d.host}:{d.port}")
    print(f"  drivers      {len(config.drivers)}")
    for ds in config.drivers:
        fx = ", ".join(e.kind for e in ds.effects) or "none"
        dests = ", ".join(f"{d.host}:{d.port}" for d in ds.destinations)
        print(
            f"    - {ds.name:<16} kind={ds.kind:<8} rate={ds.rate_hz:>5.1f}Hz"
            f"  dest=[{dests}]  effects=[{fx}]"
        )
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    _bootstrap_scenarios(
        args.scenarios_dir, refresh=getattr(args, "refresh_scenarios", False)
    )
    scenarios = list_scenarios(args.scenarios_dir)
    if not scenarios:
        print(f"(no scenarios in {args.scenarios_dir})")
        return 0
    for i, sc in enumerate(scenarios, start=1):
        print(f"  {i}. {sc.name:<24} {sc.path}")
    return 0


# ---------------------------------------------------------------------
# Bundled scenarios
# ---------------------------------------------------------------------


def _bootstrap_scenarios(target_dir: Path, *, refresh: bool = False) -> None:
    """Copy bundled scenarios to ``target_dir`` on first run (or refresh).

    Default behaviour copies only when the target dir is empty — that
    way an operator's hand-edited scenarios survive an upgrade. Pass
    ``refresh=True`` (the ``--refresh-scenarios`` CLI flag) to overwrite
    bundled YAMLs on disk with the versions baked into this build;
    files whose names don't match a bundled scenario are left alone.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    yamls = list(_iter_bundled_yamls())
    if not yamls:
        log.warning(
            "no bundled scenarios found — drop a YAML into %s and rerun",
            target_dir,
        )
        return

    if not refresh and any(target_dir.glob("*.yaml")):
        return

    for src in yamls:
        dest = target_dir / src.name
        if dest.exists() and not refresh:
            continue
        shutil.copyfile(src, dest)


def _iter_bundled_yamls() -> list[Path]:
    """Find every bundled scenario YAML, frozen-build-aware.

    PyInstaller's ``importlib.resources`` backend does not always
    enumerate data-file siblings of a package via ``iterdir``, so a
    naive ``resources.files(pkg).iterdir()`` walk silently misses the
    YAMLs we collected via ``collect_data_files`` in the spec. When
    running frozen we can read the same files directly out of
    ``sys._MEIPASS``, which always reflects the on-disk extraction.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "qinsim" / "scenarios"
            if bundled.is_dir():
                return sorted(bundled.glob("*.yaml"))

    # Source / dev install — importlib.resources is the right path.
    try:
        bundle = resources.files(_BUNDLE_PACKAGE)
    except (ModuleNotFoundError, FileNotFoundError):
        return []
    out: list[Path] = []
    for entry in bundle.iterdir():
        if not entry.name.endswith(".yaml"):
            continue
        with resources.as_file(entry) as src_path:
            # ``as_file`` may yield a temp path that disappears once
            # the context exits, so copy/cache the path eagerly. The
            # frozen branch above sidesteps this entirely.
            out.append(Path(src_path))
    return out
