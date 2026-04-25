# qinsim

Minimal Qinsy NMEA simulator. One executable, one config, one TUI.

## What it does

Drives Qinsy 9.x with realistic NMEA-0183 sentences over UDP. Five sensor lanes
(GNSS, heading, motion, depth, environmental) with a fault-injection chain so
you can exercise Qinsy's error paths.

## Operator quickstart (Windows)

1. Download `qinsim.exe` from the latest release.
2. Drop it on the box. Double-click it.
3. The TUI opens. Press `1`–`9` to switch scenarios. `q` to quit.
4. Point Qinsy at `127.0.0.1:13130` UDP for each lane.

That's it. No Python install. No Docker. No admin rights.

## Developer quickstart

```bash
git clone https://github.com/spacecowboy515/qinsim.git
cd qinsim
uv venv && .venv/Scripts/activate
uv pip install -e ".[dev]"
python -m qinsim
```

## Build the exe

```bash
uv pip install pyinstaller
pyinstaller pyinstaller.spec
# dist/qinsim.exe
```

## Bundled scenarios

| File | What it tests |
|---|---|
| `harbour_rtk_fixed.yaml` | Sydney harbour, RTK fixed, calm — default smoke test |
| `open_ocean_survey.yaml` | Sea state 3, DGPS, slow ramp lines |
| `rtk_outage.yaml` | RTK fixed → float → DGPS → no-fix on schedule |
| `dropout_burst.yaml` | 5-second silent windows every 30s on GNSS |
| `nmea_corruption.yaml` | 5% checksum-corruption rate across drivers |

## Sibling projects

- [`aqps`](https://github.com/spacecowboy515/aqps) — kitchen-sink rig: full vessel-data platform with MQTT, replayers, dashboard. qinsim shares its `_core` engine but escapes the supervisor / Docker / dashboard weight.
- [`kmall-replay`](https://github.com/spacecowboy515/kmall-replay) — TCP + KMALL binary replayer. Run alongside qinsim when Qinsy needs both lanes.

## Non-goals

No serial transport (use com0com if needed), no MQTT, no web UI, no REST API,
no Postgres/NATS, no Docker, no live config hot-reload, no record/replay.
Those exist in aqps. qinsim stays small on purpose.
