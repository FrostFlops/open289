"""End-to-end CLI tests against the virtual IR3000 FC + Fluke 289 (conftest selects the simulator)."""
import csv
import json
import threading
import time

import pytest

from open289 import cli, sim
from open289.lock import AdapterLock


def run(capsys, *argv):
    """Run the CLI in-process; return (exit code, list of JSON objects printed on stdout)."""
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, [json.loads(line) for line in out.splitlines() if line.strip()]


def one(capsys, *argv):
    code, objs = run(capsys, *argv)
    assert len(objs) == 1, objs
    return code, objs[0]


def sim_set(**kw):
    st = sim.load_state()
    st.update(kw)
    sim.save_state(st)


def test_info(capsys):
    code, o = one(capsys, 'info')
    assert code == 0 and o['ok'] and o['cmd'] == 'info'
    assert o['adapter']['msp_fw'] == '01.00.15'
    assert o['meter']['attached'] and o['meter']['model'] == 'FLUKE 289'
    assert o['saved'] == {'recordings': 1, 'minmax': 0, 'peak': 1, 'measurements': 2}


def test_read(capsys):
    code, o = one(capsys, 'read')
    assert code == 0 and o['function'] == 'V_DC'
    assert {r['id'] for r in o['readings']} >= {'LIVE', 'PRIMARY'}
    assert o['primary']['unit'] == 'VDC' and 4.9 < o['primary']['value'] < 5.2


def test_read_every(capsys):
    code, objs = run(capsys, 'read', '--every', '0.1s', '-n', '3')
    assert code == 0
    assert [o.get('event') for o in objs] == [None, None, None, 'done']
    assert objs[-1]['snapshots'] == 3 and objs[-1]['stopped_by'] == 'count'


def test_live(capsys):
    code, objs = run(capsys, 'live', '-n', '10', '--raw')
    assert code == 0
    readings = [o for o in objs if o.get('type') == 'reading']
    assert len(readings) == 10
    assert all(r['unit'] == 'V DC' and r['function'] == 'VDC' and 4.9 < r['value'] < 5.2 for r in readings)
    assert all(len(bytes.fromhex(r['raw'])) == 16 for r in readings)
    assert objs[-1]['event'] == 'done' and objs[-1]['readings'] == 10


def test_log_csv(capsys, tmp_path):
    out = tmp_path / 'log.csv'
    code, objs = run(capsys, 'log', '--duration', '1.5s', '--out', str(out), '--status-every', '0.5s')
    assert code == 0
    assert objs[0]['event'] == 'started'
    assert any(o.get('event') == 'connected' and o['connect_s'] >= 0 for o in objs)
    done = objs[-1]
    assert done['event'] == 'done' and done['stopped_by'] == 'duration' and done['records'] > 5
    assert done['logging_s'] >= 1.5            # --duration counts from the first connection, not from the scan
    assert 4.9 < done['stats']['V DC']['mean'] < 5.2
    rows = list(csv.DictReader(open(out, encoding='utf-8')))
    assert len(rows) == done['records'] and rows[0]['unit'] == 'V DC'


def test_log_jsonl_snapshots_and_decimation(capsys, tmp_path):
    out = tmp_path / 'log.jsonl'
    code, objs = run(capsys, 'log', '--duration', '2s', '--out', str(out), '--every', '0.5s',
                     '--snapshot-every', '0.5s', '--status-every', '0')
    assert code == 0
    recs = [json.loads(line) for line in open(out, encoding='utf-8')]
    ble = [r for r in recs if r['type'] == 'ble']
    qddb = [r for r in recs if r['type'] == 'qddb']
    assert 2 <= len(ble) <= 6                  # one reading per 0.5 s, not every packet
    assert qddb and qddb[0]['ack'] == 'OK' and qddb[0]['display']['function'] == 'V_DC'


def test_log_stop(capsys, tmp_path):
    out = tmp_path / 'stop.csv'
    result = {}

    def worker():
        result['code'] = cli.main(['log', '--out', str(out), '--status-every', '0'])

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(1.5)
    assert cli.main(['stop']) == 0
    t.join(timeout=15)
    assert not t.is_alive() and result['code'] == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    done = [o for o in lines if o.get('event') == 'done']
    assert done and done[0]['stopped_by'] == 'stop'


def test_memory(capsys):
    code, o = one(capsys, 'memory')
    assert code == 0 and o['ok']
    names = {(i['kind'], i['name']) for i in o['items']}
    assert ('measurement', 'Save 1') in names and ('recording', 'Save 4') in names
    rec = next(i for i in o['items'] if i['kind'] == 'recording')
    assert rec['records'] == 72 and rec['interval_s'] == 1.0


def test_recording_download(capsys, tmp_path):
    out = tmp_path / 'rec.csv'
    code, objs = run(capsys, 'recording', '0', '--out', str(out))
    assert code == 0
    done = objs[-1]
    assert done['event'] == 'done' and done['records'] == 72
    rows = list(csv.DictReader(open(out, encoding='utf-8')))
    assert len(rows) == 72
    prim = [float(r['primary']) for r in rows if r['primary']]
    assert min(prim) < 1.1 and max(prim) > 1.9       # the 0.1 Hz square wave between ~0.99 V and ~1.99 V
    assert all(float(r['minimum']) <= float(r['average']) <= float(r['maximum']) for r in rows if r['average'])


def test_recording_out_of_range(capsys):
    code, o = one(capsys, 'recording', '9')
    assert code != 0 and not o['ok']


def test_press_hold(capsys):
    code, o = one(capsys, 'press', 'HOLD', '--settle', '0')
    assert code == 0 and 'HOLD' in o['presses'][0]['state']['modes']
    code, o = one(capsys, 'press', 'HOLD', '--settle', '0')
    assert 'HOLD' not in o['presses'][0]['state']['modes']


def test_press_minmax_save(capsys):
    code, o = one(capsys, 'press', 'MINMAX', '--settle', '0')
    assert 'MIN_MAX_AVG' in o['presses'][0]['state']['modes']
    code, o = one(capsys, 'press', 'MINMAX', 'F2', 'F2', 'F4', '--settle', '0')
    assert code == 0
    states = [p['state'] for p in o['presses']]
    assert isinstance(states[0], str) and 'result page' in states[0]
    assert isinstance(states[-1], dict)                  # F4 closed the result page
    code, o = one(capsys, 'info')
    assert o['saved']['minmax'] == 1


def test_meter_busy_on_result_page(capsys):
    run(capsys, 'press', 'MINMAX', 'MINMAX', '--settle', '0')
    code, o = one(capsys, 'read')
    assert code == 5 and o['error']['code'] == 'METER_BUSY'
    run(capsys, 'press', 'F4', '--settle', '0')
    code, o = one(capsys, 'read')
    assert code == 0


def test_press_onoff_guard(capsys):
    code, o = one(capsys, 'press', 'ONOFF')
    assert code == 7 and o['error']['code'] == 'UNSAFE_OPERATION'
    code, o = one(capsys, 'read')
    assert code == 0                                      # nothing was sent


def test_press_unknown_key(capsys):
    code, o = one(capsys, 'press', 'FOO')
    assert code == 2 and o['error']['code'] == 'INVALID_ARG'


def test_raw(capsys):
    code, o = one(capsys, 'raw', 'ID', 'QM', 'QDDB')
    assert code == 0
    r = {x['command']: x for x in o['results']}
    assert r['ID']['text'].startswith('FLUKE 289')
    assert 'binary_hex' in r['QDDB']


def test_raw_refuses_non_query(capsys):
    before = sim.load_state()
    code, o = one(capsys, 'raw', 'CSD ALL')
    assert code == 7 and o['results'][0]['error']['code'] == 'UNSAFE_OPERATION'
    assert sim.load_state() == before


def test_selftest(capsys):
    code, o = one(capsys, 'selftest')
    assert code == 0 and o['failed'] == 0 and o['passed'] >= 8


def test_scan(capsys):
    code, o = one(capsys, 'scan', '--seconds', '0.5')
    assert code == 0 and o['devices'] and o['devices'][0]['name'] == 'IR 3000 FC'


def test_busy(capsys):
    lock = AdapterLock().acquire()
    try:
        code, o = one(capsys, 'info')
    finally:
        lock.release()
    assert code == 4 and o['error']['code'] == 'BUSY'


def test_not_found(capsys):
    sim_set(adapter_on=False)
    code, o = one(capsys, 'info')
    assert code == 3 and o['error']['code'] == 'NOT_FOUND'


def test_detached(capsys):
    sim_set(meter_on=False)
    code, o = one(capsys, 'read')
    assert code == 5 and o['error']['code'] == 'DETACHED'


def test_real_ble_refused_in_tests(capsys, monkeypatch):
    monkeypatch.setenv('OPEN289_SIM', '0')
    code, o = one(capsys, 'info')
    assert code == 6 and o['error']['code'] == 'BLE_ERROR'


def test_text_mode(capsys):
    code = cli.main(['--text', 'info'])
    out = capsys.readouterr().out
    assert code == 0 and 'FLUKE 289' in out and not out.lstrip().startswith('{')


def test_no_forbidden_writes(capsys, tmp_path):
    """Every command path together must never write a firmware-update characteristic or a test opcode."""
    for argv in (['info'], ['read'], ['live', '-n', '3'], ['memory'], ['raw', 'ID', 'CSD ALL'],
                 ['press', 'HOLD', 'HOLD', '--settle', '0'], ['selftest'],
                 ['recording', '0', '--limit', '10', '--out', str(tmp_path / 'r.csv')],
                 ['log', '--duration', '0.5s', '--out', str(tmp_path / 'l.csv'), '--snapshot-every', '0.2s']):
        cli.main(argv)
    capsys.readouterr()
    audit = sim.read_audit()
    assert audit, 'the simulator recorded no writes'
    assert not [w for w in audit if w.get('forbidden')]
    allowed = {'b6982902-7562-11e2-b50d-00163e46f8fe', 'b6982918-7562-11e2-b50d-00163e46f8fe'}
    assert {w['uuid'].lower() for w in audit} <= allowed
