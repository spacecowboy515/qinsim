# CLAUDE.md — qinsim project brief

You are working on **qinsim**, a deliberately tiny Qinsy NMEA simulator. Read
this in full before writing code.

## Mission

Build a Windows-only Python tool that drives Qinsy 9.x with believable NMEA
data over UDP, with a fault-injection chain, deployable as a **single .exe**
on a fresh BNOB workstation. The whole point of this tool is "drop the exe
and run." If a feature would require Docker, a system service, or a Python
install on the operator box — it's out of scope.

## Sibling projects

- **`C:\Dev\aqps\`** — kitchen-sink rig with MQTT, dashboard, supervisor.
  qinsim's `_core/` is **vendored from aqps's `simulators/native/_core/`**.
  Two trees, no shared package. Manual port if either side improves.
- **`C:\Dev\kmall-replay\`** — TCP + KMALL replayer. Different protocol,
  different consumer plug in Qinsy. Stays separate.

## Hard rules

1. **No Pydantic.** Hand-rolled dict validation in `config.py`.
2. **No FastAPI / uvicorn / REST / WebSocket.** The TUI is the only UI.
3. **No asyncio.** Threads, one per driver. `threading.Event` for shutdown.
4. **Python 3.11+, stdlib-first.** Only deps: `pyyaml`, `rich`. Dev: `pytest`,
   `pyinstaller`, `pynmea2` (test-only), `ruff`, `mypy`.
5. **Windows-only.** `msvcrt.getch()` for keypresses. No POSIX socket options.
6. **Single executable target.** Every architectural choice asks "does this
   PyInstaller cleanly?" If unsure, simpler.
7. **Type-annotate everything public.** `mypy --strict src/` must be clean.
8. **Comments explain WHY, not WHAT.** Default to none.

## Architecture

```
qinsim.exe
    │
    ▼
cli.main() → reads qinsim.yaml → ThreadedRegistry.start(config)
    │                                 │
    │                                 ├─ Driver thread × N
    │                                 │   each: tick(dt) → channel.send(buf)
    │                                 │   channel applies effect chain → UDP
    │                                 │
    │                                 └─ TUI thread (rich Live)
    │                                     reads driver state for table
    │
    ▼
keypress thread (msvcrt)
    1–9 → registry.swap(load_scenario(N))
    q   → registry.stop() → exit
    r   → registry.swap(current)
```

## Module map (verbatim from DESIGN.md / plan)

```
src/qinsim/
├── __main__.py           → python -m qinsim
├── cli.py                → argparse + main()
├── config.py             → YAML load, ConfigError, dataclass schema
├── runtime.py            → ThreadedRegistry
├── status.py             → rich Live + msvcrt scenario picker
├── _core/                ← VENDORED FROM aqps (don't reinvent)
│   ├── motion_model.py
│   ├── effects.py
│   ├── channel.py
│   ├── transports/udp.py
│   ├── formatters/{nmea_gnss,nmea_heading,nmea_motion,nmea_depth,nmea_xdr}.py
│   └── state/{gnss,heading,motion,depth,env}_state.py
├── drivers/{gnss,heading,motion,depth,env}.py
└── scenarios/*.yaml      → 5 bundled presets
```

## Build order

Follow the plan exactly. Don't skip ahead, don't pre-optimise.

1. Scaffold (this commit).
2. `_core/` ports — formatters first, motion_model next, then state, then
   effects, then channel + transports. Each gets its own commit + tests.
3. Drivers — one per file, one commit each, each with a unit test.
4. Runtime — ThreadedRegistry, then config, then CLI, then status TUI.
5. Bundled scenarios + smoke test.
6. PyInstaller spec + CI.

## Definition of done (v1.0)

- All 5 drivers emit at configured rate via UDP.
- Effect chain tested against every effect kind.
- TUI scenario-switch works with no UDP socket churn.
- `pytest` green on `windows-latest`.
- `mypy --strict src/` clean.
- `ruff check` clean.
- `pyinstaller pyinstaller.spec` produces `dist/qinsim.exe` ≤ 50 MB.
- Cold-start on a clean Windows VM: double-click, TUI opens within 2 s.
- Manual: Qinsy ingests data, fault scenarios trigger expected alarms.

## Things you'll be tempted to do but should not

- Don't add a control REST API. The TUI is enough.
- Don't add async. Threads are correct here.
- Don't pull in `click`, `typer`, `prompt_toolkit`, `textual`, `pydantic`,
  `watchfiles`, `fastapi`. Each one is a 10-line "it could be useful"
  rationalisation that bloats the exe.
- Don't try to make `_core/` shared with aqps. The whole point of the vendor
  copy is independence.
- Don't invent new abstractions. Driver protocol, channel, effects — that's
  the architecture. Add concrete things to those slots.
- Don't gold-plate the TUI. Three regions (header, drivers table, scenarios
  list). That's it.

## Tone

Professional, terse, technical. No emoji in commits. Explain rationale in
commit bodies, not just diff descriptions. Reviewer is GPT-5 Codex; write
code that survives adversarial review.
