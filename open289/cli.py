"""open289 command line: Fluke 287/289 multimeter over a Fluke IR3000 FC Bluetooth LE adapter.

Every command prints JSON on stdout: one object per line (one-shot commands print one; live, read --every, log and
recording print event lines and end with an "event": "done" summary). --text prints a human-readable form instead.
Errors: {"ok": false, "cmd": ..., "error": {"code", "message", "hint"}} and a non-zero exit code (see errors.py).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
import traceback

from . import __version__, transport
from .errors import Open289Error
from .lock import AdapterLock
from .logger import clear_stop_request, request_stop, run_log, stop_requested
from .meter import MeterError, parse_meas_header, ts_text
from .paths import data_dir
from .records import header_dict, live_dict, session_dict, trend_dict
from .transport import BATTERY, DIS_RADIO_FW, READING_CHR, Link

PRESS_KEYS = ('F1', 'F2', 'F3', 'F4', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'HOLD', 'MINMAX', 'RANGE',
              'INFO', 'BACKLIGHT', 'ONOFF')
FOREVER = ('forever', 'inf', 'until-stop')
_TEXT = [False]


# ------------------------------------------------------------------ output

def emit(obj, text=None):
    """JSON line (default) or the human-readable text for --text."""
    if _TEXT[0]:
        t = text(obj) if callable(text) else text
        if t is None:
            t = _default_text(obj)
        if t:
            print(t, flush=True)
    else:
        print(json.dumps(obj, ensure_ascii=False, separators=(',', ':')), flush=True)


def _default_text(obj):
    if obj.get('ok') is False:
        e = obj.get('error', {})
        return f"error {e.get('code')}: {e.get('message')}" + (f"\n  hint: {e['hint']}" if e.get('hint') else '')
    ev = obj.get('event')
    if ev in ('started', 'status', 'link_lost', 'reconnected', 'progress'):
        rest = {k: v for k, v in obj.items() if k not in ('ok', 'cmd', 'event')}
        return f"[{obj.get('cmd')} {ev}] " + ', '.join(f'{k}={v}' for k, v in rest.items())
    return json.dumps(obj, ensure_ascii=False, indent=1)


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def stamp(t):
    return time.strftime('%H:%M:%S', time.localtime(t)) + f'.{int(t % 1 * 1000):03d}'


# ------------------------------------------------------------------ argument helpers

def parse_duration(s, name='--duration'):
    """'90', '90s', '5m', '1.5h', 'forever' -> seconds (None = forever)."""
    if s is None:
        return None
    s = str(s).strip().lower()
    if s in FOREVER:
        return None
    m = re.fullmatch(r'([0-9]*\.?[0-9]+)\s*(ms|s|m|min|h|d)?', s)
    if not m:
        raise Open289Error('INVALID_ARG', f'{name}: cannot parse {s!r}', "use e.g. 30s, 5m, 2h or forever")
    v = float(m.group(1)) * {'ms': 0.001, 's': 1, None: 1, 'm': 60, 'min': 60, 'h': 3600, 'd': 86400}[m.group(2)]
    if v < 0:
        raise Open289Error('INVALID_ARG', f'{name} must be >= 0')
    return v


def default_out(prefix, ext):
    return os.path.join(data_dir(), time.strftime(f'{prefix}_%Y%m%d_%H%M%S') + ext)


async def wall(coro, seconds, what):
    try:
        return await asyncio.wait_for(coro, seconds)
    except asyncio.TimeoutError:
        raise Open289Error('TIMEOUT', f'{what} did not finish within {seconds:g} s',
                           'move closer to the adapter; raise --max-wall for slow operations') from None


# ------------------------------------------------------------------ commands

async def cmd_scan(args):
    devices = await transport.scan(args.seconds)
    emit({'ok': True, 'cmd': 'scan', 'devices': devices},
         lambda o: '\n'.join(f"{d['address']}  rssi={d.get('rssi')}  name={d['name']!r}" for d in o['devices'])
         or 'no IR3000 FC adapters found')


async def cmd_info(args):
    async with Link(args.address, args.verbose) as link:
        c, m = link.client, link.meter
        model = await link.attached_model()
        adapter = {'address': c.address, 'name': 'IR 3000 FC', 'radio_fw': await link.read_text(DIS_RADIO_FW),
                   'msp_fw': await link.adapter.version(), 'battery_pct': (await c.read_gatt_char(BATTERY))[0]}
        out = {'ok': True, 'cmd': 'info', 'adapter': adapter}
        if 'PC3000' in model.upper():
            out['meter'] = {'attached': False}
        else:
            ident = await m.identify()
            meter = {'attached': True, **ident}
            for cmd, key in (('QBL', 'battery'), ('QMEMLEVEL', 'memory_level'), ('QMP clock', 'clock')):
                try:
                    v = await m.query_text(cmd)
                    if key == 'clock':
                        meter['clock_epoch'] = int(float(v))
                        v = ts_text(float(v))
                    meter[key] = v
                except (MeterError, ValueError):
                    meter[key] = None
            out['meter'] = meter
            out['saved'] = await m.saved_counts()

    def text(o):
        a, mt = o['adapter'], o['meter']
        lines = [f"adapter : {a['address']}  radio fw {a['radio_fw']}  MSP fw {a['msp_fw']}  battery {a['battery_pct']}%"]
        if not mt['attached']:
            return '\n'.join(lines + ['meter   : not attached / off (adapter reports FLUKE PC3000)'])
        lines += [f"meter   : {mt['model']}  firmware {mt['firmware']}  serial {mt['serial']}",
                  f"battery : {mt['battery']}   memory: {mt['memory_level']}   clock: {mt['clock']} (meter local time)",
                  f"saved   : {o['saved']}"]
        return '\n'.join(lines)
    emit(out, text)


def _read_text(o):
    lines = [f"{stamp(o['t'])}  {o['function']}" + (f" / {o['secondary_function']}" if o['secondary_function'] != 'NONE' else '')
             + f"   range {'AUTO' if o['autorange'] else 'MANUAL'} {o['range']:g}   modes {','.join(o['modes']) or 'NONE'}"]
    for r in o['readings']:
        lines.append(f"    {r['id']:<14} {r['display']:<22} {r['time']}")
    return '\n'.join(lines)


async def cmd_read(args):
    every = parse_duration(args.every, '--every') if args.every else None
    for_s = parse_duration(args.for_, '--for') if args.for_ else None
    if every:
        clear_stop_request()          # before connecting: a stop sent while connecting must not be lost
    async with Link(args.address, args.verbose) as link:
        await link.require_meter()
        if not every:
            h = await link.meter.display()
            emit({'ok': True, 'cmd': 'read', 't': round(time.time(), 3), **header_dict(h)}, _read_text)
            return 0
        t0, n = time.time(), 0
        stopped_by = None
        while True:
            t = time.time()
            try:
                h = await link.meter.display()
                emit({'ok': True, 'cmd': 'read', 't': round(t, 3), **header_dict(h)}, _read_text)
            except MeterError as e:
                emit({'ok': False, 'cmd': 'read', 't': round(t, 3), 'error': e.to_dict()})
            n += 1
            if args.count and n >= args.count:
                stopped_by = 'count'
            elif for_s is not None and time.time() - t0 >= for_s:
                stopped_by = 'duration'
            elif stop_requested():
                stopped_by = 'stop'
            if stopped_by:
                break
            await asyncio.sleep(max(0.0, every - (time.time() - t)))
        clear_stop_request()
        emit({'ok': True, 'cmd': 'read', 'event': 'done', 'snapshots': n, 'stopped_by': stopped_by},
             lambda o: f"-- {o['snapshots']} snapshots ({o['stopped_by']})")


async def cmd_live(args):
    for_s = parse_duration(args.for_, '--for') if args.for_ else None
    clear_stop_request()              # before connecting: a stop sent while connecting must not be lost
    async with Link(args.address, args.verbose) as link:
        await link.require_meter()
        q: asyncio.Queue = asyncio.Queue()
        await link.client.start_notify(READING_CHR, lambda _h, d: q.put_nowait((time.time(), bytes(d))))
        n, t0, stopped_by, silent = 0, time.time(), None, time.time()
        try:
            while True:
                try:
                    t, data = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if link.lost.is_set() or not link.client.is_connected:
                        raise Open289Error('LINK_LOST', 'BLE link to the adapter was lost') from None
                    if time.time() - silent > args.timeout:
                        raise Open289Error('TIMEOUT', f'no live reading for {args.timeout:g} s',
                                           'is the meter on and the adapter seated on its IR port?') from None
                    t = None
                if t is not None and len(data) >= 8:
                    silent = t
                    n += 1
                    rec = {'type': 'reading', **live_dict(t, data, raw=args.raw)}
                    emit(rec, lambda o: f"{stamp(o['t'])}  {o['display']}"
                         + (f"   | {o['secondary']['display']}" if o.get('secondary') else '')
                         + (f"   [{o['raw']}]" if o.get('raw') else ''))
                if args.count and n >= args.count:
                    stopped_by = 'count'
                elif for_s is not None and time.time() - t0 >= for_s:
                    stopped_by = 'duration'
                elif stop_requested():
                    stopped_by = 'stop'
                if stopped_by:
                    break
        finally:
            try:
                await link.client.stop_notify(READING_CHR)
            except Exception:  # noqa: BLE001
                pass
            clear_stop_request()
        dt = time.time() - t0
        emit({'ok': True, 'cmd': 'live', 'event': 'done', 'readings': n, 'duration_s': round(dt, 3),
              'rate_hz': round(n / dt, 2) if dt else None, 'stopped_by': stopped_by},
             lambda o: f"-- {o['readings']} readings in {o['duration_s']} s ({o['rate_hz']}/s)")


async def cmd_log(args):
    fmt = args.format or ('jsonl' if args.out and args.out.lower().endswith(('.jsonl', '.json')) else 'csv')
    out = args.out or default_out('log', '.' + fmt)
    duration = parse_duration(args.duration)
    if duration is not None and duration <= 0:
        raise Open289Error('INVALID_ARG', '--duration must be > 0', 'use "forever" for no limit')
    every = parse_duration(args.every, '--every') or 0.0
    snap = parse_duration(args.snapshot_every, '--snapshot-every') or 0.0
    status = parse_duration(args.status_every, '--status-every') or 0.0
    reconnect = parse_duration(args.reconnect_for, '--reconnect-for') or 0.0
    clear_stop_request()
    summary = await run_log(address=args.address, out_path=out, fmt=fmt, duration=duration, every=every,
                            snapshot_every=snap, status_every=status, reconnect_for=reconnect,
                            verbose=args.verbose, emit=emit)

    def text(o):
        st = '; '.join(f"{u}: min {s['min']:g} max {s['max']:g} mean {s['mean']:.6g} (n={s['n']})"
                       for u, s in o['stats'].items())
        return (f"-- {o['records']} records in {o['duration_s']:.1f} s -> {o['file']} ({o['stopped_by']})"
                + (f"\n   {st}" if st else '') + (f"\n   gaps: {len(o['gaps'])}" if o['gaps'] else ''))
    emit(summary, text)


async def cmd_memory(args):
    async with Link(args.address, args.verbose) as link:
        await link.require_meter()
        m = link.meter
        counts = await m.saved_counts()
        items, errors = [], []
        for key, get in (('measurements', m.saved_measurement), ('minmax', m.minmax_session),
                         ('peak', m.peak_session), ('recordings', m.recording)):
            for i in range(counts[key]):
                try:
                    items.append(session_dict(await get(i)))
                except (Open289Error, ValueError) as e:
                    errors.append({'kind': key, 'index': i,
                                   'error': e.to_dict() if isinstance(e, Open289Error) else {'code': 'PROTOCOL_ERROR', 'message': str(e)}})
    out = {'ok': not errors, 'cmd': 'memory', 'counts': counts, 'items': items}
    if errors:
        out['errors'] = errors

    def text(o):
        lines = [f"saved items: {o['counts']}"]
        for x in o['items']:
            p = x.get('primary') or (x['readings'][0] if x['readings'] else {})
            extra = (f"  {x['records']} records, interval {x['interval_s']:g} s, {x['duration_s'] / 60:.1f} min"
                     if x['kind'] == 'recording' else '')
            when = x.get('start') or p.get('time', '')
            lines.append(f"{x['kind']:<11} #{x['index']:<3} {x['name']!r:<16} {when}  {x['function']}  {p.get('display', '')}{extra}")
            if args.detail:
                lines += [f"      {r['id']:<14} {r['display']:<22} {r['time']}" for r in x['readings']]
        lines += [f"{e['kind']} #{e['index']}: error {e['error']['code']}: {e['error']['message']}" for e in o.get('errors', [])]
        return '\n'.join(lines)
    emit(out, text)
    return 0 if not errors else 6


async def cmd_recording(args):
    if args.start < 0 or (args.limit is not None and args.limit < 0):
        raise Open289Error('INVALID_ARG', '--start and --limit must be >= 0')
    fmt = args.format or ('jsonl' if args.out and args.out.lower().endswith(('.jsonl', '.json')) else 'csv')
    async with Link(args.address, args.verbose) as link:
        await link.require_meter()
        m = link.meter
        x = await m.recording(args.index)
        n = x.num_samples if args.limit is None else min(args.limit, x.num_samples)
        out = args.out or default_out(f'recording{args.index}', '.' + fmt)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        resume = args.start > 0 and os.path.exists(out)
        fh = open(out, 'a' if resume else 'w', newline='' if fmt == 'csv' else None, encoding='utf-8')
        fields = list(trend_dict(0, _DummyTrend()).keys())
        w = csv.DictWriter(fh, fieldnames=fields) if fmt == 'csv' else None
        if w and not resume:
            w.writeheader()
        emit({'ok': True, 'cmd': 'recording', 'event': 'started', 'file': os.path.abspath(out), 'records': n,
              'recording': session_dict(x)}, lambda o: f"{o['recording']['name']}: {o['records']} records -> {o['file']}")
        t0 = last_progress = time.time()
        k = args.start
        pending: list = []
        try:
            while k < n or pending:
                if not pending:
                    for attempt in range(4):
                        try:
                            if args.no_batch or attempt > 0:
                                pending = [await m.trend_record(x.reading_index, k)]
                            else:
                                pending = await m.trend_records(x.reading_index, k, n - k)
                            break
                        except (Open289Error, ValueError) as e:
                            if isinstance(e, MeterError) and e.ack == 'EXECUTION_ERROR':
                                raise Open289Error('METER_BUSY', f'record {k}: the meter is on a menu / result page',
                                                   f'close it on the meter, then resume with --start {k}') from None
                            if attempt == 3:
                                raise Open289Error('PROTOCOL_ERROR', f'record {k} failed: {e}',
                                                   f'resume with --start {k}') from None
                            m.retries += 1
                            await asyncio.sleep(0.5)
                rec = trend_dict(k, pending.pop(0))
                if w:
                    w.writerow(rec)
                else:
                    fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
                fh.flush()
                if _TEXT[0]:
                    print(f"{k:5d} {rec['start']}  prim {rec['primary']}  min {rec['minimum']}  avg {rec['average']}  "
                          f"max {rec['maximum']} {rec['unit']}  {rec['record_type']} {rec['stable']}", flush=True)
                elif time.time() - last_progress >= 10:
                    last_progress = time.time()
                    emit({'ok': True, 'cmd': 'recording', 'event': 'progress', 'done': k + 1, 'of': n})
                k += 1
        finally:
            fh.close()
        dt = time.time() - t0
        done = max(n - args.start, 0)
        emit({'ok': True, 'cmd': 'recording', 'event': 'done', 'file': os.path.abspath(out), 'format': fmt,
              'records': done, 'duration_s': round(dt, 2), 's_per_record': round(dt / max(done, 1), 3),
              'retries': m.retries},
             lambda o: f"-- {o['records']} records in {o['duration_s']} s ({o['s_per_record']} s/record, "
                       f"{o['retries']} retries) -> {o['file']}")


class _DummyTrend:
    """Field order for the recording CSV header."""
    def __init__(self):
        from .meter import BinReading
        r = BinReading('PRIMARY', 0.0, 'VDC', 0, 0, 0, 'NORMAL', 'NONE', 0.0)
        self.start = self.end = 0.0
        self.primary = self.minimum = self.average = self.maximum = r
        self.samples, self.record_type, self.stable, self.transient = 0, '', '', ''


async def cmd_press(args):
    keys = [k.upper() for k in args.keys]
    bad = [k for k in keys if k not in PRESS_KEYS]
    if bad:
        raise Open289Error('INVALID_ARG', f"unknown key(s) {', '.join(bad)}", f"keys: {' '.join(PRESS_KEYS)}")
    if 'ONOFF' in keys and not args.allow_off:
        raise Open289Error('UNSAFE_OPERATION', 'ONOFF would switch the meter off',
                           'add --allow-off; the meter asks for confirmation (F1 OK, F4 cancel)')
    presses = []
    async with Link(args.address, args.verbose) as link:
        await link.require_meter()
        powering_off = False
        for idx, k in enumerate(keys):
            cmd = f'PRESS {k}' + (',HELD_DOWN' if args.held else '')
            entry = {'key': k, 'held': bool(args.held)}
            try:
                ack, _ = await link.meter.raw(cmd, unsafe=True)
            except Open289Error as e:
                if powering_off or not link.client.is_connected:
                    entry.update(ack='LINK_CLOSED', state='meter powering off')
                    presses.append(entry)
                    break
                raise
            entry['ack'] = ack
            presses.append(entry)
            if ack != 'OK':
                raise Open289Error('METER_ERROR', f'{cmd} was not acknowledged ({ack}); stopped before {keys[idx + 1:]}')
            if k == 'ONOFF':
                powering_off = True
                entry['state'] = 'power-off confirmation shown (F1 OK, F4 cancel)'
                continue
            if powering_off and k == 'F1':
                entry['state'] = 'meter powering off; the IR3000 switches itself off too'
                break
            powering_off = False
            await asyncio.sleep(args.settle)
            try:
                entry['state'] = header_dict(await link.meter.display())
            except MeterError as e:
                entry['state'] = 'menu / result page open (measurement queries blocked)' if e.ack == 'EXECUTION_ERROR' else e.to_dict()
            except (Open289Error, ValueError) as e:
                entry['state'] = {'code': 'UNAVAILABLE', 'message': str(e)}

    def text(o):
        out = []
        for p in o['presses']:
            st = p.get('state')
            if isinstance(st, dict) and 'function' in st:
                pr = st.get('primary') or {}
                st = f"{st['function']} {'AUTO' if st['autorange'] else 'MANUAL'} {st['range']:g} modes={','.join(st['modes']) or 'NONE'} primary={pr.get('display', '-')}"
            out.append(f"PRESS {p['key']}{',HELD_DOWN' if p['held'] else ''} -> {p['ack']}" + (f"\n   now: {st}" if st else ''))
        return '\n'.join(out)
    emit({'ok': True, 'cmd': 'press', 'presses': presses}, text)


async def cmd_raw(args):
    results = []
    async with Link(args.address, args.verbose) as link:
        for cmd in args.commands:
            try:
                ack, payload = await link.meter.raw(cmd, unsafe=args.unsafe)
            except Open289Error as e:
                results.append({'command': cmd, 'error': e.to_dict()})
                continue
            r = {'command': cmd, 'ack': ack}
            if payload.startswith(b'#0'):
                r['binary_hex'] = payload[2:].hex()
            else:
                r['text'] = payload.decode('latin-1')
            results.append(r)

    def text(o):
        out = []
        for r in o['results']:
            if 'error' in r:
                out.append(f"{r['command']!r}: {r['error']['code']}: {r['error']['message']}")
            elif 'binary_hex' in r:
                out.append(f"{r['command']!r} -> {r['ack']}  binary {len(r['binary_hex']) // 2} bytes: {r['binary_hex']}")
            else:
                out.append(f"{r['command']!r} -> {r['ack']}  {r['text']}")
        return '\n'.join(out)
    ok = all('error' not in r for r in results)
    emit({'ok': ok, 'cmd': 'raw', 'results': results}, text)
    return 0 if ok else next(Open289Error(r['error']['code'], '').exit_code for r in results if 'error' in r)


async def cmd_selftest(args):
    """Health check of adapter, link and meter (read-only: never changes the meter)."""
    items = []

    def check(cid, name, ok, measured=None, expected=None):
        items.append({'id': cid, 'name': name, 'pass': bool(ok), 'measured': measured, 'expected': expected})
        return ok

    try:
        dev = await transport.find_adapter(args.address)
        check('S01', 'adapter advertising', True, getattr(dev, 'address', str(dev)))
    except Open289Error as e:
        check('S01', 'adapter advertising', False, e.message, 'an IR3000 FC advertising service B6981800')
        return _selftest_emit(items)
    try:
        async with Link(args.address, args.verbose) as link:
            check('S02', 'connect and arm the adapter control point', True, link.client.address)
            model = await link.attached_model()
            if not check('S03', 'meter attached', 'PC3000' not in model.upper(), model, 'FLUKE 287/289'):
                return _selftest_emit(items)
            v = await link.adapter.version()
            check('S04', 'adapter firmware answers', bool(v), v)
            ident = await link.meter.identify()
            check('S05', 'pass-through ID', ident['model'].startswith('FLUKE'), ident)
            try:
                h = await link.meter.display()
                hd = header_dict(h)
                check('S06', 'display snapshot (QDDB) decodes', True, {'function': hd['function'], 'primary': (hd.get('primary') or {}).get('display')})
            except MeterError as e:
                check('S06', 'display snapshot (QDDB) decodes', False, e.to_dict(), 'meter on its measurement screen')
                hd = None
            q: asyncio.Queue = asyncio.Queue()
            await link.client.start_notify(READING_CHR, lambda _h, d: q.put_nowait(bytes(d)))
            try:
                data = await asyncio.wait_for(q.get(), timeout=5.0)
                rec = live_dict(time.time(), data)
                check('S07', 'live reading within 5 s', len(data) >= 8, {'bytes': len(data), 'display': rec['display']})
            except asyncio.TimeoutError:
                check('S07', 'live reading within 5 s', False, 'no notification', '16-byte notifications ~4/s')
            finally:
                try:
                    await link.client.stop_notify(READING_CHR)
                except Exception:  # noqa: BLE001
                    pass
            batt = (await link.client.read_gatt_char(BATTERY))[0]
            check('S08', 'adapter battery', batt >= 20, f'{batt}%', '>= 20%')
            try:
                qbl = await link.meter.query_text('QBL')
                check('S09', 'meter battery', qbl not in ('EMPTY', 'ALMOST_EMPTY'), qbl, 'not EMPTY / ALMOST_EMPTY')
            except MeterError as e:
                check('S09', 'meter battery', False, e.to_dict())
    except Open289Error as e:
        check('S02', 'connect and arm the adapter control point', False, e.to_dict())
    return _selftest_emit(items)


def _selftest_emit(items):
    failed = [i for i in items if not i['pass']]
    out = {'ok': not failed, 'cmd': 'selftest', 'passed': len(items) - len(failed), 'failed': len(failed), 'items': items}
    emit(out, lambda o: '\n'.join(f"{i['id']} {'PASS' if i['pass'] else 'FAIL'}  {i['name']}: {i['measured']}"
                                  for i in o['items']) + f"\n-- {o['passed']} passed, {o['failed']} failed")
    return 0 if not failed else 1


# ------------------------------------------------------------------ parser and main

def build_parser():
    ap = argparse.ArgumentParser(prog='open289', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', action='version', version=f'open289 {__version__}')
    ap.add_argument('--address', '-a', help='adapter BLE address (default: the first IR3000 FC found)')
    ap.add_argument('--sim', action='store_true', help='use the virtual IR3000 + Fluke 289 (no hardware)')
    ap.add_argument('--text', action='store_true', help='human-readable output instead of JSON')
    ap.add_argument('--verbose', '-v', action='store_true', help='log adapter control-point traffic to stderr')
    ap.add_argument('--max-wall', type=float, default=None,
                    help='time limit in seconds for one-shot commands (default 120; memory 600)')
    sub = ap.add_subparsers(dest='cmd', required=True, metavar='command')

    p = sub.add_parser('scan', help='find IR3000 FC adapters')
    p.add_argument('--seconds', type=float, default=10.0)
    sub.add_parser('info', help='adapter + meter identity, battery, memory level, clock, saved counts')

    p = sub.add_parser('read', help='full display state now (function, range, modes, all readings)')
    p.add_argument('--every', help='repeat at this interval (e.g. 1s); one JSON line per snapshot')
    p.add_argument('-n', '--count', type=int, help='with --every: stop after N snapshots')
    p.add_argument('--for', dest='for_', help='with --every: stop after this long (e.g. 5m)')

    p = sub.add_parser('live', help='real-time readings from BLE notifications (~4/s), one JSON line each')
    p.add_argument('-n', '--count', type=int, help='stop after N readings')
    p.add_argument('--for', dest='for_', help='stop after this long (e.g. 30s)')
    p.add_argument('--raw', action='store_true', help='include the raw notification bytes')
    p.add_argument('--timeout', type=float, default=10.0, help='error when no reading arrives for this long (s)')

    p = sub.add_parser('log', help='log readings to a CSV / JSONL file until --duration, "open289 stop" or Ctrl+C')
    p.add_argument('--out', '-o', help='output file (.csv or .jsonl; default data/log_<time>.csv)')
    p.add_argument('--format', choices=['csv', 'jsonl'], help='default: from the --out extension, else csv')
    p.add_argument('--duration', '-d', default='forever', help='e.g. 30s, 10m, 8h or forever (default)')
    p.add_argument('--every', default='0', help='write one reading per interval (e.g. 1s); 0 = every packet (~4/s)')
    p.add_argument('--snapshot-every', default='0',
                   help='also record the full display (modes, MIN/MAX/AVG) at this interval, e.g. 10s')
    p.add_argument('--status-every', default='10s', help='status line interval on stdout (0 = off)')
    p.add_argument('--reconnect-for', default='5m', help='keep retrying this long after the link is lost (0 = stop)')

    p = sub.add_parser('memory', help='saved measurements, min/max, peak sessions and recordings')
    p.add_argument('--detail', action='store_true', help='with --text: list every reading')

    p = sub.add_parser('recording', help='download trend recording INDEX to CSV / JSONL')
    p.add_argument('index', type=int)
    p.add_argument('--out', '-o', help='output file (default data/recording<INDEX>_<time>.csv)')
    p.add_argument('--format', choices=['csv', 'jsonl'])
    p.add_argument('--start', type=int, default=0, help='first record (resume an interrupted download)')
    p.add_argument('--limit', type=int, help='stop before this record index (end index, not a count)')
    p.add_argument('--no-batch', action='store_true', help='one record per round-trip (slower)')

    p = sub.add_parser('press', help='simulate front-panel keys')
    p.add_argument('keys', nargs='+', metavar='KEY', help=' '.join(PRESS_KEYS))
    p.add_argument('--held', action='store_true', help='long press (HELD_DOWN); RANGE held = auto range')
    p.add_argument('--settle', type=float, default=1.0, help='seconds to wait before reading the state back')
    p.add_argument('--allow-off', action='store_true', help='permit ONOFF (the meter asks; F1 confirms)')

    p = sub.add_parser('raw', help='raw meter commands (ID, IM and Q* queries unless --unsafe)')
    p.add_argument('commands', nargs='+', metavar='COMMAND')
    p.add_argument('--unsafe', action='store_true',
                   help='allow commands that change or erase meter state (MP, CSD, RI, OFF, ...)')

    sub.add_parser('selftest', help='read-only health check of adapter, link and meter')
    sub.add_parser('stop', help='end a running live / read --every / log cleanly')
    return ap


HANDLERS = {'scan': cmd_scan, 'info': cmd_info, 'read': cmd_read, 'live': cmd_live, 'log': cmd_log,
            'memory': cmd_memory, 'recording': cmd_recording, 'press': cmd_press, 'raw': cmd_raw,
            'selftest': cmd_selftest}
STREAMING = ('live', 'log', 'recording')        # no wall-clock limit
NEEDS_LOCK = ('info', 'read', 'live', 'log', 'memory', 'recording', 'press', 'raw', 'selftest')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):        # the console may use GBK; units contain micro, ohm, degree
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    _TEXT[0] = args.text
    if args.sim:
        transport.use_sim(True)
    if args.cmd == 'stop':
        request_stop()
        emit({'ok': True, 'cmd': 'stop', 'note': 'a running live / read --every / log stops within about a second'},
             'stop requested')
        return 0
    lock = None
    try:
        if args.cmd in NEEDS_LOCK:
            lock = AdapterLock().acquire()
        coro = HANDLERS[args.cmd](args)
        if args.cmd not in STREAMING and not (args.cmd == 'read' and args.every):
            limit = args.max_wall or (600.0 if args.cmd == 'memory' else 120.0)
            coro = wall(coro, limit, args.cmd)
        code = asyncio.run(coro)
        return code or 0
    except Open289Error as e:
        emit({'ok': False, 'cmd': args.cmd, 'error': e.to_dict()})
        return e.exit_code
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        emit({'ok': False, 'cmd': args.cmd, 'error': {'code': 'INTERNAL', 'message': repr(e),
                                                      'trace': traceback.format_exc().splitlines()[-6:]}})
        return 1
    finally:
        if lock:
            lock.release()


if __name__ == '__main__':
    sys.exit(main())
