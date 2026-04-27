# qinsim

Minimal Qinsy NMEA simulator. One executable, one config, one TUI.

## What it does

Drives Qinsy 9.x with realistic NMEA-0183 sentences over UDP. Five sensor lanes
(GNSS, heading, motion, depth, environmental) with a fault-injection chain so
you can exercise Qinsy's error paths.

## Operator quickstart (Windows)

1. Download `qinsim.exe` from the latest GitHub release.
2. Put it in a folder of its own — e.g. `C:\qinsim\qinsim.exe`.
   The first run writes a `scenarios\` directory next to the exe with
   the bundled YAMLs unpacked, so don't drop the exe onto the desktop
   or somewhere you don't mind a folder appearing.
3. Double-click `qinsim.exe`. A console window opens with a live panel
   showing each driver's emit rate, last sentence, slip, and the list
   of scenarios it found.
4. Press `1`–`9` to load a scenario from the picker. `r` restarts the
   current one. `q` quits cleanly.
5. Configure Qinsy to read NMEA over UDP on `127.0.0.1:13130` for each
   driver lane (or whatever host:port you set in the YAML).

No Python install. No Docker. No admin rights.

### What you should see

The first run creates `scenarios\` next to the exe and loads
`harbour_rtk_fixed.yaml`. Within ~2 seconds the drivers table fills in:

```
gnss_primary    gnss     10.0  10.0  $GPGGA,143052.10,3351.0000,S,15112.6000,E,4,12,...
heading_primary heading  20.0  20.0  $GPHDT,90.00,T*34
motion_primary  motion   25.0  25.0  :000000  0007G 0005  0020
depth_primary   depth     5.0   5.0  $SDDPT,18.5,0.5,200.0*64
env_primary     env       1.0   1.0  $YXMTW,18.5,C*1E
```

If the rates stay at 0/s after a few seconds, something's wrong — see
*Troubleshooting* below.

### Troubleshooting

**The window flashed open and closed.** Open `cmd` or PowerShell, `cd`
to the folder containing `qinsim.exe`, and run `qinsim.exe` from there
so the error stays on screen. Common causes: no `scenarios\` dir yet
*and* no `--scenario` argument, malformed YAML, or another process
already bound to the destination port.

**Validate a scenario without running it:**

```cmd
qinsim.exe validate scenarios\harbour_rtk_fixed.yaml
```

**Run a specific scenario directly:**

```cmd
qinsim.exe serve scenarios\open_ocean_survey.yaml
```

**Qinsy isn't seeing the data.** Check the destination in the YAML
matches Qinsy's input config, and that Windows Firewall isn't
swallowing UDP between hosts. Loopback (`127.0.0.1`) bypasses the
firewall; LAN destinations may not.

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
