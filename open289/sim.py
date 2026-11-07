"""Virtual Fluke IR3000 FC adapter with a Fluke 289 attached: a drop-in for the part of bleak that open289 uses.

OPEN289_SIM=1 (or the CLI's --sim) makes open289.transport use SimBleakClient / SimBleakScanner from this module
instead of bleak's classes, so the whole tool runs without Bluetooth or hardware (CI, demos, development).

The model follows what was measured on real hardware on 2026-11-06 (IR3000 FC radio firmware 01.00.01, MSP
firmware 01.00.15, Fluke 289 firmware V1.71; see logs/README.md and notes/07): the GATT table, the CNX ID write that
arms the adapter control point, the 18-byte control-point write limit, the pass-through framing, the out-buffer
overflow and hang, the binary QDDB/QSMR/QMMSI/QPSI/QRSI/QSRR layouts, the 16-byte live packets and the front-panel
key behaviour. Behaviour that was never observed is marked GUESS in the code.

Device state (meter function, modes, screen, saved memory, power, adapter hang, boot counter) is one JSON file shared
by every process: $OPEN289_SIM_STATE, else <open289 state dir>/sim_state.json. Two sidecar files next to it make
the adapter behave as one device across processes: <state>.link names the process holding the single BLE
connection (refreshed by a heartbeat; while it is live the adapter does not advertise and refuses other
connections), and <state>.ram carries the adapter RAM (OUT/IN buffers, last control-point value) from one
connection to the next. power_cycle() / reset_state() bump the boot counter, which drops a live link in any
process and empties the adapter RAM. Every GATT write is appended to an audit log: $OPEN289_SIM_AUDIT, else
<open289 state dir>/sim_writes.jsonl.

Environment:
  OPEN289_SIM_RATE          live notifications per second (default 4, like the hardware)
  OPEN289_SIM_DELAY         latency of a pass-through SEND in seconds (default 0.05; the hardware takes ~0.6 s)
  OPEN289_SIM_OFF_DELAY     seconds from meter OFF until the adapter stops advertising and drops the link
                            (default 40 x OPEN289_SIM_DELAY)
  OPEN289_SIM_SCAN_MAX      longest wait of the scanner class methods (default 1.0 s: the virtual adapter either
                            advertises or it does not)
  OPEN289_SIM_LINK_TIMEOUT  seconds without a heartbeat after which a connection held by another process counts
                            as lost (default 2.0; a BLE supervision timeout plays this role on hardware)

Command line: python -m open289.sim reset | show | power-cycle | set key=value ... | audit [--clear]
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import dataclasses
import datetime as _dt
import inspect
import json
import math
import os
import re
import struct
import sys
import time
import uuid as _uuid
import weakref
from dataclasses import dataclass

from bleak.exc import (BleakCharacteristicNotFoundError, BleakDeviceNotFoundError, BleakError,
                       BleakGATTProtocolError)

from . import paths
from .meter import MAPS, MODE_BITS, OVERLOAD, BinReading, MeasHeader, Session, TrendRecord
from .reading import Reading

# --------------------------------------------------------------------------------------------------------------
# Identity (redacted: never the real adapter address or meter serial from the captures)

ADDRESS = 'FA:28:90:00:00:01'
NAME = 'IR 3000 FC'
SERIAL = '12345678'
METER_MODEL = 'FLUKE 289'
DETACHED_MODEL = 'FLUKE PC3000'
METER_FW = 'V1.71'
MSP_FW = '01.00.15'
RADIO_FW = '01.00.01  '
IM_TAIL = 'MSP V0.88,clem/branches/v1/trunk,r32730,10'
RSSI = -60


def _std(short: str) -> str:
    return f'0000{short}-0000-1000-8000-00805f9b34fb'


def _fl(short: str) -> str:
    return f'b698{short}-7562-11e2-b50d-00163e46f8fe'


ADV_SERVICE = _fl('1800')
READING = _fl('290f')
ASCII_READING = _fl('2901')
CNX_ID = _fl('2902')
USER_STRING = _fl('2903')
CP = _fl('2918')
BUF = _fl('2919')
FW_RADIO_SVC = _fl('1804')
FORBIDDEN_CHARS = frozenset(_fl(s) for s in ('2911', '2912', '2913', '2914'))
DIS_MODEL = _std('2a24')

MTU = 23
CP_MAX_WRITE = 18     # measured: longer control-point writes fail with ATT error 0x0D
ATT_MAX_WRITE = MTU - 3
OUT_OK = 128          # model: <= 128 bytes in the OUT buffer work (87 verified on hardware)
HANG_AT = 373         # measured: 373+ bytes hung the adapter until power-cycled (181 got no reply)

ST_IN_PROCESS, ST_INCORRECT_MODEL, ST_COMPLETE, ST_CANCELLED, ST_FAILED = 1, 2, 3, 4, 5

MODAL = ('minmax_result', 'save_page', 'power_off_confirm')
SCREENS = ('normal',) + MODAL
BACKLIGHT = ('off', 'bright', 'dim')
KEYS = ('F1', 'F2', 'F3', 'F4', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'HOLD', 'MINMAX', 'RANGE', 'INFO', 'BACKLIGHT',
        'ONOFF')
PRESS_TYPES = ('PRESSED', 'HELD_DOWN', 'RELEASED', 'REPEATED')


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, ''))
    except ValueError:
        return default
    return v if v >= 0 and math.isfinite(v) else default


def sim_delay() -> float:
    return _env_float('OPEN289_SIM_DELAY', 0.05)


def sim_rate() -> float:
    r = _env_float('OPEN289_SIM_RATE', 4.0)
    return r if r > 0 else 4.0


def off_delay() -> float:
    return _env_float('OPEN289_SIM_OFF_DELAY', 40 * sim_delay())


def scan_max() -> float:
    return _env_float('OPEN289_SIM_SCAN_MAX', 1.0)


def link_timeout() -> float:
    return _env_float('OPEN289_SIM_LINK_TIMEOUT', 2.0) or 2.0


def state_path() -> str:
    return os.environ.get('OPEN289_SIM_STATE') or os.path.join(paths.state_dir(), 'sim_state.json')


def audit_path() -> str:
    return os.environ.get('OPEN289_SIM_AUDIT') or os.path.join(paths.state_dir(), 'sim_writes.jsonl')


# --------------------------------------------------------------------------------------------------------------
# Binary encoders: exact inverses of the parsers in open289.meter and open289.reading


def encode_double(x: float) -> bytes:
    """Meter double: two little-endian u32 words, high word first (inverse of meter.fdouble)."""
    hi, lo = struct.unpack('>II', struct.pack('>d', float(x)))
    return struct.pack('<II', hi, lo)


def map_code(map_name: str, name) -> int:
    """qemap name -> code; also accepts what meter.lookup returns for unknown codes ('unit#99')."""
    if isinstance(name, int):
        return name
    for k, v in MAPS.get(map_name, {}).items():
        if v == name:
            return k
    m = re.fullmatch(re.escape(map_name) + r'#(-?\d+)', str(name))
    if m:
        return int(m.group(1))
    raise ValueError(f'unknown {map_name} value {name!r}')


def mode_mask(names) -> int:
    """Mode names (as meter.modes returns them, ['NONE'] for none) -> bitmask."""
    mask = 0
    for n in names:
        if n == 'NONE':
            continue
        for bit, nm in MODE_BITS.items():
            if nm == n:
                mask |= bit
                break
        else:
            raise ValueError(f'unknown mode {n!r}')
    return mask


def encode_reading30(r: BinReading) -> bytes:
    """30-byte reading record (inverse of meter.parse_reading)."""
    return (struct.pack('<H', map_code('readingid', r.reading_id)) + encode_double(r.value)
            + struct.pack('<HhhhHH', map_code('unit', r.unit), r.multiplier, r.decimals, r.digits,
                          map_code('state', r.state), map_code('attribute', r.attribute))
            + encode_double(r.ts))


def encode_meas_header(h: MeasHeader) -> bytes:
    """34-byte measurement header + 30 bytes per reading: the QDDB payload after b'#0' (inverse of
    meter.parse_meas_header; the unknown u16 at offset 30 is written as 0, as on every capture)."""
    return (struct.pack('<HHHH', map_code('primfunction', h.prim), map_code('secfunction', h.sec),
                        map_code('autorange', h.autorange), map_code('unit', h.unit))
            + encode_double(h.range_max) + struct.pack('<hH', h.multiplier, map_code('bolt', h.bolt))
            + encode_double(h.minmax_start) + struct.pack('<HHH', mode_mask(h.mode), 0, len(h.readings))
            + b''.join(encode_reading30(r) for r in h.readings))


def _name_bytes(name: str) -> bytes:
    return name.encode('latin-1')


def encode_qsmr(s: Session) -> bytes:
    """Saved measurement (inverse of meter.parse_qsmr)."""
    return struct.pack('<I', s.seq) + encode_meas_header(s.header) + _name_bytes(s.name)


def encode_qmmsi(s: Session) -> bytes:
    """Saved min/max or peak session (inverse of meter.parse_qmmsi; QPSI uses the same layout)."""
    return (struct.pack('<I', s.seq) + encode_double(s.start) + encode_double(s.end)
            + encode_meas_header(s.header) + _name_bytes(s.name))


encode_qpsi = encode_qmmsi


def encode_qrsi(s: Session) -> bytes:
    """Recording session info (inverse of meter.parse_qrsi)."""
    return (struct.pack('<I', s.seq) + b''.join(encode_double(x) for x in (s.start, s.end, s.interval, s.threshold))
            + struct.pack('<II', s.reading_index, s.num_samples) + encode_meas_header(s.header)
            + _name_bytes(s.name))


def _sum_for_mean(mean: float, n: int) -> float:
    """A double s with s / n == mean (the meter stores the SUM of n samples; meter.parse_qsrr divides)."""
    s = mean * n
    if s / n == mean:
        return s
    for direction in (math.inf, -math.inf):
        x = s
        for _ in range(16):
            x = math.nextafter(x, direction)
            if x / n == mean:
                return x
    return s


def encode_qsrr(rec: TrendRecord) -> bytes:
    """146-byte trend record (inverse of meter.parse_qsrr, which divides AVERAGE by the sample count)."""
    avg = rec.average
    if rec.samples and avg.is_number:
        avg = dataclasses.replace(avg, value=_sum_for_mean(avg.value, rec.samples))
    return (encode_double(rec.start) + encode_double(rec.end) + encode_reading30(rec.maximum)
            + encode_reading30(rec.minimum) + encode_reading30(avg) + struct.pack('<I', rec.samples)
            + encode_reading30(rec.primary)
            + struct.pack('<HHH', map_code('recordtype', rec.record_type), map_code('isstableflag', rec.stable),
                          map_code('transientstate', rec.transient)))


_LIVE_FIELDS = (('counts', 0, 21), ('state', 21, 4), ('decimals', 25, 3), ('magnitude', 28, 3), ('sign', 31, 1),
                ('unit', 32, 8), ('function', 40, 8), ('value_range', 48, 7), ('range_decade', 55, 3),
                ('attribute', 58, 5), ('capture', 63, 1))


def encode_record(reading: Reading | None = None, **fields) -> bytes:
    """8-byte live reading record (inverse of reading.decode_record). Pass a Reading or its fields by name."""
    if reading is not None:
        fields = {name: getattr(reading, name) for name, _, _ in _LIVE_FIELDS} | fields
    unknown = set(fields) - {name for name, _, _ in _LIVE_FIELDS}
    if unknown:
        raise TypeError(f'unknown live record field(s): {sorted(unknown)}')
    w = 0
    for name, off, width in _LIVE_FIELDS:
        v = int(fields.get(name, 0))
        if not 0 <= v < (1 << width):
            raise ValueError(f'{name}={v} does not fit in {width} bits')
        w |= v << off
    return w.to_bytes(8, 'little')


def encode_notification(primary, secondary=None) -> bytes:
    """16-byte B698290F notification: primary + secondary record (all zero when there is no secondary)."""
    def rec(x):
        if x is None:
            return bytes(8)
        return bytes(x) if isinstance(x, (bytes, bytearray)) else encode_record(x)
    return rec(primary) + rec(secondary)


# --------------------------------------------------------------------------------------------------------------
# Meter model: functions, display composition, live packets

OVERRANGE = 1.2   # GUESS: how far past rangeMax a range still displays (5.0622 V was shown in the 5 V range)


@dataclass(frozen=True)
class FuncProfile:
    prim: str                  # qemap primfunction
    unit: str                  # QDDB base unit
    live_function: int         # function code in the live packet (notes/07)
    live_unit: int             # unit code in the live packet
    ranges: tuple              # ((rangeMax, multiplier, decimals, digits), ...) as reported by QDDB
    auto: bool = True          # False: fixed (MANUAL) range, RANGE does nothing
    default: float | None = 0.0  # default input in base units; None = open circuit (OL)
    noise: float = 0.0         # +- noise (base units, or a fraction of the input when relative)
    relative: bool = False
    hz: bool = False           # the Hz secondary function is available
    temp_offset: bool = False  # QDDB adds a TEMP_OFFSET reading


_V_RANGES = ((5, 0, 4, 5), (50, 0, 3, 5), (500, 0, 2, 5), (1000, 0, 1, 5))
FUNCTIONS: dict[str, FuncProfile] = {
    'V_DC': FuncProfile('V_DC', 'VDC', 12, 2, _V_RANGES, default=5.0622, noise=0.001, hz=True),
    'V_AC': FuncProfile('V_AC', 'VAC', 2, 1, _V_RANGES, default=1.0443, noise=0.0005, hz=True),
    'MV_DC': FuncProfile('MV_DC', 'VDC', 11, 2, ((50, -3, 3, 5), (500, -3, 2, 5)), default=0.025562,
                         noise=0.000005),
    'MV_AC': FuncProfile('MV_AC', 'VAC', 1, 1, ((50, -3, 3, 5), (500, -3, 2, 5)), default=0.034916,
                         noise=0.000005),
    'OHMS': FuncProfile('OHMS', 'OHM', 40, 11,
                        ((500, 0, 2, 5), (5, 3, 4, 5), (50, 3, 3, 5), (500, 3, 2, 5), (5, 6, 4, 5), (50, 6, 3, 5),
                         (500, 6, 1, 4)), default=None, noise=0.00005, relative=True),
    'CONTINUITY': FuncProfile('CONTINUITY', 'OHM', 39, 11, ((500, 0, 2, 5),), auto=False, default=None,
                              noise=0.01),
    'DIODE_TEST': FuncProfile('DIODE_TEST', 'VDC', 46, 2, ((5, 0, 4, 5),), auto=False, default=None, noise=0.0002),
    'TEMPERATURE': FuncProfile('TEMPERATURE', 'CEL', 34, 7, ((1350, 0, 1, 5),), auto=False, default=26.0,
                               noise=0.05, temp_offset=True),
    'MA_DC': FuncProfile('MA_DC', 'ADC', 20, 4, ((50, -3, 3, 5), (400, -3, 2, 5)), default=0.0047,
                         noise=0.000002),
}

LIVE_UNIT = {'NONE': 0, 'VAC': 1, 'VDC': 2, 'AAC': 3, 'ADC': 4, 'Hz': 5, 'CEL': 7, 'FAR': 8, 'OHM': 11, 'SIE': 12,
             'PCT': 13, 'S': 14, 'F': 15, 'dB': 16, 'dBm': 17, 'dBV': 31, 'CREST_FACTOR': 32, 'VAC_PLUS_DC': 33,
             'AAC_PLUS_DC': 34, 'V': 2, 'A': 4}
LIVE_ATTR = {'NONE': 0, 'OPEN_CIRCUIT': 1, 'SHORT_CIRCUIT': 2, 'GLITCH_CIRCUIT': 3, 'GOOD_DIODE': 4,
             'NEGATIVE_EDGE': 5, 'POSITIVE_EDGE': 6, 'HIGH_CURRENT': 7, 'LO_OHMS': 9}
LIVE_MAGNITUDE = {0: 0, 9: 1, 6: 2, 3: 3, -3: 4, -6: 5, -9: 6, -12: 7}
LIVE_FULL_COUNTS = 0x1FFFFF  # counts field of OL / INVALID / BLANK packets (captured)


def _q(t: float) -> float:
    """Meter time resolution: 1/1024 s (every captured timestamp is a multiple of it)."""
    return round(t * 1024) / 1024


def _ms_to_meter(ms: int) -> float:
    """First 1/1024 s tick inside a millisecond: reproduces captured timestamps from their printed form."""
    return ms // 1000 + math.ceil((ms % 1000) * 1024 / 1000) / 1024


def _mt(text: str) -> float:
    """Meter timestamp from 'YYYY-MM-DD HH:MM:SS.mmm' (meter local wall-clock time, as meter.ts_text prints it)."""
    d = _dt.datetime.strptime(text, '%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=_dt.timezone.utc)
    return _ms_to_meter(int(d.replace(microsecond=0).timestamp()) * 1000 + d.microsecond // 1000)


def local_wall_epoch(t: float | None = None) -> float:
    """The PC's local wall-clock time encoded as an epoch (how the meter keeps time: no timezone)."""
    t = time.time() if t is None else t
    return t + time.localtime(t).tm_gmtoff


def meter_epoch(st: dict | None = None) -> float:
    """Meter clock before quantisation. The captured meter ran 1 h ahead of the PC's local wall clock (it was
    still on daylight-saving time), so the default clock_offset is +3600 s; MP clock changes it. On a UTC machine
    (CI) this is time.time() + 3600."""
    off = 3600.0 if st is None else float(st.get('clock_offset', 3600.0))
    return local_wall_epoch() + off


def meter_now(st: dict | None = None) -> float:
    return _q(meter_epoch(st))


_M64 = (1 << 64) - 1


def _prng(seed: int, tick: int, salt: int = 0) -> float:
    """Deterministic noise in [-1, 1) for (seed, 100 ms tick, channel): splitmix64, no state to persist."""
    z = (seed * 0x9E3779B97F4A7C15 + tick * 0xBF58476D1CE4E5B9 + salt * 0x94D049BB133111EB + 0x9E3779B97F4A7C15) & _M64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _M64
    z ^= z >> 31
    return z / 2.0 ** 63 - 1.0


def _input(st: dict, key: str):
    v = st['inputs'].get(key, FUNCTIONS[key].default if key in FUNCTIONS else None)
    return None if v is None else float(v)


def _sample_tick(st: dict, fn: str, tick: int):
    """True input value of function fn during 100 ms tick (None = open circuit / OL)."""
    base = _input(st, fn)
    if base is None:
        return None
    prof = FUNCTIONS[fn]
    amp = prof.noise * abs(base) if prof.relative else prof.noise
    return base + amp * _prng(int(st.get('seed', 289)), tick, 1)


def _sample(st: dict, fn: str, t: float):
    return _sample_tick(st, fn, int(t * 10))


def _hz(st: dict, fn: str, t: float) -> float:
    if fn == 'V_DC':
        return 0.0
    base = st['inputs'].get('HZ', 60.0)
    return float(base) + 0.002 * _prng(int(st.get('seed', 289)), int(t * 10), 2)


def _pick_range(prof: FuncProfile, st: dict, v) -> int:
    if prof.auto and st.get('autorange', True):
        if v is None:
            return len(prof.ranges) - 1
        for i, (rmax, mult, _dec, _dig) in enumerate(prof.ranges):
            if abs(v) / 10 ** mult <= rmax * OVERRANGE:
                return i
        return len(prof.ranges) - 1
    return max(0, min(int(st.get('range_index', 0)), len(prof.ranges) - 1))


def _counts_value(counts: int, mult: int, dec: int) -> float:
    """The meter computes a displayed value as counts * 10^(mult - dec) (matches every captured double)."""
    return counts * 10.0 ** (mult - dec)


def _reading(rid: str, v, unit: str, rng: tuple, attr: str, ts: float) -> BinReading:
    rmax, mult, dec, digits = rng
    if v is None or not math.isfinite(v) or abs(v) / 10 ** mult > rmax * OVERRANGE:
        state = 'OL_MINUS' if (v is not None and v < 0) else 'OL'
        return BinReading(rid, OVERLOAD, unit, mult, dec, digits, state, attr, ts)
    counts = round(abs(v) * 10 ** (dec - mult))
    val = _counts_value(counts, mult, dec)
    return BinReading(rid, -val if (v < 0 and counts) else val, unit, mult, dec, digits, 'NORMAL', attr, ts)


def _attr(fn: str, v) -> str:
    if fn == 'CONTINUITY':  # GUESS: beeper threshold 25 ohm
        return 'SHORT_CIRCUIT' if (v is not None and v < 25) else 'OPEN_CIRCUIT'
    if fn == 'DIODE_TEST':  # captured: 0.2237 V good diode, 1.6736 V none
        return 'GOOD_DIODE' if (v is not None and 0.1 < v < 0.9) else 'NONE'
    return 'NONE'


def _hz_rng(hz: float) -> tuple:
    dec = 3 if hz < 100 else 2 if hz < 1000 else 1 if hz < 10000 else 0
    return (1e6, 0, dec, 5)


_MM_MAX_SAMPLES = 3000   # most samples folded in one go (a long unattended MIN MAX session is subsampled)
# REL %: captured PRIMARY -66.72 % (unit PCT, multiplier 0, 2 decimals, 5 digits). GUESS: OL beyond 999.99 % and
# with a zero reference.
_PCT_RNG = (999.99 / OVERRANGE, 0, 2, 5)
REL_MODES = ('REL', 'REL_PERCENT')
MODELLED_MODES = ('HOLD', 'MIN_MAX_AVG') + REL_MODES


def _rel_active(st: dict) -> bool:
    return bool(st.get('rel')) and bool(set(REL_MODES) & set(st.get('modes') or ()))


def _rel_primary(st: dict, live: BinReading, rng: tuple, t: float) -> tuple[BinReading, tuple] | None:
    """PRIMARY and its scale in REL / REL % (captured in V AC, logs/monitor_functions.jsonl: PRIMARY = LIVE -
    reference, or that difference in percent of the reference; the range stays where REL locked it). None when no
    relative mode is on."""
    if not _rel_active(st):
        return None
    ref = BinReading(**st['rel']['reference'])
    if 'REL_PERCENT' in st['modes']:
        if not live.is_number or not ref.value:
            return BinReading('PRIMARY', OVERLOAD, 'PCT', 0, 2, 5, 'OL', 'NONE', t), _PCT_RNG
        return _reading('PRIMARY', (live.value - ref.value) / ref.value * 100, 'PCT', _PCT_RNG, 'NONE', t), _PCT_RNG
    if not live.is_number:
        return dataclasses.replace(live, reading_id='PRIMARY'), rng
    return _reading('PRIMARY', live.value - ref.value, live.unit, rng, live.attribute, t), rng


def _measure(st: dict, fn: str, v, t: float) -> tuple[BinReading, BinReading, tuple, tuple]:
    """For true input v of function fn at meter time t: LIVE, the measurement's PRIMARY (REL applied; the Hz
    secondary function is not), the function range and the scale of that PRIMARY."""
    prof = FUNCTIONS[fn]
    rng = prof.ranges[_pick_range(prof, st, v)]
    live = _reading('LIVE', v, prof.unit, rng, _attr(fn, v), t)
    rel = _rel_primary(st, live, rng, t)
    if rel is not None:
        return live, rel[0], rng, rel[1]
    return live, dataclasses.replace(live, reading_id='PRIMARY'), rng, rng


# MIN MAX keeps running accumulators in st['minmax'] (base units, so a range change does not matter). Samples are
# taken every 100 ms from the deterministic noise and folded in lazily: before every state change (input, range,
# function, modes, clock, keys) and before MIN/MAX/AVG are reported. A fold samples the ticks since the previous
# fold under the state as it is now, which is exactly the state that held during those ticks, so a change of the
# input never rewrites the history (a real 289 keeps MAX >= every value it has seen).

def _mm_new(t: float) -> dict:
    t = _q(t)
    return {'start': t, 'stop': None, 'result': None, 'last_tick': int(t * 10) - 1, 'n': 0, 'sum': 0.0,
            'lo': None, 'hi': None, 'ol_lo': None, 'ol_hi': None}


def _mm_fold(st: dict, t: float) -> None:
    """Fold the 100 ms samples after the previous fold, up to meter time t (or the stop time), into the
    accumulators."""
    mm = st.get('minmax')
    if not mm or 'MIN_MAX_AVG' not in (st.get('modes') or ()):
        return
    t0 = float(mm['start'])
    for k, v in _mm_new(t0).items():
        mm.setdefault(k, v)   # state written before the accumulators existed: replay from the start
    if mm['stop'] is not None:
        t = min(t, float(mm['stop']))
    k0, k1 = int(mm['last_tick']) + 1, int(t * 10)
    if k1 < k0:
        return
    fn = st['function']
    step = max(1, math.ceil((k1 - k0 + 1) / _MM_MAX_SAMPLES))
    for k in range(k0, k1 + 1, step):
        ts = _q(max(t0, k / 10))
        r = _measure(st, fn, _sample_tick(st, fn, k), ts)[1]
        if r.is_number:
            r = dataclasses.replace(r, attribute='NONE')
            if mm['lo'] is None or r.value < mm['lo']['value']:
                mm['lo'] = dataclasses.asdict(dataclasses.replace(r, reading_id='MINIMUM'))
            if mm['hi'] is None or r.value > mm['hi']['value']:
                mm['hi'] = dataclasses.asdict(dataclasses.replace(r, reading_id='MAXIMUM'))
            mm['sum'] = float(mm['sum']) + r.value
            mm['n'] = int(mm['n']) + 1
        elif r.state == 'OL_MINUS':
            if mm['ol_lo'] is None:
                mm['ol_lo'] = ts
        elif mm['ol_hi'] is None:
            mm['ol_hi'] = ts
    mm['last_tick'] = k1


def _mm_rebase(st: dict, old_epoch: float, new_epoch: float) -> None:
    """The meter clock jumped (MP clock / set clock_offset): continue sampling from the new time, so the jump is not
    counted as elapsed time."""
    mm = st.get('minmax')
    if mm and 'last_tick' in mm:
        mm['last_tick'] = int(mm['last_tick']) + int(new_epoch * 10) - int(old_epoch * 10)


def _mm_readings(mm: dict, unit: str, rng: tuple, t: float) -> list[BinReading]:
    """MINIMUM, MAXIMUM, AVERAGE from the accumulators (fold first). An OL sample shows as OL in MAXIMUM, an -OL
    sample as -OL in MINIMUM."""
    _rmax, mult, dec, digits = rng
    t0 = float(mm['start'])
    t1 = _q(float(mm['stop'])) if mm.get('stop') is not None else _q(t)

    def ol(rid, state, ts):
        return BinReading(rid, OVERLOAD, unit, mult, dec, digits, state, 'NONE', float(ts))

    if mm.get('ol_lo') is not None:
        mn = ol('MINIMUM', 'OL_MINUS', mm['ol_lo'])
    else:
        mn = BinReading(**mm['lo']) if mm.get('lo') else ol('MINIMUM', 'OL', t0)
    if mm.get('ol_hi') is not None:
        mx = ol('MAXIMUM', 'OL', mm['ol_hi'])
    else:
        mx = BinReading(**mm['hi']) if mm.get('hi') else ol('MAXIMUM', 'OL', t0)
    n = int(mm.get('n') or 0)
    avg = _reading('AVERAGE', float(mm['sum']) / n, unit, rng, 'NONE', t1) if n else ol('AVERAGE', 'OL', t1)
    return [mn, mx, avg]


def _ordered_modes(names) -> list[str]:
    s = set(names)
    return [n for n in MODE_BITS.values() if n in s] or ['NONE']


def build_display(st: dict, t: float | None = None, minmax: bool = True) -> MeasHeader:
    """What QDDB reports for the current meter state at meter time t (folds pending MIN MAX samples into st)."""
    t = meter_now(st) if t is None else t
    fn = st['function']
    prof = FUNCTIONS[fn]
    sec = st.get('sec', 'NONE') if prof.hz else 'NONE'
    v = _sample(st, fn, t)
    live, measured, rng, mrng = _measure(st, fn, v, t)
    secondary = None
    rel = []
    if sec == 'HERTZ':
        hz = _hz(st, fn, t)
        primary = _reading('PRIMARY', hz, 'Hz', _hz_rng(hz), 'POSITIVE_EDGE', t)
        secondary = dataclasses.replace(live, reading_id='SECONDARY')
    else:
        primary = measured
        if _rel_active(st):  # captured order: LIVE, PRIMARY, REL_LIVE, REL_REFERENCE
            rel = [dataclasses.replace(live, reading_id='REL_LIVE'), BinReading(**st['rel']['reference'])]
    modes = list(st.get('modes') or [])
    hold = st.get('hold')
    if 'HOLD' in modes and hold:
        primary = BinReading(**hold['primary'])
        if secondary is not None and hold.get('secondary'):
            secondary = BinReading(**hold['secondary'])
    readings = [live, primary] + ([secondary] if secondary is not None else []) + rel
    if prof.temp_offset:
        readings.append(BinReading('TEMP_OFFSET', 0.0, prof.unit, 0, 1, 5, 'NORMAL', 'NONE', 0.0))
    mmst = 0.0
    mm = st.get('minmax')
    if 'MIN_MAX_AVG' in modes and mm:
        mmst = float(mm['start'])
        if minmax and mm.get('result'):   # stopped: the frozen MINIMUM, MAXIMUM, AVERAGE of the result page
            readings += [BinReading(**r) for r in mm['result']['readings'][1:]]
        elif minmax:
            _mm_fold(st, t)
            readings += _mm_readings(mm, measured.unit, mrng, t)
    bolt = 'ON' if (v is not None and prof.unit in ('VDC', 'VAC') and abs(v) >= 30) else 'OFF'
    autorange = 'AUTO' if (prof.auto and st.get('autorange', True)) else 'MANUAL'
    return MeasHeader(prof.prim, sec, autorange, prof.unit, float(rng[0]), rng[1], bolt, mmst,
                      _ordered_modes(modes), readings)


def minmax_result(st: dict, t: float) -> MeasHeader:
    """The MIN MAX session as of meter time t, in the layout a saved min/max session has (QMMSI): PRIMARY at t,
    then MINIMUM, MAXIMUM, AVERAGE from the accumulators (folded up to t first)."""
    _mm_fold(st, t)
    mm = st.get('minmax') or _mm_new(t)
    fn = st['function']
    prof = FUNCTIONS[fn]
    _live, primary, rng, mrng = _measure(st, fn, _sample(st, fn, t), t)
    autorange = 'AUTO' if (prof.auto and st.get('autorange', True)) else 'MANUAL'
    return MeasHeader(prof.prim, 'NONE', autorange, prof.unit, float(rng[0]), rng[1], 'ON', float(mm['start']),
                      ['MIN_MAX_AVG'], [primary] + _mm_readings(mm, primary.unit, mrng, t))


def _hold_capture(st: dict, t: float) -> None:
    """HOLD freezes what the display shows now (PRIMARY and SECONDARY; LIVE keeps updating)."""
    h = build_display(st, t, minmax=False)
    prim = next(r for r in h.readings if r.reading_id == 'PRIMARY')
    sec = next((r for r in h.readings if r.reading_id == 'SECONDARY'), None)
    st['hold'] = {'primary': dataclasses.asdict(prim),
                  'secondary': dataclasses.asdict(sec) if sec is not None else None}


def _rel_reference(st: dict, t: float) -> tuple[BinReading, int]:
    """The reading REL would take as its reference now, and the range it locks. Raises ValueError where the
    simulator does not model REL."""
    fn = st['function']
    prof = FUNCTIONS[fn]
    if prof.hz and st.get('sec', 'NONE') != 'NONE':
        raise ValueError('REL with the Hz secondary function is not modelled')
    v = _sample(st, fn, t)
    idx = _pick_range(prof, st, v)
    ref = _reading('REL_REFERENCE', v, prof.unit, prof.ranges[idx], 'NONE', _q(t))
    if not ref.is_number:  # GUESS: the meter would not take OL as a reference
        raise ValueError('REL needs a numeric reading (the display shows OL)')
    return ref, idx


def _rel_off(st: dict) -> None:
    rel = st.get('rel')
    if rel:
        st['autorange'] = bool(rel.get('autorange', True))
    st['rel'] = None


def set_modes(st: dict, names, t: float | None = None) -> None:
    """Switch to exactly these meter modes the way the meter does it ('python -m open289.sim set modes=...'): HOLD
    freezes the display, MIN_MAX_AVG starts fresh accumulators, REL / REL_PERCENT take the live reading as the
    reference and lock the range (captured: AUTO 5 V became MANUAL 5 V; REL -> REL % kept the reference). Modes
    the simulator does not model raise ValueError, and nothing changes then."""
    names = list(dict.fromkeys(names))
    bad = [m for m in names if m not in MODE_BITS.values()]
    if bad:
        raise ValueError(f'unknown mode(s) {bad}')
    unmodelled = [m for m in names if m not in MODELLED_MODES]
    if unmodelled:
        raise ValueError(f'mode(s) {unmodelled} are not modelled (supported: {", ".join(MODELLED_MODES)})')
    new = set(names)
    if set(REL_MODES) <= new:
        raise ValueError('REL and REL_PERCENT exclude each other')
    t = meter_now(st) if t is None else t
    rel_ref = None
    if new & set(REL_MODES) and not st.get('rel'):
        rel_ref = _rel_reference(st, t)   # validate before changing anything
    _mm_fold(st, t)
    cur = set(st.get('modes') or ())
    if 'MIN_MAX_AVG' in cur - new:
        st['minmax'] = None
        if st['screen'] in ('minmax_result', 'save_page'):
            st['screen'] = 'normal'
    if 'HOLD' not in new:
        st['hold'] = None
    if not new & set(REL_MODES):
        _rel_off(st)
    elif rel_ref is not None:
        ref, idx = rel_ref
        st['rel'] = {'reference': dataclasses.asdict(ref), 'autorange': bool(st.get('autorange', True))}
        st.update(autorange=False, range_index=idx)
    if 'MIN_MAX_AVG' in new and not (st.get('minmax') and 'MIN_MAX_AVG' in cur):
        st['minmax'] = _mm_new(t)
    st['modes'] = [m for m in names if m != 'HOLD']
    if 'HOLD' in new:
        if not (st.get('hold') and 'HOLD' in cur):
            _hold_capture(st, t)   # what the display shows with the other modes applied
        st['modes'].append('HOLD')


def live_record(r: BinReading, function: int) -> bytes:
    """One 8-byte live record for a QDDB-style reading, with the encodings observed on hardware (notes/07)."""
    unit = LIVE_UNIT.get(r.unit, 0)
    attr = LIVE_ATTR.get(r.attribute, 0)
    mag = LIVE_MAGNITUDE.get(r.multiplier, 0)
    dec = max(0, min(r.decimals, 7))
    if r.state == 'NORMAL' and math.isfinite(r.value) and abs(r.value) < OVERLOAD:
        counts = min(round(abs(r.value) * 10 ** (r.decimals - r.multiplier)), LIVE_FULL_COUNTS - 1)
        return encode_record(counts=counts, state=0, decimals=dec, magnitude=mag, sign=bool(r.value < 0 and counts),
                             unit=unit, function=function, attribute=attr)
    if r.state in ('OL', 'OL_MINUS', 'NORMAL'):
        return encode_record(counts=LIVE_FULL_COUNTS, state=4, decimals=dec, magnitude=mag,
                             sign=r.state == 'OL_MINUS', unit=unit, function=function, attribute=attr)
    if r.state == 'BLANK':
        return encode_record(counts=LIVE_FULL_COUNTS, state=1, decimals=dec, magnitude=mag, unit=unit,
                             function=function)
    return encode_record(counts=LIVE_FULL_COUNTS, state=3, function=function)  # INVALID and the rest


def live_packet(st: dict, t: float | None = None) -> bytes:
    """16-byte B698290F notification for the current state: what the LCD main and secondary display show (the
    held value in HOLD; Hz becomes the primary and V the secondary, as captured)."""
    h = build_display(st, t, minmax=False)
    prim = next(r for r in h.readings if r.reading_id == 'PRIMARY')
    sec = next((r for r in h.readings if r.reading_id == 'SECONDARY'), None)
    fcode = FUNCTIONS[st['function']].live_function
    return live_record(prim, fcode) + (live_record(sec, fcode) if sec is not None else bytes(8))


def _fmt_value(r: BinReading) -> str:
    if r.state != 'NORMAL' or not math.isfinite(r.value) or abs(r.value) >= OVERLOAD:
        # GUESS: no QDDA of an overload was ever captured; the sentinel is printed like the numbers (no '+')
        return '-9.99999999E+37' if r.state == 'OL_MINUS' else '9.99999999E+37'
    return f'{r.value:.{max(r.decimals - r.multiplier, 0)}f}'


def format_qm(r: BinReading) -> str:
    """QM: value,unit,state,attribute with the value in display units plus exponent ('5.0622E0', '-0.023E-3').
    Overload and other non-numbers are '+9.99999999E+37' (Fluke's remote-interface document, notes/06 4.4; no real
    QM of an overload was captured), '-9.99999999E+37' for -OL."""
    if r.state != 'NORMAL' or not math.isfinite(r.value) or abs(r.value) >= OVERLOAD:
        val = '-9.99999999E+37' if r.state == 'OL_MINUS' else '+9.99999999E+37'
    else:
        val = f'{r.value / 10 ** r.multiplier:.{max(r.decimals, 0)}f}E{r.multiplier}'
    return f'{val},{r.unit},{r.state},{r.attribute}'


def format_qdda(h: MeasHeader) -> str:
    """QDDA: the ASCII twin of QDDB (format of notes/06 4.4 and the probe_adapter_3 capture)."""
    modes = [m for m in h.mode if m != 'NONE']
    f = [h.prim, h.sec, h.autorange, h.unit, f'{h.range_max:g}', str(h.multiplier), h.bolt,
         f'{h.minmax_start:.3f}', str(len(modes)), *modes, str(len(h.readings))]
    for r in h.readings:
        f += [r.reading_id, _fmt_value(r), r.unit, str(r.multiplier), str(r.decimals), str(r.digits), r.state,
              r.attribute, f'{r.ts:.3f}']
    return ','.join(f)


# --------------------------------------------------------------------------------------------------------------
# Saved memory (redacted equivalent of the meter the captures came from)

# Recording 'Save 4' (logs/recording0.csv): per record "start_ms end_ms primary minimum maximum average samples
# type" where times are ms after the first record, values are counts of 0.0001 V (average: the decoded mean),
# type N/I = INTERVAL/INPUT and S/U = STABLE/UNSTABLE.
_REC0_T0_MS = 1794001219248  # 2026-11-06 21:40:19.248 meter time
_REC0_ROWS = """
0 504 14904 14903 14904 1.49032 5 NS; 504 1508 14903 14902 14903 1.4902600000000001 10 NS;
1508 2514 14902 14902 14903 1.4902700000000002 10 NS; 2514 3424 14903 14902 14903 1.490288888888889 9 NS;
3424 4425 14902 14902 14903 1.49024 10 NS; 4425 5431 14903 14903 14903 1.4903 10 NS;
5431 6437 14903 14903 14903 1.4903 10 NS; 6437 7443 14903 14902 14903 1.4902900000000001 10 NS;
7443 7644 14902 14902 18118 1.651 2 IU; 7644 8452 19857 19857 19876 1.987225 8 NS;
8452 9455 19875 19874 19877 1.98761 10 NS; 9455 10460 19876 19874 19876 1.9875099999999999 10 NS;
10460 11466 19877 19874 19877 1.9875600000000002 10 NS;
11466 12576 19877 19876 19878 1.987657142857143 7 NS; 12576 12676 11143 11143 11143 1.1143 1 IU;
12676 13481 9942 9934 9942 0.9935750000000001 8 NS; 13481 14487 9935 9934 9936 0.99351 10 NS;
14487 15493 9935 9933 9935 0.99346 10 NS; 15493 16501 9934 9934 9937 0.9935400000000001 10 NS;
16501 17504 9936 9935 9936 0.99359 10 NS; 17504 17605 10594 10594 10594 1.0594000000000001 1 IU;
17605 18510 19551 19551 19875 1.9838111111111114 9 NS; 18510 19420 19874 19872 19874 1.987311111111111 9 NS;
19420 20421 19873 19872 19876 1.98738 10 NS; 20421 21427 19873 19871 19874 1.98733 10 NS;
21427 22433 19873 19873 19875 1.9874500000000002 10 NS;
22433 22634 19874 15132 19874 1.7503000000000002 2 IU; 22634 23439 10008 9935 10008 0.994425 8 NS;
23439 24448 9936 9935 9937 0.99358 10 NS; 24448 25463 9936 9935 9936 0.9935666666666667 6 NS;
25463 26464 9935 9935 9936 0.99351 10 NS; 26464 27470 9936 9935 9936 0.9935600000000001 10 NS;
27470 27672 9935 9935 18525 1.423 2 IU; 27672 28476 19867 19867 19877 1.987475 8 NS;
28476 29482 19877 19877 19879 1.98778 10 NS; 29482 30491 19879 19875 19879 1.98778 10 NS;
30491 31494 19875 19874 19878 1.9876 10 NS; 31494 32500 19874 19874 19876 1.9875 10 NS;
32500 32600 19398 19398 19398 1.9398000000000002 1 IU; 32600 33505 10302 9935 10302 0.9976444444444444 9 NS;
33505 34511 9936 9935 9936 0.99359 10 NS; 34511 35417 9936 9936 9937 0.9936666666666666 9 NS;
35417 36422 9937 9936 9937 0.9936300000000001 10 NS; 36422 37733 9936 9935 9936 0.9935888888888891 9 NS;
37733 38438 19874 19874 19876 1.9875142857142858 7 NS; 38438 39443 19875 19874 19877 1.98753 10 NS;
39443 40449 19875 19874 19877 1.9875500000000001 10 NS;
40449 41460 19877 19876 19878 1.9877200000000002 10 NS;
41460 42460 19876 19875 19878 1.9877000000000002 10 NS; 42460 42662 19877 11775 19877 1.5826 2 IU;
42662 43466 9949 9937 9949 0.9938625000000001 8 NS; 43466 44472 9936 9936 9937 0.9936300000000001 10 NS;
44472 45478 9936 9936 9938 0.9937000000000001 10 NS; 45478 46487 9937 9936 9937 0.99365 10 NS;
46487 47490 9936 9936 9938 0.9937000000000001 10 NS; 47490 47590 10081 10081 10081 1.0081 1 IU;
47590 48496 19357 19357 19877 1.9818444444444445 9 NS;
48496 49502 19877 19874 19879 1.9876200000000002 10 NS; 49502 50510 19876 19876 19878 1.9877 6 NS;
50510 51416 19878 19878 19879 1.9878333333333333 9 NS; 51416 52421 19878 19875 19878 1.98774 10 NS;
52421 52623 19878 16292 19878 1.8085 2 IU; 52623 53427 10045 9936 10045 0.995 8 NS;
53427 54434 9936 9936 9937 0.9936300000000001 10 NS; 54434 55439 9935 9935 9937 0.9936 10 NS;
55439 56445 9936 9934 9936 0.99352 10 NS; 56445 57456 9935 9935 9936 0.99359 10 NS;
57456 57652 9936 9936 17393 1.3664500000000002 2 IU; 57652 58457 19858 19858 19877 1.9873875 8 NS;
58457 59462 19874 19874 19876 1.98752 10 NS; 59462 59563 19876 19876 19876 1.9876 1 IS;
59563 59563 19874 19874 19874 1.9874 1 NS;
"""


def _rec0_records() -> list[list]:
    """[start, end, primary, minimum, maximum, average, samples, record_type, stable, transient] per record."""
    out = []
    for row in _REC0_ROWS.replace('\n', ' ').split(';'):
        f = row.split()
        if not f:
            continue
        s, e, p, mn, mx = (int(x) for x in f[:5])
        out.append([_ms_to_meter(_REC0_T0_MS + s), _ms_to_meter(_REC0_T0_MS + e), _counts_value(p, 0, 4),
                    _counts_value(mn, 0, 4), _counts_value(mx, 0, 4), float(f[5]), int(f[6]),
                    'INTERVAL' if f[7][0] == 'N' else 'INPUT', 'STABLE' if f[7][1] == 'S' else 'UNSTABLE', 'NON_T'])
    return out


def _r(rid, counts, unit, mult, dec, digits, ts, state='NORMAL', attr='NONE') -> dict:
    val = OVERLOAD if state != 'NORMAL' else _counts_value(counts, mult, dec)
    return dataclasses.asdict(BinReading(rid, val, unit, mult, dec, digits, state, attr, ts))


def _hdr(prim, sec, ar, unit, rng, mult, bolt, mmst, mode, readings) -> dict:
    return {'prim': prim, 'sec': sec, 'autorange': ar, 'unit': unit, 'range_max': float(rng), 'multiplier': mult,
            'bolt': bolt, 'minmax_start': mmst, 'mode': mode, 'readings': readings}


def initial_memory() -> dict:
    """Saved items of the captured meter (logs/memory_after3.txt, state_check.txt QRSI 0). Sequence numbers other
    than the recording's (771, captured) are GUESSES."""
    t1, t1o = _mt('2026-09-29 03:15:52.785'), _mt('2026-09-29 03:15:43.786')
    t2 = _mt('2026-11-01 01:56:42.182')
    p0, p_end = _mt('2026-11-06 21:29:01.471'), _mt('2026-11-06 21:29:36.349')
    rec = _rec0_records()
    r_start, r_end = rec[0][0], rec[-1][1]
    return {
        'measurements': [
            {'seq': 1, 'name': 'Save 1', 'header': _hdr(
                'TEMPERATURE', 'NONE', 'MANUAL', 'CEL', 1350, 0, 'OFF', 0.0, ['HOLD'],
                [_r('PRIMARY', 347, 'CEL', 0, 1, 5, t1), _r('TEMP_OFFSET', 0, 'CEL', 0, 1, 5, t1o)])},
            {'seq': 2, 'name': 'Save 2', 'header': _hdr(
                'CONTINUITY', 'NONE', 'MANUAL', 'OHM', 500, 0, 'OFF', 0.0, ['HOLD'],
                [_r('PRIMARY', 0, 'OHM', 0, 2, 5, t2, state='OL', attr='OPEN_CIRCUIT')])},
        ],
        'minmax': [],
        'peak': [
            {'seq': 642, 'name': 'Save 3', 'start': p0, 'end': p_end, 'header': _hdr(
                'V_DC', 'PEAK_MIN_MAX', 'AUTO', 'VDC', 5, 0, 'ON', p0, ['MIN_MAX_AVG'],
                [_r('PRIMARY', 5, 'VDC', 0, 3, 5, p_end), _r('MINIMUM', -20, 'V', 0, 3, 5, _mt('2026-11-06 21:29:05.623')),
                 _r('MAXIMUM', 3029, 'V', 0, 3, 5, _mt('2026-11-06 21:29:02.109')),
                 _r('AVERAGE', 1371, 'VDC', 0, 3, 5, p_end)])},
        ],
        'recordings': [
            {'seq': 771, 'name': 'Save 4', 'start': r_start, 'end': r_end, 'interval': 1.0, 'threshold': 0.04,
             'reading_index': 772, 'header': _hdr('V_DC', 'NONE', 'MANUAL', 'VDC', 5, 0, 'ON', 0.0, ['RECORD'],
                                                  [_r('PRIMARY', 19874, 'VDC', 0, 4, 5, r_end)]),
             'records': rec},
        ],
    }


DEFAULT_PROPS = {'apoffto': '0', 'ablto': '900', 'beeper': 'ON', 'digits': '5', 'lang': 'ENGLISH',
                 'dateFmt': 'MM_DD', 'timeFmt': '24', 'numFmt': 'POINT', 'ahEventTh': '4', 'recEventTh': '4',
                 'cusDBm': '600', 'dBmRef': '600', 'tempOS': '0', 'tempUnit': 'C', 'acSmooth': 'OFF',
                 'hzEdge': 'RISING', 'pwPol': 'POS', 'dcPol': 'POS', 'contBeep': 'ON', 'contBeepOS': 'SHORT',
                 'rsm': 'OFF', 'SI': 'OFF', 'lcdCont': '10'}
DEFAULT_PROPQ = {'company': '', 'contact': '', 'operator': '', 'site': ''}


def default_state(memory: bool = True) -> dict:
    st = {
        'version': 1,
        'adapter_on': True,          # advertising (the IR3000 is awake)
        'adapter_hung': False,       # MSP hung by an oversized send: no control-point replies until power-cycled
        'adapter_off_at': None,      # wall time at which the adapter switches itself off (after meter OFF)
        'boot': 0,                   # adapter boot counter: power_cycle()/reset_state() bump it (drops any link)
        'meter_on': True,
        'user_string': NAME,
        'function': 'V_DC',
        'sec': 'NONE',
        'autorange': True,
        'range_index': 0,
        'modes': [],
        'screen': 'normal',
        'prev_screen': 'normal',
        'backlight': 'off',
        'hold': None,
        'minmax': None,              # MIN MAX accumulators (see _mm_new)
        'rel': None,                 # REL / REL %: {'reference': reading, 'autorange': before REL}
        'inputs': {**{fn: p.default for fn, p in FUNCTIONS.items()}, 'HZ': 60.0},
        'seed': 289,
        'clock_offset': 3600.0,
        'props': dict(DEFAULT_PROPS),
        'propq': dict(DEFAULT_PROPQ),
        'savnames': ['Save'] * 8,
        'next_seq': 845,
    }
    if memory:
        st['memory'] = initial_memory()
    return st


# --------------------------------------------------------------------------------------------------------------
# State persistence (one JSON file shared by all processes)

def _file_key(path: str):
    try:
        s = os.stat(path)
    except OSError:
        return None
    return (s.st_ino, s.st_mtime_ns, s.st_size)


def _apply_timers(st: dict) -> dict:
    off_at = st.get('adapter_off_at')
    if off_at is not None and time.time() >= off_at:
        st['adapter_on'] = False
        st['adapter_off_at'] = None
    return st


def load_state(path: str | None = None) -> dict:
    """The persisted device state (defaults when there is none yet)."""
    path = path or state_path()
    st = None
    for attempt in range(20):
        try:
            with open(path, encoding='utf-8') as fh:
                st = json.load(fh)
            break
        except FileNotFoundError:
            break
        except PermissionError:   # Windows: another process is replacing the file
            time.sleep(0.01)
        except (ValueError, OSError):
            if attempt >= 3:
                break
            time.sleep(0.01)
    if not isinstance(st, dict):
        return _apply_timers(default_state())
    base = default_state(memory=False)
    for k in ('inputs', 'props', 'propq'):
        base[k].update(st.get(k) or {})
        st[k] = base[k]
    base.update(st)
    if not isinstance(base.get('memory'), dict):
        base['memory'] = initial_memory()
    return _apply_timers(base)


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(text)
    for _ in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:   # Windows: another process has the file open
            time.sleep(0.01)
    os.replace(tmp, path)


def save_state(st: dict, path: str | None = None) -> None:
    _atomic_write(path or state_path(), json.dumps(st, indent=1))


def _read_json(path: str):
    for attempt in range(20):
        try:
            with open(path, encoding='utf-8') as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None
        except PermissionError:
            time.sleep(0.01)
        except (ValueError, OSError):
            if attempt >= 3:
                return None
            time.sleep(0.01)
    return None


def _remove(path: str) -> None:
    for _ in range(50):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.01)


def _link_path(path: str) -> str:
    return os.path.abspath(path) + '.link'


def _ram_path(path: str) -> str:
    return os.path.abspath(path) + '.ram'


class _AdapterMemory:
    """Adapter RAM (OUT/IN buffers, last control-point value). It survives reconnects like the real adapter (handed
    from one connection to the next through <state>.ram, also across processes) and is emptied by a power cycle."""

    def __init__(self, out=b'', inbuf=b'', cp=b'\x00'):
        self.out = bytearray(out)
        self.inbuf = bytearray(inbuf)
        self.cp = bytes(cp)

    @classmethod
    def load(cls, path: str, boot: int) -> _AdapterMemory:
        d = _read_json(_ram_path(path))
        if not isinstance(d, dict) or d.get('boot') != boot:
            return cls()
        try:
            return cls(bytes.fromhex(d['out']), bytes.fromhex(d['in']), bytes.fromhex(d['cp']))
        except (KeyError, TypeError, ValueError):
            return cls()

    def save(self, path: str, boot: int) -> None:
        try:
            _atomic_write(_ram_path(path), json.dumps({'boot': boot, 'out': self.out.hex(), 'in': self.inbuf.hex(),
                                                       'cp': self.cp.hex()}))
        except OSError:
            pass


def _reboot(st: dict, path: str) -> None:
    """The adapter restarts: new boot number (a link held by any process drops), adapter RAM empty."""
    st['boot'] = int(st.get('boot', 0)) + 1
    save_state(st, path)
    _remove(_link_path(path))
    _remove(_ram_path(path))


def reset_state(path: str | None = None) -> dict:
    """Factory state: meter on in V DC, adapter awake (and restarted), the captured meter's saved memory."""
    path = path or state_path()
    st = default_state()
    st['boot'] = int(load_state(path).get('boot', 0))
    _reboot(st, path)
    return st


def power_cycle(path: str | None = None) -> dict:
    """Adapter button + meter dial: both on, hang cleared, modal pages closed, any BLE link dropped, adapter RAM
    empty. A meter that was off starts fresh."""
    path = path or state_path()
    st = load_state(path)
    if not st['meter_on']:
        st.update(modes=[], hold=None, minmax=None, rel=None)
    if st['screen'] in ('minmax_result', 'save_page'):
        st['modes'] = [m for m in st['modes'] if m != 'MIN_MAX_AVG']
        st['minmax'] = None
    st.update(adapter_on=True, meter_on=True, adapter_hung=False, adapter_off_at=None, screen='normal',
              prev_screen='normal')
    _reboot(st, path)
    return st


def _audit(uuid: str, data: bytes, forbidden: bool, response, error: str | None = None) -> None:
    rec = {'t': round(time.time(), 6), 'uuid': uuid, 'hex': data.hex(), 'forbidden': forbidden,
           'response': response}
    if error:
        rec['error'] = error
    path = audit_path()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(rec) + '\n')
    except OSError:
        pass


def read_audit(path: str | None = None) -> list[dict]:
    """Every GATT write recorded by the simulator (oldest first)."""
    path = path or audit_path()
    try:
        with open(path, encoding='utf-8') as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except FileNotFoundError:
        return []


def clear_audit(path: str | None = None) -> None:
    try:
        os.remove(path or audit_path())
    except FileNotFoundError:
        pass


def is_forbidden_write(uuid: str, data: bytes) -> bool:
    """Writes open289 must never make: firmware-update characteristics, the radio-image service, and adapter
    opcodes 0x90-0x94 (1550/1555 insulation test)."""
    u = uuid.lower()
    return (u in FORBIDDEN_CHARS or _CHAR_SERVICE.get(u) == FW_RADIO_SVC
            or (u == CP and len(data) > 0 and 0x90 <= data[0] <= 0x94))


# --------------------------------------------------------------------------------------------------------------
# The meter: Fluke 289 V1.71 remote-protocol subset

class _Ack(Exception):
    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


_EXTRA_MAPS = {
    'mode': {0: 'NONE', **MODE_BITS},
    'buttonname': {606: 'UP', 607: 'DOWN', 608: 'LEFT', 609: 'RIGHT', 650: 'F1', 651: 'F2', 652: 'F3', 653: 'F4',
                   654: 'HOLD', 655: 'MINMAX', 656: 'RANGE', 657: 'INFO', 658: 'BACKLIGHT', 660: 'ONOFF'},
    'presstype': dict(enumerate(PRESS_TYPES)),
    'powermode': dict(enumerate(('ON', 'BATTERY_SAVER', 'OFF', 'OFF_WITH_WAKEUP', 'RESTART'))),
    'sessiontype': dict(enumerate(('ALL', 'RECORDED', 'MIN_MAX', 'PEAK', 'MEASUREMENT'))),
    'blvals': {1: 'EMPTY', 2: 'ALMOST_EMPTY', 3: 'PARTLY_EMPTY_1', 4: 'PARTLY_EMPTY_2', 5: 'PARTLY_EMPTY_3',
               6: 'FULL'},
    'memvals': dict(enumerate(('EXHAUSTED', 'SEVERE', 'WARNING', 'OK'))),
}
_CSD_KINDS = {'ALL': ('recordings', 'minmax', 'peak', 'measurements'), 'RECORDED': ('recordings',),
              'MIN_MAX': ('minmax',), 'PEAK': ('peak',), 'MEASUREMENT': ('measurements',)}


def _header_from(d: dict) -> MeasHeader:
    d = dict(d)
    d['readings'] = [BinReading(**r) for r in d['readings']]
    return MeasHeader(**d)


def session_from(kind: str, index: int, d: dict) -> Session:
    return Session(kind, index, int(d['seq']), d['name'], _header_from(d['header']), start=float(d.get('start', 0.0)),
                   end=float(d.get('end', 0.0)), interval=float(d.get('interval', 0.0)),
                   threshold=float(d.get('threshold', 0.0)), reading_index=int(d.get('reading_index', 0)),
                   num_samples=len(d.get('records') or []))


def trend_record_from(row: list, template: dict) -> TrendRecord:
    start, end, p, mn, mx, avg, n, rtype, stable, transient = row
    t = template

    def mk(rid, v, ts):
        return BinReading(rid, float(v), t['unit'], t['multiplier'], t['decimals'], t['digits'], 'NORMAL', 'NONE',
                          float(ts))
    # GUESS: per-reading timestamps of a trend record were never decoded; extremes at the start, mean at the end.
    return TrendRecord(float(start), float(end), mk('MAXIMUM', mx, start), mk('MINIMUM', mn, start),
                       mk('AVERAGE', avg, end), int(n), mk('PRIMARY', p, start), rtype, stable, transient)


class VirtualMeter:
    """Executes meter commands against a state dict; .changed tells whether the state must be saved."""

    def __init__(self, st: dict):
        self.st = st
        self.changed = False

    # -- framing
    def run(self, out: bytes) -> bytes:
        """The adapter's OUT buffer (CR appended by the adapter): every CR-terminated command, in order."""
        cmds = out.split(b'\r')[:-1]
        reply = bytearray()
        for c in cmds:
            if not self.st['meter_on']:
                break
            reply += self.execute(c)
        return bytes(reply)

    def execute(self, line: bytes) -> bytes:
        try:
            text = bytes(line).decode('ascii').strip()
        except UnicodeDecodeError:
            return b'1\r'
        word, _, arg = text.partition(' ')
        word, arg = word.upper(), arg.strip()
        handler = getattr(self, f'cmd_{word}', None) if re.fullmatch(r'[A-Z]{2,12}', word) else None
        if handler is None:
            return b'1\r'
        if self.st['screen'] in MODAL and word not in ('ID', 'IM', 'PRESS'):
            return b'2\r'
        _mm_fold(self.st, self._now())   # MIN MAX samples up to now, before the command changes anything
        try:
            payload = handler(arg)
        except _Ack as e:
            return f'{e.code}\r'.encode()
        if payload is None:
            return b'0\r'
        if isinstance(payload, str):
            payload = payload.encode('latin-1')
        return b'0\r' + payload + b'\r'

    def _mark(self):
        self.changed = True

    @staticmethod
    def _noarg(arg: str):
        if arg:
            raise _Ack(1)

    @staticmethod
    def _int(arg: str) -> int:
        try:
            return int(arg.strip())
        except ValueError:
            raise _Ack(1) from None

    def _now(self) -> float:
        return meter_now(self.st)

    # -- identification and display
    def cmd_ID(self, arg):
        self._noarg(arg)
        return f'{METER_MODEL},{METER_FW},{SERIAL}'

    def cmd_IM(self, arg):
        self._noarg(arg)
        return f'{METER_MODEL},{METER_FW},{SERIAL},{IM_TAIL}'

    def cmd_QSN(self, arg):
        self._noarg(arg)
        return SERIAL

    def cmd_QM(self, arg):
        self._noarg(arg)
        h = build_display(self.st, self._now(), minmax=False)
        return format_qm(next(r for r in h.readings if r.reading_id == 'PRIMARY'))

    def cmd_QDDA(self, arg):
        self._noarg(arg)
        return format_qdda(build_display(self.st, self._now()))

    def cmd_QDDB(self, arg):
        self._noarg(arg)
        return b'#0' + encode_meas_header(build_display(self.st, self._now()))

    def cmd_QBL(self, arg):
        self._noarg(arg)
        return 'FULL'

    def cmd_QMEMLEVEL(self, arg):
        self._noarg(arg)
        return 'OK'

    def cmd_QEMAP(self, arg):
        name = arg.lower()
        m = MAPS.get(name) or _EXTRA_MAPS.get(name)
        if not m:
            raise _Ack(1)  # GUESS: unknown map name
        return f'{len(m)},' + ','.join(f'{k},{v}' for k, v in sorted(m.items()))

    # -- saved memory
    def _mem(self, kind: str) -> list:
        return self.st['memory'].setdefault(kind, [])

    def cmd_QSLS(self, arg):
        self._noarg(arg)
        return ','.join(str(len(self._mem(k))) for k in ('recordings', 'minmax', 'peak', 'measurements'))

    def _saved(self, kind: str, arg: str) -> tuple[int, dict]:
        i = self._int(arg)
        lst = self._mem(kind)
        if not 0 <= i < len(lst):
            raise _Ack(5)  # GUESS: the real meter's answer to a missing index was never captured
        return i, lst[i]

    def cmd_QSMR(self, arg):
        i, d = self._saved('measurements', arg)
        return b'#0' + encode_qsmr(session_from('measurement', i, d))

    def cmd_QMMSI(self, arg):
        i, d = self._saved('minmax', arg)
        return b'#0' + encode_qmmsi(session_from('minmax', i, d))

    def cmd_QPSI(self, arg):
        i, d = self._saved('peak', arg)
        return b'#0' + encode_qmmsi(session_from('peak', i, d))

    def cmd_QRSI(self, arg):
        i, d = self._saved('recordings', arg)
        return b'#0' + encode_qrsi(session_from('recording', i, d))

    def cmd_QSRR(self, arg):
        parts = arg.split(',')
        if len(parts) != 2:
            raise _Ack(1)
        idx, k = self._int(parts[0]), self._int(parts[1])
        for d in self._mem('recordings'):
            if int(d['reading_index']) == idx:
                recs = d.get('records') or []
                if not 0 <= k < len(recs):
                    raise _Ack(5)  # GUESS
                tmpl = d['header']['readings'][0]
                return b'#0' + encode_qsrr(trend_record_from(recs[k], tmpl))
        raise _Ack(5)  # GUESS

    def cmd_QSAVNAME(self, arg):
        i = self._int(arg)
        names = self.st['savnames']
        if not 0 <= i < len(names):
            raise _Ack(5)  # GUESS
        return names[i]

    def cmd_SAVNAME(self, arg):
        i_txt, _, name = arg.partition(',')
        i = self._int(i_txt)
        if not 0 <= i < len(self.st['savnames']):
            raise _Ack(2)
        self.st['savnames'][i] = name.strip().strip('"\'')
        self._mark()

    def cmd_CSD(self, arg):
        kinds = _CSD_KINDS.get(arg.upper())
        if kinds is None:
            raise _Ack(1)
        for k in kinds:
            self.st['memory'][k] = []
        self._mark()

    # -- properties
    def _prop_key(self, table: dict, name: str) -> str:
        for k in table:
            if k.lower() == name.lower():
                return k
        raise _Ack(1)  # GUESS: unknown property

    def cmd_QMP(self, arg):
        if arg.lower() == 'clock':
            return str(int(meter_epoch(self.st)))
        return self.st['props'][self._prop_key(self.st['props'], arg)]

    def cmd_MP(self, arg):
        name, _, val = arg.partition(',')
        if not val:
            raise _Ack(1)
        if name.strip().lower() == 'clock':
            try:
                new = float(int(val))
            except ValueError:
                raise _Ack(1) from None
            old = meter_epoch(self.st)
            self.st['clock_offset'] = new - local_wall_epoch()
            _mm_rebase(self.st, old, meter_epoch(self.st))
        else:
            self.st['props'][self._prop_key(self.st['props'], name.strip())] = val.strip()
        self._mark()

    def cmd_QMPQ(self, arg):
        return "'" + self.st['propq'][self._prop_key(self.st['propq'], arg)].replace("'", "''") + "'"

    def cmd_MPQ(self, arg):
        name, _, val = arg.partition(',')
        val = val.strip()
        if len(val) < 2 or val[0] != val[-1] or val[0] not in '\'"':
            raise _Ack(1)
        self.st['propq'][self._prop_key(self.st['propq'], name.strip())] = val[1:-1].replace("''", "'")
        self._mark()

    def cmd_RMP(self, arg):
        self._noarg(arg)
        self.st['props'] = dict(DEFAULT_PROPS)
        self.st['propq'] = dict(DEFAULT_PROPQ)
        self._mark()

    def cmd_DS(self, arg):
        self._noarg(arg)
        self.st['props'].update(hzEdge='RISING', pwPol='POS', contBeep='ON', contBeepOS='SHORT')
        self._mark()

    def cmd_RI(self, arg):
        # GUESS: reset instrument = setup back to defaults and the display back to normal; saved memory is kept.
        self._noarg(arg)
        self.st.update(props=dict(DEFAULT_PROPS), propq=dict(DEFAULT_PROPQ), modes=[], hold=None, minmax=None,
                       rel=None, screen='normal', prev_screen='normal', autorange=True, range_index=0,
                       backlight='off')
        self._mark()

    def cmd_LEDT(self, arg):
        if arg.upper() not in ('ON', 'OFF'):
            raise _Ack(1)

    # -- power and keys
    def cmd_OFF(self, arg):
        self._noarg(arg)
        self._power_off()

    def _power_off(self):
        self.st.update(meter_on=False, screen='normal', prev_screen='normal', modes=[], hold=None, minmax=None,
                       rel=None, adapter_off_at=time.time() + off_delay())
        self._mark()

    def cmd_PRESS(self, arg):
        parts = [p.strip().upper() for p in arg.split(',')] if arg else []
        if not parts or parts[0] not in KEYS:
            raise _Ack(1)
        ptype = parts[1] if len(parts) > 1 else 'PRESSED'
        if ptype not in PRESS_TYPES or len(parts) > (3 if ptype == 'REPEATED' else 2):
            raise _Ack(1)
        if ptype == 'RELEASED':
            return None
        self._press(parts[0], ptype == 'HELD_DOWN')
        return None

    def _press(self, key: str, held: bool) -> None:
        st = self.st
        screen = st['screen']
        if key == 'BACKLIGHT':  # cycles on every screen (captured on the save page)
            st['backlight'] = BACKLIGHT[(BACKLIGHT.index(st.get('backlight', 'off')) + 1) % len(BACKLIGHT)]
            return self._mark()
        if key == 'ONOFF' and screen != 'power_off_confirm':
            st.update(prev_screen=screen, screen='power_off_confirm')
            return self._mark()
        if screen == 'power_off_confirm':
            if key == 'F1':
                return self._power_off()
            if key == 'F4':
                st['screen'] = st.get('prev_screen') or 'normal'
                return self._mark()
            return None
        if screen == 'minmax_result':
            if key == 'F1':    # restart
                st['minmax'] = _mm_new(self._now())
                st['screen'] = 'normal'
            elif key == 'F2':  # save...
                st['screen'] = 'save_page'
            elif key in ('MINMAX', 'F4'):  # close
                st['modes'] = [m for m in st['modes'] if m != 'MIN_MAX_AVG']
                st['minmax'] = None
                st['screen'] = 'normal'
            else:
                return None
            return self._mark()
        if screen == 'save_page':
            if key == 'F2':
                self._save_minmax()
                st['screen'] = 'minmax_result'
            elif key == 'F4':
                st['screen'] = 'minmax_result'
            else:
                return None  # F1 (+name) would open the name editor: not modelled, the page stays
            return self._mark()
        # normal screen
        running = 'MIN_MAX_AVG' in st['modes'] and st.get('minmax')
        if key == 'MINMAX' or (key == 'F4' and running):
            now = self._now()
            if running:
                _mm_fold(st, now)
                result = dataclasses.asdict(minmax_result(st, now))
                st['minmax'].update(stop=now, result=result)   # the result page shows (and saves) this session
                st['screen'] = 'minmax_result'
            elif key == 'MINMAX':
                st['modes'] = [m for m in st['modes'] if m != 'MIN_MAX_AVG'] + ['MIN_MAX_AVG']
                st['minmax'] = _mm_new(now)
            return self._mark()
        if key == 'HOLD':
            if 'HOLD' in st['modes']:
                st['modes'] = [m for m in st['modes'] if m != 'HOLD']
                st['hold'] = None
            else:
                _hold_capture(st, self._now())
                st['modes'] = st['modes'] + ['HOLD']
            return self._mark()
        if key == 'RANGE':
            prof = FUNCTIONS[st['function']]
            if not prof.auto:
                return None
            if held:
                st['autorange'] = True
                st['range_index'] = 0
            elif st.get('autorange', True):
                st['range_index'] = _pick_range(prof, st, _sample(st, st['function'], self._now()))
                st['autorange'] = False
            else:
                st['range_index'] = (int(st.get('range_index', 0)) + 1) % len(prof.ranges)
            return self._mark()
        return None  # INFO, arrows, F1-F4: menus are not modelled

    def _save_minmax(self) -> None:
        st = self.st
        now = self._now()
        mm = st.get('minmax') or {}
        # the result page froze the session when MIN MAX was stopped; later input changes do not alter it
        hdr = mm.get('result') or dataclasses.asdict(minmax_result(st, now))
        t_end = float(mm['stop']) if mm.get('stop') is not None else now
        start = float(mm.get('start', t_end))
        seq = int(st.get('next_seq', 845))
        st['next_seq'] = seq + 1
        self._mem('minmax').append({'seq': seq, 'name': f'Save {self._next_save_number()}', 'start': start,
                                    'end': t_end, 'header': hdr})

    def _next_save_number(self) -> int:
        nums = [int(m.group(1)) for lst in self.st['memory'].values() for d in lst
                if (m := re.fullmatch(r'Save (\d+)', d.get('name', '')))]
        return max(nums, default=0) + 1


def meter_command(cmd, path: str | None = None) -> bytes:
    """Run one meter command against the persisted state without BLE (no reply when the meter is off)."""
    st = load_state(path)
    if not st['meter_on']:
        return b''
    m = VirtualMeter(st)
    reply = m.execute(cmd.encode('ascii') if isinstance(cmd, str) else bytes(cmd))
    if m.changed:
        save_state(st, path)
    return reply


# --------------------------------------------------------------------------------------------------------------
# GATT table (logs/gatt_dump_1.txt)

_CCCD = _std('2902')
_GATT = [
    (0x0001, _std('1800'), 'Generic Access Profile', [
        (0x0002, _std('2a00'), ['write', 'read', 'write-without-response'], 'Device Name', []),
        (0x0004, _std('2a01'), ['read'], 'Appearance', []),
        (0x0006, _std('2a02'), ['write', 'read'], 'Peripheral Privacy Flag', []),
        (0x0008, _std('2a03'), ['write'], 'Reconnection Address', []),
        (0x000a, _std('2a04'), ['read'], 'Peripheral Preferred Connection Parameters', []),
    ]),
    (0x000c, _std('1801'), 'Generic Attribute Profile', [
        (0x000d, _std('2a05'), ['indicate'], 'Service Changed', [0x000f]),
    ]),
    (0x0010, _std('180a'), 'Device Information', [
        (0x0011, _std('2a24'), ['read'], 'Model Number String', []),
        (0x0013, _std('2a25'), ['read'], 'Serial Number String', []),
        (0x0015, _std('2a26'), ['read'], 'Firmware Revision String', []),
        (0x0017, _std('2a28'), ['read'], 'Software Revision String', []),
        (0x0019, _std('2a29'), ['read'], 'Manufacturer Name String', []),
    ]),
    (0x001b, _std('180f'), 'Battery Service', [
        (0x001c, _std('2a19'), ['notify', 'read'], 'Battery Level', [0x001e]),
    ]),
    (0x001f, _fl('1800'), 'Unknown', [
        (0x0020, _fl('2901'), ['notify', 'read'], 'Unknown', [0x0022]),
        (0x0023, _fl('290f'), ['notify', 'read'], 'Unknown', [0x0025]),
    ]),
    (0x0026, _fl('1801'), 'Unknown', [
        (0x0027, _fl('2902'), ['write', 'read'], 'Unknown', []),
        (0x0029, _fl('2903'), ['write', 'read'], 'Unknown', []),
        (0x002b, _fl('2904'), ['write'], 'Unknown', []),
        (0x002d, _fl('2905'), ['write'], 'Unknown', []),
        (0x002f, _fl('290e'), ['write', 'notify'], 'Unknown', [0x0031]),
        (0x0032, _fl('2911'), ['write', 'notify', 'read', 'write-without-response'], 'Unknown', [0x0034]),
        (0x0035, _fl('2912'), ['write', 'write-without-response'], 'Unknown', []),
    ]),
    (0x0037, _fl('1804'), 'Unknown', [
        (0x0038, _fl('2913'), ['write', 'notify', 'write-without-response'], 'Unknown', [0x003a]),
        (0x003b, _fl('2914'), ['write', 'notify', 'write-without-response'], 'Unknown', [0x003d]),
    ]),
    (0x003e, _fl('1805'), 'Unknown', [
        (0x003f, _fl('2918'), ['write', 'notify', 'read', 'write-without-response'], 'Unknown', [0x0041]),
        (0x0042, _fl('2919'), ['notify'], 'Unknown', [0x0044]),
    ]),
]
_CHAR_SERVICE = {c[1]: svc_uuid for _h, svc_uuid, _d, chars in _GATT for c in chars}


class SimGATTDescriptor:
    def __init__(self, uuid: str, handle: int, characteristic: SimGATTCharacteristic):
        self.uuid = uuid
        self.handle = handle
        self.characteristic_uuid = characteristic.uuid
        self.characteristic_handle = characteristic.handle
        self.description = 'Client Characteristic Configuration'

    def __repr__(self):
        return f'{self.uuid} (Handle: {self.handle}): {self.description}'


class SimGATTCharacteristic:
    def __init__(self, uuid: str, handle: int, properties: list, description: str, service: SimGATTService):
        self.uuid = uuid
        self.handle = handle
        self.properties = list(properties)
        self.description = description
        self.service_uuid = service.uuid
        self.service_handle = service.handle
        self.max_write_without_response_size = ATT_MAX_WRITE
        self.descriptors: list[SimGATTDescriptor] = []

    def get_descriptor(self, specifier):
        for d in self.descriptors:
            if d.handle == specifier or d.uuid == str(specifier).lower():
                return d
        return None

    def __repr__(self):
        return f'{self.uuid} (Handle: {self.handle}): {self.description}'


class SimGATTService:
    def __init__(self, uuid: str, handle: int, description: str):
        self.uuid = uuid
        self.handle = handle
        self.description = description
        self.characteristics: list[SimGATTCharacteristic] = []

    def get_characteristic(self, uuid):
        u = _norm_uuid(uuid)
        return next((c for c in self.characteristics if c.uuid == u), None)

    def __repr__(self):
        return f'{self.uuid} (Handle: {self.handle}): {self.description}'


class SimGATTServiceCollection:
    """Iterable over the services, like bleak's BleakGATTServiceCollection."""

    def __init__(self):
        self.services: dict[int, SimGATTService] = {}
        self.characteristics: dict[int, SimGATTCharacteristic] = {}
        self.descriptors: dict[int, SimGATTDescriptor] = {}
        for sh, suuid, sdesc, chars in _GATT:
            svc = SimGATTService(suuid, sh, sdesc)
            for ch, cuuid, props, cdesc, descs in chars:
                c = SimGATTCharacteristic(cuuid, ch, props, cdesc, svc)
                for dh in descs:
                    d = SimGATTDescriptor(_CCCD, dh, c)
                    c.descriptors.append(d)
                    self.descriptors[dh] = d
                svc.characteristics.append(c)
                self.characteristics[ch] = c
            self.services[sh] = svc

    def __iter__(self):
        return iter(self.services.values())

    def __getitem__(self, specifier):
        return self.get_service(specifier) or self.get_characteristic(specifier) or self.get_descriptor(specifier)

    def get_service(self, specifier):
        if isinstance(specifier, int):
            return self.services.get(specifier)
        u = _norm_uuid(specifier)
        return next((s for s in self.services.values() if s.uuid == u), None)

    def get_characteristic(self, specifier):
        if isinstance(specifier, int):
            return self.characteristics.get(specifier)
        u = _norm_uuid(specifier)
        return next((c for c in self.characteristics.values() if c.uuid == u), None)

    def get_descriptor(self, handle):
        return self.descriptors.get(handle) if isinstance(handle, int) else None


def _norm_uuid(spec) -> str:
    u = str(spec).strip().lower()
    if len(u) == 4:
        return _std(u)
    if len(u) == 8 and '-' not in u:
        return f'{u}-0000-1000-8000-00805f9b34fb'
    return u


_SERVICES = SimGATTServiceCollection()


# --------------------------------------------------------------------------------------------------------------
# bleak drop-ins: device, advertisement, scanner, client

class SimBLEDevice:
    def __init__(self, address: str, name: str | None, details=None):
        self.address = address
        self.name = name
        self.details = details

    def __repr__(self):
        return f'SimBLEDevice({self.address}, {self.name})'

    def __eq__(self, other):
        return isinstance(other, SimBLEDevice) and other.address == self.address

    def __hash__(self):
        return hash(self.address)


class SimAdvertisementData:
    def __init__(self, local_name=None, service_uuids=None, rssi=RSSI, manufacturer_data=None, service_data=None,
                 tx_power=None, platform_data=()):
        self.local_name = local_name
        self.service_uuids = list(service_uuids or [])
        self.rssi = rssi
        self.manufacturer_data = dict(manufacturer_data or {})
        self.service_data = dict(service_data or {})
        self.tx_power = tx_power
        self.platform_data = tuple(platform_data)

    def __repr__(self):
        return f'SimAdvertisementData(local_name={self.local_name!r}, service_uuids={self.service_uuids}, ' \
               f'rssi={self.rssi})'


_CONNECTED: weakref.WeakSet = weakref.WeakSet()
ADV_INTERVAL = 0.1
HEARTBEAT = 0.2   # seconds between refreshes of the <state>.link heartbeat by the connected client


def _connected_client(exclude=None, path: str | None = None):
    """A live connection of this process (to the device whose state file is path, when given)."""
    want = os.path.abspath(path) if path else None
    for c in list(_CONNECTED):
        if c is exclude or not c._connected:
            continue
        loop = c._loop
        if loop is None or loop.is_closed():
            c._connected = False
            continue
        if want is None or os.path.abspath(c._path) == want:
            return c
    return None


def link_owner(path: str | None = None, st: dict | None = None) -> dict | None:
    """The connection the virtual adapter holds, from any process ({'pid', 'token', 'boot', 'since'}), or None.

    The record lives in <state>.link. It is void after a power cycle (other boot number), when its process stops
    refreshing the heartbeat for OPEN289_SIM_LINK_TIMEOUT seconds (crashed or killed: on hardware the BLE
    supervision timeout ends such a link), or, for this process, when no live client holds that token."""
    path = path or state_path()
    lp = _link_path(path)
    try:
        mtime = os.stat(lp).st_mtime
    except OSError:
        return None
    rec = _read_json(lp)
    fresh = time.time() - mtime <= link_timeout()
    if not isinstance(rec, dict):
        return {'pid': None, 'token': None} if fresh else None   # being written right now, or garbage
    if st is None:
        st = load_state(path)
    if rec.get('boot') != st.get('boot', 0):
        return None
    if rec.get('pid') == os.getpid():
        live = any(c._connected and c._token == rec.get('token') and c._loop is not None and not c._loop.is_closed()
                   for c in list(_CONNECTED))
        return rec if live else None
    return rec if fresh else None


def _claim_link(path: str, rec: dict, st: dict) -> bool:
    """Create <state>.link for a new connection; False when another live connection holds it."""
    lp = _link_path(path)
    try:
        os.makedirs(os.path.dirname(lp), exist_ok=True)
    except OSError:
        return True
    for _ in range(5):
        try:
            fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner = link_owner(path, st)
            if owner is not None:
                return owner.get('token') == rec['token']
            _remove(lp)   # stale
            continue
        except OSError:
            return True   # no writable state folder: only the in-process rule applies
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(rec))
        return True
    return False


def _release_link(path: str, token) -> None:
    rec = _read_json(_link_path(path))
    if isinstance(rec, dict) and token is not None and rec.get('token') == token:
        _remove(_link_path(path))


@atexit.register
def _release_all_links() -> None:
    """A process that exits with a connection still open frees the adapter at once (no heartbeat wait)."""
    for c in list(_CONNECTED):
        if c._connected:
            c._connected = False
            c._ram.save(c._path, c._boot)
            _release_link(c._path, c._token)


def advertising() -> bool:
    """True while the virtual adapter advertises: switched on and not connected (it accepts one connection, from
    any process)."""
    path = state_path()
    st = load_state(path)
    return bool(st['adapter_on']) and _connected_client(path=path) is None and link_owner(path, st) is None


def _advert() -> tuple[SimBLEDevice, SimAdvertisementData]:
    rssi = RSSI + int(round(2 * _prng(7, int(time.time() * 10))))
    return (SimBLEDevice(ADDRESS, NAME),
            SimAdvertisementData(NAME, [ADV_SERVICE], rssi, {}, {}, None, ()))


async def _call(cb, *args):
    res = cb(*args)
    if inspect.isawaitable(res):
        await res


class SimBleakScanner:
    """Drop-in for bleak.BleakScanner: reports the virtual adapter about every 0.1 s while it advertises."""

    def __init__(self, detection_callback=None, service_uuids=None, scanning_mode='active', **kwargs):
        self._callback = detection_callback
        self._service_uuids = [u.lower() for u in service_uuids] if service_uuids else None
        self.scanning_mode = scanning_mode
        self._task: asyncio.Task | None = None
        self._seen: dict[str, tuple[SimBLEDevice, SimAdvertisementData]] = {}

    def _visible(self) -> tuple | None:
        if not advertising():
            return None
        if self._service_uuids is not None and ADV_SERVICE not in self._service_uuids:
            return None
        return _advert()

    async def _run(self):
        while True:
            hit = self._visible()
            if hit:
                self._seen[hit[0].address] = hit
                if self._callback is not None:
                    try:
                        await _call(self._callback, *hit)
                    except Exception:  # noqa: BLE001 - a failing user callback must not stop scanning
                        pass
            await asyncio.sleep(ADV_INTERVAL)

    async def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())
        await asyncio.sleep(0)

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.stop()

    @property
    def discovered_devices(self) -> list[SimBLEDevice]:
        return [d for d, _a in self._seen.values()]

    @property
    def discovered_devices_and_advertisement_data(self) -> dict:
        return dict(self._seen)

    @classmethod
    async def find_device_by_filter(cls, filterfunc, timeout: float = 10.0, **kwargs):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(timeout, scan_max())
        scanner = cls(**{k: v for k, v in kwargs.items() if k == 'service_uuids'})
        while True:
            hit = scanner._visible()
            if hit and filterfunc(*hit):
                return hit[0]
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(ADV_INTERVAL)

    @classmethod
    async def find_device_by_address(cls, device_identifier: str, timeout: float = 10.0, **kwargs):
        want = str(device_identifier).upper()
        return await cls.find_device_by_filter(lambda d, a: d.address.upper() == want, timeout=timeout, **kwargs)

    @classmethod
    async def find_device_by_name(cls, name: str, timeout: float = 10.0, **kwargs):
        return await cls.find_device_by_filter(lambda d, a: a.local_name == name, timeout=timeout, **kwargs)

    @classmethod
    async def discover(cls, timeout: float = 5.0, *, return_adv: bool = False, **kwargs):
        async with cls(**kwargs) as scanner:
            await asyncio.sleep(max(min(timeout, scan_max()), ADV_INTERVAL * 1.5))
        if return_adv:
            return scanner.discovered_devices_and_advertisement_data
        return scanner.discovered_devices


class SimBleakClient:
    """Drop-in for bleak.BleakClient talking to the virtual IR3000 FC + Fluke 289."""

    def __init__(self, address_or_ble_device, *args, timeout: float = 10.0, disconnected_callback=None,
                 services=None, **kwargs):
        for a in args:  # accept bleak's (device, disconnected_callback) and (device, timeout) positional forms
            if callable(a) and disconnected_callback is None:
                disconnected_callback = a
            elif isinstance(a, (int, float)) and not isinstance(a, bool):
                timeout = float(a)
        self._address = str(getattr(address_or_ble_device, 'address', address_or_ble_device))
        self._timeout = timeout
        self._disconnected_callback = disconnected_callback
        self._connected = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._path = state_path()
        self._st: dict | None = None
        self._key = None
        self._boot = 0
        self._token: str | None = None
        self._ram = _AdapterMemory()
        self._armed = False
        self._cnx = b'\x00'
        self._subs: dict[str, object] = {}
        self._tasks: set[asyncio.Task] = set()
        self._live_task: asyncio.Task | None = None
        self._send_task: asyncio.Task | None = None
        self._last_packet = bytes(16)

    # -- bleak API
    @property
    def address(self) -> str:
        return self._address

    @property
    def name(self) -> str:
        return NAME

    @property
    def mtu_size(self) -> int:
        return MTU

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def services(self) -> SimGATTServiceCollection:
        if not self._connected:
            raise BleakError('Service Discovery has not been performed yet')
        return _SERVICES

    def set_disconnected_callback(self, callback, **kwargs) -> None:
        self._disconnected_callback = callback

    async def connect(self, **kwargs) -> None:
        if self._connected:
            return
        await asyncio.sleep(min(sim_delay(), 0.05))
        self._path = state_path()
        st = load_state(self._path)
        token = _uuid.uuid4().hex
        rec = {'pid': os.getpid(), 'token': token, 'boot': int(st.get('boot', 0)), 'since': round(time.time(), 3)}
        # one connection at a time, whichever process holds it (the adapter stops advertising while connected)
        if (not st['adapter_on'] or self._address.upper() != ADDRESS
                or _connected_client(exclude=self, path=self._path) is not None
                or link_owner(self._path, st) is not None or not _claim_link(self._path, rec, st)):
            raise BleakDeviceNotFoundError(self._address, f'Device with address {self._address} was not found.')
        if _file_key(self._path) is None:
            save_state(st, self._path)
        self._st, self._key = st, _file_key(self._path)
        self._boot, self._token = rec['boot'], token
        self._ram = _AdapterMemory.load(self._path, self._boot)   # adapter RAM survives reconnects, not reboots
        self._loop = asyncio.get_running_loop()
        self._connected = True
        self._armed = False      # every connection starts unarmed: the CNX ID write arms the control point
        self._cnx = b'\x00'
        self._subs = {}
        _CONNECTED.add(self)
        self._spawn(self._watch())

    async def disconnect(self) -> None:
        if not self._connected:
            return
        self._teardown()
        await asyncio.sleep(0)
        self._fire_disconnected()

    async def pair(self, *args, **kwargs) -> None:
        return None

    async def unpair(self) -> None:
        return None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    async def read_gatt_char(self, char_specifier, **kwargs) -> bytearray:
        c = self._char(char_specifier)
        if 'read' not in c.properties:
            raise BleakGATTProtocolError(0x02)
        await asyncio.sleep(0)
        return bytearray(self._read_value(c.uuid))

    async def write_gatt_char(self, char_specifier, data, response=None) -> None:
        c = self._char(char_specifier)
        data = bytes(data)
        forbidden = is_forbidden_write(c.uuid, data)
        if not ({'write', 'write-without-response'} & set(c.properties)):
            _audit(c.uuid, data, forbidden, response, 'write not permitted')
            raise BleakGATTProtocolError(0x03)
        limit = CP_MAX_WRITE if c.uuid == CP else ATT_MAX_WRITE
        if len(data) > limit:
            _audit(c.uuid, data, forbidden, response, 'invalid attribute value length')
            raise BleakGATTProtocolError(0x0D)
        _audit(c.uuid, data, forbidden, response)
        await asyncio.sleep(0)
        if not self._connected:
            raise BleakError('Not connected')
        if c.uuid == CP:
            self._cp_write(data)
        elif c.uuid == CNX_ID:
            self._cnx = data[:1] or b'\x00'
            self._armed = True
        elif c.uuid == USER_STRING:
            st = self._fresh()
            st['user_string'] = data.decode('latin-1')
            self._commit()
        # everything else (including the firmware-update characteristics) is accepted and ignored

    async def start_notify(self, char_specifier, callback, **kwargs) -> None:
        c = self._char(char_specifier)
        if not ({'notify', 'indicate'} & set(c.properties)):
            raise BleakError(f'Characteristic {c.uuid} does not support notifications or indications')
        self._subs[c.uuid] = callback
        if c.uuid == READING and (self._live_task is None or self._live_task.done()):
            self._live_task = self._spawn(self._live_loop())
        await asyncio.sleep(0)

    async def stop_notify(self, char_specifier) -> None:
        c = self._char(char_specifier)
        self._subs.pop(c.uuid, None)
        if c.uuid == READING and self._live_task is not None:
            self._live_task.cancel()
            self._live_task = None
        await asyncio.sleep(0)

    # -- internals
    def _char(self, spec) -> SimGATTCharacteristic:
        if not self._connected:
            raise BleakError('Not connected')
        if isinstance(spec, SimGATTCharacteristic):
            return spec
        if isinstance(spec, int):
            c = _SERVICES.get_characteristic(spec)
        else:
            key = spec if isinstance(spec, (str, _uuid.UUID)) else getattr(spec, 'uuid', spec)
            c = _SERVICES.get_characteristic(str(key))
        if c is None:
            raise BleakCharacteristicNotFoundError(spec)
        return c

    def _spawn(self, coro) -> asyncio.Task:
        t = self._loop.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def _fresh(self) -> dict:
        key = _file_key(self._path)
        if key != self._key or self._st is None:
            self._st, self._key = load_state(self._path), key
        return _apply_timers(self._st)

    def _commit(self) -> None:
        save_state(self._st, self._path)
        self._key = _file_key(self._path)

    @property
    def _mem(self) -> _AdapterMemory:
        return self._ram

    def _teardown(self, keep_ram: bool = True) -> None:
        if self._connected:
            if keep_ram:
                self._ram.save(self._path, self._boot)   # handed to the next connection (any process)
            _release_link(self._path, self._token)
        self._connected = False
        self._subs = {}
        _CONNECTED.discard(self)
        current = asyncio.current_task() if self._loop is not None and self._loop.is_running() else None
        for t in list(self._tasks):
            if t is not current:
                t.cancel()
        self._live_task = None
        self._send_task = None

    def _fire_disconnected(self) -> None:
        cb = self._disconnected_callback
        if cb is not None:
            try:
                cb(self)
            except Exception:  # noqa: BLE001
                pass

    def _drop_link(self, keep_ram: bool = True) -> None:
        """The link is lost (adapter switched itself off or was power-cycled): bleak reports the disconnect."""
        if not self._connected:
            return
        self._teardown(keep_ram)
        self._fire_disconnected()

    def _heartbeat(self) -> bool:
        """Refresh <state>.link; False when another connection has taken the adapter over meanwhile."""
        lp = _link_path(self._path)
        rec = _read_json(lp)
        if not isinstance(rec, dict):   # removed (or unreadable): claim it again
            rec = {'pid': os.getpid(), 'token': self._token, 'boot': self._boot, 'since': round(time.time(), 3)}
            return _claim_link(self._path, rec, self._st or load_state(self._path))
        if rec.get('token') != self._token:
            return False
        try:
            os.utime(lp)
        except OSError:
            pass
        return True

    async def _watch(self):
        """Follows state changes made by other processes (python -m open289.sim ...), the adapter's own switch-off
        after a meter OFF and power cycles, and keeps the connection's heartbeat fresh."""
        last_beat = time.monotonic()
        while self._connected:
            await asyncio.sleep(0.05)
            st = self._fresh()
            if int(st.get('boot', 0)) != self._boot:
                self._drop_link(keep_ram=False)   # power-cycled / reset: the real adapter ends the BLE link
                return
            if not st['adapter_on']:
                self._commit()  # persist a switch-off that so far only happened in memory (OFF timer)
                self._drop_link()
                return
            if time.monotonic() - last_beat >= HEARTBEAT:
                last_beat = time.monotonic()
                if not self._heartbeat():
                    self._drop_link()   # our heartbeat lapsed and another process connected
                    return

    def _notify(self, uuid: str, data: bytes) -> None:
        if uuid in self._subs and self._connected:
            self._loop.call_soon(self._deliver, uuid, bytes(data))

    def _deliver(self, uuid: str, data: bytes) -> None:
        cb = self._subs.get(uuid)
        if cb is None or not self._connected:
            return
        res = cb(_SERVICES.get_characteristic(uuid), bytearray(data))
        if inspect.isawaitable(res):
            self._spawn(res)

    def _cp(self, status: int, op: int, payload: bytes = b'') -> None:
        self._notify(CP, bytes([status, op]) + payload)

    def _read_value(self, uuid: str) -> bytes:
        st = self._fresh()
        if uuid == DIS_MODEL:
            return (METER_MODEL if st['meter_on'] else DETACHED_MODEL).encode()
        fixed = {
            _std('2a00'): NAME.encode(), _std('2a01'): b'\x00\x00', _std('2a02'): b'\x00',
            _std('2a04'): bytes.fromhex('5000a0000000e803'), _std('2a25'): SERIAL.encode() + b'\0',
            _std('2a26'): RADIO_FW.encode() + b'\0', _std('2a28'): METER_FW.encode() + b'\0',
            _std('2a29'): b'Fluke Mfg Co.\0', _std('2a19'): b'\x64',
            ASCII_READING: bytes([1, 2, 3, 4, 5]) + bytes(12),
            _fl('2911'): b'\x00',
        }
        if uuid in fixed:
            return fixed[uuid]
        if uuid == CNX_ID:
            return self._cnx
        if uuid == USER_STRING:
            return str(st.get('user_string', NAME)).encode('latin-1')
        if uuid == CP:
            return self._mem.cp
        if uuid == READING:
            if self._meter_live(st):
                self._last_packet = live_packet(st)
            elif not st['meter_on']:
                return bytes(16)
            return self._last_packet
        return b''

    @staticmethod
    def _meter_live(st: dict) -> bool:
        return st['adapter_on'] and st['meter_on'] and not st['adapter_hung'] and st['screen'] not in MODAL

    async def _live_loop(self):
        period = 1.0 / sim_rate()
        while self._connected:
            await asyncio.sleep(period)
            if not self._connected or READING not in self._subs:
                return
            st = self._fresh()
            if not self._meter_live(st):  # nothing while off, hung, or on a menu / result page (captured)
                continue
            self._last_packet = live_packet(st)
            self._notify(READING, self._last_packet)

    # -- the adapter control point
    def _cp_write(self, data: bytes) -> None:
        mem = self._mem
        mem.cp = data
        st = self._fresh()
        if not self._armed or st['adapter_hung'] or not data:
            return  # stored, but no reply: unarmed (no CNX ID write yet on this connection) or hung
        op = data[0]
        if op == 0x81:      # CLEAR: OUT and IN buffers
            mem.out.clear()
            mem.inbuf.clear()
            self._cp(ST_COMPLETE, op)
        elif op == 0x8D:    # APPEND to OUT
            mem.out += data[1:]
            self._cp(ST_COMPLETE, op)
        elif op == 0x8E:    # SEND OUT (+CR) to the meter
            if self._send_task is not None and not self._send_task.done():
                self._cp(ST_FAILED, op)  # GUESS: a second SEND while one is in flight
                return
            self._cp(ST_IN_PROCESS, op)
            self._send_task = self._spawn(self._finish_send())
        elif op == 0x80:    # GET_BUFFER_SIZE (IN)
            self._cp(ST_COMPLETE, op, struct.pack('<I', len(mem.inbuf)))
        elif op == 0x82:    # GET_BUFFER_DATA off,len -> BUF notifications of <= 20 bytes
            if len(data) < 9:
                self._cp(ST_FAILED, op)  # GUESS
                return
            off, ln = struct.unpack_from('<II', data, 1)
            self._cp(ST_IN_PROCESS, op)
            chunk = bytes(mem.inbuf[off:off + ln])
            for i in range(0, len(chunk), 20):
                self._notify(BUF, chunk[i:i + 20])
            self._cp(ST_COMPLETE, op)
        elif op == 0x83:    # CANCEL (GUESS: aborts a SEND in flight)
            if self._send_task is not None and not self._send_task.done():
                self._send_task.cancel()
                self._send_task = None
                self._cp(ST_CANCELLED, 0x8E)
            self._cp(ST_COMPLETE, op)
        elif op == 0x8F:    # GET_ADAPTER_VERSION
            self._cp(ST_COMPLETE, op, f'{MSP_FW},'.encode())
        elif op == 0x95:    # GET_MODEL_NAME: empty with a 289
            self._cp(ST_COMPLETE, op)
        elif 0x84 <= op <= 0x8C or 0x90 <= op <= 0x94:
            self._cp(ST_INCORRECT_MODEL, op)
        else:
            self._cp(ST_FAILED, op)

    async def _finish_send(self):
        await asyncio.sleep(sim_delay())
        if not self._connected:
            return
        mem = self._mem
        st = self._fresh()
        out = bytes(mem.out)
        mem.out.clear()
        if st['adapter_hung']:
            return
        if len(out) >= HANG_AT:
            mem.inbuf.clear()
            st['adapter_hung'] = True   # measured: replies 03 8E, then no control-point reply until power-cycled
            self._commit()
        elif len(out) > OUT_OK or not (st['meter_on'] and st['adapter_on']):
            # 129..372 bytes: SEND completes with an empty IN buffer (measured for 181 bytes). GUESS: the same with
            # the meter off (nothing answers)
            mem.inbuf.clear()
        else:
            # GUESS: a normal SEND appends to IN (open289 always CLEARs first, so this was never observed)
            meter = VirtualMeter(st)
            mem.inbuf += meter.run(out + b'\r')
            if meter.changed:
                self._commit()
        self._cp(ST_COMPLETE, 0x8E)


# --------------------------------------------------------------------------------------------------------------
# Command line: python -m open289.sim ...

def _bool(v: str) -> bool:
    low = v.strip().lower()
    if low in ('1', 'true', 'on', 'yes'):
        return True
    if low in ('0', 'false', 'off', 'no'):
        return False
    raise ValueError(f'not a boolean: {v!r}')


def _input_value(v: str):
    low = v.strip().lower()
    if low in ('none', 'null', 'open', 'ol'):
        return None
    return float(v)


def apply_setting(st: dict, key: str, raw: str) -> None:
    """One 'key=value' of 'python -m open289.sim set'. Raises ValueError on a bad key or value."""
    k = key.strip()
    kl = k.lower()
    v = raw.strip()
    _mm_fold(st, meter_now(st))   # MIN MAX samples taken under the old setting stay in the accumulators
    if kl == 'function':
        name = v.upper().replace('/', '_')
        sec = 'NONE'
        if name in ('V_AC_HZ', 'V_AC_HERTZ', 'V_DC_HZ', 'V_DC_HERTZ'):
            name, sec = name[:4], 'HERTZ'
        if name not in FUNCTIONS:
            raise ValueError(f'unknown function {v!r} (one of {", ".join(FUNCTIONS)}, V_AC_HZ, V_DC_HZ)')
        st.update(function=name, sec=sec, autorange=True, range_index=0, modes=[], hold=None, minmax=None,
                  rel=None, screen='normal', prev_screen='normal')
    elif kl in ('sec', 'secondary'):
        sec = v.upper()
        if sec not in ('NONE', 'HERTZ'):
            raise ValueError('sec must be NONE or HERTZ')
        if sec == 'HERTZ' and not FUNCTIONS[st['function']].hz:
            raise ValueError(f'{st["function"]} has no Hz secondary function')
        if sec == 'HERTZ' and _rel_active(st):
            raise ValueError('REL with the Hz secondary function is not modelled: set modes=NONE first')
        st.update(sec=sec, hold=None, modes=[m for m in st['modes'] if m != 'HOLD'])
    elif kl == 'input':
        st['inputs'][st['function']] = _input_value(v)
    elif kl == 'hz':
        st['inputs']['HZ'] = float(v)
    elif kl.startswith('inputs.'):
        name = k.split('.', 1)[1].upper()
        if name not in FUNCTIONS and name != 'HZ':
            raise ValueError(f'unknown input {name!r}')
        st['inputs'][name] = _input_value(v)
    elif kl in ('meter_on', 'adapter_on', 'adapter_hung', 'autorange'):
        st[kl] = _bool(v)
        if kl in ('meter_on', 'adapter_on') and st[kl]:
            st['adapter_off_at'] = None
        if kl == 'meter_on' and not st[kl]:
            st.update(screen='normal', modes=[], hold=None, minmax=None, rel=None)
    elif kl == 'range_index':
        n = int(v)
        if not 0 <= n < len(FUNCTIONS[st['function']].ranges):
            raise ValueError(f'range_index out of range for {st["function"]}')
        st.update(range_index=n, autorange=False)
    elif kl == 'screen':
        if v not in SCREENS:
            raise ValueError(f'screen must be one of {", ".join(SCREENS)}')
        st['screen'] = v
    elif kl == 'backlight':
        if v not in BACKLIGHT:
            raise ValueError(f'backlight must be one of {", ".join(BACKLIGHT)}')
        st['backlight'] = v
    elif kl == 'modes':
        set_modes(st, [] if v.upper() in ('', 'NONE') else [m.strip().upper() for m in v.split(',')])
    elif kl == 'seed':
        st['seed'] = int(v)
    elif kl == 'clock_offset':
        new = float(v)
        old = meter_epoch(st)
        st['clock_offset'] = new
        _mm_rebase(st, old, meter_epoch(st))
    elif kl == 'user_string':
        st['user_string'] = v
    elif kl.startswith('props.'):
        st['props'][k.split('.', 1)[1]] = v
    else:
        raise ValueError(f'unknown key {k!r}')


def summary(st: dict) -> dict:
    owner = link_owner(state_path(), st)
    d = {
        'state_file': state_path(),
        'audit_file': audit_path(),
        'adapter': {'address': ADDRESS, 'on': st['adapter_on'], 'hung': st['adapter_hung'],
                    'off_at': st['adapter_off_at'], 'boot': st.get('boot', 0),
                    'connected_pid': owner.get('pid') if owner else None,
                    'advertising': bool(st['adapter_on']) and owner is None},
        'meter': {k: st[k] for k in ('meter_on', 'function', 'sec', 'autorange', 'range_index', 'modes', 'screen',
                                     'backlight')} | {'hold': st['hold'] is not None, 'minmax': st['minmax'],
                                                      'rel': st.get('rel'), 'inputs': st['inputs']},
        'memory': {k: [x['name'] for x in st['memory'].get(k, [])]
                   for k in ('recordings', 'minmax', 'peak', 'measurements')},
    }
    if st['meter_on'] and st['screen'] == 'normal':
        d['display'] = format_qdda(build_display(st))
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python -m open289.sim',
                                 description='Control the virtual IR3000 FC + Fluke 289 used by OPEN289_SIM=1 / --sim.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('reset', help='factory state (meter on in V DC, adapter awake, sample saved memory)')
    sub.add_parser('show', help='print the state as JSON')
    sub.add_parser('power-cycle', help='switch adapter and meter on again (clears an adapter hang)')
    p_set = sub.add_parser('set', help='change the state, e.g. set function=V_AC meter_on=false input=open '
                                       'modes=REL (modes: NONE or any of HOLD, MIN_MAX_AVG, REL, REL_PERCENT)')
    p_set.add_argument('assignments', nargs='+', metavar='key=value')
    p_audit = sub.add_parser('audit', help='print the GATT writes recorded by the simulator')
    p_audit.add_argument('--clear', action='store_true', help='delete the audit log')
    args = ap.parse_args(argv)

    if args.cmd == 'reset':
        st = reset_state()
    elif args.cmd == 'power-cycle':
        st = power_cycle()
    elif args.cmd == 'set':
        st = load_state()
        try:
            for a in args.assignments:
                key, eq, val = a.partition('=')
                if not eq:
                    raise ValueError(f'expected key=value, got {a!r}')
                apply_setting(st, key, val)
        except ValueError as e:
            print(f'error: {e}', file=sys.stderr)
            return 2
        save_state(st)
    elif args.cmd == 'audit':
        if args.clear:
            clear_audit()
            return 0
        for rec in read_audit():
            print(json.dumps(rec))
        return 0
    else:
        st = load_state()
    print(json.dumps(summary(st), indent=2))
    return 0


__all__ = [
    'ADDRESS', 'NAME', 'SERIAL', 'ADV_SERVICE', 'CP', 'BUF', 'CNX_ID', 'READING', 'FUNCTIONS',
    'SimBLEDevice', 'SimAdvertisementData', 'SimBleakScanner', 'SimBleakClient',
    'SimGATTServiceCollection', 'SimGATTService', 'SimGATTCharacteristic', 'SimGATTDescriptor',
    'VirtualMeter', 'build_display', 'live_packet', 'live_record', 'format_qm', 'format_qdda',
    'encode_double', 'encode_reading30', 'encode_meas_header', 'encode_qsmr', 'encode_qmmsi', 'encode_qpsi',
    'encode_qrsi', 'encode_qsrr', 'encode_record', 'encode_notification',
    'load_state', 'save_state', 'reset_state', 'power_cycle', 'default_state', 'initial_memory', 'apply_setting',
    'set_modes', 'read_audit', 'clear_audit', 'is_forbidden_write', 'meter_command', 'advertising', 'link_owner',
    'main',
]

if __name__ == '__main__':
    sys.exit(main())
