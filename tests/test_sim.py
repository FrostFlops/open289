"""Tests of the virtual IR3000 FC + Fluke 289 (open289.sim): encoders against real captures, the adapter control
point as measured on hardware, the meter key state machine, persistence, the audit log, and open289's own Adapter
and Meter classes running end to end against SimBleakClient.

Run: .venv\\Scripts\\python.exe -m pytest tests/test_sim.py -q
(tests/conftest.py gives every test its own OPEN289_HOME and fast simulator timing.)
"""
import asyncio
import csv
import dataclasses
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from bleak.exc import BleakDeviceNotFoundError, BleakError, BleakGATTProtocolError

from open289 import sim
from open289.adapter import Adapter, AdapterError
from open289.meter import (MAPS, OVERLOAD, BinReading, MeasHeader, Meter, MeterError, Session, TrendRecord,
                           parse_meas_header, parse_qmmsi, parse_qrsi, parse_qsmr, parse_qsrr, ts_text)
from open289.reading import decode_notification, decode_record

ROOT = Path(__file__).resolve().parents[1]
DIS_MODEL = '00002a24-0000-1000-8000-00805f9b34fb'
FW_CP = 'b6982911-7562-11e2-b50d-00163e46f8fe'
FW_RADIO = 'B6982913-7562-11E2-B50D-00163E46F8FE'


def hx(s: str) -> bytes:
    return bytes.fromhex(re.sub(r'\s+', '', s))


# Real replies captured on 2026-11-06 (Fluke 289 V1.71 through IR3000 FC MSP 01.00.15); payloads after b'#0'.
QDDB_PROBE3 = hx(  # logs/probe_adapter_3.txt
    '0300 0000 0100 0100 00001440 00000000 0000 0000 00000000 00000000 0000 0000 0200'
    '0100 cb3f1440c8293a92 0100 0000 0400 0500 0200 0000 8fbbda4100508c28'
    '0200 cb3f1440c8293a92 0100 0000 0400 0500 0200 0000 8fbbda4100508c28')
QDDB_STATE = hx(  # logs/state_check.txt
    '0300 0000 0000 0100 00001440 00000000 0000 0000 00000000 00000000 0000 0000 0200'
    '0100 92cbff3f7ac7293a 0100 0000 0400 0500 0200 0000 92bbda4100f0ef7f'
    '0200 92cbff3f7ac7293a 0100 0000 0400 0500 0200 0000 92bbda4100f0ef7f')
QRSI_0 = hx(  # logs/state_check.txt, 'QRSI 0' (114 bytes; nothing in it needed redaction)
    '03030000 92bbda4100e0cf50 92bbda4100f0b35f 0000f03f00000000 e17aa43f7b14ae47 04030000 48000000'
    '0300 0000 0000 0100 00001440 00000000 0000 0100 00000000 00000000 2000 0000 0100'
    '0200 63ccff3f5c2041f1 0100 0000 0400 0500 0200 0000 92bbda4100f0b35f'
    '53617665 2034')
# V AC with REL and then REL % (logs/monitor_functions.jsonl, 2026-11-06 21:26:27 and 21:26:31 meter time):
# LIVE 0.3486 V, PRIMARY -0.6988 V / -66.72 %, REL_LIVE, REL_REFERENCE 1.0474 V taken when REL was switched on.
QDDB_REL = hx(
    '0100 0000 0000 0200 00001440 00000000 0000 0000 00000000 00000000 4000 0000 0400'
    '0100 764fd63facadd85f 0200 0000 0400 0500 0200 0000 91bbda410020 4981'
    '0200 915ce6bfce3b4ed1 0200 0000 0400 0500 0200 0000 91bbda410020 4981'
    '0400 764fd63facadd85f 0200 0000 0400 0500 0200 0000 91bbda410020 4981'
    '0b00 26c2f03f52499d80 0200 0000 0400 0500 0200 0000 91bbda410050 607b')
QDDB_REL_PCT = hx(
    '0100 0000 0000 0200 00001440 00000000 0000 0000 00000000 00000000 8000 0000 0400'
    '0100 764fd63facadd85f 0200 0000 0400 0500 0200 0000 91bbda410070 c381'
    '0200 14ae50c0ae47e17a 1000 0000 0200 0500 0200 0000 91bbda410070 c381'
    '0400 764fd63facadd85f 0200 0000 0400 0500 0200 0000 91bbda410070 c381'
    '0b00 26c2f03f52499d80 0200 0000 0400 0500 0200 0000 91bbda410050 607b')
# Live 16-byte notifications captured on B698290F (logs/live_1.hex, logs/monitor_functions.analysis.txt).
LIVE_CAPTURES = {
    'V_DC 5.0649': 'd9c50008020c0000 0000000000000000',
    'V_AC/Hz 20.001 + 1.0444 V AC': '214e000605020018 cc28000801020000',
    'OHMS OL': 'ffff9f220b280000 0000000000000000',
    'CONTINUITY OL open': 'ffff9f040b270004 0000000000000000',
    'TEMPERATURE 26.1': '0501000207220000 0000000000000000',
    'V_AC dBm + Hz': '05010004110200005fea000605020000',
    'INVALID': 'ffff7f0000020000 0000000000000000',
    'negative': 'd2040088020c0500 0000000000000000',
}
# Rows of logs/recording0.csv (always checked; the full file is compared too when it is present).
CSV_SAMPLE = [
    (0, '2026-11-06 21:40:19.248', '2026-11-06 21:40:19.752', '1.4904000000000002', '1.4903000000000002',
     '1.49032', '1.4904000000000002', 5, 'INTERVAL', 'STABLE'),
    (8, '2026-11-06 21:40:26.691', '2026-11-06 21:40:26.892', '1.4902', '1.4902', '1.651', '1.8118', 2, 'INPUT',
     'UNSTABLE'),
    (70, '2026-11-06 21:41:18.710', '2026-11-06 21:41:18.811', '1.9876', '1.9876', '1.9876', '1.9876', 1, 'INPUT',
     'STABLE'),
    (71, '2026-11-06 21:41:18.811', '2026-11-06 21:41:18.811', '1.9874', '1.9874', '1.9874', '1.9874', 1,
     'INTERVAL', 'STABLE'),
]


def run(coro, timeout=30.0):
    async def guarded():
        return await asyncio.wait_for(coro, timeout)
    return asyncio.run(guarded())


async def connect(**kw) -> sim.SimBleakClient:
    c = sim.SimBleakClient(sim.ADDRESS, **kw)
    await c.connect()
    return c


class Probe:
    """Raw control-point access to the virtual adapter (what tools/probe_adapter.py did on hardware)."""

    def __init__(self, client):
        self.c = client
        self.q: asyncio.Queue = asyncio.Queue()
        self.buf = bytearray()
        self.chunks: list[bytes] = []

    async def subscribe(self):
        await self.c.start_notify(sim.CP, lambda _h, d: self.q.put_nowait(bytes(d)))
        await self.c.start_notify(sim.BUF, self._on_buf)

    def _on_buf(self, _h, d):
        self.chunks.append(bytes(d))
        self.buf.extend(d)

    async def arm(self):
        await self.c.write_gatt_char(sim.CNX_ID, b'\x01', response=True)

    async def cmd(self, payload: bytes, wait: float = 1.0) -> list[bytes]:
        """Write one CP command; collect CP notifications until a final status for its opcode (or `wait`)."""
        await self.c.write_gatt_char(sim.CP, payload, response=True)
        loop = asyncio.get_running_loop()
        end = loop.time() + wait
        got = []
        while True:
            left = end - loop.time()
            if left <= 0:
                return got
            try:
                m = await asyncio.wait_for(self.q.get(), left)
            except (asyncio.TimeoutError, TimeoutError):
                return got
            got.append(m)
            if len(m) >= 2 and m[1] == payload[0] and m[0] != 1:
                return got

    async def send(self, out: bytes) -> list[bytes]:
        """CLEAR, APPEND in 17-byte parts, SEND; returns the SEND notifications."""
        assert (await self.cmd(b'\x81'))[-1] == b'\x03\x81'
        for i in range(0, len(out), 17):
            assert (await self.cmd(b'\x8d' + out[i:i + 17]))[-1] == b'\x03\x8d'
        return await self.cmd(b'\x8e')

    async def in_size(self) -> int:
        size = await self.cmd(b'\x80')
        assert size[-1][:2] == b'\x03\x80'
        return int.from_bytes(size[-1][2:6], 'little')

    async def reply(self) -> bytes:
        n = await self.in_size()
        self.buf.clear()
        self.chunks.clear()
        r = await self.cmd(b'\x82' + (0).to_bytes(4, 'little') + n.to_bytes(4, 'little'))
        assert r == [b'\x01\x82', b'\x03\x82']
        return bytes(self.buf)


async def open_meter(client) -> tuple[Adapter, Meter]:
    a = Adapter(client)
    await a.start()
    return a, Meter(a)


async def press(m: Meter, *keys: str) -> None:
    for k in keys:
        ack, _ = await m.raw(f'PRESS {k}', unsafe=True)
        assert ack == 'OK', (k, ack)


def reading(h: MeasHeader, rid: str) -> BinReading:
    return next(r for r in h.readings if r.reading_id == rid)


def set_state(**kv) -> None:
    st = sim.load_state()
    for k, v in kv.items():
        sim.apply_setting(st, k, str(v))
    sim.save_state(st)


def qddb() -> MeasHeader:
    """QDDB straight from the persisted state (no BLE)."""
    reply = sim.meter_command('QDDB')
    assert reply[:4] == b'0\r#0', reply
    return parse_meas_header(reply[4:-1], 0)[0]


def sim_press(*keys: str) -> None:
    for k in keys:
        assert sim.meter_command(f'PRESS {k}') == b'0\r', k


# ---------------------------------------------------------------------------------------------------------------
# Encoders: exact inverses of the decoders, checked against real captures


def test_double_encoding_matches_meter_layout():
    assert sim.encode_double(5.0) == hx('00001440 00000000')
    rnd = random.Random(289)
    for _ in range(200):
        x = rnd.uniform(-1e9, 1e9)
        from open289.meter import fdouble
        assert fdouble(sim.encode_double(x), 0) == x


@pytest.mark.parametrize('payload', [QDDB_PROBE3, QDDB_STATE], ids=['probe_adapter_3', 'state_check'])
def test_qddb_encoder_reproduces_captured_bytes(payload):
    h, end = parse_meas_header(payload, 0)
    assert end == len(payload) == 34 + 30 * len(h.readings)
    rebuilt = MeasHeader(h.prim, h.sec, h.autorange, h.unit, h.range_max, h.multiplier, h.bolt, h.minmax_start,
                         list(h.mode), [dataclasses.replace(r) for r in h.readings])
    assert sim.encode_meas_header(rebuilt) == payload


def test_qrsi_encoder_reproduces_captured_bytes_and_sim_serves_them():
    s = parse_qrsi(QRSI_0, 0)
    assert (s.name, s.reading_index, s.num_samples, s.interval, s.threshold) == ('Save 4', 772, 72, 1.0, 0.04)
    assert sim.encode_qrsi(s) == QRSI_0
    # the simulator's own recording #0 is byte-identical to the real meter's reply
    assert sim.meter_command('QRSI 0') == b'0\r#0' + QRSI_0 + b'\r'


def test_session_and_trend_record_round_trips():
    st = sim.default_state()
    mem = st['memory']
    for kind, enc, parse, lst in (('measurement', sim.encode_qsmr, parse_qsmr, mem['measurements']),
                                  ('peak', sim.encode_qmmsi, parse_qmmsi, mem['peak']),
                                  ('recording', sim.encode_qrsi, parse_qrsi, mem['recordings'])):
        for i, d in enumerate(lst):
            s = sim.session_from(kind, i, d)
            b = enc(s)
            back = parse(b, i, 'peak') if kind == 'peak' else parse(b, i)
            assert back == s, kind
            assert enc(back) == b
    # min/max session with every reading kind, plus a header code the qemap tables do not name
    h = MeasHeader('V_AC', 'HERTZ', 'MANUAL', 'unit#99', 50.0, -3, 'ON', 1794000000.5, ['HOLD', 'MIN_MAX_AVG', 'REL'],
                   [BinReading(rid, -1.25 * k, 'VAC', -3, 2, 5, st_, 'NONE', 1794000000.0 + k)
                    for k, (rid, st_) in enumerate([('LIVE', 'NORMAL'), ('PRIMARY', 'OL'), ('MINIMUM', 'OL_MINUS'),
                                                    ('MAXIMUM', 'INVALID'), ('AVERAGE', 'NORMAL')])])
    s = Session('minmax', 3, 4242, 'Save 9', h, start=1.5, end=2.5)
    b = sim.encode_qmmsi(s)
    assert parse_qmmsi(b, 3) == s and sim.encode_qmmsi(parse_qmmsi(b, 3)) == b
    # trend records: AVERAGE travels as a sum and comes back as the same mean
    rec = mem['recordings'][0]
    tmpl = rec['header']['readings'][0]
    for row in rec['records']:
        tr = sim.trend_record_from(row, tmpl)
        b = sim.encode_qsrr(tr)
        assert len(b) == 146
        assert parse_qsrr(b) == tr
        assert sim.encode_qsrr(parse_qsrr(b)) == b


def test_live_record_round_trip_and_captured_packets():
    for name, h in LIVE_CAPTURES.items():
        pkt = hx(h)
        p, s = decode_notification(pkt)
        assert sim.encode_notification(p, s) == pkt, name
        assert sim.encode_record(decode_record(pkt[:8])) == pkt[:8], name
    rnd = random.Random(7)
    widths = {name: w for name, _off, w in sim._LIVE_FIELDS}
    for _ in range(500):
        fields = {name: rnd.randrange(1 << w) for name, w in widths.items()}
        r = decode_record(sim.encode_record(**fields))
        assert {k: int(getattr(r, k)) for k in fields} == fields
    with pytest.raises(ValueError):
        sim.encode_record(decimals=8)


def test_live_encoding_of_display_readings_matches_hardware():
    def br(v, unit, mult, dec, digits, state='NORMAL', attr='NONE'):
        return BinReading('PRIMARY', v, unit, mult, dec, digits, state, attr, 0.0)
    assert sim.live_record(br(5.0649, 'VDC', 0, 4, 5), 12) == hx('d9c50008020c0000')
    assert sim.live_record(br(-0.1234, 'VDC', 0, 4, 5), 12)[:6] == hx('d2040088020c')
    assert sim.live_record(br(20.001, 'Hz', 0, 3, 5, attr='POSITIVE_EDGE'), 2) == hx('214e000605020018')
    assert sim.live_record(br(1.0444, 'VAC', 0, 4, 5), 2) == hx('cc28000801020000')
    assert sim.live_record(br(OVERLOAD, 'OHM', 6, 1, 4, 'OL'), 40) == hx('ffff9f220b280000')
    assert sim.live_record(br(OVERLOAD, 'OHM', 0, 2, 5, 'OL', 'OPEN_CIRCUIT'), 39) == hx('ffff9f040b270004')
    assert sim.live_record(br(26.1, 'CEL', 0, 1, 5), 34) == hx('0501000207220000')


@pytest.mark.parametrize('setting, want', [
    ('V_DC', dict(function=12, unit=2, decimals=4, magnitude=0, state=0, second=None)),
    ('MV_DC', dict(function=11, unit=2, magnitude=4, state=0, second=None)),
    ('V_AC_HZ', dict(function=2, unit=5, attribute=6, decimals=3, state=0, second=(2, 1))),
    ('OHMS', dict(function=40, unit=11, state=4, magnitude=2, second=None)),
    ('CONTINUITY', dict(function=39, unit=11, state=4, attribute=1, second=None)),
    ('TEMPERATURE', dict(function=34, unit=7, decimals=1, state=0, second=None)),
])
def test_live_packets_per_function(setting, want):
    st = sim.load_state()
    sim.apply_setting(st, 'function', setting)
    t = sim.meter_now(st)
    pkt = sim.live_packet(st, t)
    assert len(pkt) == 16
    p, s = decode_notification(pkt)
    want = dict(want)
    second = want.pop('second')
    for k, v in want.items():
        assert getattr(p, k) == v, (setting, k)
    assert (s is None) if second is None else ((s.function, s.unit) == second)
    if setting == 'OHMS':
        assert pkt == hx('ffff9f220b280000') + bytes(8)  # byte-identical to the captured open-circuit packet
    if setting == 'CONTINUITY':
        assert pkt == hx('ffff9f040b270004') + bytes(8)
    # the live primary is what QDDB reports as PRIMARY at the same instant
    prim = reading(sim.build_display(st, t), 'PRIMARY')
    if prim.is_number:
        assert abs(p.value - prim.value) < 1e-9


def test_display_layouts_follow_captures():
    st = sim.load_state()
    t = sim.meter_now(st)
    h = sim.build_display(st, t)
    assert (h.prim, h.sec, h.autorange, h.unit, h.range_max, h.multiplier) == ('V_DC', 'NONE', 'AUTO', 'VDC', 5.0, 0)
    assert [r.reading_id for r in h.readings] == ['LIVE', 'PRIMARY']
    assert abs(h.readings[0].value - 5.0622) <= 0.0011 and h.readings[0].decimals == 4
    sim.apply_setting(st, 'function', 'V_AC_HZ')
    h = sim.build_display(st, t)
    assert (h.prim, h.sec, h.unit) == ('V_AC', 'HERTZ', 'VAC')
    assert [(r.reading_id, r.unit, r.attribute) for r in h.readings] == [
        ('LIVE', 'VAC', 'NONE'), ('PRIMARY', 'Hz', 'POSITIVE_EDGE'), ('SECONDARY', 'VAC', 'NONE')]
    sim.apply_setting(st, 'function', 'OHMS')
    h = sim.build_display(st, t)
    assert (h.range_max, h.multiplier) == (500.0, 6)
    assert [(r.state, r.multiplier, r.decimals, r.digits) for r in h.readings] == [('OL', 6, 1, 4)] * 2
    sim.apply_setting(st, 'input', '10027')
    h = sim.build_display(st, t)
    assert (h.range_max, h.multiplier, h.readings[0].decimals, h.readings[0].text()) == (50.0, 3, 3, '10.027 kΩ')
    sim.apply_setting(st, 'function', 'CONTINUITY')
    h = sim.build_display(st, t)
    assert (h.autorange, h.range_max, h.readings[1].text()) == ('MANUAL', 500.0, 'OL Ω [OPEN_CIRCUIT]')
    sim.apply_setting(st, 'function', 'TEMPERATURE')
    h = sim.build_display(st, t)
    assert (h.autorange, h.unit, h.range_max) == ('MANUAL', 'CEL', 1350.0)
    assert [r.reading_id for r in h.readings] == ['LIVE', 'PRIMARY', 'TEMP_OFFSET']


def test_rel_and_rel_percent_reproduce_captured_qddb(monkeypatch):
    monkeypatch.setattr(sim, '_prng', lambda *a: 0.0)   # no noise: the virtual input is exactly what was measured
    rel, _ = parse_meas_header(QDDB_REL, 0)
    pct, _ = parse_meas_header(QDDB_REL_PCT, 0)
    ref = reading(rel, 'REL_REFERENCE')
    st = sim.default_state()
    sim.apply_setting(st, 'function', 'V_AC')
    sim.apply_setting(st, 'input', '1.0474')
    assert sim.build_display(st, ref.ts).autorange == 'AUTO'
    sim.set_modes(st, ['REL'], t=ref.ts)                # the reading becomes the reference; the range locks
    sim.apply_setting(st, 'input', '0.3486')
    t = reading(rel, 'LIVE').ts
    assert sim.encode_meas_header(sim.build_display(st, t)) == QDDB_REL
    p = decode_record(sim.live_packet(st, t))            # the live packet shows the relative value
    assert (p.value, p.sign, p.unit) == (pytest.approx(-0.6988), 1, 1)
    sim.apply_setting(st, 'modes', 'REL_PERCENT')       # REL -> REL %: same reference, PRIMARY in percent
    assert sim.encode_meas_header(sim.build_display(st, reading(pct, 'LIVE').ts)) == QDDB_REL_PCT
    sim.apply_setting(st, 'modes', 'NONE')              # REL off: autorange again, plain layout
    h = sim.build_display(st)
    assert (h.autorange, h.mode, [r.reading_id for r in h.readings]) == ('AUTO', ['NONE'], ['LIVE', 'PRIMARY'])
    assert st['rel'] is None


def test_set_modes_follows_the_meter():
    st = sim.load_state()
    for bad in ('AUTO_HOLD', 'RECORD', 'LOW_PASS_FILTER', 'CALIBRATION', 'REL,REL_PERCENT', 'FLY'):
        with pytest.raises(ValueError):
            sim.apply_setting(st, 'modes', bad)            # unknown, or not modelled by the simulator
    sim.apply_setting(st, 'function', 'V_AC_HZ')
    with pytest.raises(ValueError):
        sim.apply_setting(st, 'modes', 'REL')              # REL with the Hz secondary: not modelled
    sim.apply_setting(st, 'function', 'OHMS')
    with pytest.raises(ValueError):
        sim.apply_setting(st, 'modes', 'REL')              # OL cannot become the reference
    assert (st['modes'], st['rel'], st['autorange']) == ([], None, True)
    sim.apply_setting(st, 'function', 'V_DC')
    sim.apply_setting(st, 'modes', 'REL')
    with pytest.raises(ValueError):
        sim.apply_setting(st, 'sec', 'HERTZ')
    sim.apply_setting(st, 'modes', 'MIN_MAX_AVG')          # REL off, MIN MAX started like PRESS MINMAX does
    sim.save_state(st)
    time.sleep(0.15)
    h = qddb()
    assert h.mode == ['MIN_MAX_AVG'] and h.minmax_start > 0 and h.autorange == 'AUTO'
    assert [r.reading_id for r in h.readings] == ['LIVE', 'PRIMARY', 'MINIMUM', 'MAXIMUM', 'AVERAGE']
    assert all(r.is_number for r in h.readings)
    sim_press('MINMAX')                                     # a running session: MINMAX stops it
    st = sim.load_state()
    assert (st['screen'], st['modes']) == ('minmax_result', ['MIN_MAX_AVG'])
    sim_press('MINMAX', 'MINMAX')                           # close, start again: the mode is not stored twice
    assert sim.load_state()['modes'] == ['MIN_MAX_AVG']
    set_state(modes='HOLD')                                 # MIN MAX off, HOLD freezes the display
    st = sim.load_state()
    assert st['minmax'] is None and st['hold'] and st['modes'] == ['HOLD']
    held = reading(qddb(), 'PRIMARY')
    set_state(input='2.5')
    h = qddb()
    assert h.mode == ['HOLD'] and reading(h, 'PRIMARY') == held and abs(reading(h, 'LIVE').value - 2.5) < 0.002
    set_state(modes='HOLD,REL')                             # HOLD stays held while REL is added
    h = qddb()
    assert h.mode == ['HOLD', 'REL'] and reading(h, 'PRIMARY') == held
    assert [r.reading_id for r in h.readings] == ['LIVE', 'PRIMARY', 'REL_LIVE', 'REL_REFERENCE']


def test_qdda_and_qm_formats():
    # format_qdda reproduces the captured QDDA text from the same values
    def h(ar, v, ts):
        r = [BinReading(rid, v, 'VDC', 0, 4, 5, 'NORMAL', 'NONE', ts) for rid in ('LIVE', 'PRIMARY')]
        return MeasHeader('V_DC', 'NONE', ar, 'VDC', 5.0, 0, 'OFF', 0.0, ['NONE'], r)
    assert sim.format_qdda(h('AUTO', 5.0625, 1793997982.873)) == (
        'V_DC,NONE,AUTO,VDC,5,0,OFF,0.000,0,2,LIVE,5.0625,VDC,0,4,5,NORMAL,NONE,1793997982.873,'
        'PRIMARY,5.0625,VDC,0,4,5,NORMAL,NONE,1793997982.873')
    assert sim.format_qdda(h('MANUAL', 1.9871, 1794001409.057)) == (
        'V_DC,NONE,MANUAL,VDC,5,0,OFF,0.000,0,2,LIVE,1.9871,VDC,0,4,5,NORMAL,NONE,1794001409.057,'
        'PRIMARY,1.9871,VDC,0,4,5,NORMAL,NONE,1794001409.057')
    mv = [BinReading(rid, 0.005029, 'VAC', -3, 3, 5, 'NORMAL', 'NONE', 1197308998.282) for rid in ('LIVE', 'PRIMARY')]
    assert sim.format_qdda(MeasHeader('MV_AC', 'NONE', 'AUTO', 'VAC', 50.0, -3, 'OFF', 0.0, ['NONE'], mv)) == (
        'MV_AC,NONE,AUTO,VAC,50,-3,OFF,0.000,0,2,LIVE,0.005029,VAC,-3,3,5,NORMAL,NONE,1197308998.282,'
        'PRIMARY,0.005029,VAC,-3,3,5,NORMAL,NONE,1197308998.282')
    assert sim.format_qm(BinReading('PRIMARY', 5.0622, 'VDC', 0, 4, 5, 'NORMAL', 'NONE', 0)) == \
        '5.0622E0,VDC,NORMAL,NONE'
    assert sim.format_qm(BinReading('PRIMARY', -0.000023, 'VDC', -3, 3, 5, 'NORMAL', 'NONE', 0)) == \
        '-0.023E-3,VDC,NORMAL,NONE'
    # overload / invalid: '+9.99999999E+37' as in Fluke's remote-interface document (notes/06 4.4); -OL negative
    assert sim.format_qm(BinReading('PRIMARY', OVERLOAD, 'OHM', 6, 1, 4, 'OL', 'NONE', 0)) == \
        '+9.99999999E+37,OHM,OL,NONE'
    assert sim.format_qm(BinReading('PRIMARY', OVERLOAD, 'VDC', 0, 4, 5, 'INVALID', 'NONE', 0)) == \
        '+9.99999999E+37,VDC,INVALID,NONE'
    assert sim.format_qm(BinReading('PRIMARY', -OVERLOAD, 'VDC', 0, 4, 5, 'OL_MINUS', 'NONE', 0)) == \
        '-9.99999999E+37,VDC,OL_MINUS,NONE'
    set_state(function='OHMS')
    assert sim.meter_command('QM') == b'0\r+9.99999999E+37,OHM,OL,NONE\r'
    # the simulator's QDDA and QDDB describe the same display
    st = sim.default_state()
    sim.apply_setting(st, 'modes', 'MIN_MAX_AVG')
    t = sim.meter_now(st) + 2.0                       # two seconds of MIN MAX samples
    disp = sim.build_display(st, t)
    f = sim.format_qdda(disp).split(',')
    hb, _ = parse_meas_header(sim.encode_meas_header(disp), 0)
    assert f[:4] == [hb.prim, hb.sec, hb.autorange, hb.unit] and f[8:10] == ['1', 'MIN_MAX_AVG']
    assert float(f[7]) == pytest.approx(hb.minmax_start, abs=1e-3) and int(f[10]) == len(hb.readings) == 5
    for i, r in enumerate(hb.readings):
        rid, val, unit, mult, dec, dig, state, attr, ts = f[11 + 9 * i: 20 + 9 * i]
        assert (rid, unit, int(mult), int(dec), int(dig), state, attr) == (
            r.reading_id, r.unit, r.multiplier, r.decimals, r.digits, r.state, r.attribute)
        assert float(val) == pytest.approx(r.value, abs=1e-12) and float(ts) == pytest.approx(r.ts, abs=1e-3)


def _check_csv_row(tr: TrendRecord, start, end, prim, mn, avg, mx, n, rtype, stable):
    assert (ts_text(tr.start), ts_text(tr.end)) == (start, end)
    assert (tr.primary.value, tr.minimum.value, tr.average.value, tr.maximum.value) == (
        float(prim), float(mn), float(avg), float(mx))
    assert (tr.samples, tr.record_type, tr.stable, tr.transient) == (int(n), rtype, stable, 'NON_T')
    assert tr.primary.unit == tr.average.unit == 'VDC'


def test_recording_records_decode_to_csv_values():
    def record(k):
        reply = sim.meter_command(f'QSRR 772,{k}')
        assert reply[:4] == b'0\r#0' and reply[-1:] == b'\r' and len(reply) == 151
        return parse_qsrr(reply[4:-1])
    for k, *row in CSV_SAMPLE:
        _check_csv_row(record(k), *row)
    path = ROOT / 'logs' / 'recording0.csv'
    if not path.exists():
        pytest.skip('logs/recording0.csv not present')
    with path.open(encoding='utf-8', newline='') as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 72
    for k, r in enumerate(rows):
        _check_csv_row(record(k), r['start'], r['end'], r['primary'], r['minimum'], r['average'], r['maximum'],
                       r['samples'], r['record_type'], r['stable'])
    assert sim.meter_command('QSRR 772,72') == b'5\r'


# ---------------------------------------------------------------------------------------------------------------
# Advertising and GATT


def test_scanner_finds_the_virtual_adapter():
    async def go():
        seen = []
        async with sim.SimBleakScanner(lambda d, a: seen.append((d, a))) as scanner:
            await asyncio.sleep(0.35)
        assert len(seen) >= 2
        dev, adv = seen[0]
        assert (dev.address, dev.name) == (sim.ADDRESS, 'IR 3000 FC')
        assert adv.local_name == 'IR 3000 FC' and adv.service_uuids == ['b6981800-7562-11e2-b50d-00163e46f8fe']
        assert -63 <= adv.rssi <= -57 and adv.manufacturer_data == {} and adv.service_data == {}
        assert adv.platform_data == () and hasattr(adv, 'tx_power')
        assert scanner.discovered_devices == [dev]
        assert await sim.SimBleakScanner.find_device_by_address(sim.ADDRESS.lower(), timeout=1) == dev
        assert await sim.SimBleakScanner.find_device_by_filter(
            lambda d, a: sim.ADV_SERVICE in a.service_uuids, timeout=1) == dev
        assert await sim.SimBleakScanner.find_device_by_address('00:11:22:33:44:55', timeout=0.2) is None
        assert await sim.SimBleakScanner.discover(timeout=0.2) == [dev]
        found = await sim.SimBleakScanner.discover(timeout=0.2, return_adv=True)
        assert list(found) == [sim.ADDRESS] and found[sim.ADDRESS][1].local_name == 'IR 3000 FC'
        # one connection at a time: a connected adapter stops advertising
        c = await connect()
        assert await sim.SimBleakScanner.find_device_by_address(sim.ADDRESS, timeout=0.2) is None
        with pytest.raises(BleakDeviceNotFoundError):
            await sim.SimBleakClient(sim.ADDRESS).connect()
        await c.disconnect()
        assert await sim.SimBleakScanner.find_device_by_address(sim.ADDRESS, timeout=0.5) is not None
        set_state(adapter_on='false')
        assert await sim.SimBleakScanner.find_device_by_address(sim.ADDRESS, timeout=0.2) is None
        with pytest.raises(BleakDeviceNotFoundError) as ei:
            await sim.SimBleakClient(sim.ADDRESS).connect()
        assert ei.value.identifier == sim.ADDRESS
        with pytest.raises(BleakDeviceNotFoundError):
            await sim.SimBleakClient('00:11:22:33:44:55').connect()
    run(go())


def test_gatt_table_and_dis_values():
    async def go():
        c = await connect()
        assert c.is_connected and c.address == sim.ADDRESS and c.mtu_size == 23
        svcs = list(c.services)
        assert [s.uuid[:8] for s in svcs] == ['00001800', '00001801', '0000180a', '0000180f', 'b6981800', 'b6981801',
                                             'b6981804', 'b6981805']
        cp = c.services.get_characteristic(sim.CP.upper())
        assert (cp.handle, cp.properties) == (0x3f, ['write', 'notify', 'read', 'write-without-response'])
        assert [d.uuid for d in cp.descriptors] == ['00002902-0000-1000-8000-00805f9b34fb']
        assert c.services.get_service('b6981805-7562-11e2-b50d-00163e46f8fe').characteristics[1].uuid == sim.BUF

        async def read(u):
            return bytes(await c.read_gatt_char(u))
        assert await read(DIS_MODEL) == b'FLUKE 289'
        assert await read('2a25') == b'12345678\0'
        assert await read('00002a26-0000-1000-8000-00805f9b34fb') == b'01.00.01  \0'
        assert await read('00002a28-0000-1000-8000-00805f9b34fb') == b'V1.71\0'
        assert await read('00002a29-0000-1000-8000-00805f9b34fb') == b'Fluke Mfg Co.\0'
        assert await read('00002a19-0000-1000-8000-00805f9b34fb') == b'\x64'
        assert await read('B6982903-7562-11E2-B50D-00163E46F8FE') == b'IR 3000 FC'
        assert await read(sim.CNX_ID) == b'\x00'
        assert await read('b6982901-7562-11e2-b50d-00163e46f8fe') == bytes([1, 2, 3, 4, 5]) + bytes(12)
        assert await read(cp) == b'\x00'
        pkt = await read(sim.READING)
        assert len(pkt) == 16 and decode_record(pkt).function == 12
        with pytest.raises(BleakGATTProtocolError):
            await c.read_gatt_char(sim.BUF)  # notify-only
        await c.disconnect()
        assert not c.is_connected
    run(go())


# ---------------------------------------------------------------------------------------------------------------
# Adapter control point, as measured


def test_control_point_silent_until_cnx_id_written():
    async def go():
        c = await connect()
        p = Probe(c)
        await p.subscribe()
        assert await p.cmd(b'\x8f', wait=0.2) == []          # stored, not answered
        assert bytes(await c.read_gatt_char(sim.CP)) == b'\x8f'
        await p.arm()
        assert bytes(await c.read_gatt_char(sim.CNX_ID)) == b'\x01'
        assert await p.cmd(b'\x8f') == [b'\x03\x8f01.00.15,']
        assert await p.cmd(b'\x95') == [b'\x03\x95']
        assert await p.cmd(b'\x80') == [b'\x03\x80' + bytes(4)]
        assert await p.cmd(b'\x83') == [b'\x03\x83']
        assert await p.cmd(b'\x87') == [b'\x02\x87']           # 189/289 download macro: incorrect model
        assert await p.cmd(b'\x77') == [b'\x05\x77']
        await c.disconnect()
        c2 = await connect()
        p2 = Probe(c2)
        await p2.subscribe()
        assert await p2.cmd(b'\x8f', wait=0.2) == []         # every connection starts unarmed
        await p2.arm()
        assert await p2.cmd(b'\x8f') == [b'\x03\x8f01.00.15,']
        await c2.disconnect()
    run(go())


def test_control_point_rejects_writes_over_18_bytes():
    async def go():
        c = await connect()
        p = Probe(c)
        await p.subscribe()
        await p.arm()
        with pytest.raises(BleakGATTProtocolError) as ei:
            await c.write_gatt_char(sim.CP, b'\x8d' + b'Q' * 18, response=True)
        assert ei.value.code == 13
        assert await p.cmd(b'\x8d' + b'Q' * 17) == [b'\x03\x8d']   # 18 bytes is fine
        assert sim.read_audit()[-2]['error'] == 'invalid attribute value length'
        await c.disconnect()
    run(go())


def test_passthrough_framing_like_hardware():
    async def go():
        c = await connect()
        p = Probe(c)
        await p.subscribe()
        await p.arm()
        assert await p.send(b'ID') == [b'\x01\x8e', b'\x03\x8e']
        data = await p.reply()
        assert data == b'0\rFLUKE 289,V1.71,12345678\r'
        assert [len(x) for x in p.chunks] == [20, 7]
        assert await p.send(b'ID\r') == [b'\x01\x8e', b'\x03\x8e']   # the adapter adds the CR itself
        assert await p.reply() == b'0\rFLUKE 289,V1.71,12345678\r1\r'
        assert await p.send(b'QSLS\rQSAVNAME 0\rnope') == [b'\x01\x8e', b'\x03\x8e']
        assert await p.reply() == b'0\r1,0,1,2\r0\rSave\r1\r'
        await c.disconnect()
    run(go())


def test_out_buffer_overflow_then_hang_until_power_cycle():
    async def go():
        c = await connect()
        p = Probe(c)
        await p.subscribe()
        await p.arm()
        assert await p.send(b'ID\r' * 28 + b'QM\r') == [b'\x01\x8e', b'\x03\x8e']   # 87 bytes: works
        assert (await p.reply()).count(b'FLUKE 289') == 28
        assert await p.send(b'ID\r' * 50) == [b'\x01\x8e', b'\x03\x8e']             # 150 bytes: no reply
        assert await p.cmd(b'\x80') == [b'\x03\x80' + bytes(4)]
        assert not sim.load_state()['adapter_hung']
        assert await p.send(b'ID\r' * 125) == [b'\x01\x8e', b'\x03\x8e']            # 375 bytes: hangs
        assert sim.load_state()['adapter_hung']
        assert await p.cmd(b'\x8f', wait=0.2) == []
        await c.disconnect()
        c = await connect()
        p = Probe(c)
        await p.subscribe()
        await p.arm()
        assert await p.cmd(b'\x8f', wait=0.2) == []                                 # survives reconnects
        await c.disconnect()
        sim.power_cycle()
        assert not sim.load_state()['adapter_hung']
        c = await connect()
        p = Probe(c)
        await p.subscribe()
        await p.arm()
        assert await p.cmd(b'\x8f') == [b'\x03\x8f01.00.15,']
        await c.disconnect()
    run(go())


def test_send_without_a_reply_leaves_in_empty():
    async def go():
        c = await connect()
        p = Probe(c)
        await p.subscribe()
        await p.arm()
        assert await p.send(b'ID') == [b'\x01\x8e', b'\x03\x8e']
        assert await p.in_size() == 27
        for _ in range(10):                                  # no CLEAR: 170 bytes go out (overflow)
            assert await p.cmd(b'\x8d' + b'Q' * 17) == [b'\x03\x8d']
        assert await p.cmd(b'\x8e') == [b'\x01\x8e', b'\x03\x8e']
        assert await p.in_size() == 0                        # not the 27 bytes of the earlier ID reply
        assert await p.send(b'ID') == [b'\x01\x8e', b'\x03\x8e']
        assert await p.in_size() == 27
        set_state(meter_on='false')                          # nobody answers: SEND completes, IN empty
        assert await p.cmd(b'\x8dID') == [b'\x03\x8d']
        assert await p.cmd(b'\x8e') == [b'\x01\x8e', b'\x03\x8e']
        assert await p.in_size() == 0
        await c.disconnect()
    run(go())


async def _wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        await asyncio.sleep(0.02)
    return cond()


def test_power_cycle_drops_the_link_and_empties_adapter_ram():
    async def go():
        drops = []
        c = await connect()
        p = Probe(c)
        await p.subscribe()
        await p.arm()
        assert await p.send(b'ID') == [b'\x01\x8e', b'\x03\x8e']
        await c.disconnect()
        c = await connect(disconnected_callback=lambda _c: drops.append('cli'))
        p = Probe(c)
        await p.subscribe()
        await p.arm()
        assert await p.in_size() == 27                       # adapter RAM survives a reconnect
        set_state(adapter_hung='true')
        assert await p.cmd(b'\x8f', wait=0.2) == []
        r = await asyncio.to_thread(_sim_cli, 'power-cycle')  # another process power-cycles the adapter
        assert r.returncode == 0, r.stderr
        assert await _wait_for(lambda: drops == ['cli']) and not c.is_connected
        with pytest.raises(BleakError):
            await c.write_gatt_char(sim.CP, b'\x8f', response=True)
        c = await connect(disconnected_callback=lambda _c: drops.append('local'))
        p = Probe(c)
        await p.subscribe()
        assert await p.cmd(b'\x8f', wait=0.2) == []          # a new connection starts unarmed
        await p.arm()
        assert await p.cmd(b'\x8f') == [b'\x03\x8f01.00.15,']   # hang cleared
        assert await p.in_size() == 0                        # RAM emptied by the power cycle
        sim.power_cycle()                                    # the same from this process
        assert await _wait_for(lambda: drops == ['cli', 'local']) and not c.is_connected
        assert sim.advertising()
    run(go())


_SECOND_CLIENT = r'''
import asyncio
from bleak.exc import BleakDeviceNotFoundError
from open289 import sim

async def main():
    dev = await sim.SimBleakScanner.find_device_by_address(sim.ADDRESS, timeout=0.3)
    print('seen', dev is not None)
    c = sim.SimBleakClient(sim.ADDRESS)
    try:
        await c.connect()
    except BleakDeviceNotFoundError:
        print('NOT_FOUND')
        return
    q = asyncio.Queue()
    await c.start_notify(sim.CP, lambda _h, d: q.put_nowait(bytes(d)))
    await c.write_gatt_char(sim.CNX_ID, b'\x01', response=True)
    await c.write_gatt_char(sim.CP, b'\x80', response=True)
    print('in', int.from_bytes((await asyncio.wait_for(q.get(), 5))[2:6], 'little'))
    await c.disconnect()

asyncio.run(main())
'''


def _second_process() -> list[str]:
    r = subprocess.run([sys.executable, '-c', _SECOND_CLIENT], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


def test_one_connection_across_processes():
    async def go():
        c = await connect()
        p = Probe(c)
        await p.subscribe()
        await p.arm()
        assert await p.send(b'ID') == [b'\x01\x8e', b'\x03\x8e']
        assert sim.link_owner()['pid'] == os.getpid()
        out = await asyncio.to_thread(_second_process)       # this loop keeps the heartbeat going meanwhile
        assert out == ['seen', 'False', 'NOT_FOUND']         # no advertising, no second connection
        assert c.is_connected
        await c.disconnect()
        assert sim.link_owner() is None
    run(go())
    assert _second_process() == ['seen', 'True', 'in', '27']   # free again; the adapter RAM was handed over


def test_link_records_of_lost_connections_are_ignored():
    sim.save_state(sim.load_state())
    lp = Path(sim._link_path(sim.state_path()))
    other = os.getpid() + 100000

    def write(pid, boot=0, age=0.0):
        lp.write_text(json.dumps({'pid': pid, 'token': 'other', 'boot': boot, 'since': 0}), encoding='utf-8')
        t = time.time() - age
        os.utime(lp, (t, t))

    async def go():
        write(other)                                         # another process holds the adapter
        assert not sim.advertising()
        with pytest.raises(BleakDeviceNotFoundError):
            await sim.SimBleakClient(sim.ADDRESS).connect()
        write(other, age=10)                                 # its heartbeat lapsed (crashed / killed)
        assert sim.advertising()
        write(other, boot=7)                                 # recorded before a power cycle
        assert sim.advertising()
        write(os.getpid())                                   # this process, but no live client holds it
        assert sim.advertising()
        drops = []
        c = await connect(disconnected_callback=lambda _c: drops.append(1))
        assert sim.link_owner()['token'] == c._token and not sim.advertising()
        write(other)                                         # someone else took the adapter over
        assert await _wait_for(lambda: drops == [1]) and not c.is_connected
        write(other, age=10)
        c = await connect()
        await c.disconnect()
        assert not lp.exists()
    run(go())


def test_audit_records_every_write_and_flags_forbidden_ones():
    async def go():
        c = await connect()
        p = Probe(c)
        await p.subscribe()
        await p.arm()
        assert await p.cmd(b'\x90') == [b'\x02\x90']             # 1550/1555 opcode: recorded, not raised
        await c.write_gatt_char(FW_CP, b'\x00', response=True)
        await c.write_gatt_char(FW_RADIO, b'\x00', response=False)
        a = Adapter(c)
        with pytest.raises(AdapterError):
            await a.command(b'\x91')                                # open289 itself refuses before writing
        await c.disconnect()
    run(go())
    recs = sim.read_audit()
    assert all({'t', 'uuid', 'hex', 'forbidden'} <= set(r) for r in recs)
    assert [(r['uuid'], r['hex']) for r in recs if r['forbidden']] == [
        (sim.CP, '90'), (FW_CP, '00'), (FW_RADIO.lower(), '00')]
    assert recs[0] == {**recs[0], 'uuid': sim.CNX_ID, 'hex': '01', 'forbidden': False}
    assert not any(r['hex'] == '91' for r in recs)


# ---------------------------------------------------------------------------------------------------------------
# open289's Adapter + Meter against the simulator


def test_adapter_and_meter_end_to_end():
    async def go():
        dev = await sim.SimBleakScanner.find_device_by_filter(lambda d, a: sim.ADV_SERVICE in a.service_uuids)
        async with sim.SimBleakClient(dev, timeout=5.0) as c:
            a, m = await open_meter(c)
            assert await a.version() == '01.00.15'
            assert await a.model() == ''
            assert await m.identify() == {'model': 'FLUKE 289', 'firmware': 'V1.71', 'serial': '12345678'}
            h = await m.display()
            assert (h.prim, h.autorange, h.range_max, h.mode) == ('V_DC', 'AUTO', 5.0, ['NONE'])
            assert 5.0 < reading(h, 'PRIMARY').value < 5.1
            assert ts_text(reading(h, 'LIVE').ts)[:4] >= '2026'
            assert await m.saved_counts() == {'recordings': 1, 'minmax': 0, 'peak': 1, 'measurements': 2}
            s0, s1 = await m.saved_measurement(0), await m.saved_measurement(1)
            assert (s0.name, s0.header.prim, reading(s0.header, 'PRIMARY').text()) == ('Save 1', 'TEMPERATURE',
                                                                                       '34.7 °C')
            assert (s1.name, reading(s1.header, 'PRIMARY').text()) == ('Save 2', 'OL Ω [OPEN_CIRCUIT]')
            pk = await m.peak_session(0)
            assert (pk.name, pk.header.sec) == ('Save 3', 'PEAK_MIN_MAX')
            assert [(r.reading_id, r.text()) for r in pk.header.readings] == [
                ('PRIMARY', '0.005 V DC'), ('MINIMUM', '-0.020 V'), ('MAXIMUM', '3.029 V'), ('AVERAGE', '1.371 V DC')]
            rec = await m.recording(0)
            assert (rec.name, rec.reading_index, rec.num_samples, rec.interval) == ('Save 4', 772, 72, 1.0)
            recs = await m.trend_records(rec.reading_index, 0, 8)
            assert len(recs) == 8
            for k, *row in CSV_SAMPLE[:1]:
                _check_csv_row(recs[k], *row)
            assert len(await m.trend_records(rec.reading_index, 64, 8)) == 7   # 'QSRR 772,64' x 8 > 87 bytes
            tail = await m.trend_records(rec.reading_index, 70, 2)
            for tr, (_k, *row) in zip(tail, CSV_SAMPLE[2:], strict=True):
                _check_csv_row(tr, *row)
            with pytest.raises(MeterError) as ei:                              # past the end: ack 5
                await m.trend_records(rec.reading_index, 70, 3)
            assert ei.value.ack == 'NO_DATA'
            assert m.retries == 0
            with pytest.raises(MeterError) as ei:
                await m.query('QSMR 7')
            assert ei.value.ack == 'NO_DATA'
            ack, _ = await m.raw('QNOSUCH')
            assert ack == 'SYNTAX_ERROR'
            assert (await m.query_text('QMP apoffto')) == '0'
            local = time.time() + time.localtime().tm_gmtoff        # meter clock = PC local time + 1 h
            assert abs(int(await m.query_text('QMP clock')) - (local + 3600)) <= 2
            await m.load_maps()
            before = len(sim.read_audit())
            with pytest.raises(AdapterError):
                await a.passthrough(b'PRESS HOLD')     # not a query: refused by SAFE_METER_CMD
            with pytest.raises(AdapterError):
                await a.passthrough(b'CSD ALL')
            assert len(sim.read_audit()) == before      # refused before anything reached the adapter
            assert sim.load_state()['modes'] == []
            await a.stop()
    run(go())


def test_qemap_matches_the_decoder_tables():
    for name, table in MAPS.items():
        reply = sim.meter_command(f'QEMAP {name}')
        assert reply.startswith(b'0\r') and reply.endswith(b'\r')
        tok = reply[2:-1].decode().split(',')
        assert int(tok[0]) == len(table)
        assert {int(tok[i]): tok[i + 1] for i in range(1, len(tok) - 1, 2)} == table
    assert sim.meter_command('QEMAP nosuchmap') == b'1\r'


def test_meter_commands_without_ble():
    assert sim.meter_command('ID') == b'0\rFLUKE 289,V1.71,12345678\r'
    assert sim.meter_command('id') == b'0\rFLUKE 289,V1.71,12345678\r'
    assert sim.meter_command('IM') == b'0\rFLUKE 289,V1.71,12345678,MSP V0.88,clem/branches/v1/trunk,r32730,10\r'
    assert re.fullmatch(rb'0\r5\.06\d\dE0,VDC,NORMAL,NONE\r', sim.meter_command('QM'))
    assert sim.meter_command('QBL') == b'0\rFULL\r'
    assert sim.meter_command('QMEMLEVEL') == b'0\rOK\r'
    assert sim.meter_command('QSAVNAME 3') == b'0\rSave\r'
    assert sim.meter_command('QMP beeper') == b'0\rON\r'
    assert sim.meter_command('QMP nosuchprop') == b'1\r'
    assert sim.meter_command('QSLS') == b'0\r1,0,1,2\r'
    assert sim.meter_command('QSMR 2') == b'5\r'
    assert sim.meter_command('QMMSI 0') == b'5\r'
    assert sim.meter_command('QSRR 1,0') == b'5\r'
    assert sim.meter_command('QSMR x') == b'1\r'
    assert sim.meter_command('XYZZY') == b'1\r'
    assert sim.meter_command('PRESS NOSUCHKEY') == b'1\r'
    assert sim.meter_command('QDDB')[:4] == b'0\r#0'
    assert sim.meter_command('MP apoffto,900') == b'0\r'
    assert sim.meter_command('QMP apoffto') == b'0\r900\r'
    assert sim.meter_command('CSD MEASUREMENT') == b'0\r'
    assert sim.meter_command('QSLS') == b'0\r1,0,1,0\r'
    assert sim.meter_command('MP clock,1800000000') == b'0\r'
    assert abs(int(sim.meter_command('QMP clock')[2:-1]) - 1800000000) <= 2
    assert abs(sim.build_display(sim.load_state()).readings[0].ts - 1800000000) <= 2


# ---------------------------------------------------------------------------------------------------------------
# Front-panel keys (behaviour verified on hardware: logs/press_test.txt, logs/minmax_save_test*.txt)


def test_minmax_result_page_save_and_modal_screens():
    async def go():
        async with sim.SimBleakClient(sim.ADDRESS) as c:
            a, m = await open_meter(c)
            await press(m, 'MINMAX')
            h = await m.display()
            assert (h.sec, h.mode) == ('NONE', ['MIN_MAX_AVG']) and h.minmax_start > 0
            assert [r.reading_id for r in h.readings] == ['LIVE', 'PRIMARY', 'MINIMUM', 'MAXIMUM', 'AVERAGE']
            mn, mx, avg = (reading(h, k) for k in ('MINIMUM', 'MAXIMUM', 'AVERAGE'))
            assert mn.value <= avg.value <= mx.value and {mn.unit, mx.unit, avg.unit} == {'VDC'}
            await asyncio.sleep(0.25)
            await press(m, 'MINMAX')                       # stop: modal result page
            assert sim.load_state()['screen'] == 'minmax_result'
            for q in ('QDDB', 'QDDA', 'QM', 'QSLS', 'QMP clock', 'QEMAP unit', 'QSMR 0'):
                assert (await m.raw(q))[0] == 'EXECUTION_ERROR', q
            assert (await m.raw('OFF', unsafe=True))[0] == 'EXECUTION_ERROR'
            assert (await m.raw('XYZZY', unsafe=True))[0] == 'SYNTAX_ERROR'
            with pytest.raises(MeterError) as ei:
                await m.display()
            assert ei.value.ack == 'EXECUTION_ERROR'
            assert (await m.identify())['serial'] == '12345678'   # ID/IM still answer
            assert (await m.raw('IM'))[0] == 'OK'
            await press(m, 'F2')                           # save page
            assert sim.load_state()['screen'] == 'save_page'
            await press(m, 'BACKLIGHT', 'F1')              # still on the save page
            assert sim.load_state()['screen'] == 'save_page'
            await press(m, 'F2')                           # Save -> back to the result page
            assert sim.load_state()['screen'] == 'minmax_result'
            await press(m, 'F4')                           # close
            h = await m.display()
            assert h.mode == ['NONE'] and h.minmax_start == 0.0
            assert await m.saved_counts() == {'recordings': 1, 'minmax': 1, 'peak': 1, 'measurements': 2}
            s = await m.minmax_session(0)
            assert s.name == 'Save 5'                      # next free number, as on the real meter
            assert [r.reading_id for r in s.header.readings] == ['PRIMARY', 'MINIMUM', 'MAXIMUM', 'AVERAGE']
            assert s.header.mode == ['MIN_MAX_AVG'] and s.header.minmax_start == s.start
            assert s.end - s.start >= 0.2
            lo, hi = reading(s.header, 'MINIMUM').value, reading(s.header, 'MAXIMUM').value
            assert 5.06 <= lo <= hi <= 5.064

            # F1 restarts; F4 also stops; F4 on the save page goes back without saving
            await press(m, 'MINMAX', 'MINMAX', 'F1')
            assert (await m.display()).mode == ['MIN_MAX_AVG']
            await press(m, 'F4')
            assert sim.load_state()['screen'] == 'minmax_result'
            await press(m, 'F2', 'F4')
            assert sim.load_state()['screen'] == 'minmax_result'
            await press(m, 'MINMAX')
            assert (await m.display()).mode == ['NONE']
            # a held MINMAX behaves like a press
            await press(m, 'MINMAX,HELD_DOWN')
            assert (await m.display()).mode == ['MIN_MAX_AVG']
            await press(m, 'MINMAX,HELD_DOWN')
            assert sim.load_state()['screen'] == 'minmax_result'
            await press(m, 'MINMAX')
            assert (await m.saved_counts())['minmax'] == 1
            # keys without a modelled menu change nothing
            await press(m, 'INFO', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'F1', 'F2', 'F3', 'F4')
            h = await m.display()
            assert (h.mode, h.autorange) == (['NONE'], 'AUTO') and sim.load_state()['screen'] == 'normal'
            await a.stop()
    run(go())


def test_minmax_keeps_its_history_when_the_input_changes():
    def stats(h):
        return [reading(h, k) for k in ('MINIMUM', 'MAXIMUM', 'AVERAGE')]
    sim_press('MINMAX')                                     # starts at ~5.062 V
    time.sleep(0.25)
    set_state(input='1.5')                                  # the input steps down during MIN MAX
    time.sleep(0.25)
    mn, mx, avg = stats(qddb())
    assert 1.499 <= mn.value <= 1.501 and 5.06 <= mx.value <= 5.064   # MAX keeps what it saw before the step
    assert mn.value < avg.value < mx.value and mx.ts < mn.ts
    set_state(input='7.5')                                  # autorange goes up to 50 V
    time.sleep(0.25)
    h = qddb()
    mn2, mx2, avg2 = stats(h)
    assert h.range_max == 50.0 and mn2 == mn
    assert 7.499 <= mx2.value <= 7.501 and (mx2.decimals, mn2.decimals) == (3, 4)
    assert mn2.value < avg2.value < mx2.value and avg2.decimals == 3
    set_state(range_index=0)                                # RANGE by hand: 7.5 V is OL in the 5 V range
    time.sleep(0.15)
    mn3, mx3, avg3 = stats(qddb())
    assert mn3 == mn and mx3.state == 'OL' and avg3.is_number
    sim_press('MINMAX')                                     # stop: the result page freezes the session
    frozen = stats(sim.build_display(sim.load_state()))
    assert (frozen[0], frozen[1].state) == (mn, 'OL')
    set_state(input='0.5')                                  # a change after the stop does not alter it
    time.sleep(0.15)
    assert stats(sim.build_display(sim.load_state())) == frozen
    sim_press('F2', 'F2', 'F4')                             # save, close
    s = parse_qmmsi(sim.meter_command('QMMSI 0')[4:-1], 0)
    assert [r for r in s.header.readings if r.reading_id != 'PRIMARY'] == frozen
    assert s.header.minmax_start == s.start and s.end > s.start
    # a session that only saw OL / -OL inputs
    set_state(function='V_DC')
    set_state(input='-9')
    sim_press('RANGE')                                      # manual 50 V: -9 V is a number
    sim_press('MINMAX')
    time.sleep(0.15)
    set_state(range_index=0)                                # 5 V range: -9 V is -OL
    time.sleep(0.15)
    mn, mx, avg = stats(qddb())
    assert mn.state == 'OL_MINUS' and mx.is_number and -9.001 <= mx.value <= -8.999


def test_hold_range_and_backlight():
    async def go():
        async with sim.SimBleakClient(sim.ADDRESS) as c:
            a, m = await open_meter(c)
            await press(m, 'HOLD')
            h1 = await m.display()
            held = reading(h1, 'PRIMARY')
            assert h1.mode == ['HOLD'] and 5.06 <= held.value <= 5.064
            set_state(input='1.5')                            # the input changes while HOLD is on
            await asyncio.sleep(0.15)
            h2 = await m.display()
            assert reading(h2, 'PRIMARY') == held             # held value and timestamp
            assert abs(reading(h2, 'LIVE').value - 1.5) < 0.002 and reading(h2, 'LIVE').ts > held.ts
            q: asyncio.Queue = asyncio.Queue()
            await c.start_notify(sim.READING, lambda _h, d: q.put_nowait(bytes(d)))
            pkts = [await asyncio.wait_for(q.get(), 2) for _ in range(4)]
            assert all(decode_record(p).value == held.value for p in pkts)   # live packets carry the held value
            await press(m, 'HOLD')
            h3 = await m.display()
            assert h3.mode == ['NONE'] and abs(reading(h3, 'PRIMARY').value - 1.5) < 0.002
            await asyncio.sleep(0.1)
            while not q.empty():
                q.get_nowait()
            assert abs(decode_record(await asyncio.wait_for(q.get(), 2)).value - 1.5) < 0.002
            await c.stop_notify(sim.READING)

            seen = []
            for keys in (['RANGE'], ['RANGE'], ['RANGE'], ['RANGE'], ['RANGE'], ['RANGE,HELD_DOWN']):
                await press(m, *keys)
                h = await m.display()
                seen.append((h.autorange, h.range_max, reading(h, 'PRIMARY').decimals))
            assert seen == [('MANUAL', 5.0, 4), ('MANUAL', 50.0, 3), ('MANUAL', 500.0, 2), ('MANUAL', 1000.0, 1),
                            ('MANUAL', 5.0, 4), ('AUTO', 5.0, 4)]

            lights = []
            for _ in range(3):
                await press(m, 'BACKLIGHT')
                lights.append(sim.load_state()['backlight'])
            assert lights == ['bright', 'dim', 'off']
            await a.stop()
    run(go())


def test_onoff_confirm_cancel_then_power_off(monkeypatch):
    monkeypatch.setenv('OPEN289_SIM_OFF_DELAY', '1.0')

    async def go():
        lost = asyncio.Event()
        async with sim.SimBleakClient(sim.ADDRESS, disconnected_callback=lambda _c: lost.set()) as c:
            a, m = await open_meter(c)
            await press(m, 'ONOFF')
            assert sim.load_state()['screen'] == 'power_off_confirm'
            assert (await m.raw('QDDB'))[0] == 'EXECUTION_ERROR'
            await press(m, 'F4')                                    # cancel
            assert (await m.display()).prim == 'V_DC'
            await press(m, 'ONOFF', 'F1')                           # confirm: the meter switches off
            st = sim.load_state()
            assert not st['meter_on'] and st['adapter_on']
            assert bytes(await c.read_gatt_char(DIS_MODEL)) == b'FLUKE PC3000'
            assert await m.raw('ID') == ('NO_REPLY', b'')           # SEND completes, nothing comes back
            await asyncio.wait_for(lost.wait(), 5)                  # then the adapter switches itself off
            assert not c.is_connected
        assert not sim.load_state()['adapter_on']
        assert await sim.SimBleakScanner.find_device_by_address(sim.ADDRESS, timeout=0.3) is None
        with pytest.raises(BleakDeviceNotFoundError):
            await sim.SimBleakClient(sim.ADDRESS).connect()
        sim.power_cycle()
        async with sim.SimBleakClient(sim.ADDRESS) as c:
            assert bytes(await c.read_gatt_char(DIS_MODEL)) == b'FLUKE 289'
            a, m = await open_meter(c)
            assert (await m.identify())['model'] == 'FLUKE 289'
    run(go())


def test_off_command_and_live_stream(monkeypatch):
    monkeypatch.setenv('OPEN289_SIM_OFF_DELAY', '1.0')

    async def go():
        lost = asyncio.Event()
        async with sim.SimBleakClient(sim.ADDRESS, disconnected_callback=lambda _c: lost.set()) as c:
            a, m = await open_meter(c)
            stamps = []
            q: asyncio.Queue = asyncio.Queue()

            def on_live(_h, d):
                stamps.append(time.monotonic())
                q.put_nowait(bytes(d))
            await c.start_notify(sim.READING, on_live)
            pkts = [await asyncio.wait_for(q.get(), 2) for _ in range(8)]
            assert all(len(p) == 16 and p[8:] == bytes(8) for p in pkts)
            assert all(decode_record(p).text().endswith('V DC') for p in pkts)
            assert 5.0 < sum(decode_record(p).value for p in pkts) / 8 < 5.1
            assert stamps[-1] - stamps[0] < 0.6                     # ~40 packets/s with OPEN289_SIM_RATE=40
            with pytest.raises(AdapterError):
                await a.passthrough(b'OFF')                          # refused without the unsafe opt-in
            assert sim.load_state()['meter_on']
            assert await m.raw('OFF', unsafe=True) == ('OK', b'')
            await asyncio.sleep(0.15)
            while not q.empty():
                q.get_nowait()
            await asyncio.sleep(0.2)
            assert q.empty()                                        # live notifications stopped
            assert bytes(await c.read_gatt_char(DIS_MODEL)) == b'FLUKE PC3000'
            await asyncio.wait_for(lost.wait(), 5)
        assert not sim.advertising()
    run(go())


# ---------------------------------------------------------------------------------------------------------------
# Persistence and the module command line


def _sim_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, '-m', 'open289.sim', *args], cwd=ROOT, capture_output=True, text=True,
                          timeout=60)


def test_state_persists_across_clients_and_processes():
    async def hold_on():
        async with sim.SimBleakClient(sim.ADDRESS) as c:
            a, m = await open_meter(c)
            await press(m, 'HOLD')
            return reading(await m.display(), 'PRIMARY')

    async def read_display():
        async with sim.SimBleakClient(sim.ADDRESS) as c:
            a, m = await open_meter(c)
            return await m.display()
    held = run(hold_on())
    h = run(read_display())                                   # a second client instance sees the same meter
    assert h.mode == ['HOLD'] and reading(h, 'PRIMARY') == held

    shown = _sim_cli('show')                                  # ... and so does another process
    assert shown.returncode == 0, shown.stderr
    info = json.loads(shown.stdout)
    assert info['meter']['modes'] == ['HOLD'] and info['adapter']['address'] == sim.ADDRESS

    r = _sim_cli('set', 'function=V_AC_HZ', 'hz=50')
    assert r.returncode == 0, r.stderr
    h = run(read_display())
    assert (h.prim, h.sec, h.mode) == ('V_AC', 'HERTZ', ['NONE'])
    assert abs(reading(h, 'PRIMARY').value - 50.0) < 0.01 and reading(h, 'SECONDARY').unit == 'VAC'

    r = _sim_cli('set', 'function=NOPE')
    assert r.returncode == 2 and 'unknown function' in r.stderr
    r = _sim_cli('set', 'meter_on=false')
    assert r.returncode == 0 and json.loads(r.stdout)['meter']['meter_on'] is False
    assert sim.meter_command('ID') == b''
    r = _sim_cli('power-cycle')
    assert r.returncode == 0 and json.loads(r.stdout)['meter']['meter_on'] is True
    r = _sim_cli('reset')
    assert r.returncode == 0
    st = sim.load_state()
    assert (st['function'], st['modes'], st['meter_on']) == ('V_DC', [], True)


def test_settings_validation():
    st = sim.load_state()
    for k, v in (('function', 'X'), ('sec', 'HERTZ_X'), ('range_index', '9'), ('screen', 'menu'),
                 ('modes', 'FLY'), ('nosuchkey', '1'), ('meter_on', 'maybe')):
        with pytest.raises(ValueError):
            sim.apply_setting(st, k, v)
    sim.apply_setting(st, 'function', 'TEMPERATURE')
    with pytest.raises(ValueError):
        sim.apply_setting(st, 'sec', 'HERTZ')
    sim.apply_setting(st, 'input', 'open')
    assert st['inputs']['TEMPERATURE'] is None
    assert reading(sim.build_display(st), 'PRIMARY').state == 'OL'


# ---------------------------------------------------------------------------------------------------------------
# Redaction: the real adapter address and meter serial (from the hardware logs) never appear in the simulator


_REAL_ADDRESS_SHA256 = 'e6198657556b4871cdcfb2a8dfc0dbaea0575eeade2a643e5f0433b39db4e0df'
_REAL_SERIAL_SHA256 = '6b0bc687853358dc31125081d33e9d46efd20d1db366a5872235cd947ef1d2cd'


@pytest.mark.parametrize('path', [ROOT / 'open289' / 'sim.py', Path(__file__)], ids=['sim.py', 'test_sim.py'])
def test_no_real_identifiers(path):
    text = path.read_text(encoding='utf-8')
    macs = {m.upper() for m in re.findall(r'(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])',
                                          text)}
    assert all(hashlib.sha256(m.encode()).hexdigest() != _REAL_ADDRESS_SHA256 for m in macs)
    assert macs <= {sim.ADDRESS, '00:11:22:33:44:55', 'AA:BB:CC:DD:EE:FF'}
    for num in set(re.findall(r'(?<!\d)\d{8}(?!\d)', text)):
        assert hashlib.sha256(num.encode()).hexdigest() != _REAL_SERIAL_SHA256
    assert sim.SERIAL == '12345678' and sim.ADDRESS == 'FA:28:90:00:00:01'
