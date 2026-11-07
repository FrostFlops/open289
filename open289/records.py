"""JSON-ready dictionaries for readings, display snapshots and saved items (shared by the CLI, logger and MCP)."""
from __future__ import annotations

from .meter import BinReading, MeasHeader, Session, TrendRecord, ts_text
from .reading import ATTRIB, FUNCTION, UNIT, Reading, decode_notification


def _round(r: Reading):
    v = r.value
    if v is None:
        return None
    return round(v, max(r.decimals - r.mag_exp, 0))


def reading_dict(r: Reading) -> dict:
    d = {'value': _round(r), 'unit': UNIT.get(r.unit, f'unit{r.unit}'), 'display': r.text(),
         'state': r.state_name, 'function': FUNCTION.get(r.function, f'func{r.function}')}
    if r.attribute:
        d['attribute'] = ATTRIB.get(r.attribute, f'attr{r.attribute}')
    if r.capture:
        d['capture'] = True
    return d


def live_dict(t: float, data: bytes, raw: bool = False) -> dict:
    """One live notification: primary reading (+ secondary when the meter shows one). value is in SI base units."""
    p, s = decode_notification(data)
    d = {'t': round(t, 3), **reading_dict(p)}
    if s is not None:
        d['secondary'] = reading_dict(s)
    if raw:
        d['raw'] = data.hex()
    return d


def binreading_dict(r: BinReading) -> dict:
    v = r.csv_value(2 if r.reading_id == 'AVERAGE' else 0)
    return {'id': r.reading_id, 'value': None if v == '' else v, 'unit': r.unit, 'display': r.text(),
            'state': r.state, 'attribute': r.attribute, 'decimals': r.decimals,
            'time': ts_text(r.ts), 'ts': r.ts}


def header_dict(h: MeasHeader, bolt: bool = True) -> dict:
    d = {'function': h.prim, 'secondary_function': h.sec, 'autorange': h.autorange == 'AUTO',
         'range': h.range_max, 'unit': h.unit, 'modes': [m for m in h.mode if m != 'NONE'],
         'readings': [binreading_dict(r) for r in h.readings]}
    if bolt:
        d['hazard'] = h.bolt == 'ON'
    if h.minmax_start:
        d['minmax_start'] = ts_text(h.minmax_start)
    prim = next((r for r in h.readings if r.reading_id == 'PRIMARY'), None)
    if prim is not None:
        d['primary'] = binreading_dict(prim)
    return d


def session_dict(x: Session) -> dict:
    d = {'kind': x.kind, 'index': x.index, 'name': x.name, 'seq': x.seq, **header_dict(x.header, bolt=False)}
    if x.start:
        d['start'] = ts_text(x.start)
    if x.end:
        d['end'] = ts_text(x.end)
    if x.kind == 'recording':
        d.update({'interval_s': x.interval, 'event_threshold': x.threshold, 'records': x.num_samples,
                  'reading_index': x.reading_index, 'duration_s': round(x.end - x.start, 3)})
    return d


def trend_dict(k: int, r: TrendRecord) -> dict:
    def v(b, extra=0):
        x = b.csv_value(extra)
        return None if x == '' else x
    return {'index': k, 'start': ts_text(r.start), 'end': ts_text(r.end), 'unit': r.primary.unit,
            'primary': v(r.primary), 'primary_state': r.primary.state,
            'minimum': v(r.minimum), 'minimum_state': r.minimum.state,
            'average': v(r.average, 2), 'average_state': r.average.state,
            'maximum': v(r.maximum), 'maximum_state': r.maximum.state,
            'samples': r.samples, 'record_type': r.record_type, 'stable': r.stable, 'transient': r.transient}
