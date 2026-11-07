# open289

English | [简体中文](https://github.com/FrostFlops/open289/blob/main/README.zh-CN.md)

[![CI](https://github.com/FrostFlops/open289/actions/workflows/ci.yml/badge.svg)](https://github.com/FrostFlops/open289/actions/workflows/ci.yml)

An open Python package, command-line interface and MCP server for the **Fluke 287/289 multimeter**, read over a
**Fluke IR3000 FC** infrared-to-Bluetooth-LE adapter, written so that an AI agent (Claude Code, or any MCP client)
can read, log and download real measurements by itself, without Fluke Connect. It reads the live value (about 4
readings/s), the full display state (function, range, HOLD / MIN MAX / REL modes, MIN / MAX / AVERAGE), everything
saved in the meter (measurements, MIN MAX and peak sessions, trend recordings), logs to CSV or JSONL for hours with
automatic reconnect, downloads trend recordings, and can press the meter's front-panel keys.

The meter is reached through the adapter's pass-through to the meter's own serial command set (`ID`, `QM`, `QDDB`,
`QSRR`, ...); the protocol was worked out on real hardware, see `docs/PROTOCOL.md`.

## Status and tested hardware

Version 0.1.0 (beta). What was verified on real hardware on 2026-11-06: an IR3000 FC with radio firmware 01.00.01 and
MSP firmware 01.00.15, a Fluke 289 with firmware V1.71, a Windows 11 PC with Intel Bluetooth (Python and bleak over
WinRT).

- Only a **289** was tested. The 287 speaks the same protocol and should work; that is untested.
- Linux and macOS: bleak supports them and the package has no Windows-only code in the Bluetooth path, but real
  hardware was never tried there. CI runs the whole test suite on Linux and Windows against the virtual meter only
  (see [Virtual meter](#virtual-meter)).
- Measured speeds: live readings arrive about 4 times per second; one pass-through command takes about 1.3 s (QDDB display snapshots ran about 1.2 to 1.8 s apart); a
  trend recording downloads at 0.44 s per record (up to 8 records per round-trip, as many as fit in 87 bytes).

## Install

Requirements: Python 3.10+, a Bluetooth LE radio, a Fluke 287/289 and a Fluke IR3000 FC.

```
pip install open289
```

This installs the commands `open289` and `open289-mcp` (`python -m open289 ...` is the same as `open289 ...`) and the
single dependency `bleak`.

From a clone, with uv or a plain venv:

```
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e .
```

```
python -m venv .venv
.venv\Scripts\pip install -e ".[test]"        # Linux / macOS: .venv/bin/pip
```

Windows notes:

- Switch Bluetooth on (Settings, Bluetooth and devices). No pairing or bonding is needed.
- The console may use a legacy code page (GBK, CP1252); measurement strings contain `µ`, `Ω` and `°`. The CLI
  reconfigures its own stdout and stderr to UTF-8, and the launchers in a clone start Python with `-X utf8` for
  everything else: `open289.cmd live -n 10` (Windows) and `./open289.sh live -n 10` (POSIX shell, Git Bash). With a
  pip install, `python -X utf8 -m open289 ...` does the same.
- If pip's `Scripts` folder is not on `PATH`, call `python -m open289` or give the full path to `open289.exe`.

## Hardware setup

1. Clip the IR3000 FC onto the meter's infrared port on the back of the 287/289 (the meter must be switched on).
2. Wake the adapter: press its button for more than 1 s until the LED flashes green. It advertises over Bluetooth
   only while it is awake and not connected.
3. Close Fluke Connect on any phone or tablet. **The adapter accepts exactly one Bluetooth connection.** While
   another program holds it, `open289` reports `NOT_FOUND` (the adapter does not advertise) or `BUSY`. open289 itself
   takes a lock, so two open289 processes never fight over the adapter: the second gets `BUSY`.
4. Check: `open289 scan` lists the adapter; `open289 info` shows the meter. `info` reports `"meter":
   {"attached": false}` when the adapter is awake but not on a powered meter.

Adapter and meter both go to sleep or off by themselves and cannot be switched on remotely; wake the adapter with its
button and the meter with its dial.

## Quick start

```
open289 scan                                # find the adapter
open289 info                                # adapter and meter identity, batteries, clock, saved counts
open289 read                                # the whole display now: function, range, modes, every reading
open289 live -n 20                          # 20 real-time readings, one JSON line each
open289 log --duration 10m --out volts.csv  # log to a file; ends by itself, or: open289 stop / Ctrl+C
open289 stop                                # end a running live / read --every / log cleanly
```

Every command prints JSON (one object per line) unless `--text` is given; add `--sim` to try everything without
hardware. Output of the virtual meter (never invented; hardware values differ, the fields are the same):

```
$ open289 --sim scan
{"ok":true,"cmd":"scan","devices":[{"address":"FA:28:90:00:00:01","name":"IR 3000 FC","rssi":-60}]}

$ open289 --sim info
{"ok":true,"cmd":"info","adapter":{"address":"FA:28:90:00:00:01","name":"IR 3000 FC","radio_fw":"01.00.01","msp_fw":"01.00.15","battery_pct":100},"meter":{"attached":true,"model":"FLUKE 289","firmware":"V1.71","serial":"12345678","battery":"FULL","memory_level":"OK","clock_epoch":1794013461,"clock":"2026-11-07 01:04:21.000"},"saved":{"recordings":1,"minmax":0,"peak":1,"measurements":2}}

$ open289 --sim --text info
adapter : FA:28:90:00:00:01  radio fw 01.00.01  MSP fw 01.00.15  battery 100%
meter   : FLUKE 289  firmware V1.71  serial 12345678
battery : FULL   memory: OK   clock: 2026-11-07 01:04:31.000 (meter local time)
saved   : {'recordings': 1, 'minmax': 0, 'peak': 1, 'measurements': 2}
```

`info.meter.clock` is the meter's own clock (local time, as set on the meter), not the PC's.

## Real-time acquisition

| Command | Source | Rate | Carries |
|---|---|---|---|
| `live` | the adapter's BLE notifications | ~4/s, one JSON line per notification | the primary reading and, when the meter shows one, the secondary reading |
| `read` | one `QDDB` display query through the pass-through | one round-trip, ~1.3 s | the whole display: function, secondary function, range, autorange, modes, every reading |
| `read --every 1s` | the same, repeated | about one per interval (at least ~1.3 s) | one snapshot per line; ends with a `done` line |

Use `live` for fast values and logging, `read` when you need to know what the meter is doing (HOLD? MIN MAX? REL?
which range?), and `read --every` when you need modes or MIN / MAX / AVERAGE over time. `live` and `read` can be
combined by `log --snapshot-every` (next section) on one connection.

```
$ open289 --sim live -n 3
{"type":"reading","t":1794027861.982,"value":5.0625,"unit":"V DC","display":"5.0625 V DC","state":"NORMAL","function":"VDC"}
{"type":"reading","t":1794027862.227,"value":5.0625,"unit":"V DC","display":"5.0625 V DC","state":"NORMAL","function":"VDC"}
{"type":"reading","t":1794027862.481,"value":5.0625,"unit":"V DC","display":"5.0625 V DC","state":"NORMAL","function":"VDC"}
{"ok":true,"cmd":"live","event":"done","readings":3,"duration_s":0.748,"rate_hz":4.01,"stopped_by":"count"}
```

### A `live` reading line

| Field | Meaning |
|---|---|
| `type` | `"reading"` |
| `t` | PC time of the notification, Unix seconds (3 decimals) |
| `value` | the number in **SI base units** (V, A, ohm, F, Hz, s, S; degrees C / F and percent as they are): 1.5 mV is `0.0015`. `null` for an overload (`OL`) or a blank / invalid display |
| `unit` | the base unit (without the SI prefix; `display` carries the prefix): `V DC`, `V AC`, `V AC+DC`, `A DC`, `Ω`, `S`, `Hz`, `F`, `s`, `°C`, `°F`, `%`, `dB`, `dBV`, `dBm`, `CF` |
| `display` | the text the meter shows, with the SI prefix and decimals of the display (`5.0625 V DC`, `OL V AC`) |
| `state` | `NORMAL`, `OL_RANGE` (overload), `BLANK` (autorange blanking), `INVALID` (during a dial change) |
| `function` | the live function name (`VDC`, `VAC`, `mVDC`, `Ω`, `Continuity`, `Diode`, `Temp`, `mADC`, ...) |
| `attribute` | present when the function has one: positive edge for Hz, continuity open / short / glitch, a good diode |
| `capture` | `true` when the reading is a captured value (HOLD or similar) |
| `secondary` | present when the meter shows a secondary value: an object with the same `value`, `unit`, `display`, `state`, `function` fields |
| `raw` | with `--raw` only: the 16 notification bytes in hex |

The primary and secondary readings are what the meter's main and secondary display show. With Hz on a voltage
function the Hz value becomes primary and the volts the secondary, as on the meter:

```
$ open289 --sim live -n 1            # V AC with the Hz secondary function
{"type":"reading","t":1794027906.375,"value":60.002,"unit":"Hz","display":"60.002 Hz  [positive edge]","state":"NORMAL","function":"VAC","attribute":"positive edge","secondary":{"value":null,"unit":"V AC","display":"OL V AC","state":"OL_RANGE","function":"VAC"}}
```

**What `live` does not carry:** no mode flags (HOLD, MIN MAX, REL, RECORD), no MIN / MAX / AVERAGE values, no range,
no function name in the display-snapshot vocabulary. In HOLD the primary is the held value, in REL the relative value.
Use `read` (or `log --snapshot-every`) for those. A live run ends at `-n`, `--for` or `open289 stop` and
prints a `done` line (`readings`, `duration_s`, `rate_hz`, `stopped_by`: `count`, `duration`, `stop`); Ctrl+C ends it without a summary (exit 130). `--timeout`
(default 10 s) turns a silent link into an error instead of waiting forever.

### A display snapshot (`read`)

```
$ open289 --sim read
{"ok":true,"cmd":"read","t":1794027861.456,"function":"V_DC","secondary_function":"NONE","autorange":true,"range":5.0,"unit":"VDC","modes":["HOLD"],"readings":[{"id":"LIVE","value":5.0613,"unit":"VDC","display":"5.0613 V DC","state":"NORMAL","attribute":"NONE","decimals":4,"time":"2026-11-07 01:04:21.456","ts":1794013461.4560547},{"id":"PRIMARY","value":5.0625,"unit":"VDC","display":"5.0625 V DC","state":"NORMAL","attribute":"NONE","decimals":4,"time":"2026-11-07 01:03:58.325","ts":1794013438.3251953}],"hazard":false,"primary":{"id":"PRIMARY",...}}
```

| Field | Meaning |
|---|---|
| `t` | PC time of the query, Unix seconds |
| `function` | the primary function: `V_DC`, `V_AC`, `MV_DC`, `OHMS`, `CONTINUITY`, `DIODE_TEST`, `CAPACITANCE`, `TEMPERATURE`, `MA_DC`, ... |
| `secondary_function` | `NONE`, `HERTZ`, `PEAK_MIN_MAX`, ... |
| `autorange`, `range` | autorange on / off and the range's full-scale value (in base units; `5.0` = the 5 V range) |
| `unit` | the primary unit code (`VDC`, `OHM`, `CEL`, ...) |
| `modes` | active modes, e.g. `HOLD`, `MIN_MAX_AVG`, `REL`, `RECORD`; empty list = none |
| `readings` | every number on the screen, in display order (below) |
| `primary` | a copy of the `PRIMARY` entry of `readings` |
| `hazard` | `true` when the meter shows its high-voltage warning (bolt) |
| `minmax_start` | when MIN MAX was started, local meter time (only when MIN MAX is active) |

Each `readings` entry has: `id` (`LIVE`, `PRIMARY`, `SECONDARY`, `MINIMUM`, `MAXIMUM`, `AVERAGE`, `REL_REFERENCE`,
`TEMP_OFFSET`, ...), `value` (SI base units, `null` for OL), `unit`, `display`, `state`, `attribute`, `decimals`,
`time` (meter local time) and `ts` (the same meter clock value as a number: local wall-clock time encoded as epoch seconds, no timezone; not comparable with `t`). `LIVE` is the instantaneous input; `PRIMARY` is
the main reading, which differs from `LIVE` in HOLD or REL. With MIN MAX active the snapshot also has `MINIMUM`,
`MAXIMUM` and `AVERAGE`:

```
$ open289 --sim press MINMAX ; open289 --sim --text read
00:05:09.566  V_DC   range AUTO 5   modes MIN_MAX_AVG
    LIVE           5.0626 V DC            2026-11-07 01:05:09.567
    PRIMARY        5.0626 V DC            2026-11-07 01:05:09.567
    MINIMUM        5.0612 V DC            2026-11-07 01:05:09.099
    MAXIMUM        5.0631 V DC            2026-11-07 01:05:07.500
    AVERAGE        5.0621 V DC            2026-11-07 01:05:09.567
```

`read --every 1s [-n N] [--for 5m]` prints one snapshot per line (`ok`, `cmd`, `t`, ...), an `{"ok":false,...}` line when
one query fails (for example METER_BUSY) without ending the run, and a final `{"event":"done","snapshots":N,
"stopped_by":...}`.

## Logging

`log` writes live readings to a file, in the foreground, until `--duration`, `open289 stop` or Ctrl+C. It never
stops because of a lost link as long as `--reconnect-for` allows retries.

```
open289 log --duration 10m --out volts.csv
open289 log --duration forever --out run.jsonl --snapshot-every 10s     # until: open289 stop
open289 log --every 1s --duration 8h --out night.csv                    # one reading per second
```

| Option | Default | Meaning |
|---|---|---|
| `--out`, `-o` | `data/log_<time>.csv` | output file (`.csv` or `.jsonl`; relative to the current folder; the default is in the [data folder](#data-folders)) |
| `--format csv\|jsonl` | from the `--out` extension, else csv | file format |
| `--duration`, `-d` | `forever` | logging time, counted from the first connection: `30s`, `10m`, `8h`, `1.5h`, `2d`, `forever` |
| `--every` | `0` | one reading per interval (the most recent reading at each tick); `0` = every notification, ~4/s |
| `--snapshot-every` | `0` (off) | also record the full display (modes, MIN / MAX / AVG) at this interval, e.g. `10s` |
| `--status-every` | `10s` | status line on stdout; `0` = off |
| `--reconnect-for` | `5m` | keep retrying this long after the link is lost; `0` = end the log at the first loss |

**CSV** columns: `pc_time` (Unix seconds), `local_time` (PC local time with milliseconds), `value`, `unit`, `display`,
`state`, `function`, `attribute`, `secondary_value`, `secondary_unit`, `secondary_display`:

```
pc_time,local_time,value,unit,display,state,function,attribute,secondary_value,secondary_unit,secondary_display
1794027865.921,2026-11-07 00:04:25.921,5.062,V DC,5.0620 V DC,NORMAL,VDC,,,,
1794027866.168,2026-11-07 00:04:26.167,5.062,V DC,5.0620 V DC,NORMAL,VDC,,,,
```

`value` is in SI base units and empty for an overload. With `--snapshot-every`, a CSV log puts the snapshots in a
sidecar file `<file>.display.jsonl`, one JSON object per line: `t` (Unix seconds), `ack`, `hex` (the raw `QDDB`
answer) and `display` (the same object as a `read` snapshot). A failed snapshot query is written as
`{"t":..., "ack":"ERROR", "error":{...}}` and the log goes on.

**JSONL** has one object per line, with a `type`:

| `type` | Content |
|---|---|
| `ble` | a reading: the fields of a `live` line (`t`, `value`, `unit`, `display`, `state`, `function`, `attribute`, `secondary`) plus `hex`, the 16 notification bytes |
| `qddb` | a display snapshot (`t`, `ack`, `hex`, `display`), only with `--snapshot-every` |
| `event` | `link_lost` (with `reason`) and `reconnected`, with `t` |

```
{"t": 1794027869.307, "value": 5.0612, "unit": "V DC", "display": "5.0612 V DC", "state": "NORMAL", "function": "VDC", "type": "ble", "hex": "b4c50008020c00000000000000000000"}
```

**Decimation.** `--every 1s` writes one row per second: the latest reading at that moment. The statistics in the
summary then cover only the written rows. Use `--every 0` when you need every notification (or to see
transients), `--every` for long runs.

**Status lines** on stdout while it runs (a `started` line first, then `connected` once the adapter is found and
armed, then a `status` line every `--status-every`):

```
{"ok":true,"cmd":"log","event":"started","file":"...x.csv","format":"csv","duration_s":3.0,"every_s":0.0,"snapshot_every_s":1.0}
{"ok":true,"cmd":"log","event":"connected","connect_s":2.41,"model":"FLUKE 289","adapter":"FA:28:90:00:00:01"}
{"ok":true,"cmd":"log","event":"status","elapsed_s":3.5,"records":4,"last":"5.0627 V DC"}
```

**Connecting takes time.** Every command first has to see the adapter advertise. The IR3000 FC advertises only every
few seconds, and right after a previous connection ends it can take about 20 s before it advertises again (measured on
hardware). `--duration` therefore counts from the first connection (`connect_s` says how long finding the adapter
took), so a `--duration 30s` log really holds about 30 s of readings.

**Reconnect and gaps.** When the Bluetooth link drops (meter off, adapter asleep, out of range, "no live reading for 10
s") the log prints `link_lost` (with `retry_for_s`), writes a `link_lost` event into a JSONL file, retries every 3 s,
and on success prints `reconnected` (with `gap_s`) and carries on appending to the same file. If the link does not
come back within `--reconnect-for`, the log ends with `stopped_by: "link_lost"` and the data written so far is kept.
It fails with `LINK_LOST` only when not one reading was logged; a log that never connected at all fails at once with
the real error (`NOT_FOUND`, `DETACHED`, ...).

**Stopping.** `open289 stop` (from another terminal or process) ends it within about a second; so do Ctrl+C, the
duration, and an unrecoverable link loss. The file is flushed after every row, so a killed process loses at most the
last row.

**Summary** (the last line, `"event":"done"`):

```
{"ok":true,"cmd":"log","event":"done","file":"...x.csv","format":"csv","records":12,"packets":12,"snapshots":2,"started":1794027863.2,"ended":1794027868.7,"duration_s":5.535,"connect_s":2.41,"logging_s":3.125,"stopped_by":"duration","gaps":[],"stats":{"V DC":{"n":12,"min":5.0614,"max":5.0629,"mean":5.0622}},"meter":{"model":"FLUKE 289","adapter":"FA:28:90:00:00:01"},"display_file":"...x.csv.display.jsonl"}
```

`records` rows written, `packets` notifications received, `snapshots` display snapshots taken, `duration_s` the
whole command, `connect_s` the time until the first connection, `logging_s` the time from then on, `stopped_by`
(`duration`, `stop`, `link_lost`, `interrupted`), `gaps` (a list of `{from, to, reason}`, Unix seconds), `stats`
(`n`, `min`, `max`, `mean` of the primary value per unit; overloads are left out) and `display_file` (CSV with
snapshots only). `--text` prints a two-line version with the same numbers.

## Saved data

```
open289 memory                       # everything saved in the meter (--text --detail lists every reading)
open289 recording 0 --out rec0.csv   # download trend recording 0
open289 recording 0 --out rec0.csv --start 40   # resume an interrupted download (appends)
```

`memory` reads the counts and then every saved item, and prints one JSON object: `counts` (`measurements`, `minmax`,
`peak`, `recordings`) and `items`. Each item has `kind` (`measurement`, `minmax`, `peak`, `recording`), `index` (the
index within its kind: this is what `recording INDEX` takes), `name`, `seq`, the same fields as a display snapshot
(`function`, `modes`, `readings`, `primary`, ...), `start` / `end` for sessions, and for recordings `interval_s`,
`event_threshold`, `records`, `reading_index` and `duration_s`. If some items cannot be read the result has
`"ok":false` and an `errors` list, and the exit code is 6 (the readable items are still listed). Every saved item is
one query, so `memory` has a 600 s limit instead of 120 s.

```
$ open289 --sim --text memory
saved items: {'recordings': 1, 'minmax': 0, 'peak': 1, 'measurements': 2}
measurement #0   'Save 1'         2026-09-29 03:15:52.785  TEMPERATURE  34.7 °C
measurement #1   'Save 2'         2026-11-01 01:56:42.182  CONTINUITY  OL Ω [OPEN_CIRCUIT]
peak        #0   'Save 3'         2026-11-06 21:29:01.471  V_DC  0.005 V DC
recording   #0   'Save 4'         2026-11-06 21:40:19.248  V_DC  1.9874 V DC  72 records, interval 1 s, 1.0 min
```

**Recording download.** `recording INDEX` fetches the trend records of one recording (the meter's RECORD mode) and
writes one row per record. It batches up to 8 queries per round-trip and takes 0.44 s per record on the hardware
(`--no-batch`: one per round-trip, about 1.4 s). A record that fails is retried (up to 3 more times). If the meter
shows a menu, the download stops with `METER_BUSY` and a hint with the record to resume from; so does a failing
record (`PROTOCOL_ERROR`). Resume with the same `--out` and `--start K`: the file is appended to, without a second
header. `--limit K` stops before record K (an end index, not a count). Events: `started`, `progress` every 10 s
(`done`, `of`), and `done` (`records`, `duration_s`, `s_per_record`, `retries`, `file`).

CSV columns (JSONL has the same keys): `index`, `start`, `end` (meter local time), `unit`, `primary`,
`primary_state`, `minimum`, `minimum_state`, `average`, `average_state`, `maximum`, `maximum_state`, `samples`,
`record_type` (`INTERVAL`, ...), `stable`, `transient`. Values are in SI base units; an `_state` of `OL` means the
value is empty.

```
index,start,end,unit,primary,primary_state,minimum,minimum_state,average,average_state,maximum,maximum_state,samples,record_type,stable,transient
0,2026-11-06 21:40:19.248,2026-11-06 21:40:19.752,VDC,1.4904,NORMAL,1.4903,NORMAL,1.49032,NORMAL,1.4904,NORMAL,5,INTERVAL,STABLE,NON_T
```

## Front-panel keys

`press KEY [KEY ...]` simulates the keys in order and returns the display state after each one. Keys: `F1 F2 F3 F4
UP DOWN LEFT RIGHT HOLD MINMAX RANGE INFO BACKLIGHT ONOFF`.

```
open289 press HOLD                    # toggle HOLD
open289 press RANGE                   # step the range (manual)
open289 press RANGE --held            # back to autorange
open289 press MINMAX                  # start MIN MAX
open289 press MINMAX F4               # stop it (opens the result page), then close the page
open289 press ONOFF F1 --allow-off    # switch the meter off for good
```

Options: `--held` sends a long press (`HELD_DOWN`) for every key; `--settle S` (default 1.0) waits that long before the
state is read back; `--allow-off` permits ONOFF. Each entry of the result has `key`, `held`, `ack` (`OK`) and `state`
(a display snapshot; or a text when the meter shows a page, see below). `press` stops at the first key the meter does
not acknowledge. `press` is the only command that changes the meter's state (apart from `raw --unsafe`).

```
$ open289 --sim press HOLD
{"ok":true,"cmd":"press","presses":[{"key":"HOLD","held":false,"ack":"OK","state":{"function":"V_DC","secondary_function":"NONE","autorange":true,"range":5.0,"unit":"VDC","modes":[],...}}]}
```

What was observed on the hardware:

- `HELD_DOWN` is a long press. `RANGE` held = back to autorange. Held `MINMAX` behaves like a press.
- **MIN MAX has pages.** The first `MINMAX` starts MIN MAX (`modes` has `MIN_MAX_AVG`). The second stops it and opens
  the **result page** (F1 restart, F2 save, `MINMAX` or F4 close). F2 on the result page opens the **save page** (F1
  adds to the name, F2 saves, F4 closes). Closing a page returns to the measurement screen.
- **Why queries return `METER_BUSY` there.** While a menu, result page, save page or setup screen is open, the meter
  answers every measurement query (`QDDB`, `QSLS`, ...) with an execution error. open289 names it `METER_BUSY`
  (exit 5). `press` then shows `"state": "menu / result page open (measurement queries blocked)"`. Close the page
  with the keys (`F4`) or on the meter, then retry.
- `BACKLIGHT` cycles off, bright, dim, also on the pages.
- `ONOFF` is refused (`UNSAFE_OPERATION`, exit 7) unless `--allow-off`. With it, the meter asks for confirmation
  (`turning off meter will break communication`): `F1` confirms, `F4` cancels. After F1 the meter is off, the
  adapter switches itself off a moment later, and neither can be switched on remotely. This cannot be undone from
  the PC.

## Raw commands

`raw COMMAND [COMMAND ...]` sends meter commands through the pass-through and returns each answer (`ack`, and `text`
or, for binary replies, `binary_hex`). **Query-only by default:** `ID`, `IM` and `Q*` commands (`QM`, `QDDB`, `QDDA`,
`QBL`, `QSLS`, `QMP clock`, ...) pass; anything else (`MP`, `CSD` = clear memory, `RI` = reset, `OFF`, `PRESS`, ...)
is refused with `UNSAFE_OPERATION` before it is sent. `--unsafe` lifts the filter; use it only when you know exactly
what the command does, because some commands erase saved data or switch the meter off.

```
$ open289 --sim raw ID QM
{"ok":true,"cmd":"raw","results":[{"command":"ID","ack":"OK","text":"FLUKE 289,V1.71,12345678"},{"command":"QM","ack":"OK","text":"5.0628E0,VDC,NORMAL,NONE"}]}

$ open289 --sim raw MP
{"ok":false,"cmd":"raw","results":[{"command":"MP","error":{"code":"UNSAFE_OPERATION","message":"meter command b'MP' is not a read-only query","hint":"only ID, IM and Q* queries pass by default; add --unsafe for other commands"}}]}
```

Each command is sent and answered in turn; a refused or failed one is reported in its own result and the exit code
is that of the first error (7 for a refusal). A command over 87 bytes is refused before it is sent (see
[Safety](#safety)).

## Self-test

`open289 selftest` is a read-only health check of the adapter, the link and the meter (it never changes the meter):

| Check | What |
|---|---|
| S01 | adapter advertising |
| S02 | connect and arm the adapter control point |
| S03 | meter attached (`FLUKE 287/289`, not the detached `FLUKE PC3000`) |
| S04 | adapter firmware answers |
| S05 | pass-through `ID` |
| S06 | display snapshot (`QDDB`) decodes (the meter must be on its measurement screen) |
| S07 | a live reading arrives within 5 s |
| S08 | adapter battery >= 20 % |
| S09 | meter battery not EMPTY / ALMOST_EMPTY |

The result lists every check with `pass`, `measured` and `expected`, plus `passed` and `failed` counts; the exit code
is 1 when a check failed. A failed S01, S02 or S03 ends the run early (the later checks need that link).

```
$ open289 --sim selftest
{"ok":true,"cmd":"selftest","passed":9,"failed":0,"items":[{"id":"S01","name":"adapter advertising","pass":true,"measured":"FA:28:90:00:00:01","expected":null},...]}
```

## Virtual meter

The package contains a model of an IR3000 FC with a Fluke 289 attached (`open289/sim.py`), so the whole tool, the MCP
server and the test suite run with no Bluetooth and no hardware. Select it with `--sim` or `OPEN289_SIM=1`.

It simulates what was measured on the real hardware: the GATT table and the CNX ID write that arms the adapter, the
18-byte control-point write limit, the pass-through framing, the out-buffer overflow (no reply above 128 bytes, a modelled guess; 181 measured) and the adapter hang at 373 bytes and more (open289 itself refuses anything over 87 bytes) when it
is overrun, the binary `QDDB` / `QSMR` / `QMMSI` / `QPSI` / `QRSI` / `QSRR` layouts, the 16-byte live notifications at 4/s,
the single connection (a second process is refused and the adapter stops advertising), the front-panel keys with
their pages (MIN MAX result and save pages, power-off confirmation, backlight cycle), a meter that is switched off and
an adapter that goes to sleep, saved memory with two measurements, a peak session and a 72-record recording. Behaviour
that was never observed on hardware is marked GUESS in the code. Identity: address `FA:28:90:00:00:01`, serial
`12345678`. It is a stand-in for tests and demos, not a measurement instrument: the "readings" are synthetic.

The state of the virtual device is one JSON file shared by every process (`state/sim_state.json` in the
[data folder](#data-folders)), so `open289 press HOLD` in one command is seen by the next.

```
python -m open289.sim show                            # the state as JSON
python -m open289.sim reset                           # factory state
python -m open289.sim power-cycle                     # switch adapter and meter on again (clears an adapter hang)
python -m open289.sim set function=V_AC input=open meter_on=false
python -m open289.sim audit [--clear]                 # every GATT write the tool made (state/sim_writes.jsonl)
```

`set` keys: `function` (`V_DC V_AC MV_DC MV_AC OHMS CONTINUITY DIODE_TEST TEMPERATURE MA_DC`, and `V_AC_HZ` / `V_DC_HZ` for
the Hz secondary function), `secondary` (`NONE` / `HERTZ`), `input` (the input of the current function; `open` for
OL), `inputs.<FUNCTION>`, `hz`, `meter_on`, `adapter_on`, `adapter_hung`, `autorange`, `range_index`, `screen`
(`normal`, `minmax_result`, `save_page`, `power_off_confirm`), `backlight`, `modes`, `seed`, `clock_offset`.
Environment: `OPEN289_SIM_RATE` (live notifications per second, default 4), `OPEN289_SIM_DELAY` (latency of a
pass-through send, default 0.05 s; the hardware takes about 0.6 s), `OPEN289_SIM_STATE`, `OPEN289_SIM_AUDIT`
(file overrides). `OPEN289_NO_BLE=1` makes the real Bluetooth backend refuse to start (tests set it, so a test can
never reach hardware).

## CLI reference

Global options go before the command:

| Option | Meaning |
|---|---|
| `--address`, `-a ADDRESS` | adapter BLE address (default: the first IR3000 FC found) |
| `--sim` | use the virtual IR3000 + Fluke 289 (no hardware) |
| `--text` | human-readable output instead of JSON |
| `--verbose`, `-v` | log adapter control-point traffic to stderr |
| `--max-wall MAX_WALL` | time limit in seconds for one-shot commands (default 120; `memory` 600). `live`, `log`, `recording` and `read --every` have no wall limit |
| `--version` | print the version |

`open289 <command> --help` lists the options of one command.

| Command | Purpose | Options (defaults) |
|---|---|---|
| `scan` | find IR3000 FC adapters | `--seconds SECONDS` (10) |
| `info` | adapter and meter identity, battery, memory level, clock, saved counts | |
| `read` | full display state now (function, range, modes, all readings) | `--every EVERY` (one snapshot per interval, e.g. `1s`), `-n`, `--count COUNT` (with `--every`: stop after N snapshots), `--for FOR_` (with `--every`: stop after this long, e.g. `5m`) |
| `live` | real-time readings from BLE notifications (~4/s), one JSON line each | `-n`, `--count COUNT`, `--for FOR_` (e.g. `30s`), `--raw` (include the raw bytes), `--timeout TIMEOUT` (10 s without a reading is an error) |
| `log` | log readings to a CSV / JSONL file | `--out`, `-o OUT` (`data/log_<time>.csv`), `--format {csv,jsonl}`, `--duration`, `-d DURATION` (`forever`), `--every EVERY` (`0`), `--snapshot-every SNAPSHOT_EVERY` (`0`), `--status-every STATUS_EVERY` (`10s`), `--reconnect-for RECONNECT_FOR` (`5m`) |
| `memory` | saved measurements, MIN MAX, peak sessions and recordings | `--detail` (with `--text`: list every reading) |
| `recording` | download trend recording INDEX to CSV / JSONL | `index`, `--out`, `-o OUT` (`data/recording<INDEX>_<time>.csv`), `--format {csv,jsonl}`, `--start START` (0), `--limit LIMIT` (end index), `--no-batch` |
| `press` | simulate front-panel keys | `KEY [KEY ...]`, `--held`, `--settle SETTLE` (1.0 s), `--allow-off` |
| `raw` | raw meter commands (`ID`, `IM` and `Q*` unless `--unsafe`) | `COMMAND [COMMAND ...]`, `--unsafe` |
| `selftest` | read-only health check of adapter, link and meter | |
| `stop` | end a running `live` / `read --every` / `log` cleanly | |

Durations (`--every`, `--for`, `--duration`, `--snapshot-every`, `--status-every`, `--reconnect-for`) take `ms`, `s`,
`m` (or `min`), `h`, `d`; a bare number is seconds: `500ms`, `30`, `90s`, `5m`, `1.5h`, `2d`. `forever` (also `inf`,
`until-stop`) means no limit (for `--duration`).

`stop` writes a request file that a running `live`, `read --every` or `log` (in any process) sees within about a
second; it does not need the adapter. `recording` and the one-shot commands do not react to it.

## Output format

Every command prints JSON on stdout, one object per line, compact. One-shot commands print one object. `live`, `read
--every`, `log` and `recording` print event lines and end with an `"event": "done"` summary. Event lines carry
`"ok": true, "cmd": ..., "event": ...` (`started`, `connected`, `status`, `progress`, `link_lost`, `reconnected`, `done`); `live`
readings are `{"type":"reading", ...}`. The first field of a result is `ok`. Warnings and `--verbose` traffic go to
stderr. Values are in SI base units; `display` is the text from the meter. Times: `t`, `pc_time`, `started`, `ended` are Unix seconds on the PC; `time`, `start`, `end`, `info.meter.clock`, and the numeric `ts` / `clock_epoch`, are the meter's local clock (no timezone).

Errors are one object and a non-zero exit code:

```
{"ok":false,"cmd":"read","error":{"code":"METER_BUSY","message":"'QDDB' -> EXECUTION_ERROR","hint":"the meter is on a menu, result page or setup screen: close it on the meter (or with open289 press), then retry"}}
```

`--text` prints a human-readable form of the same data: one line per reading for `live` (`HH:MM:SS.mmm  value unit`
and the secondary after `|`), a short table for `read`, `memory`, `press` and `selftest`, one line per record for `recording`, and a short summary for
`log`. Errors in `--text` are `error CODE: message` plus a `hint:` line. Scripts and agents should use
the JSON.

## Errors and exit codes

Every error has a `code`, a `message` and (mostly) a `hint`; the exit code follows the code.

| Code | Exit | Meaning and typical fix |
|---|---|---|
| `INVALID_ARG` | 2 | an argument cannot be parsed or is out of range (duration, key, index): fix the command |
| `NOT_FOUND` | 3 | no IR3000 FC is advertising: wake the adapter (button until the LED flashes green), come closer, close Fluke Connect on phones |
| `BUSY` | 4 | another open289 process holds the adapter (wait, or `open289 stop`); in the MCP server also a running background log |
| `METER_BUSY` | 5 | the meter answers EXECUTION_ERROR: a menu, result page, save page or setup screen is open; close it on the meter or with `press F4` |
| `DETACHED` | 5 | the adapter reports no powered meter (per the Fluke app it then reports `FLUKE PC3000`; not observed on hardware): seat it on the IR port, switch the meter on |
| `NO_DATA` | 5 | the meter has no data for the request, e.g. no saved item with that index: check `memory` |
| `TIMEOUT` | 6 | a one-shot command passed `--max-wall`, or no reading for `--timeout` seconds: move closer, check the meter is on, raise `--max-wall` |
| `BLE_ERROR` | 6 | scan or connect failed: is Bluetooth switched on? (also: `OPEN289_NO_BLE` is set) |
| `LINK_LOST` | 6 | the Bluetooth link dropped (a `log` reconnects by itself within `--reconnect-for`) |
| `PROTOCOL_ERROR` | 6 | an unexpected or undecodable answer; a recording download names the record to resume from |
| `METER_ERROR` | 6 | the meter did not accept the command (syntax error) or did not answer |
| `WRITE_FAILED` | 6 | reserved (not emitted yet; a failed Bluetooth write currently surfaces as `INTERNAL`) |
| `UNSAFE_OPERATION` | 7 | refused by the safety rules: a non-query `raw` without `--unsafe`, ONOFF without `--allow-off`, a send over 87 bytes |
| `STOPPED` | 0 | reserved; a stopped run ends normally with `stopped_by: "stop"` |
| `INTERNAL` | 1 | unexpected exception (the result carries the last trace lines); anything else maps to exit 1 |

Exit 0 is success; Ctrl+C gives 130. A failed `selftest` (a check failed, it is not an error object) and a `memory`
where some items could not be read exit 1 and 6 respectively. The MCP server uses the same codes and adds `TIMEOUT`
for tool calls past `max_wall`, `CANCELLED` and `SELFTEST_FAILED`.

## MCP server

`open289-mcp` (or `python -m open289.mcp_server`) serves the meter over the Model Context Protocol (stdio, standard
library only; installed with `pip install open289`):

```
claude mcp add open289 -- open289-mcp
```

If pip's `Scripts` folder is not on `PATH`, give the full path to `open289-mcp`. Other clients:
`{"mcpServers": {"open289": {"command": "open289-mcp"}}}`. Each tool call runs one CLI command in a child process,
so the adapter is connected and released per call, a hang never blocks the server, and every rule of the CLI applies
(safety filter, adapter lock, error codes). The server's own instructions tell the agent to start with `status`.

11 tools:

| Tool | Runs | Key arguments (defaults) |
|---|---|---|
| `status` | `info` | `max_wall`. Adapter address / firmware / battery, meter model / firmware / serial / battery / memory level / clock, saved counts. `meter.attached` false = not on a powered meter |
| `read` | `read` | `max_wall`. One display snapshot: function, range, modes, primary and every other reading |
| `live` | `live` | `count` (20, 1..400), `seconds` (0.1..120; with `count`: whichever comes first), `max_wall`. A **summary** instead of the lines: count, rate, first / last time, min / max / mean of the primary per unit, functions and units seen, the last 5 readings |
| `memory` | `memory` | `max_wall` (600). Counts and every saved item |
| `download_recording` | `recording` | `index` (required), `out`, `format`, `start` (0), `limit`, `max_wall` (900). File, number of records, seconds per record, retries, the recording header. A failed result carries `resume` with the `start` and `out` to continue with |
| `press` | `press` | `keys` (required, 1..20 of `F1 F2 F3 F4 UP DOWN LEFT RIGHT HOLD MINMAX RANGE INFO BACKLIGHT ONOFF`), `held` (false), `allow_off` (false), `max_wall`. The display state after each key |
| `query` | `raw` | `commands` (required, 1..10, e.g. `["ID","QM"]`), `max_wall`. Read-only: `ID`, `IM`, `Q*`; anything else is refused with `UNSAFE_OPERATION` before it reaches the meter (there is no `--unsafe` in MCP) |
| `selftest` | `selftest` | `max_wall`. The checks S01-S09; a failure returns `SELFTEST_FAILED` with the failed checks |
| `log_start` | `log` in the background | `duration` (`10m`; `forever` = until `log_stop`; must be > 0), `every` (`0`), `snapshot_every` (`0`), `out`, `format`, `reconnect_for` (`5m`). Returns a job id and the file |
| `log_status` | none (reads the job's output) | `job` (the latest). State (running / done / failed), records so far, the last status line, the file, and when ended its summary |
| `log_stop` | `stop` | `job` (the latest). Stops cleanly and returns the summary: file, records, duration, min / max / mean per unit, link gaps. Without `job` it also stops a `log` or `live` started elsewhere (a terminal, another client); `already_ended: true` = it had ended before |

`status`, `read`, `live`, `memory`, `query`, `selftest`, `log_status` are marked read-only. All durations are strings
(`"30s"`), all numbers are SI.

**Background logging.** `log_start` returns at once (after the first reading, at most 30 s) with a job id; the log
keeps running when the client disconnects, and its output and metadata are kept in `state/mcp_jobs/<job>/`, so a
restarted server, or another client's server, still sees it. Follow it with `log_status`, end it with `log_stop`
(or it ends after `duration`, or when the link stays lost longer than `reconnect_for`). The file holds the data;
the tool results are summaries and never hundreds of readings.

**BUSY rules.** The adapter accepts one connection, so tools run one at a time, and **while a background log runs,
every other meter tool (`status`, `read`, `live`, `memory`, `press`, `query`, ...) returns `BUSY`** until `log_stop`
(`log_status` and `log_stop` still work). `BUSY` also means another open289 (a terminal running `live`/`log`, another client) holds the adapter. Fluke Connect on a phone shows up as `NOT_FOUND` (the adapter stops advertising). `METER_BUSY` is different: a menu or result page is open on the meter; close it
(`press` `F4`) and retry. Safety for agents: `press` changes the meter (HOLD, MIN MAX, range); `ONOFF` needs
`allow_off: true` and should be used only when the user asked for it: it switches the meter off for good, and the
adapter goes to sleep with it.

CLI commands with no MCP tool: `scan` (the server finds the adapter itself), `stop` (used internally by `log_stop`),
and `raw --unsafe` (deliberately not exposed). An agent that has a shell can use the CLI for all of them.

## Data folders

open289 keeps its files in one folder with two subfolders:

- `state/`: the adapter lock (`open289.lock`), the stop request (`stop.request`), MCP background jobs
  (`mcp_jobs/`) and the virtual meter's state (`sim_state.json`, `sim_writes.jsonl`).
- `data/`: the default place of logs (`log_<time>.csv`) and recording downloads (`recording<INDEX>_<time>.csv`).
  `--out` writes anywhere else; relative paths are relative to the current folder.

Where the folder is: `OPEN289_HOME` if set; else, in a source checkout (a `pyproject.toml` next to the package), the
checkout itself; else, for an installed package, `%LOCALAPPDATA%\open289` on Windows and `~/.local/share/open289`
elsewhere, so nothing is written into `site-packages`. The MCP server's child processes use the same folder.

## Development

Layout:

- `open289/` package: `cli.py` (commands, output), `transport.py` (backend selection real or virtual, discovery,
  connection), `adapter.py` (IR3000 service client, pass-through, the opcode allowlist, query filter and the 87-byte
  limit), `meter.py` (the meter's remote commands and the binary record decoders), `reading.py` (live notification
  decoder), `records.py` (JSON-ready dictionaries), `logger.py` (long-running log with reconnect), `lock.py` (single
  client lock), `errors.py`, `paths.py`, `sim.py` (virtual meter) and `mcp_server.py` (MCP server).
- `tests/` offline tests against the virtual meter: decoders (against real captures), the transport and its safety
  rules, the CLI, the MCP server (JSON-RPC, jobs, BUSY rules) and the simulator itself.
- `docs/PROTOCOL.md` (and `PROTOCOL.zh-CN.md`): the protocol, with evidence levels.
- `tools/` diagnostic scripts for the hardware.
- `.github/workflows/`: `ci.yml` and `release.yml`.

```
python -m pip install -e ".[test]"
OPEN289_SIM=1 OPEN289_NO_BLE=1 python -m pytest tests -m "not hw" -q
```

The tests set the simulator environment themselves (`tests/conftest.py`: a private `OPEN289_HOME` per test,
`OPEN289_SIM=1`, `OPEN289_NO_BLE=1`, a fast simulator), so a test can never reach Bluetooth. Tests marked `hw` need a
real adapter and meter and are skipped by default (`pytest -m hw`).

CI (`ci.yml`, on pushes to main, pull requests and by hand): the tests on Windows and Linux with Python 3.10, 3.12 and
3.14, all against the virtual meter; a check that tracked files carry no local paths; a package build with `twine
check --strict` and a smoke test of the built wheel (`open289 info`, `open289 live -n 5` on the virtual meter).

Release (`release.yml`): push a tag `vX.Y.Z` equal to `open289.__version__` (`v0.1.0` for 0.1.0). The workflow runs the
CI checks, refuses a tag that does not match the version, builds, creates a GitHub release with the files, and
publishes to PyPI by **trusted publishing** (no token). One-time setup on pypi.org: add a (pending) trusted
publisher for the project `open289` with owner `FrostFlops`, repository `open289`, workflow `release.yml` and
environment `pypi`, and create the environment `pypi` in the repository's settings.

## Protocol

How the adapter and the meter talk (GATT table, the CNX ID write that arms the control point, the pass-through
framing, buffer limits, the 16-byte live packet layout, the binary saved-data records, key behaviour), with every
statement tagged VERIFIED (seen on the hardware) or DERIVED (not exercised): [docs/PROTOCOL.md](docs/PROTOCOL.md)
([中文](docs/PROTOCOL.zh-CN.md)). Observed live encodings per function: section 4.8 of [docs/PROTOCOL.md](docs/PROTOCOL.md).

## What is NOT here

Fluke's application (Fluke Connect), anything decompiled from it, and the private reverse-engineering workspace are
kept out of this repository. The firmware images and updater of the adapter are not used.

## Safety

- The tool never writes the adapter's firmware-update characteristics (the MSP updater and the radio-image updater;
  writing the wrong value there erases flash) and never sends the adapter opcodes `0x90` to `0x94` (the 1550 / 1555
  insulation-test commands, which switch a high voltage).
- The adapter's out-buffer is capped at **87 bytes** per send: larger sends got no reply or hung the adapter until it
  was power-cycled (373 bytes and more), so open289 refuses them.
- `raw` is query-only (`ID`, `IM`, `Q*`) unless `--unsafe`. `MP` can change settings, `CSD` erases saved data, `RI`
  resets the meter, `OFF` switches it off.
- `press ONOFF` needs `--allow-off`, and the meter asks for confirmation (`F1`). **OFF and ONOFF cannot be undone
  remotely:** neither the meter nor the adapter can be switched on again from the PC.
- `press` otherwise changes meter state (HOLD, MIN MAX, range, saving a measurement on the save page): it is not
  read-only, and an agent should only use it on request.
- The adapter accepts **one** Bluetooth connection: close Fluke Connect on phones. open289 holds a lock so that only
  one open289 process connects at a time.
- This is a measurement reader, not a safety device. Respect the meter's category rating and its own safety rules;
  open289 does not make a measurement safer.

## License

GPL-3.0-only (see `LICENSE`). Fluke, Fluke Connect and IR3000 are trademarks of Fluke Corporation; this project is
not affiliated with Fluke Corporation.
