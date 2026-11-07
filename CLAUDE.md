# CLAUDE.md

Guide for an AI agent or developer changing open289. Read the invariants section before touching `adapter.py`, `transport.py`, `meter.py` or anything that writes to Bluetooth.

## What this is

open289 is a GPL-3.0 Python package (`pip install open289`, commands `open289` and `open289-mcp`) with a CLI, a Python library and an MCP server for the Fluke 287/289 multimeter. It talks to the meter through a Fluke IR3000 FC Bluetooth LE adapter, using bleak, without the Fluke Connect app. It is made for AI agents: every command prints JSON and has a stable exit code, and the MCP server wraps the CLI. Hardware was verified on 2026-11-06 with IR3000 FC radio firmware 01.00.01, MSP 01.00.15 and a Fluke 289 firmware V1.71 on Windows 11 (Intel Bluetooth). Only a 289 was tested; the 287 should work and is untested. CI runs the tests on Windows and Linux against the virtual meter only; Linux and macOS were never tried with real hardware (macOS is not in CI). The sister project is openm454 (same author, same style).

## Layout

| Path | Responsibility |
|---|---|
| `open289/__init__.py` | `__version__` (single source of the version; `pyproject.toml` reads it) |
| `open289/__main__.py` | `python -m open289` entry point |
| `open289/cli.py` | argparse commands (`scan`, `info`, `read`, `live`, `log`, `memory`, `recording`, `press`, `raw`, `selftest`, `stop`), JSON/text emit, error to exit-code mapping, per-command wall-clock limit |
| `open289/transport.py` | backend selection (real bleak or simulator), scan, `find_adapter`, `Link` (connected adapter + meter, one per command), GATT UUID constants for the reading and Device Information characteristics |
| `open289/adapter.py` | `Adapter`: IR3000 FC AdapterLogging service client (control point, buffer characteristic, CNX ID arming), the pass-through, opcode allow/forbid lists, out-buffer accounting |
| `open289/meter.py` | `Meter`: the Fluke 287/289 serial protocol over the pass-through (ASCII queries, binary QDDB/QSMR/QMMSI/QPSI/QRSI/QSRR decoders, PRESS keys, enumeration tables) |
| `open289/reading.py` | decoder for the 8-byte live reading record (notifications of characteristic B698290F, 8 or 16 bytes) |
| `open289/records.py` | JSON-ready dicts for readings, display snapshots and saved items; shared by CLI, logger and MCP |
| `open289/logger.py` | long-running `log`: CSV or JSONL, `--every`, snapshots, reconnect after link loss; the stop-request file used by `live`, `read --every` and `log` |
| `open289/lock.py` | single-process lock (the adapter accepts one connection); msvcrt on Windows, flock elsewhere |
| `open289/paths.py` | `home()`, `state_dir()`, `data_dir()`; `OPEN289_HOME`, else the checkout, else a per-user folder |
| `open289/errors.py` | `Open289Error` with a stable `code`, and `EXIT_CODES` |
| `open289/mcp_server.py` | MCP server (stdio JSON-RPC): tool schemas, `INSTRUCTIONS`, background log jobs; runs CLI children |
| `open289/sim.py` | virtual IR3000 FC + Fluke 289 (drop-in for the bleak client and scanner), plus `python -m open289.sim reset|show|power-cycle|set|audit` |
| `tests/` | pytest suite (`test_cli`, `test_decoders`, `test_mcp`, `test_sim`, `test_transport`), `conftest.py` isolation |
| `tools/` | real-hardware investigation scripts (`probe_clear.py`, `gatt_dump.py`, `analyze_monitor.py`), not part of the package |
| `docs/PROTOCOL.md` (+ `.zh-CN.md`) | the protocol reference with evidence levels |
| `.github/workflows/` | `ci.yml` (tests, package checks), `release.yml` (tag to PyPI) |
| `re/`, `notes/`, `logs/` | local only, untracked (see invariants); some docstrings point into `notes/` |
| `state/`, `data/` | runtime data in a checkout (locks, stop request, simulator state, jobs, logs); untracked |

## Architecture

```
CLI command (cli.py)
  -> transport.Link            connect (3 attempts), owns client, adapter, meter
       -> adapter.Adapter      pass-through: APPEND (0x8D) / SEND (0x8E) / read buffer (0x82)
            -> meter.Meter     289 protocol: ID, QDDB, QSRR ..., binary decoders, PRESS
```

- Each command that talks to the meter (all but `scan` and `stop`, see `NEEDS_LOCK`) takes `AdapterLock`, opens one `Link`, does its work and releases. Commands other than the streaming ones run under `wall()` (default 120 s, 600 s for `memory`).
- Live path: `live` and `log` subscribe to characteristic B698290F (`read --every` repeats QDDB pass-through queries instead); `reading.py` decodes each notification and `records.live_dict` builds the JSON. About 4 notifications per second. The logger adds optional QDDB snapshots through the pass-through, reconnects after link loss within `--reconnect-for`, and ends at `--duration`, on `open289 stop` (a file in `state/` that the loop polls), or on Ctrl+C.
- Pass-through costs about 1.3 s per command on hardware (about 0.6 s of it is the SEND latency). Trend recordings download at 0.44 s per record, up to 8 records per round trip, as many as fit in 87 bytes (`passthrough_batch`, 8 x QSRR = 87 bytes out, 1208 bytes back).
- Output contract: `cli.emit` prints one JSON document (or JSONL for streaming) on stdout; `--text` gives a short human form. Errors print `{"ok": false, "cmd": ..., "error": {"code", "message", "hint"}}` and exit with `EXIT_CODES[code]`.
- `mcp_server.py` never talks to Bluetooth itself. Every tool runs the CLI in a child process (`Cli.run`), with `OPEN289_HOME` pinned so server and children share `state/`. Background logs are detached children tracked as jobs under `state/mcp_jobs/` (`meta.json`, `stdout.jsonl`, `stderr.txt`), so they survive a server restart. `_take_device` / `_release_device` arbitrate the single adapter between jobs and foreground tools.
- Backend selection: `transport.backend()` returns bleak, or `sim.SimBleakClient` / `sim.SimBleakScanner` when `--sim` or `OPEN289_SIM=1` is set. `OPEN289_NO_BLE=1` makes the real backend refuse to start. Everything above the transport is identical for both, so the simulator exercises the real adapter and meter code.

## Invariants that must never break

Safety (hardware can be damaged or hung):

1. Never write the firmware-update characteristics B6982911 to B6982914, or anything in service B6981804. The updater reuses adapter opcode values; `0x80` on B6982911 erases the MSP flash. The adapter's own opcodes go only to the control point B6982918. UUIDs are hard-coded for this reason.
2. Never send adapter opcodes `0x90` to `0x94` (`adapter.FORBIDDEN`, high-voltage insulation test control on 1550/1555 testers). `Adapter.command` must keep rejecting them. Opcodes outside `ALLOWED` stay rejected; `0x84` to `0x8C` only by explicit opt-in.
3. The CNX ID (B6982902) write of `01` must happen in `Adapter.start()` before notifications are enabled or any command is sent. Before it, the control point never answers.
4. Control-point writes are at most 18 bytes (longer writes are rejected by the adapter). `_send_raw` therefore appends the meter command 17 bytes at a time (opcode 0x8D plus 17 bytes), then sends once; a command longer than 87 bytes is refused before anything is written.
5. The out-buffer holds at most 87 bytes per SEND (`adapter.MAX_OUT`). 181 bytes got no reply and 373 or more hung the adapter until power-cycled. `pending_out` counts bytes appended since the last successful send, before the write (a lost reply may still have landed), and `_send_raw` refuses a command that could push possible leftovers over 87. Whether CLEAR (0x81) empties the buffer is unknown, so do not rely on it to reset the count.
6. `raw` and the pass-through are query-only (`SAFE_METER_CMD`: `ID`, `IM`, `Q...`) unless `--unsafe`. `MP`, `CSD` (clears memory), `RI` (reset) and `OFF` change or erase state. Command text must be ASCII.
7. `press ONOFF` needs `--allow-off`, and the meter still asks for confirmation (F1). OFF and ONOFF cannot be undone remotely. Keep the guard in the CLI and in the MCP tool schema.
8. One connection only: the adapter accepts a single central. `AdapterLock` must wrap every command that opens a `Link`.

Public API:

9. The JSON output of the CLI and the MCP tool results are a public API, as are the exit codes in `errors.EXIT_CODES` and the error `code` strings. Adding a field is fine. Renaming or removing a field, changing a unit (values are SI base units) or reusing an exit code is a breaking change and needs a major or minor version bump and a README note. `tests/test_cli.py` and `tests/test_mcp.py` pin the contract.
10. Do not change the meaning of `STOPPED` (exit 0) or the 5/6/7 groupings: 5 means the meter or adapter state (busy menu, detached, no data), 6 means link or protocol failure, 7 means refused for safety.

Repository hygiene:

11. No absolute local paths and no real device identifiers in any tracked file, including this one and test fixtures. The CI job `package` greps tracked files for drive-letter paths and fails. Examples use the address `FA:28:90:00:00:01` and serial `12345678`; never put a real adapter address or meter serial anywhere.
12. `re/` (Fluke's APK, decompiled sources, jadx), `notes/` (working notes quoting decompiled code) and `logs/` (raw captures containing the address and serial) stay untracked and listed in `.gitignore`. Do not copy Fluke's code or decompiled text into tracked files; describe behaviour in your own words, and mark the evidence level in `docs/PROTOCOL.md`.
13. No emojis in code, docs or commit messages.

## Testing

```
python -m pip install -e ".[test]"
python -m pytest tests -m "not hw" -q
```

- `tests/conftest.py` has an autouse fixture: every test gets a fresh `OPEN289_HOME` (tmp_path), `OPEN289_SIM=1`, `OPEN289_NO_BLE=1`, a fast simulator (`OPEN289_SIM_RATE=40`, `OPEN289_SIM_DELAY=0.01`). A test must never reach a real adapter; do not undo this isolation. If you add a new environment variable that changes behaviour, set it in the fixture.
- The simulator models measured behaviour: GATT table, arming by the CNX ID, the 18-byte write limit, pass-through framing, the out-buffer overflow and hang, binary record layouts, 16-byte live packets, front-panel key behaviour, single-connection semantics across processes (state, `.link` and `.ram` sidecars next to the state file), and an audit log of every GATT write (`python -m open289.sim audit`). Tests can assert that a forbidden write never happened by reading the audit log.
- Try the tool by hand without hardware: `python -m open289 --sim info`, `read`, `live -n 3`. Reset the virtual meter with `python -m open289.sim reset`; `show`, `power-cycle` and `set key=value` inspect and change it.
- When the hardware shows something new: first record it in `docs/PROTOCOL.md` with its evidence level, then teach `sim.py` to reproduce it, then add a test that fails without the fix. Anything in `sim.py` that was never observed on hardware is marked GUESS; keep that marking honest and never present a guess as verified.
- Hardware tests carry `@pytest.mark.hw` and are excluded by default (`addopts = "-m 'not hw'"`); run them deliberately with `pytest -m hw` with an IR3000 FC and a meter present. CI never runs them. There are none yet.
- Tools for real-hardware investigation (all need a real address, none belong in CI):
  - `tools/gatt_dump.py <address>`: read-only dump of the GATT table; skips the firmware-update service and the characteristics with side effects, never writes.
  - `tools/probe_clear.py <address>`: read-only probe of whether CLEAR empties the out-buffer (at most 6 bytes out); settles the first open question below. Not yet run.
  - `tools/analyze_monitor.py capture.jsonl [report.txt]`: cross-checks live packets against QDDB snapshots from a `log --format jsonl --snapshot-every 0.5s` capture.
- CI matrix: Windows and Linux, Python 3.10, 3.12 and 3.14, plus a package job (path grep, build, `twine check --strict`, wheel smoke test against the virtual meter). Keep the code at Python 3.10 compatibility.

## Protocol reference and open questions

`docs/PROTOCOL.md` is the reference (the Chinese edition is `docs/PROTOCOL.zh-CN.md`; keep both in step). It tags every statement VERIFIED (seen on the hardware) or DERIVED (from the app, public sources or analogy; not exercised); sections cover the GATT table, the connection sequence, the live 8-byte record, adapter pass-through, the 287/289 serial protocol, timing and a safety summary. Change a protocol claim only with hardware evidence, and update the level.

Open questions (section 9 of the document):

- Does CLEAR (`0x81`) empty the out-buffer? Not verified; `pending_out` guards against it. `tools/probe_clear.py` is meant to settle it.
- The real out-buffer capacity: 87 works, 181 fails, the limit lies in between.
- Maximum reply size and read-chunk size beyond the 1208 bytes verified.
- Parameters and reply formats of opcodes `0x84` to `0x8C` (never used by the official app).
- Effect of CANCEL (`0x83`); what IDLE and INCORRECT_MODEL look like.
- Other CNX ID values; what B6982903, B6982904, B6982905 and B698290E do (untouched).
- Whether live notifications need the CNX ID.
- Detached adapter behaviour (`FLUKE PC3000` model string) is known from the app only.
- 287 behaviour; `QEMAP`, `IM`, `MP`, `QLCDBM`, `LEDT` against real hardware.

## Release process

1. Make sure CI is green on main and `docs/PROTOCOL.md`, the README and the MCP `INSTRUCTIONS` match the code.
2. Bump `open289.__version__` in `open289/__init__.py` (the only place; `pyproject.toml` reads it dynamically).
3. Commit, then tag `vX.Y.Z` equal to the version and push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. `release.yml` runs: `checks` (the full CI workflow) -> `build` (fails if the tag differs from `__version__`; builds sdist and wheel; `twine check --strict`) -> `github-release` (creates the GitHub release with the artifacts and generated notes) -> `pypi` (trusted publishing, no token, environment `pypi`).
5. First release only: on pypi.org add a pending trusted publisher for project `open289`, owner `FrostFlops`, repository `open289`, workflow `release.yml`, environment `pypi`.
6. Verify with `pip install open289` in a clean environment and `open289 --sim info`.
