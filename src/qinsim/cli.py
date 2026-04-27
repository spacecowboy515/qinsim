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
from typing import List, Optional

from .config import Config, ScenarioEntry, load_config, list_scenarios
from .runtime import ThreadedRegistry
from .status import KeyEvent, make_live, render, start_keypress_thread


log = logging.getLogger(__name__)


# Where bundled scenarios live inside the package — `importlib.resources`
# resolves this whether qinsim is run from source or from a frozen
# PyInstaller exe with the bundled tree.
_BUNDLE_PACKAGE = "qinsim.scenarios"

# Operator-visible scenarios directory — first run copies the bundle here.
_LOCAL_SCENARIO_DIR = Path("scenarios")


def main(argv: Optional[List[str]] = None) -> int:
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


def _main_inner(argv: Optional[List[str]]) -> int:
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


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
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

    s_val = sub.add_parser("validate", help="Validate a scenario YAML and print the parsed config")
    s_val.add_argument("scenario", type=Path)

    s_list = sub.add_parser("list", help="List scenarios in the scenarios directory")
    s_list.add_argument(
        "--scenarios-dir",
        type=Path,
        default=_LOCAL_SCENARIO_DIR,
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
    _bootstrap_scenarios(args.scenarios_dir)
    scenarios = list_scenarios(args.scenarios_dir)
    if not scenarios and args.scenario is None:
        print(
            f"no scenarios found in {args.scenarios_dir} and no path supplied",
            file=sys.stderr,
        )
        return 2

    initial = args.scenario or scenarios[0].path
    try:
        config = load_config(initial)
    except Exception as exc:
        print(f"failed to load {initial}: {exc}", file=sys.stderr)
        return 1

    registry = ThreadedRegistry()
    registry.start(config)

    events: "queue.Queue[KeyEvent]" = queue.Queue()
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

    try:
        with make_live() as live:
            while not interrupted:
                live.update(render(registry, scenarios, active, started_at))
                try:
                    ev = events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if ev.key == "q":
                    break
                if ev.key == "r":
                    registry.swap(config)
                    started_at = time.monotonic()
                    continue
                if ev.key.isdigit():
                    idx = int(ev.key) - 1
                    if 0 <= idx < len(scenarios):
                        target = scenarios[idx].path
                        try:
                            config = load_config(target)
                        except Exception as exc:
                            log.error("failed to load %s: %s", target, exc)
                            continue
                        registry.swap(config)
                        active = target
                        started_at = time.monotonic()
    finally:
        keypress_stop.set()
        registry.stop()

    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.scenario)
    except Exception as exc:
        print(f"INVALID  {args.scenario}: {exc}", file=sys.stderr)
        return 1
    print(f"OK       {args.scenario}")
    print(f"  name         {config.name}")
    print(f"  destinations {len(config.destinations)}")
    for d in config.destinations:
        print(f"    - {d.host}:{d.port}")
    print(f"  drivers      {len(config.drivers)}")
    for ds in config.drivers:
        fx = ", ".join(e.kind for e in ds.effects) or "none"
        print(f"    - {ds.name:<16} kind={ds.kind:<8} rate={ds.rate_hz:>5.1f}Hz  effects=[{fx}]")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    _bootstrap_scenarios(args.scenarios_dir)
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


def _bootstrap_scenarios(target_dir: Path) -> None:
    """Copy bundled scenarios to ``target_dir`` if it doesn't exist yet.

    No-op once the operator has anything in place — we never overwrite,
    so hand-edited scenarios survive across upgrades.
    """
    if target_dir.exists() and any(target_dir.glob("*.yaml")):
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        bundle = resources.files(_BUNDLE_PACKAGE)
    except (ModuleNotFoundError, FileNotFoundError):
        # Pre-bundling dev environments: the package may not have any
        # bundled scenarios yet. That's fine — operator has to supply
        # their own YAML for the first run.
        return
    for entry in bundle.iterdir():
        name = entry.name
        if not name.endswith(".yaml"):
            continue
        dest = target_dir / name
        if dest.exists():
            continue
        with resources.as_file(entry) as src_path:
            shutil.copyfile(src_path, dest)
