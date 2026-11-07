"""Long-running logging of live readings (BLE notifications) to CSV or JSONL, with reconnect after link loss.

One reading per notification (about 4 per second), or one per --every interval (the most recent reading at each
tick). Optional periodic display snapshots (QDDB through the adapter pass-through) add what the live packets do not
carry: modes (HOLD, MIN MAX, REL ...) and MIN/MAX/AVG values. A run ends at --duration, on "open289 stop", on
Ctrl+C, or when the link is lost and does not come back within --reconnect-for.
"""
from __future__ import annotations

import asyncio
import csv
import json
import math
import os
import time

from .errors import Open289Error
from .meter import parse_meas_header
from .paths import state_dir
from .records import header_dict, live_dict
from .transport import READING_CHR, Link

CSV_FIELDS = ['pc_time', 'local_time', 'value', 'unit', 'display', 'state', 'function', 'attribute',
              'secondary_value', 'secondary_unit', 'secondary_display']


def stop_request_path():
    return os.path.join(state_dir(), 'stop.request')


def request_stop():
    p = stop_request_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        f.write(str(time.time()))


def clear_stop_request():
    try:
        os.remove(stop_request_path())
    except OSError:
        pass


def stop_requested():
    return os.path.exists(stop_request_path())


class _Stats:
    """Running min / max / mean of the primary value per unit."""

    def __init__(self):
        self.by_unit = {}

    def add(self, unit, value):
        if value is None or not math.isfinite(value):
            return
        s = self.by_unit.setdefault(unit, {'n': 0, 'min': value, 'max': value, 'sum': 0.0})
        s['n'] += 1
        s['min'] = min(s['min'], value)
        s['max'] = max(s['max'], value)
        s['sum'] += value

    def public(self):
        return {u: {'n': s['n'], 'min': s['min'], 'max': s['max'], 'mean': s['sum'] / s['n']}
                for u, s in self.by_unit.items() if s['n']}


class _Writer:
    def __init__(self, path, fmt):
        self.path = path
        self.fmt = fmt
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        self.fh = open(path, 'w', newline='' if fmt == 'csv' else None, encoding='utf-8')
        self.side = None
        if fmt == 'csv':
            self.csv = csv.DictWriter(self.fh, fieldnames=CSV_FIELDS)
            self.csv.writeheader()

    def reading(self, rec, raw_hex):
        if self.fmt == 'jsonl':
            self.fh.write(json.dumps({**rec, 'type': 'ble', 'hex': raw_hex}, ensure_ascii=False) + '\n')
        else:
            sec = rec.get('secondary') or {}
            self.csv.writerow({
                'pc_time': f"{rec['t']:.3f}",
                'local_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(rec['t'])) + f".{int(rec['t'] % 1 * 1000):03d}",
                'value': '' if rec['value'] is None else rec['value'], 'unit': rec['unit'],
                'display': rec['display'], 'state': rec['state'], 'function': rec['function'],
                'attribute': rec.get('attribute') or '',
                'secondary_value': '' if sec.get('value') is None else sec['value'],
                'secondary_unit': sec.get('unit', ''), 'secondary_display': sec.get('display', '')})
        self.fh.flush()

    def snapshot(self, obj):
        if self.fmt == 'jsonl':
            self.fh.write(json.dumps({**obj, 'type': 'qddb'}, ensure_ascii=False) + '\n')
            self.fh.flush()
            return
        if self.side is None:                      # CSV: snapshots go to a JSONL sidecar
            self.side = open(self.path + '.display.jsonl', 'w', encoding='utf-8')
        self.side.write(json.dumps(obj, ensure_ascii=False) + '\n')
        self.side.flush()

    def event(self, obj):
        if self.fmt == 'jsonl':
            self.fh.write(json.dumps(obj, ensure_ascii=False) + '\n')
            self.fh.flush()

    def close(self):
        for f in (self.fh, self.side):
            if f:
                f.close()

    @property
    def side_path(self):
        return self.side.name if self.side else None


async def run_log(*, address, out_path, fmt='csv', duration=None, every=0.0, snapshot_every=0.0,
                  status_every=10.0, reconnect_for=300.0, verbose=False, emit=print, live_timeout=10.0):
    """Log until duration / stop request / Ctrl+C / unrecoverable link loss. Returns the summary dict."""
    w = _Writer(out_path, fmt)
    stats = _Stats()
    t_start = time.time()
    deadline = t_start + duration if duration else None
    n_written = 0
    n_packets = 0
    n_snapshots = 0
    gaps = []
    stopped_by = None
    last_rec = None
    last_written_t = 0.0
    pending = None                   # decimation: the most recent reading not yet written
    next_status = t_start + status_every if status_every else None
    meter_info = {}

    def finished():
        nonlocal stopped_by
        if deadline and time.time() >= deadline:
            stopped_by = 'duration'
        elif stop_requested():
            stopped_by = 'stop'
        return stopped_by is not None

    def write(rec, hexs):
        nonlocal n_written, last_written_t
        w.reading(rec, hexs)
        stats.add(rec['unit'], rec['value'])
        n_written += 1
        last_written_t = rec['t']

    emit({'ok': True, 'cmd': 'log', 'event': 'started', 'file': os.path.abspath(out_path), 'format': fmt,
          'duration_s': duration, 'every_s': every, 'snapshot_every_s': snapshot_every})
    try:
        while not finished():
            try:
                link = Link(address, verbose=verbose)
                async with link:
                    model = await link.require_meter()
                    meter_info = {'model': model, 'adapter': link.client.address}
                    if gaps and gaps[-1].get('to') is None:
                        gaps[-1]['to'] = time.time()
                        w.event({'t': time.time(), 'type': 'event', 'event': 'reconnected'})
                        emit({'ok': True, 'cmd': 'log', 'event': 'reconnected', 'gap_s': gaps[-1]['to'] - gaps[-1]['from']})
                    q: asyncio.Queue = asyncio.Queue()
                    await link.client.start_notify(READING_CHR, lambda _h, d: q.put_nowait((time.time(), bytes(d))))
                    snap_task = None
                    if snapshot_every:
                        async def snapshots():
                            nonlocal n_snapshots
                            while True:
                                await asyncio.sleep(snapshot_every)
                                t = time.time()
                                try:
                                    ack, payload = await link.meter.raw('QDDB')
                                except Open289Error as e:
                                    w.snapshot({'t': t, 'ack': 'ERROR', 'error': e.to_dict()})
                                    continue
                                obj = {'t': t, 'ack': ack, 'hex': payload.hex()}
                                if ack == 'OK' and payload.startswith(b'#0'):
                                    try:
                                        obj['display'] = header_dict(parse_meas_header(payload[2:], 0)[0])
                                    except (ValueError, Exception):  # noqa: BLE001
                                        pass
                                w.snapshot(obj)
                                n_snapshots += 1
                        snap_task = asyncio.create_task(snapshots())
                    try:
                        silent_since = time.time()
                        while not finished():
                            try:
                                t, data = await asyncio.wait_for(q.get(), timeout=1.0)
                            except asyncio.TimeoutError:
                                if link.lost.is_set() or not link.client.is_connected:
                                    raise Open289Error('LINK_LOST', 'BLE link lost')
                                if time.time() - silent_since > live_timeout:
                                    raise Open289Error('LINK_LOST', f'no live reading for {live_timeout:g} s')
                                t = None
                            if t is not None:
                                silent_since = t
                                if len(data) < 8:
                                    continue
                                n_packets += 1
                                rec = live_dict(t, data)
                                last_rec = rec
                                if not every:
                                    write(rec, data.hex())
                                else:
                                    pending = (rec, data.hex())
                            if every and pending and time.time() - last_written_t >= every:
                                write(*pending)
                                pending = None
                            if next_status and time.time() >= next_status:
                                next_status = time.time() + status_every
                                emit({'ok': True, 'cmd': 'log', 'event': 'status', 'elapsed_s': round(time.time() - t_start, 1),
                                      'records': n_written, 'last': last_rec and last_rec['display']})
                    finally:
                        if snap_task:
                            snap_task.cancel()
                            try:
                                await snap_task
                            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                                pass
                        try:
                            await link.client.stop_notify(READING_CHR)
                        except Exception:  # noqa: BLE001
                            pass
            except Open289Error as e:
                if e.code not in ('LINK_LOST', 'NOT_FOUND', 'BLE_ERROR', 'TIMEOUT', 'DETACHED'):
                    raise
                if not meter_info:          # never connected: report the error instead of waiting to reconnect
                    raise
                if not reconnect_for:
                    stopped_by = 'link_lost'
                    break
                if not gaps or gaps[-1].get('to') is not None:
                    gaps.append({'from': time.time(), 'to': None, 'reason': e.message})
                    w.event({'t': time.time(), 'type': 'event', 'event': 'link_lost', 'reason': e.message})
                    emit({'ok': True, 'cmd': 'log', 'event': 'link_lost', 'reason': e.message,
                          'retry_for_s': reconnect_for})
                if time.time() - gaps[-1]['from'] > reconnect_for:
                    stopped_by = 'link_lost'
                    break
                await asyncio.sleep(3.0)
    except (KeyboardInterrupt, asyncio.CancelledError):
        stopped_by = 'interrupted'
    finally:
        if pending:
            write(*pending)
        w.close()
        clear_stop_request()
    t_end = time.time()
    summary = {'ok': True, 'cmd': 'log', 'event': 'done', 'file': os.path.abspath(out_path), 'format': fmt,
               'records': n_written, 'packets': n_packets, 'snapshots': n_snapshots,
               'started': t_start, 'ended': t_end, 'duration_s': round(t_end - t_start, 3),
               'stopped_by': stopped_by, 'gaps': gaps, 'stats': stats.public(), **({'meter': meter_info} if meter_info else {})}
    if w.side_path:
        summary['display_file'] = os.path.abspath(w.side_path)
    if not n_written and stopped_by == 'link_lost':
        raise Open289Error('LINK_LOST', 'no readings were logged: the link was lost and did not come back',
                           'check that the meter is on and the adapter is awake, then retry')
    return summary
