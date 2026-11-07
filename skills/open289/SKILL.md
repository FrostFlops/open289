---
name: open289
description: Read the Fluke 287/289 multimeter attached to this PC over a Fluke IR3000 FC Bluetooth LE adapter (voltage, current, resistance, continuity, capacitance, frequency, temperature, diode test; the display with its modes and MIN/MAX/AVG; live readings about 4 per second; long logs to CSV/JSONL; the meter's saved measurements and trend recordings; front-panel key presses). Use whenever a task needs a reading from the multimeter or a change of its display state ("read the meter", "what does the 289 show", "measure with the Fluke", "what is the voltage on the meter", "watch the current", "log the voltage for an hour", "download the recording from the meter", "what is saved in the meter", "press HOLD on the meter", "switch the meter to MIN MAX", "is the meter connected", "is the adapter awake").
---

# Fluke 287/289 -- `open289` (CLI + MCP server, GPL-3.0)

Python package that talks to a Fluke 287/289 through the IR3000 FC adapter clipped onto the meter's IR port. Written
for AI agents: every command prints JSON. Protocol and internals: `docs/PROTOCOL.md` in the repository. Do not
reverse-engineer again; read it if you need to change the code.

Run it (the working directory does not matter for an installed package):
- installed (`pip install open289`): `open289 <cmd>` and the MCP server `open289-mcp`
  (register: `claude mcp add open289 -- open289-mcp`; give the full path of `open289-mcp` if pip's Scripts folder is
  not on PATH);
- from a checkout: `python -m open289 <cmd>`; PowerShell `.\open289.cmd <cmd>`; Bash `./open289.sh <cmd>`.

Every command prints JSON on stdout. One-shot commands (`info`, `read`, `memory`, `press`, `raw`, `selftest`, `scan`,
`stop`) print ONE object; `live`, `read --every`, `log` and `recording` print one object per line (readings or events)
and end with `"event": "done"` and a summary. `--text` (global option, before the command) prints a human-readable
form instead; use JSON when you parse. Global options go before the command: `--address/-a ADDR` (default: first
adapter found), `--sim` (virtual meter), `--max-wall S` (limit for one-shot commands, default 120, `memory` 600),
`--verbose`.
Errors: `{"ok": false, "cmd": ..., "error": {"code", "message", "hint"}}` with a non-zero exit code. Exit codes:
0 ok (also a clean stop), 1 internal error or self-test failed, 2 bad argument, 3 not found, 4 busy, 5 meter busy /
detached / no data, 6 timeout or link or protocol error, 7 refused as unsafe. Read `error.hint`: it says what to do.
Durations are `30s`, `5m`, `1.5h`, `2d` or `forever`. Every command first waits for the adapter to advertise: usually a few seconds, up to ~20 s right after the previous command disconnected (hardware). `log --duration` counts from the first connection (`connect_s` in the `connected` line and the summary), so the file holds the full duration.

## Before you start
- The adapter must be awake: press its button for more than 1 s until the LED flashes green. It goes to sleep with the meter
  when the meter is switched off. `NOT_FOUND` (exit 3)
  = it is not advertising: ask the user to wake it.
- The adapter accepts ONE Bluetooth connection. Fluke Connect (or any other app) on a phone must be closed, and only
  one open289 command runs at a time (a second one gets `BUSY`, exit 4). A running `log` or `live` holds the adapter.
- Never kill a running open289 process (TaskStop, taskkill): the link is then dropped without a clean disconnect and the
  adapter may need a button press to advertise again. End `live`,
  `read --every` and `log` with `open289 stop` (clean within about a second; the data so far is kept).
- Meter on, adapter clipped onto its IR port. `DETACHED` (exit 5) = the adapter sees no powered meter.
- First command in a session: `open289 info` (adapter, meter model, firmware, serial, batteries, memory level, meter
  clock, saved counts) or `open289 selftest` (9 read-only checks, see below).
- `METER_BUSY` (exit 5): the meter has a menu, setup page, MIN MAX result page or save dialog open, and it refuses
  measurement queries. Do NOT guess key presses: ask the user what the meter screen shows, then close it on the meter
  or with `press` (on the MIN MAX result page `MINMAX` or `F4` closes it).
- Turning the meter off cannot be undone remotely (nothing can switch the meter or the adapter back on). Never
  `press ONOFF` or `raw --unsafe OFF` without the user's explicit OK.
- Verified on hardware (2026-11-06): IR3000 FC radio fw 01.00.01 / MSP 01.00.15, Fluke 289 fw V1.71, Windows 11 (Intel
  Bluetooth). Only a 289 was tested (a 287 should work, untested). CI runs on Windows and Linux against the virtual
  meter only; macOS is not in CI. bleak supports Linux and macOS, but real hardware there is untested.

## Two ways to drive the meter
Use the MCP tools when they are available in the session (tools named `status`, `read`, `live` ... from the `open289`
server); use the CLI via Bash for everything else. Each MCP tool runs one CLI command in a child process (connect,
work, release per call) and returns a compact summary; errors carry the same codes and hints (`error.code`).

| MCP tool | CLI | notes |
|---|---|---|
| `status` | `info` | adapter address, firmware, battery; meter model, firmware, serial, battery, memory level, clock; saved counts |
| `read` | `read` | one display snapshot (primary, readings, modes) |
| `live` | `live` | summary instead of lines: count, rate, min/max/mean per unit, last 5 readings; `count` (1-400, default 20) and/or `seconds` (0.1-120) |
| `memory` | `memory` | saved items; slow (every item is one query, up to 600 s) |
| `download_recording` | `recording INDEX` | `index`, `out`, `format`, `start`, `limit`; a failed result has a `resume` block (index, start, out) |
| `press` | `press` | `keys` (list, max 20, case-insensitive), `held`, `allow_off`; returns the display state after each key |
| `query` | `raw` | read-only: ID, IM, Q* only; max 10 commands; no `unsafe` option |
| `selftest` | `selftest` | read-only health check |
| `log_start` / `log_status` / `log_stop` | `log` / (job output) / `stop` | background job (below) |

Notes:
- All tools take `max_wall` (seconds, 1-86400) except the log job tools. Tools run one at a time; a call that has to
  wait for the previous one for longer than its `max_wall` returns `BUSY`.
- A background log (`log_start`) holds the adapter: every other meter tool returns `BUSY` until `log_stop`. The job lives in
  the open289 state folder, keeps running if the client disconnects, and another client's server can see and stop it.
  `log_start` defaults: `duration` 10m, `every` 0 (every packet), `reconnect_for` 5m. One log at a time. `log_stop`
  without `job` also stops a log or `live` started elsewhere; `already_ended: true` = it had finished before.
- No MCP tool, use the CLI: `scan` (find adapters, other addresses), `stop` (`log_stop` uses it), `read --every`
  (periodic snapshots; use `log --snapshot-every`), `raw --unsafe`.
- The MCP `live` never returns hundreds of lines. If you need every reading, use `log`.

## Choosing the mode
- One display snapshot (what the meter shows now, including function, range, HOLD / MIN MAX / REL modes, MIN / MAX /
  AVERAGE and secondary readings): `open289 read`. Costs one pass-through query (~1.3 s plus connect time).
- A real-time value over seconds, or checking that a signal settles: `open289 live -n 20` or `live --for 30s` (about 4
  readings/s, one JSON line each). Only the primary and secondary value; no modes and no MIN/MAX.
- Slow periodic snapshots (a reading every few seconds to minutes, full display each time):
  `open289 read --every 10s --for 30m` (one JSON line per snapshot; stop early with `open289 stop`).
- Anything long or saved to a file: `open289 log` (CSV or JSONL; survives short link losses). For a long run write
  one row per interval to keep the file small, instead of every packet (~4/s = 14,400 rows/hour):
  - `open289 log --duration 1h --every 1s --out hour.csv` (one reading per second);
  - add `--snapshot-every 10s` when the modes or MIN/MAX/AVG matter (full display snapshots go to
    `<file>.display.jsonl` beside a CSV, or into the JSONL log itself);
  - `--every 0` (default) keeps every packet; `--status-every 30s` prints a progress line on stdout;
    `--reconnect-for 10m` keeps retrying longer after a lost link (default 5m; `0` = end the log at the first loss); `--duration forever`
    (default) runs until `open289 stop`.
  Run long logs in the background (Bash `run_in_background`, or MCP `log_start`) and follow the status lines; end with
  `open289 stop` (or `log_stop`). Tell the user the file path (`file` in the first `started` line and the summary).
- What the meter recorded on its own (trend recordings, saved measurements, MIN MAX and peak sessions): `memory`, then
  `recording INDEX`.

## Saved data and recordings
- `open289 memory` lists `counts` (recordings, minmax, peak, measurements) and `items[]`: `kind`, `index` (within its
  kind), `name`, `function`, `unit`, `modes`, `readings[]`, `primary`; recordings add interval, number of records and
  duration. `--detail` only matters with `--text`. It queries every item (slow with much saved; allow minutes).
- `open289 recording 0 --out rec0.csv` downloads trend recording `0` (the index shown by `memory`): one row per
  record: `index,start,end,unit,primary,primary_state,minimum,minimum_state,average,average_state,maximum,maximum_state,
  samples,record_type,stable,transient`. Speed 0.44 s per record (up to 8 records per round-trip, as many as fit in 87 bytes); `--no-batch` is slower
  and only for troubleshooting. A 72-record recording takes about 30 s; a long one takes minutes, so run it in the
  background. After an interruption resume with `--start N` and the same `--out` (the error hint and the MCP
  `resume` block give N); `--limit` is an end index, not a count. `--format jsonl` for JSONL.
- Default output folder for logs and downloads: `data/` of the open289 home (the checkout, or `%LOCALAPPDATA%\open289`
  / `~/.local/share/open289` for an installed package; `OPEN289_HOME` overrides). Pass `--out` to choose a path
  and tell the user where the file is.
- Log CSV columns: `pc_time,local_time,value,unit,display,state,function,attribute,secondary_value,secondary_unit,
  secondary_display`. `pc_time` is the PC clock (epoch s), `local_time` the same in local time.

## Keys and the screen state machine
`open289 press KEY [KEY ...]` (keys: F1 F2 F3 F4 UP DOWN LEFT RIGHT HOLD MINMAX RANGE INFO BACKLIGHT ONOFF; `--held` =
long press; `--settle S` waits before reading the state back, default 1 s). It returns, per key, `ack` and the display
state afterwards. Keys press in order, so a menu path is one call, but check the state each time (`press` returns it).
- `HOLD` toggles HOLD (the reading freezes; `modes` contains `HOLD`). Press it again to release. It does not change
  what the meter measures.
- `MINMAX` starts MIN MAX AVG recording. Pressing it again stops it and opens the RESULT PAGE (F1 restart, F2 save, F4
  or MINMAX closes). While this page, any menu or a save dialog is open, queries answer `METER_BUSY`. F2 on the result
  page opens a save page (F1 add name, F2 Save, F4 Close). Saving writes into the meter's memory: only when the user
  asked.
- `RANGE`: the first press leaves autorange and keeps the current range; each further press steps it; `RANGE --held` returns to autorange.
- `BACKLIGHT` cycles off, bright, dim. `INFO` and the soft keys F1-F4 act as on the meter, depending on the screen.
- The key presses change the meter, and `read` shows the effect. Never press keys to "see what happens". Ask the user
  before anything that changes the measurement state they did not request (a key changes the meter's modes; the
  rotary dial cannot be turned remotely).
- `ONOFF` is refused unless `--allow-off` (MCP `allow_off`); the meter then shows "turning off meter will break
  communication" and F1 confirms, F4 cancels. Switching off cannot be undone remotely (see Safety).

## Raw queries (`open289 raw`, MCP `query`)
Pass-through to the meter's remote command interface; query-only unless `--unsafe` (CLI only). Allowed: `ID`, `IM`
and `Q*` queries. Useful ones:
- `ID` model, firmware, serial (`FLUKE 289,V1.71,12345678`); `IM` the meter's long identify (model, firmware, serial and more; not verified on hardware);
- `QM` the primary measurement as text: `5.0625E0,VDC,NORMAL,NONE` (value, unit, state, attribute);
- `QDDB` display data (binary, returned as hex; `read` decodes it for you); `QBL` battery; `QSLS` saved counts;
  `QMP <property>` meter property.
Example: `open289 raw ID QM` -> `{"ok":true,"cmd":"raw","results":[{"command":"ID","ack":"OK","text":"FLUKE 289,V1.71,12345678"},{"command":"QM","ack":"OK","text":"5.0625E0,VDC,NORMAL,NONE"}]}`.
Anything that is not a query (MP, CSD = clear memory, RI = reset, OFF, PRESS ...) is refused with `UNSAFE_OPERATION`
(exit 7) before it reaches the meter. Do not use `--unsafe` without the user's explicit approval; CSD erases saved
data and OFF cannot be undone. Several commands can follow in one call; each is sent and answered in turn (about 1.3 s each). Only `recording` batches QSRR queries internally (up to 8 per send). A send over 87 bytes is refused by the code: bigger ones got no reply or hung the adapter
until power-cycled. Pass-through costs about 1.3 s per command.

## Interpreting values
- Values are SI base units: volts, amperes, ohms, farads, hertz, seconds; temperature in degrees C or F as set on the meter (check `unit`; `unit` is a code such as `VDC`,
  `OHM`, `CEL` in `read`/`memory`, and the symbol form `V DC` in `live` and logs). 5 mV is `0.005`.
  `display` is the text exactly as the meter shows it (`5.0617 V DC`, `OL Ω [OPEN_CIRCUIT]`); quote it to the user,
  because it has the right digits and the unit prefix.
- `state` of a reading: `NORMAL` (value is real), `OL` / `OL_MINUS` in `read`/`memory`, `OL_RANGE` (or `OL_A2D`) in `live` and logs (overload or open: `value` is null, `display` shows
  OL), `INVALID` (`----`, dial or range changing), `BLANK` (autorange blanking), `OPEN_TC` (thermocouple open),
  `DISCHARGE`, `INACTIVE`. Use only `NORMAL` values for numbers; a burst of `INVALID` or `BLANK` while the user turns
  the dial or the range changes is normal while the dial or range changes.
- `attribute`: `OPEN_CIRCUIT` / `SHORT_CIRCUIT` / `GLITCH_CIRCUIT` for continuity, `GOOD_DIODE`, `POSITIVE_EDGE` / `NEGATIVE_EDGE` (in `read`/`memory`; `live` and logs use lowercase text: `open circuit`, `short circuit`, `glitch`, `good diode`, `positive edge`).
- `read` JSON: `function` (V_DC, V_AC, MV_DC, OHMS, CONTINUITY, CAPACITANCE, DIODE_TEST, TEMPERATURE, MA_DC...),
  `secondary_function` (NONE, HERTZ, ...), `autorange`, `range`, `unit`, `modes` (HOLD, AUTO_HOLD, MIN_MAX_AVG, REL, RECORD ...), `hazard`
  (high voltage warning), `primary`, and `readings[]` with `id`: `LIVE` (what the meter measures now), `PRIMARY` (the
  main display, which is the held value in HOLD, the relative value in REL), `SECONDARY`, `MINIMUM`, `MAXIMUM`,
  `AVERAGE`, `REL_REFERENCE`, `TEMP_OFFSET` ... Each reading: `value`, `unit`, `display`, `state`, `attribute`, `decimals`,
  `time` (meter clock text) and `ts`.
- `live` lines: `{"type":"reading","t":...,"value":5.063,"unit":"V DC","display":"5.0630 V DC","state":"NORMAL","function":"VDC"}`
  plus `secondary` (`value`, `unit`, `display`) when the meter shows a second value (frequency with volts, dBm...).
  The BLE packet carries NO mode flags and no MIN/MAX/AVG: use `read` for those. In HOLD the live primary is the
  held value; in REL it is the relative value.
- The primary and secondary swap with the function: with Hz selected on a V AC range, Hz is the primary and V AC the
  secondary (as on the display).
- Clocks: `t` and `pc_time` are PC epoch seconds. The meter's own clock (`info` `clock`, reading `time`, recording
  `start`/`end`, saved item times) is meter-local time without a time zone and can be off by a time-zone or
  daylight-saving hour (about 1 h) or drift. Do not mix it with PC time without saying so; for "when was it measured"
  with a log, use the PC time.

## Errors and what to do
| code (exit) | meaning | do |
|---|---|---|
| `INVALID_ARG` (2) | bad option or value | fix the command (`error.hint` has the format) |
| `NOT_FOUND` (3) | no IR3000 FC advertising | ask the user to press the adapter button until the LED flashes green, close Fluke Connect on phones, move closer; `open289 scan` lists adapters |
| `BUSY` (4) | another open289 process or a running log/live holds the adapter (Fluke Connect on a phone shows up as NOT_FOUND) | wait, or `open289 stop`; close Fluke Connect; do not kill processes |
| `METER_BUSY` (5) | menu / result / save page open on the meter | ask the user what the screen shows, then close it |
| `DETACHED` (5) | adapter not attached to a powered meter | ask the user: meter on, adapter clipped on |
| `NO_DATA` (5) | the meter has no data for that request (e.g. empty memory item) | not an error in the setup; report it |
| `TIMEOUT`, `BLE_ERROR`, `LINK_LOST`, `PROTOCOL_ERROR`, `WRITE_FAILED`, `METER_ERROR` (6) | link or protocol problem | retry once; then wake the adapter again (button), move closer, run `selftest`; a `log` retries for `--reconnect-for` (default 5m) after a lost link, then ends with `stopped_by: "link_lost"`; resume a recording with `--start` |
| `UNSAFE_OPERATION` (7) | refused by the safety rules | do not add `--unsafe` / `--allow-off` on your own; ask the user |
| `SELFTEST_FAILED` (MCP; the CLI prints `"ok": false` and exits 1) | a check failed | read the failed `items[]` |
Further errors from the one-shot commands: a command that runs past `--max-wall` ends with `TIMEOUT` and the
adapter is disconnected cleanly.

## Self-test
`open289 selftest` (MCP `selftest`) is read-only and never changes the meter. It checks, in order: S01 adapter
advertising, S02 connect and arm the control point, S03 meter attached, S04 adapter firmware answers, S05 pass-through
ID, S06 display snapshot decodes, S07 a live reading within 5 s, S08 adapter battery (>= 20 %), S09 meter battery. About
a few seconds. Result: `passed`, `failed`, `items[]` (`id`, `name`, `pass`, `measured`, `expected`). A failure of S01-S03
is the adapter or the link, S06 usually METER_BUSY (a page is open), S08/S09 batteries. Run it whenever something looks off.

## Try it without hardware
`open289 --sim <cmd>` (or env `OPEN289_SIM=1`) runs everything against a virtual IR3000 FC and Fluke 289 (address
`FA:28:90:00:00:01`, serial `12345678`, a V DC signal near 5 V, two saved measurements, one peak session, one 72-record
trend recording). It models the GATT table, the out-buffer overflow and hang (open289 refuses sends over 87 bytes itself), the meter's key behaviour (HOLD, MIN MAX
and its result page, menus giving `METER_BUSY`, power off), the adapter's single connection and the ~4 Hz live packets.
Its state is a JSON file shared between commands; control it with `python -m open289.sim reset | show | power-cycle |
set key=value ... | audit [--clear]` (for example reset after a `press HOLD`, or power-cycle to try the
off / reconnect paths). Use `--sim` to test a command line or a parser before touching the real meter, and use
`OPEN289_HOME=<folder>` so the test files and state do not mix with real data. The simulator is not a measurement.

## Safety (never bypass)
- Never write the adapter's firmware-update characteristics and never send adapter opcodes 0x90-0x94 (high-voltage
  insulation test); the code does neither. Do not change that.
- `raw` is query-only; `--unsafe` allows MP, CSD (clears memory), RI (reset), OFF and so on: only with the user's
  explicit approval.
- `press ONOFF` needs `--allow-off`; the meter asks for confirmation (F1). Neither the meter nor the adapter can be
  switched back on remotely. Never do this on your own.
- The out-buffer is capped at 87 bytes per send (larger sends hung the adapter); do not work around it.
- Do not run Fluke Connect and open289 at the same time; do not kill a running open289 process, use `open289 stop`.
- The meter itself is a measuring instrument: the leads, the range and the fuse are the user's business. This tool
  reads and presses keys; it cannot turn the dial or move the leads. Ask the user before suggesting a measurement that
  could be dangerous (mains, high energy), and respect the `hazard` field of `read`.
