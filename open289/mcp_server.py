"""MCP server for the Fluke 287/289 multimeter over a Fluke IR3000 FC adapter: Model Context Protocol over stdio,
standard library only.

A thin shell over the open289 CLI. Every tool runs one CLI command in a child process (the open289 package next to
this server, with its open289 folder), so the adapter is connected and released per call (it accepts one Bluetooth
connection), a hang or crash never takes the server down, and the CLI's safety rules, adapter lock and error codes
apply unchanged. A long log runs as a background job; its output and metadata stay in state/mcp_jobs/<job>/, so a
restarted server (or another client's server) still sees it and can stop it, and the log keeps running when the
client disconnects. A live past its time limit, or cancelled, is stopped cleanly (stop request, then a grace period)
so the adapter is disconnected properly.

Results are compact summaries (live: statistics and the last readings, never hundreds of lines); the files written by
a log or a recording download are named in the result. Errors carry a stable code, a message and a hint: NOT_FOUND
(adapter not advertising), BUSY (another program or a running log holds the adapter), METER_BUSY (a menu or result
page is open on the meter), DETACHED (no powered meter on the adapter), TIMEOUT, UNSAFE_OPERATION, INVALID_ARG ...
(open289/errors.py, README).

Run:          python -m open289.mcp_server      (or the open289-mcp script)
Claude Code:  claude mcp add open289 -- python -m open289.mcp_server
"""
import ctypes
import itertools
import json
import math
import os
import re
import subprocess
import sys
import threading
import time

from . import __version__
from .paths import PKG_PARENT, home, state_dir


def _wait_adapter_free(timeout=10.0):
    """Wait until no open289 process holds the adapter lock (True), or the timeout passes (False).

    A log prints its summary before it disconnects and releases the lock; waiting here means the call right after
    log_stop does not get BUSY from the log that is still shutting down."""
    from .errors import Open289Error
    from .lock import AdapterLock
    t_end = time.time() + timeout
    while True:
        try:
            AdapterLock().acquire().release()
            return True
        except Open289Error:
            if time.time() >= t_end:
                return False
            time.sleep(0.1)

PROTOCOL_VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
STRUCTURED_SINCE = '2025-06-18'      # structuredContent in tool results
DEFAULT_WALL_S = 120.0               # per-call limit of a one-shot tool
MEMORY_WALL_S = 600.0                # the CLI's own default for memory (every saved item is one query)
RECORDING_WALL_S = 900.0             # a recording download takes a while per record over Bluetooth
JOB_START_WAIT_S = 30.0              # log_start waits this long for the log to start (and its first reading)
STOP_WAIT_S = 20.0                   # log_stop waits this long for the summary
STOP_GRACE_S = 5.0                   # a live past max_wall / cancelled: time to stop and disconnect cleanly
POLL_S = 0.2                         # how often a running child is checked for max_wall / cancel
STATUS_EVERY = '5s'                  # status line interval of a background log
STOPPABLE = ('live',)                # CLI commands that end cleanly on a stop request (open289 stop)
LIVE_MAX_COUNT = 400
LIVE_MIN_SECONDS = 0.1
LIVE_MAX_SECONDS = 120
LAST_READINGS = 5                    # readings listed in a live summary
QUERY_MAX = 10
GAPS_LISTED = 10
JOBS_LISTED = 10                     # job ids named in an 'unknown job' hint

# open289/cli.py PRESS_KEYS, parse_duration and open289/adapter.py SAFE_METER_CMD, repeated here so the server imports
# no Bluetooth stack (tests/test_mcp.py checks that they match)
PRESS_KEYS = ('F1', 'F2', 'F3', 'F4', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'HOLD', 'MINMAX', 'RANGE',
              'INFO', 'BACKLIGHT', 'ONOFF')
SAFE_QUERY = re.compile(r'(ID|IM|Q[A-Z]{1,10})( [0-9A-Za-z_,]*)?', re.I | re.A)
FOREVER = ('forever', 'inf', 'until-stop')
DURATION = re.compile(r'([0-9]*\.?[0-9]+)\s*(ms|s|m|min|h|d)?')
DURATION_UNITS = {'ms': 0.001, 's': 1, None: 1, 'm': 60, 'min': 60, 'h': 3600, 'd': 86400}

# The child imports the open289 package next to this server (never a copy in the client's working directory, which
# `python -m` would put first on sys.path) and uses the same state folder (adapter lock, stop requests, mcp_jobs).
BOOT = ('import sys; sys.path[:] = [%r] + [p for p in sys.path if p not in ("", ".")]; '
        'from open289.cli import main; sys.exit(main())' % PKG_PARENT)

INSTRUCTIONS = """Read a Fluke 287/289 multimeter through a Fluke IR3000 FC Bluetooth adapter clipped onto its IR port.
Start with `status` (adapter and meter identity, batteries, saved items). `read` returns what the display shows now
(function, range, modes such as HOLD or MIN MAX, the primary value and every other reading); `live` collects real-time
readings (about 4/s) and returns their statistics and the last few; for minutes or hours use `log_start`, then
`log_status` and `log_stop` (a CSV/JSONL file written in the background). `memory` lists saved measurements, MIN MAX
and peak sessions and trend recordings; `download_recording` saves one recording to a file. `press` simulates
front-panel keys, `query` sends read-only remote commands (ID, IM, Q*), `selftest` checks adapter, link and meter.
Values are in SI base units (V, A, ohm, F, Hz, s), `display` is the text the meter shows.
The adapter accepts ONE connection: tools run one at a time, and while a background log runs every other meter tool
returns BUSY until `log_stop` (BUSY also when another program, such as Fluke Connect or another open289, holds the
adapter). METER_BUSY means a menu, setup or result page is open on the meter: close it on the meter (or with `press`)
and retry. NOT_FOUND = the adapter is not advertising (press its button until the LED flashes green); DETACHED = no
powered meter on the adapter. Safety: `press` changes the meter's state (HOLD, MIN MAX, range ...); ONOFF needs
allow_off=true and switches the meter off for good (it cannot be switched on remotely, and the adapter sleeps too).
`query` is read-only: anything that could change or erase meter state is refused."""


def _schema(props, required=()):
    return {'type': 'object', 'properties': props, 'required': list(required), 'additionalProperties': False}


def _wall(default):
    return {'type': 'number', 'minimum': 1, 'maximum': 86400,
            'description': 'time limit for the whole call in seconds (default %g); past it the command is stopped '
                           'and TIMEOUT is returned' % default}


FORMAT = {'type': 'string', 'enum': ['csv', 'jsonl'], 'description': 'file format (default: from the out extension, '
                                                                     'else csv)'}
JOB = {'type': 'string', 'description': 'job id from log_start (default: the latest log)'}

TOOLS = [
    {'name': 'status', 'title': 'Adapter and meter status',
     'description': 'Is the IR3000 FC adapter reachable and a Fluke 287/289 attached and on? Adapter address, '
                    'firmware and battery %; meter model, firmware, serial, battery, memory level and clock; the '
                    'number of saved items. meter.attached false = the adapter is not on a powered meter. NOT_FOUND = '
                    'the adapter is not advertising (press its button until the LED flashes green; close Fluke '
                    'Connect on any phone).',
     'inputSchema': _schema({'max_wall': _wall(DEFAULT_WALL_S)}), 'annotations': {'readOnlyHint': True}},
    {'name': 'read', 'title': 'Read the display',
     'description': 'One snapshot of the meter display: function (V_DC, OHMS ...), secondary function, range and '
                    'autorange, modes (HOLD, MIN_MAX_AVG, REL, RECORD ...), the primary reading and every other '
                    'reading on screen (MINIMUM, MAXIMUM, AVERAGE, SECONDARY, REL_REFERENCE ...). value is in SI '
                    'base units, display is the text the meter shows. METER_BUSY = a menu or result page is open.',
     'inputSchema': _schema({'max_wall': _wall(DEFAULT_WALL_S)}), 'annotations': {'readOnlyHint': True}},
    {'name': 'live', 'title': 'Real-time readings',
     'description': 'Collect real-time readings from the adapter (about 4 per second) for `count` readings or '
                    '`seconds`, and return a summary instead of the readings: count, rate, first/last time, '
                    'min/max/mean of the primary value per unit, the function(s) and unit(s) seen and the last 5 '
                    'readings. Default: 20 readings. For longer runs or a file use log_start.',
     'inputSchema': _schema({
         'count': {'type': 'integer', 'minimum': 1, 'maximum': LIVE_MAX_COUNT, 'default': 20,
                   'description': 'stop after this many readings (1..%d)' % LIVE_MAX_COUNT},
         'seconds': {'type': 'number', 'minimum': LIVE_MIN_SECONDS, 'maximum': LIVE_MAX_SECONDS,
                     'description': 'stop after this long (%g to %d s); with count: whichever comes first'
                                    % (LIVE_MIN_SECONDS, LIVE_MAX_SECONDS)},
         'max_wall': {'type': 'number', 'minimum': 1, 'maximum': 86400,
                      'description': 'time limit for the whole call in seconds (default: 120, or longer for a large '
                                     'count / seconds)'}}),
     'annotations': {'readOnlyHint': True}},
    {'name': 'memory', 'title': 'Saved items',
     'description': "What is saved in the meter's memory: the counts and every saved measurement, MIN MAX session, "
                    'peak session and trend recording (name, time, function, primary value and the other readings; '
                    'recordings also interval, number of records and duration). download_recording fetches the '
                    "records of a recording; the index is the item's index within its kind.",
     'inputSchema': _schema({'max_wall': _wall(MEMORY_WALL_S)}), 'annotations': {'readOnlyHint': True}},
    {'name': 'download_recording', 'title': 'Download a trend recording',
     'description': 'Download trend recording `index` (see memory) to a CSV or JSONL file, one row per record '
                    '(start/end time, primary, minimum, average, maximum, unit, states). Long recordings take '
                    'minutes over Bluetooth. Returns the file, the number of records, seconds per record, retries '
                    'and the recording header. An interrupted download resumes with `start` and the same `out` '
                    '(a failed result names both under resume).',
     'inputSchema': _schema({
         'index': {'type': 'integer', 'minimum': 0, 'description': 'recording index (0 = the first)'},
         'out': {'type': 'string', 'description': 'output file (default data/recording<index>_<time>.csv in the '
                                                  'open289 folder)'},
         'format': FORMAT,
         'start': {'type': 'integer', 'minimum': 0, 'default': 0,
                   'description': 'first record (resume an interrupted download into the same out file)'},
         'limit': {'type': 'integer', 'minimum': 0,
                   'description': 'stop before this record index (an end index, not a count)'},
         'max_wall': _wall(RECORDING_WALL_S)}, ['index'])},
    {'name': 'press', 'title': 'Press meter keys',
     'description': 'Simulate front-panel keys in order (soft keys F1-F4 under the display, the arrows, HOLD, '
                    'MINMAX, RANGE, INFO, BACKLIGHT, ONOFF) and return the display state after each key. held: a '
                    'long press (RANGE held = back to autorange). ONOFF switches the meter OFF for good (it cannot '
                    'be switched on remotely, and the adapter goes to sleep with it): it is refused unless '
                    'allow_off is true; the meter then asks for confirmation (F1 confirms, F4 cancels).',
     'inputSchema': _schema({
         'keys': {'type': 'array', 'minItems': 1, 'maxItems': 20,
                  'items': {'type': 'string', 'enum': list(PRESS_KEYS)}, 'description': 'keys, pressed in order'},
         'held': {'type': 'boolean', 'default': False, 'description': 'long press (HELD_DOWN) for every key'},
         'allow_off': {'type': 'boolean', 'default': False,
                       'description': 'permit ONOFF, which switches the meter off; only when the user asked for it'},
         'max_wall': _wall(DEFAULT_WALL_S)}, ['keys'])},
    {'name': 'query', 'title': 'Read-only meter query',
     'description': 'Send read-only remote commands to the meter and return each answer (text, or hex for binary '
                    'replies): ID (model, firmware, serial), IM, and Q* queries such as QM (primary measurement), '
                    'QDDB (display data), QBL (battery), QSLS (saved counts), QMP <property>. Anything else (PRESS, '
                    'MP, CSD, RI, OFF ...) is refused with UNSAFE_OPERATION before it reaches the meter.',
     'inputSchema': _schema({
         'commands': {'type': 'array', 'minItems': 1, 'maxItems': QUERY_MAX, 'items': {'type': 'string'},
                      'description': "e.g. ['ID', 'QM']"},
         'max_wall': _wall(DEFAULT_WALL_S)}, ['commands']),
     'annotations': {'readOnlyHint': True}},
    {'name': 'selftest', 'title': 'Self-test',
     'description': 'Read-only health check of adapter, link and meter: adapter advertising, connection, meter '
                    'attached, adapter firmware, ID, display snapshot, a live reading within 5 s, adapter and meter '
                    'battery. Never changes the meter. SELFTEST_FAILED lists the failed checks (measured vs '
                    'expected).',
     'inputSchema': _schema({'max_wall': _wall(DEFAULT_WALL_S)}), 'annotations': {'readOnlyHint': True}},
    {'name': 'log_start', 'title': 'Start a background log',
     'description': 'Start logging live readings to a CSV or JSONL file in the background and return the job id and '
                    'the file. The log ends after `duration`, at log_stop, or when the Bluetooth link stays lost '
                    'longer than `reconnect_for`; it keeps running if this client disconnects. One log at a time, '
                    'and meanwhile the other meter tools return BUSY. Follow it with log_status, end it with '
                    'log_stop.',
     'inputSchema': _schema({
         'duration': {'type': 'string', 'default': '10m',
                      'description': "e.g. '30s', '10m', '8h' or 'forever' (no limit: it ends at log_stop); "
                                     "must be longer than 0"},
         'every': {'type': 'string', 'default': '0',
                   'description': "write one reading per interval, e.g. '1s'; '0' = every reading (about 4/s)"},
         'snapshot_every': {'type': 'string', 'default': '0',
                            'description': "also save the full display (modes, MIN/MAX/AVG) at this interval, e.g. "
                                           "'10s' (a CSV log puts them in <file>.display.jsonl); '0' = off"},
         'out': {'type': 'string', 'description': 'output file (default data/log_<time>.csv in the open289 folder)'},
         'format': FORMAT,
         'reconnect_for': {'type': 'string', 'description': "keep retrying this long after the link is lost "
                                                            "(default '5m'; '0' = end the log)"}})},
    {'name': 'log_status', 'title': 'Background log status',
     'description': 'State of a background log (running / done / failed), the records written so far, the last '
                    'status line, the file, and once it has ended its summary (min/max/mean per unit, link gaps).',
     'inputSchema': _schema({'job': JOB}), 'annotations': {'readOnlyHint': True}},
    {'name': 'log_stop', 'title': 'Stop the background log',
     'description': 'Stop the background log cleanly and return its summary: file, records, duration, min/max/mean '
                    'per unit, link gaps. Without `job` it also stops a log or live started elsewhere (a terminal, '
                    'another client); already_ended true = the log had ended before this call (the summary is its '
                    'old one).',
     'inputSchema': _schema({'job': JOB})},
]
TOOL_BY_NAME = {t['name']: t for t in TOOLS}


class ToolError(Exception):
    def __init__(self, code, message, hint=None, extra=None):
        super().__init__(message)
        self.doc = {'ok': False, 'error': dict({'code': code, 'message': message}, **({'hint': hint} if hint else {}))}
        self.doc.update(extra or {})


# ------------------------------------------------------------------------------------------------ arguments
_TYPES = {'string': (str,), 'integer': (int,), 'number': (int, float), 'boolean': (bool,), 'array': (list,)}


def _value(name, s, v):
    """v checked against the property schema s (normalized: an integral float for an integer)."""
    t = s.get('type')
    if t == 'integer' and isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, bool) and t != 'boolean' or not isinstance(v, _TYPES[t]) \
            or (isinstance(v, float) and not math.isfinite(v)):     # (an int is finite: no float conversion)
        raise ToolError('INVALID_ARG', '%s must be %s %s' % (name, 'an' if t[0] in 'ai' else 'a', t))
    if 'enum' in s and v not in s['enum']:
        raise ToolError('INVALID_ARG', '%s must be one of %s' % (name, ', '.join(map(str, s['enum']))))
    if 'minimum' in s and v < s['minimum']:
        raise ToolError('INVALID_ARG', '%s must be >= %g' % (name, s['minimum']))
    if 'exclusiveMinimum' in s and v <= s['exclusiveMinimum']:
        raise ToolError('INVALID_ARG', '%s must be > %g' % (name, s['exclusiveMinimum']))
    if 'maximum' in s and v > s['maximum']:
        raise ToolError('INVALID_ARG', '%s must be <= %g' % (name, s['maximum']))
    if t == 'array':
        if len(v) < s.get('minItems', 0) or len(v) > s.get('maxItems', len(v)):
            raise ToolError('INVALID_ARG', '%s takes %d to %d items' % (name, s.get('minItems', 0),
                                                                        s.get('maxItems', len(v))))
        v = [_value('%s[%d]' % (name, i), s['items'], x) for i, x in enumerate(v)]
    return v


def check_args(tool, a):
    """Arguments checked against the tool's input schema: unknown names, types, ranges and enums are refused."""
    props = tool['inputSchema']['properties']
    unknown = sorted(set(a) - set(props))
    if unknown:
        raise ToolError('INVALID_ARG', 'unknown argument(s) for %s: %s' % (tool['name'], ', '.join(unknown)),
                        'arguments: %s' % (', '.join(props) or 'none'))
    out = {}
    for k, v in a.items():
        if v is not None:
            out[k] = _value(k, props[k], v)
    for k in tool['inputSchema'].get('required', ()):
        if k not in out:
            raise ToolError('INVALID_ARG', '%s needs %s' % (tool['name'], k))
    return out


def _flag(flag, value):
    """[flag, value], or [flag=value] for a value starting with '-': argparse takes a separate '-...' argument for
    an option and refuses the command."""
    value = str(value)
    return [flag + '=' + value] if value.startswith('-') else [flag, value]


def _opt(a, key, flag=None):
    v = a.get(key)
    return [] if v in (None, '') else _flag(flag or '--' + key.replace('_', '-'), v)


def duration_s(s):
    """A duration as open289 reads it (cli.parse_duration: '90', '90s', '5m', '1.5h', 'forever') -> seconds, None =
    forever; ValueError for text the CLI refuses (the child then reports it)."""
    s = str(s).strip().lower()
    if s in FOREVER:
        return None
    m = DURATION.fullmatch(s)
    if not m:
        raise ValueError(s)
    return float(m.group(1)) * DURATION_UNITS[m.group(2)]


def seconds_text(seconds):
    """Seconds as a CLI duration ('0.1s', '60s'): fixed point, never an exponent the CLI cannot parse."""
    return (('%.3f' % seconds).rstrip('0').rstrip('.') or '0') + 's'


# ------------------------------------------------------------------------------------------------ CLI runner
def _json_lines(data):
    out = []
    for line in (data or b'').decode('utf-8', 'replace').splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def _tail(path_or_bytes, n=3):
    try:
        data = path_or_bytes if isinstance(path_or_bytes, bytes) else open(path_or_bytes, 'rb').read()[-8192:]
    except OSError:
        return None
    lines = [l for l in data.decode('utf-8', 'replace').strip().splitlines() if l.strip()]
    return ' / '.join(lines[-n:]) or None


def _cli_wall(wall):
    """The CLI's own --max-wall, a little below the server's limit, so it can answer TIMEOUT and disconnect cleanly
    before the child is killed."""
    return max(1.0, wall - min(10.0, 0.25 * wall))


class Result:
    """The JSON lines of one CLI run, its exit code and stderr."""

    def __init__(self, cmd, docs, code, err, timed_out=None):
        self.cmd, self.docs, self.code, self.err, self.timed_out = cmd, docs, code, err, timed_out

    @property
    def final(self):
        for d in reversed(self.docs):
            if 'ok' in d:
                return d
        return None

    @property
    def failed(self):
        return bool(self.timed_out) or self.final is None or self.final.get('ok') is False or self.code != 0

    def error(self):
        """The error object of a failed run."""
        if self.timed_out:
            return {'code': 'TIMEOUT', 'message': 'open289 %s did not finish within %g s and was stopped'
                                                  % (self.cmd, self.timed_out),
                    'hint': 'raise max_wall for a slow link or a long command'}
        f = self.final
        if f is None:
            e = {'code': 'INTERNAL', 'message': 'open289 %s ended (exit %s) without a result' % (self.cmd, self.code)}
            tail = _tail(self.err or b'')
            return dict(e, hint=tail) if tail else e
        if isinstance(f.get('error'), dict):
            return f['error']
        return {'code': 'INTERNAL', 'message': 'open289 %s failed (exit %s)' % (self.cmd, self.code)}


def _no_window_flags():
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def child_env():
    """The children's environment: this server's (OPEN289_SIM ... pass through), with OPEN289_HOME set to this
    server's open289 folder, so server and children share state/ whatever their working directory (an OPEN289_HOME
    the user set is kept, made absolute)."""
    return dict(os.environ, OPEN289_HOME=home())


def stop_request_path():
    return os.path.join(state_dir(), 'stop.request')     # open289/logger.py stop_request_path


def request_stop():
    """What `open289 stop` does: a running live / read --every / log ends cleanly within about a second."""
    p = stop_request_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        f.write(str(time.time()))


class Cli:
    """Runs open289 CLI commands in child processes ([python, -X utf8, -c BOOT, ...]): the open289 package next to
    this server, its open289 folder (child_env) and this server's working directory (relative paths)."""

    def __init__(self, argv0=None):
        self.argv0 = list(argv0 or [sys.executable, '-X', 'utf8', '-c', BOOT])

    def _popen(self, args, stdout, stderr, detached=False):
        argv, kw = self.argv0 + list(args), {}
        if os.name == 'nt':
            flags = _no_window_flags()
            if detached:                         # no console Ctrl+C from the client; outlive the client's job object
                flags |= subprocess.CREATE_NEW_PROCESS_GROUP
                try:
                    return subprocess.Popen(argv, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                            env=child_env(), creationflags=flags | 0x01000000)  # BREAKAWAY
                except OSError:                  # the job does not allow breakaway
                    pass
            kw['creationflags'] = flags
        elif detached:
            kw['start_new_session'] = True       # no SIGHUP / SIGINT from the client's session
        return subprocess.Popen(argv, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, env=child_env(), **kw)

    def run(self, args, max_wall, cli_wall=True, cancel=None):
        """Run one command to its end -> Result. Past max_wall (Result.timed_out) or once `cancel` (an Event) is set,
        the child is stopped: a STOPPABLE stream gets stop requests and STOP_GRACE_S to end and disconnect cleanly
        (its readings so far are kept), anything else, or a stream that does not end, is killed. cli_wall: also pass
        the CLI its own --max-wall (one-shot commands; streaming ones ignore it)."""
        argv = (['--max-wall', '%g' % _cli_wall(max_wall)] if cli_wall else []) + list(args)
        p = self._popen(argv, subprocess.PIPE, subprocess.PIPE)
        stoppable = args[0] in STOPPABLE
        t_end = time.time() + max_wall
        timed_out = stop_t = kill_t = None
        asked = 0.0
        while True:
            try:
                out, err = p.communicate(timeout=POLL_S)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.time()
            if stop_t is None and (now >= t_end or cancel is not None and cancel.is_set()):
                stop_t = now
                timed_out = max_wall if now >= t_end else None
            if stop_t is None:
                continue
            if kill_t is None and (not stoppable or now - stop_t >= STOP_GRACE_S):
                kill_t = now
                _kill(p)
            elif kill_t is None and now - asked >= 1.0 and p.poll() is None:
                asked = now                      # again each second: a live that is just starting clears old ones
                try:
                    request_stop()
                except OSError:
                    stop_t -= STOP_GRACE_S       # no stop request possible: kill it at once
            if kill_t is not None and now - kill_t > 10.0:
                out, err = b'', b''              # killed, but its pipes did not close: give up on its output
                break
        return Result(args[0], _json_lines(out), p.returncode, err, timed_out)

    def start(self, args, out_path, err_path):
        """A background command whose stdout/stderr go to files; it keeps running when this server ends."""
        with open(out_path, 'wb') as fo, open(err_path, 'wb') as fe:
            return self._popen(args, fo, fe, detached=True)


def _kill(p):
    try:
        if p.poll() is None:
            p.kill()
    except OSError:
        pass


def _pid_alive(pid, t0=None):
    """Is process pid running (and, on Windows with t0, was it created around t0: a reused pid is not ours)?"""
    if not pid:
        return False
    if os.name == 'nt':
        from ctypes import wintypes
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(ctypes.c_ulonglong),) * 4
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:     # STILL_ACTIVE
                return False
            if t0 is None:
                return True
            ft = [ctypes.c_ulonglong() for _ in range(4)]
            if not k.GetProcessTimes(h, *[ctypes.byref(x) for x in ft]):
                return True
            created = (ft[0].value - 116444736000000000) / 1e7
            return abs(created - t0) < 60.0
        finally:
            k.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                                     # Linux: a reused pid runs something else
        with open('/proc/%d/cmdline' % int(pid), 'rb') as f:
            return b'open289' in f.read()
    except OSError:
        return True


def _read_lines(path, tail=256 * 1024):
    out = []
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail))
            data = f.read()
    except OSError:
        return out
    return _json_lines(data)


def _head_lines(path, head=16 * 1024):
    try:
        with open(path, 'rb') as f:
            data = f.read(head)
    except OSError:
        return []
    return _json_lines(data[:data.rfind(b'\n') + 1])


def jobs_dir():
    return os.path.join(state_dir(), 'mcp_jobs')


class Job:
    """A background log. Its folder (jobs_dir()/<job>/) keeps meta.json, stdout.jsonl and stderr.txt, so the state
    survives a server restart."""

    def __init__(self, job, pid, args, t0, proc=None):
        self.job, self.pid, self.args, self.t0, self.proc = job, pid, args, t0, proc
        self.dir = os.path.join(jobs_dir(), job)
        self.stdout = os.path.join(self.dir, 'stdout.jsonl')
        self.stderr = os.path.join(self.dir, 'stderr.txt')
        self._ended = False
        self._started = None
        self._count_lock = threading.Lock()
        self._count = [0, 0]                    # bytes of the data file counted, lines / records in them

    def running(self):
        if self._ended:
            return False
        if self.proc is not None:
            alive = self.proc.poll() is None
        else:
            alive = self.final() is None and _pid_alive(self.pid, self.t0)
        self._ended = not alive
        return alive

    def wait(self, timeout):
        t_end = time.time() + timeout
        while self.running():
            if time.time() >= t_end:
                return False
            time.sleep(0.1)
        return True

    def kill(self):
        if self.proc is not None:
            _kill(self.proc)
            self.wait(10)

    def started(self):
        if self._started is None:
            self._started = next((l for l in _head_lines(self.stdout) if l.get('event') == 'started'), None)
        return self._started

    def lines(self):
        return _read_lines(self.stdout)

    def final(self):
        for l in reversed(self.lines()):
            if l.get('event') == 'done' or l.get('ok') is False:
                return l
        return None

    def state(self):
        if self.running():
            return 'running'
        f = self.final()
        return 'done' if f and f.get('ok') and f.get('event') == 'done' else 'failed'

    def records(self):
        """Readings in the data file so far (counted incrementally: CSV rows, or the 'ble' lines of a JSONL log)."""
        st = self.started() or {}
        path, csv_fmt = st.get('file'), st.get('format', 'csv') == 'csv'
        if not path:
            return 0
        with self._count_lock:
            try:
                with open(path, 'rb') as f:
                    while True:
                        f.seek(self._count[0])
                        data = f.read(1 << 22)
                        end = data.rfind(b'\n') + 1
                        if not end:
                            break                # nothing new, or a line still being written
                        chunk = data[:end]
                        self._count[1] += chunk.count(b'\n') if csv_fmt else chunk.count(b'"type": "ble"')
                        self._count[0] += end
            except OSError:
                pass
            return max(0, self._count[1] - 1) if csv_fmt else self._count[1]

    def save(self):
        with open(os.path.join(self.dir, 'meta.json'), 'w') as f:
            json.dump({'job': self.job, 'pid': self.pid, 'args': self.args, 't0': self.t0,
                       'server_pid': os.getpid()}, f)

    @classmethod
    def load(cls, name):
        """The job in folder jobs_dir()/name (None: no readable meta.json yet, or not a job)."""
        try:
            with open(os.path.join(jobs_dir(), name, 'meta.json')) as f:
                d = json.load(f)
            if d['job'] != name:
                return None
            return cls(d['job'], d['pid'], d['args'], float(d['t0']))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @classmethod
    def load_all(cls, known=()):
        """The jobs in jobs_dir() (logs started by this or another server, now or earlier), except those in known."""
        jobs = {}
        try:
            names = os.listdir(jobs_dir())
        except OSError:
            return jobs
        for n in names:
            if n not in known:
                j = cls.load(n)
                if j is not None:
                    jobs[n] = j
        return jobs


# ------------------------------------------------------------------------------------------------ summaries
def _clock(t):
    """Epoch seconds -> local PC time text."""
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t)) + '.%03d' % int(t % 1 * 1000)


def _sig(x, digits=9):
    return float('%.*g' % (digits, x))


def _reading(r):
    """A display reading (records.binreading_dict) without its decoding details."""
    d = {k: r.get(k) for k in ('id', 'value', 'unit', 'display', 'state') if k in r}
    if r.get('attribute') not in (None, '', 'NONE'):
        d['attribute'] = r['attribute']
    if r.get('time') not in (None, '', '-'):
        d['time'] = r['time']
    return d


def _display(h, readings=True):
    """A display state (records.header_dict) in short: function, range, modes, primary (+ every reading)."""
    d = {k: h[k] for k in ('function', 'secondary_function', 'autorange', 'range', 'unit', 'modes', 'hazard',
                           'minmax_start') if k in h}
    if isinstance(h.get('primary'), dict):
        d['primary'] = _reading(h['primary'])
    if readings:
        d['readings'] = [_reading(r) for r in h.get('readings') or []]
    return d


def _item(x):
    """A saved item (records.session_dict) in short."""
    d = {k: x[k] for k in ('kind', 'index', 'name') if k in x}
    d.update(_display(x))
    for k in ('start', 'end', 'interval_s', 'records', 'duration_s', 'event_threshold'):
        if k in x:
            d[k] = x[k]
    return d


def _live_reading(r):
    d = {'t': r.get('t'), 'time': _clock(r['t']) if isinstance(r.get('t'), (int, float)) else None}
    d.update({k: r.get(k) for k in ('value', 'unit', 'display', 'state', 'function')})
    if r.get('attribute') not in (None, '', 'NONE'):
        d['attribute'] = r['attribute']
    if isinstance(r.get('secondary'), dict):
        d['secondary'] = {k: r['secondary'].get(k) for k in ('value', 'unit', 'display')}
    return d


def _stats(pairs):
    """[(unit, value)] -> {unit: {n, min, max, mean}} over the finite values."""
    acc = {}
    for unit, v in pairs:
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            s = acc.setdefault(unit, [0, v, v, 0.0])
            s[0] += 1
            s[1], s[2], s[3] = min(s[1], v), max(s[2], v), s[3] + v
    return {u: {'n': s[0], 'min': s[1], 'max': s[2], 'mean': _sig(s[3] / s[0])} for u, s in acc.items()}


def _unique(seq):
    out = []
    for x in seq:
        if x is not None and x not in out:
            out.append(x)
    return out


def summarize_live(docs):
    """The readings of a live run reduced to their statistics and the last few."""
    rs = [d for d in docs if d.get('type') == 'reading' and isinstance(d.get('t'), (int, float))]
    done = next((d for d in reversed(docs) if d.get('event') == 'done'), {})
    out = {'ok': True, 'cmd': 'live', 'count': len(rs)}
    rate = done.get('rate_hz')
    if rate is None and len(rs) > 1 and rs[-1]['t'] > rs[0]['t']:
        rate = round((len(rs) - 1) / (rs[-1]['t'] - rs[0]['t']), 2)
    out['rate_hz'] = rate
    for k in ('duration_s', 'stopped_by'):
        if done.get(k) is not None:
            out[k] = done[k]
    if rs:
        out.update({'first_t': rs[0]['t'], 'last_t': rs[-1]['t'], 'first_time': _clock(rs[0]['t']),
                    'last_time': _clock(rs[-1]['t']),
                    'functions': _unique(r.get('function') for r in rs), 'units': _unique(r.get('unit') for r in rs),
                    'stats': _stats((r.get('unit'), r.get('value')) for r in rs)})
        sec = [r['secondary'] for r in rs if isinstance(r.get('secondary'), dict)]
        if sec:
            out['secondary_stats'] = _stats((s.get('unit'), s.get('value')) for s in sec)
        out['last'] = [_live_reading(r) for r in rs[-LAST_READINGS:]]
    return out


def summarize_log(doc):
    """A log's 'done' summary as returned to the agent."""
    out = {k: doc[k] for k in ('ok', 'cmd', 'file', 'format', 'records', 'packets', 'snapshots', 'duration_s',
                               'stopped_by', 'stats', 'meter', 'display_file') if k in doc}
    for k in ('started', 'ended'):
        if isinstance(doc.get(k), (int, float)):
            out[k] = _clock(doc[k])
    gaps = doc.get('gaps') or []
    out['gaps'] = len(gaps)
    if gaps:
        out['gap_list'] = [{'from': _clock(g['from']) if isinstance(g.get('from'), (int, float)) else g.get('from'),
                            'to': _clock(g['to']) if isinstance(g.get('to'), (int, float)) else g.get('to'),
                            'reason': g.get('reason')} for g in gaps[:GAPS_LISTED]]
    if isinstance(out.get('stats'), dict):
        out['stats'] = {u: dict(s, mean=_sig(s['mean'])) if isinstance(s.get('mean'), float) else s
                        for u, s in out['stats'].items()}
    return out


def _last_index(path, fmt):
    """Index of the last complete record in a recording file (None when it has none)."""
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8192))
            data = f.read()
    except OSError:
        return None
    lines = data[:data.rfind(b'\n') + 1].decode('utf-8', 'replace').splitlines()
    for line in reversed(lines):
        try:
            return int(json.loads(line)['index'] if fmt == 'jsonl' else line.split(',', 1)[0])
        except (ValueError, KeyError, TypeError):
            continue
    return None


SELFTEST_HINTS = {'S01': 'the adapter is not advertising: press its button until the LED flashes green, close Fluke '
                         'Connect on any phone',
                  'S02': 'the adapter did not accept the connection: move closer, check that no phone is connected',
                  'S03': 'clip the adapter onto the meter IR port and switch the meter on',
                  'S06': 'a menu or result page is open on the meter: close it, then run selftest again'}


# ------------------------------------------------------------------------------------------------ server
def _rpc_error(mid, code, message):
    return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': code, 'message': message}}


def _rpc_result(mid, result):
    return {'jsonrpc': '2.0', 'id': mid, 'result': result}


class Replies:
    """Where the replies to one incoming message go. A single request gets its reply as is; a batch (a JSON array,
    JSON-RPC 2.0) gets ONE array with the replies to its members, sent when the last is in (tool calls answer from a
    thread). Notifications, responses and cancelled calls add no reply; a batch of only those gets none."""

    def __init__(self, send, batch=False):
        self._send, self.batch = send, batch
        self.lock = threading.Lock()
        self.msgs, self.open = [], 1             # open: the dispatch of the message(s) + tool calls still running

    def add(self, msg):
        with self.lock:
            self.msgs.append(msg)

    def hold(self):
        """A reply will follow from a thread (done)."""
        with self.lock:
            self.open += 1

    def done(self, msg=None):
        """The dispatch, or a held reply (msg None: none), is complete; the last one sends."""
        with self.lock:
            if msg is not None:
                self.msgs.append(msg)
            self.open -= 1
            if self.open:
                return
            msgs, self.msgs = self.msgs, []
        if msgs:
            self._send(msgs if self.batch else msgs[0])


class Server:
    def __init__(self, cli=None, out=None):
        self.cli = cli or Cli()
        self.out = out or sys.stdout.buffer
        self.wlock = threading.Lock()
        self.device = threading.Lock()          # one meter command at a time; also guards starting a log
        self.device_holder = None                # the CLI command holding it (named in a BUSY)
        self.version = PROTOCOL_VERSIONS[0]
        self.jobs_lock = threading.Lock()
        self.jobs = Job.load_all()               # logs started by this or another server (refreshed: _jobs)
        self.job_ids = itertools.count(1)
        self.calls = threading.Lock()            # guards inflight / cancelled
        self.inflight = {}                       # tool calls not answered yet: request id -> Event (stop its child)
        self.cancelled = set()                   # their ids whose reply is dropped (notifications/cancelled)

    # ------------------------------------------------------------ JSON-RPC plumbing
    def send(self, msg):
        data = (json.dumps(msg, separators=(',', ':')) + '\n').encode('utf-8')
        with self.wlock:
            try:
                self.out.write(data)
                self.out.flush()
            except (OSError, ValueError):       # the client has gone
                pass

    def serve(self, inp=None):
        lines = iter(inp.readline, b'') if inp is not None else _stdin_lines()
        try:
            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    self.send(_rpc_error(None, -32700, 'parse error'))
                    continue
                batch = isinstance(msg, list) and bool(msg)     # [] is answered as one invalid request
                out = Replies(self.send, batch)
                for m in (msg if batch else [msg]):
                    try:
                        self.dispatch(m, out)
                    except Exception as e:      # noqa: BLE001 - one bad message never ends the server
                        if isinstance(m, dict) and m.get('id') is not None:
                            out.add(_rpc_error(m['id'], -32603, 'internal error: %r' % e))
                out.done()
        finally:
            self.shutdown()

    def dispatch(self, m, out):
        """One message; its reply (if any) goes to out (Replies: serve closes it with out.done())."""
        if not isinstance(m, dict) or not isinstance(m.get('method', ''), str):
            mid = m.get('id') if isinstance(m, dict) else None
            out.add(_rpc_error(mid if isinstance(mid, (str, int, float)) and not isinstance(mid, bool) else None,
                               -32600, 'invalid request'))
            return
        method, mid, params = m.get('method'), m.get('id'), m.get('params', {})
        if mid is not None and (isinstance(mid, bool) or not isinstance(mid, (str, int, float))):
            out.add(_rpc_error(None, -32600, 'invalid request id'))
            return
        if method is None:
            if 'result' in m or 'error' in m:    # a response to us (we send no requests): ignore
                return
            out.add(_rpc_error(mid, -32600, 'invalid request: no method'))
            return
        if params is None:
            params = {}
        if not isinstance(params, dict):
            if mid is not None:
                out.add(_rpc_error(mid, -32602, 'params must be an object'))
            return
        if mid is None:                          # notification
            if method == 'notifications/cancelled':  # the child is stopped and the reply dropped
                rid = params.get('requestId')
                if isinstance(rid, (str, int, float)) and not isinstance(rid, bool):
                    with self.calls:
                        ev = self.inflight.get(rid)
                        if ev is not None:
                            self.cancelled.add(rid)
                            ev.set()
            return
        if method == 'initialize':
            want = params.get('protocolVersion')
            self.version = want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return out.add(_rpc_result(mid, {
                'protocolVersion': self.version, 'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': 'open289', 'title': 'Fluke 287/289 multimeter (IR3000 FC)',
                               'version': __version__},
                'instructions': INSTRUCTIONS}))
        if method == 'ping':
            return out.add(_rpc_result(mid, {}))
        if method == 'tools/list':
            return out.add(_rpc_result(mid, {'tools': TOOLS}))
        if method == 'tools/call':
            args = params.get('arguments')
            if args is None:
                args = {}
            if not isinstance(args, dict):
                return out.add(_rpc_error(mid, -32602, 'arguments must be an object'))
            with self.calls:
                if mid in self.inflight:
                    return out.add(_rpc_error(mid, -32600, 'request id %r is in use by a call in progress' % (mid,)))
                self.inflight[mid] = threading.Event()
            out.hold()
            threading.Thread(target=self.call, args=(mid, params.get('name'), args, out), daemon=True).start()
            return
        out.add(_rpc_error(mid, -32601, 'unknown method %s' % method))

    def call(self, mid, name, args, out=None):
        """One tools/call (in its own thread); its reply goes to out, none when it was cancelled."""
        out = out or Replies(self.send)
        msg = None
        try:
            msg = self._call(mid, name, args)
        except Exception as e:                   # noqa: BLE001 - the server keeps running
            msg = _rpc_error(mid, -32603, 'internal error: %r' % e)
        finally:
            with self.calls:
                self.inflight.pop(mid, None)
                dropped = mid in self.cancelled
                self.cancelled.discard(mid)
            out.done(None if dropped else msg)

    def _call(self, mid, name, args):
        tool = TOOL_BY_NAME.get(name) if isinstance(name, str) else None
        if tool is None:
            return _rpc_error(mid, -32602, 'unknown tool %s' % name)
        try:
            if name == 'press' and isinstance(args.get('keys'), list):     # keys are case-insensitive
                args = dict(args, keys=[k.upper() if isinstance(k, str) else k for k in args['keys']])
            doc = self.run_tool(name, check_args(tool, args), mid)
            err = not doc.get('ok', False)
        except ToolError as e:
            doc, err = e.doc, True
        except Exception as e:                   # noqa: BLE001 - reported to the client, the server keeps running
            doc, err = {'ok': False, 'error': {'code': 'INTERNAL', 'message': repr(e)}}, True
        result = {'content': [{'type': 'text', 'text': self.text(doc)}], 'isError': err}
        if self.version >= STRUCTURED_SINCE:
            result['structuredContent'] = doc
        return _rpc_result(mid, result)

    @staticmethod
    def text(doc):
        body = json.dumps(doc, indent=1, ensure_ascii=False)
        if doc.get('ok', False):
            return body
        e = doc.get('error') or {}
        head = '%s: %s' % (e.get('code'), e.get('message'))
        if e.get('hint'):
            head += '\nhint: %s' % e['hint']
        return head + '\n' + body

    # ------------------------------------------------------------ running commands
    def _jobs(self):
        """The known logs, with those another server (another client) started since this one began."""
        with self.jobs_lock:
            self.jobs.update(Job.load_all(known=set(self.jobs)))
            return dict(self.jobs)

    def running_jobs(self):
        return [j for j in self._jobs().values() if j.running()]

    def _not_logging(self):
        busy = self.running_jobs()
        if busy:
            st = busy[0].started() or {}
            raise ToolError('BUSY', 'background log %s holds the adapter%s' % (
                busy[0].job, ' (writing %s)' % st['file'] if st.get('file') else ''),
                'the adapter accepts one connection: log_stop ends the log (log_status shows its progress)')

    def _cancel_event(self, mid):
        with self.calls:
            return self.inflight.get(mid)

    @staticmethod
    def _check_cancel(ev):
        if ev is not None and ev.is_set():
            raise ToolError('CANCELLED', 'the call was cancelled (or the server is closing) before it started')

    def _take_device(self, wall, mid, what):
        """Take the meter (one command at a time), waiting at most wall s for the previous call of this server ->
        the time left for this command. BUSY when that call keeps it past wall; CANCELLED when this call is
        cancelled meanwhile (no child is started then)."""
        ev = self._cancel_event(mid)
        t0 = time.time()
        got = self.device.acquire(blocking=False)
        while not got:
            self._check_cancel(ev)
            left = t0 + wall - 1.0 - time.time()             # keep at least 1 s for the command itself
            if left <= 0:
                raise ToolError('BUSY', 'the previous tool call (%s) was still running after %g s'
                                % (self.device_holder or 'a meter command', wall),
                                'tool calls run one at a time (the adapter accepts one connection): retry when it '
                                'has finished, or raise max_wall to wait for it')
            got = self.device.acquire(timeout=min(POLL_S, left))
        try:
            self._check_cancel(ev)
        except ToolError:
            self.device.release()
            raise
        self.device_holder = what
        return max(1.0, wall - (time.time() - t0))

    def _release_device(self):
        self.device_holder = None
        self.device.release()

    def _run(self, args, wall, mid, cli_wall=True):
        """One meter command within wall s (waiting for the previous one included), refused (BUSY) while a
        background log runs."""
        left = self._take_device(wall, mid, args[0])
        try:
            self._not_logging()
            return self.cli.run(args, left, cli_wall=cli_wall, cancel=self._cancel_event(mid))
        finally:
            self._release_device()

    @staticmethod
    def _fail(res, **extra):
        doc = {'ok': False, 'cmd': (res.final or {}).get('cmd', res.cmd), 'error': res.error()}
        doc.update(extra)
        return doc

    def run_tool(self, name, a, mid=None):
        return getattr(self, 'tool_' + name)(a, mid)

    # ------------------------------------------------------------ tools
    def tool_status(self, a, mid):
        res = self._run(['info'], a.get('max_wall', DEFAULT_WALL_S), mid)
        if res.failed:
            return self._fail(res)
        f = res.final
        return {k: f[k] for k in ('ok', 'cmd', 'adapter', 'meter', 'saved') if k in f}

    def tool_read(self, a, mid):
        res = self._run(['read'], a.get('max_wall', DEFAULT_WALL_S), mid)
        if res.failed:
            return self._fail(res)
        f = res.final
        out = {'ok': True, 'cmd': 'read'}
        if isinstance(f.get('t'), (int, float)):
            out.update(t=f['t'], time=_clock(f['t']))
        out.update(_display(f))
        return out

    def tool_live(self, a, mid):
        count, seconds = a.get('count'), a.get('seconds')
        if count is None and seconds is None:
            count = 20
        args = ['live'] + (['-n', str(count)] if count is not None else []) + \
            (['--for', seconds_text(seconds)] if seconds is not None else [])
        need = max(seconds or 0.0, count / 2.0 if count and seconds is None else 0.0)   # hardware: ~4 readings/s
        wall = a.get('max_wall', max(DEFAULT_WALL_S, 60.0 + need))
        res = self._run(args, wall, mid, cli_wall=False)
        out = summarize_live(res.docs)
        if res.failed:
            return dict(self._fail(res), **{k: v for k, v in out.items() if k not in ('ok', 'cmd')})
        return out

    def tool_memory(self, a, mid):
        res = self._run(['memory'], a.get('max_wall', MEMORY_WALL_S), mid)
        f = res.final or {}
        items = [_item(x) for x in f.get('items') or [] if isinstance(x, dict)]
        if res.failed:
            if f.get('cmd') == 'memory' and f.get('errors') and not f.get('error') and not res.timed_out:
                errs = f['errors']
                first = errs[0].get('error') or {}
                e = {'code': first.get('code', 'PROTOCOL_ERROR'),
                     'message': '%d saved item(s) could not be read (first: %s #%s: %s)' % (
                         len(errs), errs[0].get('kind'), errs[0].get('index'), first.get('message')),
                     'hint': first.get('hint') or 'close any menu on the meter and retry'}
                return {'ok': False, 'cmd': 'memory', 'error': e, 'counts': f.get('counts'), 'items': items,
                        'errors': errs}
            return self._fail(res)
        return {'ok': True, 'cmd': 'memory', 'counts': f.get('counts'), 'items': items}

    def tool_download_recording(self, a, mid):
        args = ['recording', str(a['index'])] + _opt(a, 'out') + _opt(a, 'format')
        if a.get('start'):
            args += ['--start', str(a['start'])]
        if a.get('limit') is not None:
            args += ['--limit', str(a['limit'])]
        res = self._run(args, a.get('max_wall', RECORDING_WALL_S), mid, cli_wall=False)
        started = next((d for d in res.docs if d.get('event') == 'started' and d.get('ok')), None)
        head = {}
        if started:
            head = {'file': started.get('file'),
                    'recording': _item(started['recording']) if isinstance(started.get('recording'), dict) else None}
        if res.failed:
            doc = self._fail(res, **head)
            if started and started.get('file'):
                fmt = a.get('format') or ('jsonl' if started['file'].lower().endswith(('.jsonl', '.json')) else 'csv')
                last = _last_index(started['file'], fmt)
                hint = doc['error'].get('hint') or ''
                m = re.search(r'resume with --start (\d+)', hint)
                nxt = last + 1 if last is not None else (int(m.group(1)) if m else None)
                if nxt is not None:
                    doc['resume'] = {'index': a['index'], 'start': nxt, 'out': started['file']}
                    ours = 'resume: download_recording with index %d, start %d, out %s' % (a['index'], nxt,
                                                                                           started['file'])
                    before = hint[:m.start()].rstrip(' ,;') if m else hint
                    doc['error'] = dict(doc['error'], hint=(before + '; ' if before else '') + ours)
            return doc
        f = res.final
        out = {'ok': True, 'cmd': 'recording', 'file': f.get('file'), 'format': f.get('format')}
        for k in ('records', 'duration_s', 's_per_record', 'retries'):
            out[k] = f.get(k)
        out['recording'] = head.get('recording')
        return out

    def tool_press(self, a, mid):
        args = ['press'] + list(a['keys']) + (['--held'] if a.get('held') else []) + \
            (['--allow-off'] if a.get('allow_off') else [])
        res = self._run(args, a.get('max_wall', DEFAULT_WALL_S), mid)
        if res.failed:
            return self._fail(res)
        presses = []
        for p in res.final.get('presses') or []:
            d = {k: p.get(k) for k in ('key', 'held', 'ack')}
            st = p.get('state')
            if isinstance(st, dict) and 'function' in st:
                st = _display(st, readings=False)
            if st is not None:
                d['state'] = st
            presses.append(d)
        return {'ok': True, 'cmd': 'press', 'presses': presses}

    def tool_query(self, a, mid):
        cmds = a['commands']
        bad = [c for c in cmds if not SAFE_QUERY.fullmatch(c)]
        if bad:
            raise ToolError('UNSAFE_OPERATION', 'not a read-only query: %s' % ', '.join(repr(c) for c in bad),
                            'query passes only ID, IM and Q* commands (e.g. QM, QDDB, QBL, QSLS); front-panel keys: '
                            'press. Nothing was sent to the meter.')
        res = self._run(['raw'] + list(cmds), a.get('max_wall', DEFAULT_WALL_S), mid)
        f = res.final or {}
        results = f.get('results') if isinstance(f.get('results'), list) else None
        if res.failed:
            if results is not None and not f.get('error') and not res.timed_out:
                first = next((r['error'] for r in results if isinstance(r.get('error'), dict)), None) or res.error()
                return {'ok': False, 'cmd': 'raw', 'error': first, 'results': results}
            return self._fail(res)
        return {'ok': True, 'cmd': 'raw', 'results': results or []}

    def tool_selftest(self, a, mid):
        res = self._run(['selftest'], a.get('max_wall', DEFAULT_WALL_S), mid)
        f = res.final or {}
        if not isinstance(f.get('items'), list) or res.timed_out:
            return self._fail(res)
        out = {k: f.get(k) for k in ('passed', 'failed', 'items')}
        out = dict({'ok': not res.failed, 'cmd': 'selftest'}, **out)
        if not out['ok']:
            failed = [i for i in f['items'] if not i.get('pass')]
            ids = [i.get('id') for i in failed]
            hint = next((SELFTEST_HINTS[i] for i in ids if i in SELFTEST_HINTS),
                        'see the failed items (measured vs expected)')
            out['error'] = {'code': 'SELFTEST_FAILED',
                            'message': 'check(s) failed: %s' % (', '.join('%s %s' % (i.get('id'), i.get('name'))
                                                                          for i in failed) or 'see the items'),
                            'hint': hint}
        return out

    # ------------------------------------------------------------ background log
    def tool_log_start(self, a, mid):
        duration = a.get('duration') or '10m'
        try:
            secs = duration_s(duration)
        except ValueError:
            secs = None                          # the child refuses it with its own message
        if secs is not None and secs <= 0:       # the CLI takes 0 as 'no limit'
            raise ToolError('INVALID_ARG', 'duration must be longer than 0 (got %r)' % duration,
                            "use e.g. '30s' or '10m'; 'forever' logs until log_stop")
        args = ['log'] + _flag('--duration', duration) + _flag('--every', a.get('every') or '0') + \
            _flag('--snapshot-every', a.get('snapshot_every') or '0') + _opt(a, 'out') + _opt(a, 'format') + \
            _opt(a, 'reconnect_for') + ['--status-every', STATUS_EVERY]
        self._take_device(DEFAULT_WALL_S, mid, 'log_start')
        try:                                     # check and register together
            self._not_logging()
            job = '%s-%d-%d' % (time.strftime('%Y%m%d-%H%M%S'), os.getpid(), next(self.job_ids))
            j = Job(job, None, args, time.time())
            os.makedirs(j.dir, exist_ok=True)
            j.proc = self.cli.start(args, j.stdout, j.stderr)
            j.pid, j.t0 = j.proc.pid, time.time()
            with self.jobs_lock:
                self.jobs[job] = j
            j.save()
        finally:
            self._release_device()
        t_end = time.time() + JOB_START_WAIT_S
        while time.time() < t_end:               # until it logs (or ends at once)
            if not j.running() or (j.started() and j.records() > 0):
                break
            time.sleep(0.1)
        if not j.running():
            final = j.final()
            if final is None or not final.get('ok', False):
                e = (final or {}).get('error') or {}
                raise ToolError(e.get('code', 'INTERNAL'), e.get('message', 'the log ended at once without a result'),
                                e.get('hint') or _tail(j.stderr), extra={'job': job, 'state': 'failed'})
            return dict(summarize_log(final), job=job, state='done')
        st = j.started()
        if st is None:
            j.kill()
            raise ToolError('TIMEOUT', 'the log did not start within %g s and was stopped' % JOB_START_WAIT_S,
                            _tail(j.stderr), extra={'job': job, 'state': 'failed'})
        out = {'ok': True, 'job': job, 'state': 'running', 'pid': j.pid, 'file': st.get('file'),
               'format': st.get('format'), 'duration_s': st.get('duration_s'), 'every_s': st.get('every_s'),
               'snapshot_every_s': st.get('snapshot_every_s'), 'records': j.records()}
        if not out['records']:
            out['note'] = 'no reading yet (still connecting): follow it with log_status'
        return out

    def _job(self, a):
        """The job named in a, else the newest running log, else the newest log (this or another server's)."""
        jobs = self._jobs()
        if a.get('job'):
            if a['job'] not in jobs:
                newest = sorted(jobs, key=lambda k: jobs[k].t0)[-JOBS_LISTED:]
                raise ToolError('INVALID_ARG', 'unknown job %s' % a['job'],
                                ('latest jobs: %s' % ', '.join(newest)) if newest else 'start a log with log_start')
            return jobs[a['job']]
        if not jobs:
            raise ToolError('INVALID_ARG', 'no log is known to this server', 'start one with log_start')
        running = [j for j in jobs.values() if j.running()]
        return max(running or jobs.values(), key=lambda j: j.t0)

    def tool_log_status(self, a, mid):
        j = self._job(a)
        state = j.state()
        st = j.started() or {}
        out = {'ok': True, 'job': j.job, 'state': state, 'running': state == 'running',
               'elapsed_s': round(time.time() - j.t0, 1), 'file': st.get('file'), 'format': st.get('format'),
               'records': j.records()}
        if state != 'running':                   # its run time, not the time since it started
            final = j.final() or {}
            if isinstance(final.get('duration_s'), (int, float)):
                out['elapsed_s'] = round(final['duration_s'], 1)
            else:
                try:
                    out['elapsed_s'] = round(max(0.0, os.path.getmtime(j.stdout) - j.t0), 1)
                except OSError:
                    out.pop('elapsed_s')
        lines = j.lines()
        status = [l for l in lines if l.get('event') == 'status']
        if status:
            out['last_status'] = {k: status[-1].get(k) for k in ('elapsed_s', 'records', 'last')}
        events = [{k: v for k, v in l.items() if k not in ('ok', 'cmd')} for l in lines
                  if l.get('event') in ('link_lost', 'reconnected')]
        if events:
            out['link_events'] = events[-GAPS_LISTED:]
        if state != 'running':
            final = j.final()
            if final is not None and final.get('ok') and final.get('event') == 'done':
                out['summary'] = summarize_log(final)
                out['records'] = final.get('records', out['records'])
            else:
                e = (final or {}).get('error') or {'code': 'INTERNAL', 'message': 'the log ended without a summary'}
                tail = None if e.get('hint') else _tail(j.stderr)
                out['failure'] = dict(e, hint=tail) if tail else e
        return out

    @staticmethod
    def _send_stop():
        try:
            request_stop()
        except OSError as e:
            raise ToolError('INTERNAL', 'cannot write the stop request %s: %s' % (stop_request_path(), e),
                            'end the log from a terminal with "open289 stop"') from None

    def tool_log_stop(self, a, mid):
        if not a.get('job') and not self._jobs():    # nothing known here: stop whatever live / log runs
            self._send_stop()
            return {'ok': True, 'cmd': 'stop', 'stop_sent': True,
                    'note': 'no log is known to this server; a stop request was sent (a running open289 live, '
                            'read --every or log, e.g. one started from a terminal, ends cleanly)'}
        j = self._job(a)
        already = not j.running()
        sent = False
        if not already:
            self._send_stop()
            sent = True
            t_end, last = time.time() + STOP_WAIT_S, time.time()
            while j.running() and time.time() < t_end:
                j.wait(min(0.5, max(0.0, t_end - time.time())))
                if j.running() and time.time() - last >= 1.0:    # a log that was just starting clears old requests
                    self._send_stop()
                    last = time.time()
            if j.running():
                raise ToolError('TIMEOUT', 'log %s has not finished %g s after the stop request' % (j.job, STOP_WAIT_S),
                                'call log_stop again, or follow it with log_status', extra={'job': j.job})
        elif not a.get('job'):
            # the newest log known here has ended, yet the adapter may be held by a log or live this server cannot
            # see (started from a terminal or by another program): stop that one too
            self._send_stop()
            sent = True
        mark = {}
        if already:
            mark = {'already_ended': True, 'stop_sent': sent,
                    'note': 'log %s had already ended before this call; this is its summary%s' % (
                        j.job, '. A stop request was sent anyway: a live or log running elsewhere (a terminal, '
                               'another program) ends cleanly' if sent else '')}
        if sent:
            _wait_adapter_free()
        final = j.final()
        if final is None:
            raise ToolError('INTERNAL', 'log %s ended without a summary' % j.job, _tail(j.stderr),
                            extra=dict({'job': j.job, 'state': 'failed'}, **mark))
        if not final.get('ok', False):
            e = final.get('error') or {'code': 'INTERNAL', 'message': 'the log failed'}
            return dict({'ok': False, 'cmd': 'log', 'job': j.job, 'state': 'failed', 'error': e}, **mark)
        return dict(summarize_log(final), job=j.job, state='done', **mark)

    def shutdown(self):
        """stdin closed: running tool calls are stopped (a live cleanly, anything else killed: they would hold the
        adapter) before the server exits; a background log goes on."""
        with self.calls:
            events = list(self.inflight.values())
        for ev in events:
            ev.set()
        t_end = time.time() + STOP_GRACE_S + 5.0
        while time.time() < t_end:
            with self.calls:
                if not self.inflight:
                    break
            time.sleep(0.05)


def _stdin_lines():
    """Lines from stdin. On Windows a pipe is polled (PeekNamedPipe) and only data already there is read: a thread
    blocked in a synchronous read of the stdin pipe would block other threads that touch the handle (DLL start-up
    code does), and the server's tool calls run in threads."""
    if os.name == 'nt':
        import _winapi
        import msvcrt
        try:
            h = msvcrt.get_osfhandle(sys.stdin.fileno())
            _winapi.PeekNamedPipe(h, 0)
        except (OSError, ValueError, AttributeError):
            h = None
        if h is not None:
            buf = b''
            while True:
                try:
                    avail = _winapi.PeekNamedPipe(h, 0)[0]
                except OSError:                 # the client closed the pipe
                    avail = -1
                if avail > 0:
                    buf += os.read(sys.stdin.fileno(), avail)
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        yield line + b'\n'
                elif avail < 0:
                    if buf:
                        yield buf
                    return
                else:
                    time.sleep(0.01)
    yield from iter(sys.stdin.buffer.readline, b'')


def main():
    Server().serve()


if __name__ == '__main__':
    main()
