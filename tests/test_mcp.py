"""MCP server (open289/mcp_server.py) against the virtual IR3000 FC + Fluke 289: the server runs as a subprocess
(python -m open289.mcp_server, the way an MCP client starts it) and every tool goes through the real CLI and the
simulator (tests/conftest.py sets OPEN289_SIM, OPEN289_NO_BLE and a private OPEN289_HOME)."""
import csv
import itertools
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from open289 import __version__
from open289 import mcp_server as M

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_NAMES = ['status', 'read', 'live', 'memory', 'download_recording', 'press', 'query', 'selftest', 'log_start',
              'log_status', 'log_stop']


BATCH = '<batch>'                                                    # key of the replies to a batch


class Client:
    """A minimal MCP client over the server's stdin/stdout (newline-delimited JSON-RPC 2.0)."""

    def __init__(self, tmp_path, name='server', argv=None, cwd=ROOT):
        self.err = open(tmp_path / ('%s.stderr.txt' % name), 'wb')
        self.p = subprocess.Popen(argv or [sys.executable, '-X', 'utf8', '-m', 'open289.mcp_server'], cwd=cwd,
                                  env=dict(os.environ), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self.err)
        self.replies = {}
        self.cond = threading.Condition()
        self.ids = itertools.count(1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in iter(self.p.stdout.readline, b''):
            msg = json.loads(line)
            with self.cond:
                self.replies.setdefault(BATCH if isinstance(msg, list) else msg.get('id'), []).append(msg)
                self.cond.notify_all()

    def send(self, msg):
        data = msg if isinstance(msg, bytes) else json.dumps(msg).encode() + b'\n'
        self.p.stdin.write(data)
        self.p.stdin.flush()

    def wait(self, mid, timeout=60.0):
        with self.cond:
            assert self.cond.wait_for(lambda: self.replies.get(mid), timeout), 'no reply to %r' % (mid,)
            return self.replies[mid].pop(0)

    def request(self, method, params=None, timeout=60.0):
        mid = next(self.ids)
        self.send({'jsonrpc': '2.0', 'id': mid, 'method': method, **({'params': params} if params is not None
                                                                      else {})})
        return self.wait(mid, timeout)

    def initialize(self, version='2025-06-18'):
        r = self.request('initialize', {'protocolVersion': version, 'capabilities': {},
                                        'clientInfo': {'name': 'pytest', 'version': '1'}})
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        return r['result']

    def call(self, name, args=None, timeout=180.0):
        r = self.request('tools/call', {'name': name, 'arguments': args or {}}, timeout)
        assert 'result' in r, r
        return r['result']

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(15)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(15)
        self.err.close()


def doc(result):
    """structuredContent of a successful tool result, checked against its text content."""
    assert result['isError'] is False, result['content'][0]['text']
    d = result['structuredContent']
    assert json.loads(result['content'][0]['text']) == d
    return d


def err_code(result):
    assert result['isError'], result['content'][0]['text']
    return result['structuredContent']['error']['code']


def stop_logs():
    """Whatever a test leaves running ends (a background log outlives its server by design)."""
    subprocess.run([sys.executable, '-X', 'utf8', '-m', 'open289', 'stop'], env=dict(os.environ),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


@pytest.fixture
def mcp(tmp_path):
    c = Client(tmp_path)
    c.init = c.initialize()
    yield c
    c.close()


@pytest.fixture
def logs():
    yield
    stop_logs()


def csv_rows(path):
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


# ------------------------------------------------------------------------------------------------ protocol
def test_initialize_and_tools_list(mcp):
    assert mcp.init['protocolVersion'] == '2025-06-18'
    assert mcp.init['serverInfo']['name'] == 'open289' and mcp.init['serverInfo']['version'] == __version__
    assert mcp.init['capabilities'] == {'tools': {'listChanged': False}}
    ins = mcp.init['instructions']
    assert 'ONE connection' in ins and 'BUSY' in ins and 'log_stop' in ins and 'METER_BUSY' in ins
    assert 'allow_off' in ins and 'read-only' in ins
    assert mcp.request('ping')['result'] == {}
    assert mcp.request('resources/list')['error']['code'] == -32601
    tools = mcp.request('tools/list')['result']['tools']
    assert [t['name'] for t in tools] == TOOL_NAMES
    for t in tools:
        s = t['inputSchema']
        assert s['type'] == 'object' and s['additionalProperties'] is False, t['name']
        assert t['description'] and all(p.get('description') or p.get('enum') for p in s['properties'].values())
    by = {t['name']: t['inputSchema'] for t in tools}
    from open289.cli import PRESS_KEYS
    assert by['press']['properties']['keys']['items']['enum'] == list(PRESS_KEYS)
    assert by['press']['properties']['allow_off']['default'] is False and by['press']['required'] == ['keys']
    assert (by['query']['properties']['commands']['minItems'], by['query']['properties']['commands']['maxItems']) \
        == (1, 10)
    assert (by['live']['properties']['count']['minimum'], by['live']['properties']['count']['maximum']) == (1, 400)
    assert (by['live']['properties']['seconds']['minimum'], by['live']['properties']['seconds']['maximum']) \
        == (0.1, 120)
    assert by['download_recording']['required'] == ['index']
    assert by['log_start']['properties']['duration']['default'] == '10m'
    assert set(by['log_status']['properties']) == {'job'}


def test_protocol_versions(tmp_path):
    c = Client(tmp_path)
    try:
        assert c.initialize('2024-11-05')['protocolVersion'] == '2024-11-05'
        res = c.call('status')                                       # old protocol: text content only
        assert 'structuredContent' not in res and not res['isError']
        assert json.loads(res['content'][0]['text'])['meter']['attached'] is True
        assert c.initialize('2025-03-26')['protocolVersion'] == '2025-03-26'
        assert 'structuredContent' not in c.call('status')
        assert c.initialize('2099-01-01')['protocolVersion'] == M.PROTOCOL_VERSIONS[0]   # unknown: our newest
        assert doc(c.call('status'))['ok']
        assert c.initialize('2025-06-18')['protocolVersion'] == '2025-06-18'
        assert doc(c.call('status'))['ok']
    finally:
        c.close()


def test_bad_messages_never_end_the_server(mcp):
    mcp.send(b'this is not json\n')
    assert mcp.wait(None)['error']['code'] == -32700                 # parse error, id null
    r = mcp.request('tools/call', {'name': 'no_such_tool', 'arguments': {}})
    assert r['error']['code'] == -32602 and 'no_such_tool' in r['error']['message']
    assert mcp.request('tools/call', {'name': 'status', 'arguments': [1]})['error']['code'] == -32602
    assert mcp.request('initialize', [1])['error']['code'] == -32602
    mcp.send(b'{"jsonrpc":"2.0","id":"x7","method":7}\n')
    assert mcp.wait('x7')['error']['code'] == -32600
    mcp.send(b'{"jsonrpc":"2.0","id":901}\n')                       # an id but no method: invalid request
    assert mcp.wait(901)['error']['code'] == -32600
    mcp.send(b'{"jsonrpc":"2.0","id":902,"result":{}}\n')           # a response (we send no requests): ignored
    mcp.send(b'[]\n')                                                # empty batch: one invalid request, no array
    assert mcp.wait(None)['error']['code'] == -32600
    assert mcp.request('ping')['result'] == {}                       # still serving
    assert 902 not in mcp.replies and BATCH not in mcp.replies


def test_batch_gets_one_array_of_replies(mcp):
    mcp.send(json.dumps([{'jsonrpc': '2.0', 'id': 'a', 'method': 'ping'},
                         {'jsonrpc': '2.0', 'method': 'notifications/initialized'},          # no reply
                         {'jsonrpc': '2.0', 'id': 'b', 'method': 'tools/call',
                          'params': {'name': 'status', 'arguments': {}}},                    # answers from a thread
                         5,                                                                  # invalid member
                         {'jsonrpc': '2.0', 'id': 'c', 'method': 'nope'}]).encode() + b'\n')
    replies = mcp.wait(BATCH, 120)
    assert isinstance(replies, list) and len(replies) == 4
    by = {r['id']: r for r in replies}
    assert set(by) == {'a', 'b', 'c', None}
    assert by['a']['result'] == {} and by['c']['error']['code'] == -32601 and by[None]['error']['code'] == -32600
    assert doc(by['b']['result'])['meter']['attached'] is True
    assert not any(k in mcp.replies for k in ('a', 'b', 'c'))        # nothing sent on its own
    mcp.send(b'[{"jsonrpc":"2.0","method":"notifications/initialized"}]\n')   # only notifications: no reply at all
    assert mcp.request('ping')['result'] == {}
    assert not mcp.replies.get(BATCH)


def test_arguments_are_checked(mcp):
    for name, args in (('live', {'count': 0}), ('live', {'count': 401}), ('live', {'seconds': 500}),
                       ('live', {'count': 'ten'}), ('status', {'verbose': True}), ('download_recording', {}),
                       ('download_recording', {'index': -1}), ('press', {'keys': []}), ('press', {'keys': ['PUSH']}),
                       ('query', {'commands': ['ID'] * 11}), ('query', {'commands': []}),
                       ('log_start', {'format': 'xml'}), ('press', {'keys': ['HOLD'], 'allow_off': 'yes'}),
                       ('live', {'seconds': 1e-5}), ('live', {'seconds': 0}), ('status', {'max_wall': 10 ** 400}),
                       ('live', {'count': 10 ** 400}), ('read', {'max_wall': float('nan')}),
                       ('log_start', {'duration': '0'}), ('log_start', {'duration': '0.0s'}),
                       ('log_start', {'duration': '0ms'})):
        res = mcp.call(name, args)
        assert err_code(res) == 'INVALID_ARG', (name, args, res['content'][0]['text'])
    res = mcp.call('status', {'verbose': True})
    assert 'verbose' in res['structuredContent']['error']['message']
    res = mcp.call('log_start', {'duration': '0'})
    assert 'forever' in res['structuredContent']['error']['hint']
    assert err_code(mcp.call('log_status')) == 'INVALID_ARG'          # no log was started


# ------------------------------------------------------------------------------------------------ one-shot tools
def test_status_read_and_memory(mcp):
    st = doc(mcp.call('status'))
    assert st['cmd'] == 'info' and st['meter']['attached'] is True and st['meter']['model'].startswith('FLUKE 28')
    assert st['adapter']['address'] and isinstance(st['saved'], dict)
    rd = doc(mcp.call('read'))
    assert rd['function'] and isinstance(rd['range'], float) and isinstance(rd['modes'], list)
    assert rd['primary']['id'] == 'PRIMARY' and rd['primary']['display']
    assert 'PRIMARY' in [r['id'] for r in rd['readings']] and isinstance(rd['autorange'], bool)
    assert all('ts' not in r and 'decimals' not in r for r in rd['readings'])     # compact
    assert abs(rd['t'] - time.time()) < 60 and rd['time'][:4].isdigit()
    mem = doc(mcp.call('memory'))
    assert sum(mem['counts'].values()) == len(mem['items']) > 0
    rec = [x for x in mem['items'] if x['kind'] == 'recording']
    assert len(rec) == mem['counts']['recordings'] >= 1
    assert rec[0]['records'] > 0 and rec[0]['interval_s'] > 0 and rec[0]['function'] and rec[0]['name']


def test_live_returns_a_summary(mcp):
    lv = doc(mcp.call('live', {'count': 10}))
    assert lv['count'] == 10 and lv['stopped_by'] == 'count' and lv['rate_hz'] > 0
    assert lv['first_t'] <= lv['last_t'] and lv['first_time'] <= lv['last_time']
    assert len(lv['last']) == M.LAST_READINGS and lv['functions'] and lv['units']
    for unit, s in lv['stats'].items():
        assert unit in lv['units'] and s['min'] <= s['mean'] <= s['max'] and 0 < s['n'] <= 10
    assert all({'t', 'time', 'value', 'unit', 'display'} <= set(r) for r in lv['last'])
    assert len(json.dumps(lv)) < 4000                                # never every reading
    lv = doc(mcp.call('live', {'seconds': 0.5}))
    assert lv['stopped_by'] == 'duration' and lv['count'] > 0
    lv = doc(mcp.call('live'))                                       # default: 20 readings
    assert lv['count'] == 20
    lv = doc(mcp.call('live', {'seconds': 0.1}))                     # the smallest: '--for 0.1s', not '1e-01s'
    assert lv['stopped_by'] == 'duration'


def test_download_recording(mcp, tmp_path):
    mem = doc(mcp.call('memory'))
    head = next(x for x in mem['items'] if x['kind'] == 'recording' and x['index'] == 0)
    out = tmp_path / 'rec0.csv'
    rec = doc(mcp.call('download_recording', {'index': 0, 'out': str(out)}))
    assert rec['records'] == head['records'] and os.path.samefile(rec['file'], out) and rec['format'] == 'csv'
    assert rec['retries'] >= 0 and rec['s_per_record'] >= 0
    assert rec['recording']['name'] == head['name'] and rec['recording']['records'] == head['records']
    rows = csv_rows(out)
    assert len(rows) == head['records'] and [int(r['index']) for r in rows] == list(range(head['records']))
    out2 = tmp_path / 'rec0.jsonl'
    part = doc(mcp.call('download_recording', {'index': 0, 'out': str(out2), 'limit': 10}))
    assert part['records'] == 10 and part['format'] == 'jsonl'
    with open(out2, encoding='utf-8') as f:
        assert [json.loads(l)['index'] for l in f] == list(range(10))
    rest = doc(mcp.call('download_recording', {'index': 0, 'out': str(out2), 'start': 10}))   # resume
    assert rest['records'] == head['records'] - 10
    with open(out2, encoding='utf-8') as f:
        assert [json.loads(l)['index'] for l in f] == list(range(head['records']))
    missing = mcp.call('download_recording', {'index': mem['counts']['recordings'] + 5})
    assert err_code(missing) == 'NO_DATA'


def test_press_hold_then_read(mcp):
    assert 'HOLD' not in doc(mcp.call('read'))['modes']
    pr = doc(mcp.call('press', {'keys': ['HOLD']}))
    assert [(p['key'], p['held'], p['ack']) for p in pr['presses']] == [('HOLD', False, 'OK')]
    assert 'HOLD' in pr['presses'][0]['state']['modes'] and 'readings' not in pr['presses'][0]['state']
    assert 'HOLD' in doc(mcp.call('read'))['modes']
    assert doc(mcp.call('press', {'keys': ['hold']}))['presses'][0]['key'] == 'HOLD'     # keys are case-insensitive
    assert 'HOLD' not in doc(mcp.call('read'))['modes']


def test_press_onoff_needs_allow_off(mcp):
    res = mcp.call('press', {'keys': ['ONOFF']})
    assert err_code(res) == 'UNSAFE_OPERATION' and 'allow-off' in res['structuredContent']['error']['hint']
    assert doc(mcp.call('status'))['meter']['attached'] is True      # still on


def test_query_is_read_only(mcp):
    from open289 import sim
    q = doc(mcp.call('query', {'commands': ['ID', 'QM']}))
    assert [r['command'] for r in q['results']] == ['ID', 'QM'] and all(r['ack'] == 'OK' for r in q['results'])
    assert q['results'][0]['text'].startswith('FLUKE 28') and q['results'][1]['text']
    counts = doc(mcp.call('memory'))['counts']
    writes = len(sim.read_audit())
    for cmds in (['PRESS HOLD'], ['ID', 'CSD'], ['MP clock,0'], ['QM; CSD'], ['qm\r\nCSD'], ['RI']):
        res = mcp.call('query', {'commands': cmds})
        assert err_code(res) == 'UNSAFE_OPERATION', cmds
    assert len(sim.read_audit()) == writes                            # refused before anything reached the adapter
    assert 'HOLD' not in doc(mcp.call('read'))['modes'] and doc(mcp.call('memory'))['counts'] == counts
    st = doc(mcp.call('query', {'commands': ['qsls']}))               # case-insensitive, like the meter
    assert st['results'][0]['ack'] == 'OK'


def test_selftest_passes(mcp):
    st = doc(mcp.call('selftest'))
    assert st['ok'] and st['failed'] == 0 and st['passed'] == len(st['items']) > 0
    assert all(i['pass'] and i['id'].startswith('S') for i in st['items'])


def test_a_hung_call_is_stopped_and_releases_the_adapter(mcp):
    t0 = time.time()
    res = mcp.call('live', {'seconds': 60, 'max_wall': 2})
    assert err_code(res) == 'TIMEOUT' and time.time() - t0 < 20
    sc = res['structuredContent']
    assert 'max_wall' in sc['error']['hint']
    assert sc['stopped_by'] == 'stop' and sc['count'] > 0            # stopped cleanly (stop request), not killed
    assert doc(mcp.call('status'))['ok']                             # the child left no lock behind


def test_max_wall_includes_waiting_for_the_previous_call(mcp):
    mcp.send({'jsonrpc': '2.0', 'id': 'long', 'method': 'tools/call',
              'params': {'name': 'live', 'arguments': {'seconds': 6}}})
    time.sleep(0.5)
    t0 = time.time()
    res = mcp.call('read', {'max_wall': 2})
    assert err_code(res) == 'BUSY' and time.time() - t0 < 4
    assert 'live' in res['structuredContent']['error']['message']
    assert 'max_wall' in res['structuredContent']['error']['hint']
    mcp.send({'jsonrpc': '2.0', 'id': 'queued', 'method': 'tools/call', 'params': {'name': 'read', 'arguments': {}}})
    time.sleep(0.5)
    mcp.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 'queued'}})
    assert doc(mcp.wait('long', 60)['result'])['stopped_by'] == 'duration'
    assert doc(mcp.call('read'))['ok']                               # waits for the live, within max_wall
    assert not mcp.replies.get('queued')                             # cancelled while waiting: no reply


def test_cancel_stops_the_running_command(mcp):
    mcp.send({'jsonrpc': '2.0', 'id': 'slow', 'method': 'tools/call',
              'params': {'name': 'live', 'arguments': {'seconds': 60}}})
    time.sleep(2.0)
    mcp.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 'slow'}})
    t0 = time.time()
    assert doc(mcp.call('status', timeout=30))['ok'] and time.time() - t0 < 20
    time.sleep(0.5)
    assert not mcp.replies.get('slow')                               # the cancelled call gets no reply


def test_calls_run_one_at_a_time(mcp):
    for i, name in enumerate(('live', 'read', 'status')):
        mcp.send({'jsonrpc': '2.0', 'id': 'c%d' % i, 'method': 'tools/call',
                  'params': {'name': name, 'arguments': {'count': 40} if name == 'live' else {}}})
    for i in range(3):                                                # the adapter lock never answers BUSY here
        res = mcp.wait('c%d' % i, 120)['result']
        assert not res['isError'], res['content'][0]['text']


# ------------------------------------------------------------------------------------------------ background log
def test_background_log(mcp, tmp_path, logs):
    out = tmp_path / 'log.csv'
    st = doc(mcp.call('log_start', {'duration': '5s', 'out': str(out)}))
    t_start = time.time()
    assert st['state'] == 'running' and st['job'] and os.path.samefile(st['file'], out) and st['format'] == 'csv'
    assert st['duration_s'] == 5.0 and st['records'] > 0
    meta = os.path.join(str(tmp_path), 'state', 'mcp_jobs', st['job'])
    assert os.path.exists(os.path.join(meta, 'meta.json')) and os.path.exists(os.path.join(meta, 'stdout.jsonl'))
    busy = mcp.call('log_start', {'duration': '5s'})
    assert err_code(busy) == 'BUSY' and 'log_stop' in busy['structuredContent']['error']['hint']
    for name in ('read', 'status', 'press'):
        res = mcp.call(name, {'keys': ['HOLD']} if name == 'press' else {})
        assert err_code(res) == 'BUSY', name
    s = doc(mcp.call('log_status'))
    assert s['state'] == 'running' and s['running'] and s['job'] == st['job'] and s['records'] > 0
    assert os.path.samefile(s['file'], out)
    stop = doc(mcp.call('log_stop', {'job': st['job']}))
    # 'stop' unless a slow machine took the 5 s first (test_log_survives_... checks the stop on a 60 s log)
    assert stop['state'] == 'done' and stop['records'] > 0
    assert stop['stopped_by'] == 'stop' or (stop['stopped_by'] == 'duration' and time.time() - t_start > 4)
    assert stop['job'] == st['job'] and stop['gaps'] == 0 and os.path.samefile(stop['file'], out)
    for unit, v in stop['stats'].items():
        assert v['min'] <= v['mean'] <= v['max'] and v['n'] == stop['records']
    assert len(csv_rows(out)) == stop['records']
    assert 'already_ended' not in stop
    s = doc(mcp.call('log_status', {'job': st['job']}))
    assert s['state'] == 'done' and not s['running'] and s['summary']['records'] == stop['records']
    assert s['elapsed_s'] == round(stop['duration_s'], 1)            # its run time, frozen once it has ended
    again = doc(mcp.call('log_stop'))                                # already done: the same summary, marked
    assert again['records'] == stop['records'] and again['job'] == st['job']
    assert again['already_ended'] is True and again['stop_sent'] is True and 'already ended' in again['note']
    assert os.path.exists(os.path.join(str(tmp_path), 'state', 'stop.request'))
    again = doc(mcp.call('log_stop', {'job': st['job']}))            # a named job that has ended: nothing sent
    assert again['already_ended'] is True and again['stop_sent'] is False
    assert doc(mcp.call('read'))['ok']                               # the adapter is free again


def test_log_runs_to_its_duration(mcp, tmp_path, logs):
    st = doc(mcp.call('log_start', {'duration': '1s', 'every': '0.1s', 'format': 'jsonl',
                                    'out': str(tmp_path / 'short.jsonl')}))
    t_end = time.time() + 30
    while time.time() < t_end:
        s = doc(mcp.call('log_status'))
        if s['state'] != 'running':
            break
        time.sleep(0.3)
    assert s['state'] == 'done' and s['summary']['stopped_by'] == 'duration', s
    n = s['summary']['records']
    assert 5 <= n <= 15                                               # ~1 per 0.1 s
    with open(st['file'], encoding='utf-8') as f:
        assert sum(json.loads(l).get('type') == 'ble' for l in f) == n == s['records']
    bad = mcp.call('log_start', {'duration': 'soon'})                 # the child refuses it at once
    assert err_code(bad) == 'INVALID_ARG' and bad['structuredContent']['state'] == 'failed'
    assert doc(mcp.call('read'))['ok']


def test_log_survives_the_client_and_a_new_server_can_stop_it(tmp_path, logs):
    a = Client(tmp_path, 'a')
    a.initialize()
    st = doc(a.call('log_start', {'duration': '60s', 'out': str(tmp_path / 'long.csv')}))
    a.close()                                                         # the client goes away
    b = Client(tmp_path, 'b')
    try:
        b.initialize()
        s1 = doc(b.call('log_status'))
        assert s1['job'] == st['job'] and s1['state'] == 'running'    # known from state/mcp_jobs
        assert err_code(b.call('read')) == 'BUSY'
        time.sleep(1.0)
        s2 = doc(b.call('log_status', {'job': st['job']}))
        assert s2['running'] and s2['records'] > s1['records']        # still logging
        stop = doc(b.call('log_stop'))
        assert stop['state'] == 'done' and stop['stopped_by'] == 'stop' and stop['records'] >= s2['records']
        assert len(csv_rows(st['file'])) == stop['records']
        assert doc(b.call('status'))['ok']
    finally:
        b.close()


def test_a_log_of_another_server_is_seen_and_stopped(mcp, tmp_path, logs):
    assert err_code(mcp.call('log_status')) == 'INVALID_ARG'          # this server knows no log yet
    b = Client(tmp_path, 'b')                                         # e.g. a second Claude session
    try:
        b.initialize()
        st = doc(b.call('log_start', {'duration': '60s', 'out': str(tmp_path / 'other.csv')}))
        s = doc(mcp.call('log_status'))                               # its folder is picked up
        assert s['job'] == st['job'] and s['state'] == 'running'
        busy = mcp.call('read')
        assert err_code(busy) == 'BUSY' and st['job'] in busy['structuredContent']['error']['message']
        stop = doc(mcp.call('log_stop'))
        assert stop['job'] == st['job'] and stop['stopped_by'] == 'stop' and 'already_ended' not in stop
        r = mcp.call('read')                               # the adapter is free right after log_stop (no BUSY)
        assert not r.get('isError'), r['content'][0]['text']
        # b owns the process: it reports 'done' once the process has exited, a moment after the summary
        t_end = time.time() + 15
        while doc(b.call('log_status'))['state'] == 'running' and time.time() < t_end:
            time.sleep(0.2)
        assert doc(b.call('log_status'))['state'] == 'done'
    finally:
        b.close()


def _terminal_log(tmp_path, name):
    """`open289 log` started from a terminal: the server has no job for it."""
    out = open(tmp_path / ('%s.stdout.jsonl' % name), 'wb')
    p = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'open289', 'log', '--duration', '60s',
                          '--out', str(tmp_path / ('%s.csv' % name)), '--status-every', '1s'], cwd=ROOT,
                         env=dict(os.environ), stdout=out, stderr=subprocess.DEVNULL)
    t_end = time.time() + 30
    while time.time() < t_end and b'"started"' not in (tmp_path / ('%s.stdout.jsonl' % name)).read_bytes():
        time.sleep(0.1)
    return p, out


def test_log_stop_reaches_a_log_started_elsewhere(mcp, tmp_path, logs):
    old = doc(mcp.call('log_start', {'duration': '1s'}))             # a log of this server, which then ends
    t_end = time.time() + 30
    while doc(mcp.call('log_status'))['state'] == 'running' and time.time() < t_end:
        time.sleep(0.2)
    p, out = _terminal_log(tmp_path, 'terminal')
    try:
        busy = mcp.call('read')
        assert err_code(busy) == 'BUSY' and 'open289 stop' in busy['structuredContent']['error']['hint']
        res = doc(mcp.call('log_stop'))                               # the newest known log has ended ...
        assert res['job'] == old['job'] and res['already_ended'] is True and res['stop_sent'] is True
        assert p.wait(20) == 0                                        # ... and the terminal's log stopped too
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(10)
        out.close()
    lines = [json.loads(l) for l in (tmp_path / 'terminal.stdout.jsonl').read_bytes().splitlines() if l.strip()]
    assert lines[-1]['event'] == 'done' and lines[-1]['stopped_by'] == 'stop'
    assert doc(mcp.call('read'))['ok']


def test_log_stop_and_status_without_a_log(mcp, tmp_path, logs):
    assert err_code(mcp.call('log_status')) == 'INVALID_ARG'
    res = doc(mcp.call('log_stop'))
    assert res['ok'] and 'stop request' in res['note'] and res['stop_sent'] is True
    assert err_code(mcp.call('log_status', {'job': 'nope'})) == 'INVALID_ARG'
    p, out = _terminal_log(tmp_path, 'lone')                          # no job known: a terminal log is stopped
    try:
        assert err_code(mcp.call('status')) == 'BUSY'
        assert doc(mcp.call('log_stop'))['stop_sent'] is True
        assert p.wait(20) == 0
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(10)
        out.close()


def test_children_run_the_servers_own_package(tmp_path):
    """A client whose working directory holds another open289 (a different checkout, a stale copy) still gets this
    server's CLI, and server and children share one state folder."""
    fake = tmp_path / 'shadow' / 'open289'
    fake.mkdir(parents=True)
    (fake / '__init__.py').write_text("__version__ = '0'\n")
    (fake / '__main__.py').write_text("print('{\"ok\": true, \"cmd\": \"info\", \"which_copy\": \"shadow\"}')\n")
    (fake / 'cli.py').write_text("def main():\n    print('{\"ok\": true, \"which_copy\": \"shadow\"}')\n")
    boot = 'import sys; sys.path.insert(0, %r); from open289.mcp_server import main; main()' % ROOT
    c = Client(tmp_path, 'shadowed', argv=[sys.executable, '-X', 'utf8', '-c', boot], cwd=str(tmp_path / 'shadow'))
    try:
        c.initialize()
        st = doc(c.call('status'))
        assert 'which_copy' not in st and st['meter']['attached'] is True
        rel = doc(c.call('download_recording', {'index': 0, 'out': 'rel.csv', 'limit': 3}))
        assert os.path.samefile(rel['file'], tmp_path / 'shadow' / 'rel.csv')   # relative to the client's cwd
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ in-process
def test_constants_match_the_cli():
    from open289.adapter import SAFE_METER_CMD
    from open289.cli import PRESS_KEYS
    assert M.PRESS_KEYS == PRESS_KEYS
    samples = ['ID', 'IM', 'QM', 'qm', 'QDDB', 'QSLS', 'QMP clock', 'QRSI 0', 'QSRR 772,0', 'QEMAP primfunction',
               'Q', 'QABCDEFGHIJK', 'PRESS HOLD', 'CSD', 'MP clock,0', 'RI', 'OFF', 'ID ', 'ID\n', 'QM;CSD',
               'QM\rCSD', 'Qı', 'QM µ', '', ' ID', 'QM 1 2']
    for s in samples:
        b = s.encode('utf-8')
        cli_ok = bool(SAFE_METER_CMD.match(b.rstrip(b'\r\n'))) and b == b.rstrip(b'\r\n')
        assert bool(M.SAFE_QUERY.fullmatch(s)) == cli_ok, s


def test_durations_and_stop_requests_match_the_cli():
    from open289.cli import parse_duration
    from open289.errors import Open289Error
    from open289.logger import stop_request_path
    for s in ('0', '0s', '0.0', '.5', '90', '90s', '5m', '1.5h', '2d', '250ms', '3 min', ' 10M ', 'forever', 'INF',
              'until-stop', '-5s', '5x', '', 'soon', '1e-05s', '1.2.3'):
        try:
            want = parse_duration(s)
        except Open289Error:
            want = 'refused'
        try:
            got = M.duration_s(s)
        except ValueError:
            got = 'refused'
        assert got == want, s
    for x, text in ((0.1, '0.1s'), (60, '60s'), (0.1234, '0.123s'), (1e-5, '0s'), (119.5, '119.5s')):
        assert M.seconds_text(x) == text and (x < 0.001 or parse_duration(text) == round(x, 3))
    assert M.stop_request_path() == stop_request_path()


def test_children_share_the_servers_open289_folder(monkeypatch, tmp_path):
    from open289 import paths
    monkeypatch.delenv('OPEN289_HOME')
    assert M.child_env()['OPEN289_HOME'] == paths.home() == ROOT     # a checkout: its own folder
    monkeypatch.setenv('OPEN289_HOME', 'relative-home')
    assert M.child_env()['OPEN289_HOME'] == os.path.abspath('relative-home')
    assert M.child_env()['OPEN289_SIM'] == '1'                      # the rest passes through
    assert M.BOOT.startswith('import sys') and repr(paths.PKG_PARENT) in M.BOOT


def test_live_summary_of_a_failed_run():
    docs = [{'type': 'reading', 't': 100.0 + i * 0.25, 'value': v, 'unit': 'V DC',
             'display': '%s V DC' % ('OL' if v is None else v), 'state': 'NORMAL', 'function': 'VDC'}
            for i, v in enumerate((1.0, 2.0, None, 3.0))]
    s = M.summarize_live(docs + [{'ok': False, 'cmd': 'live', 'error': {'code': 'LINK_LOST', 'message': 'x'}}])
    assert s['count'] == 4 and s['stats'] == {'V DC': {'n': 3, 'min': 1.0, 'max': 3.0, 'mean': 2.0}}
    assert s['rate_hz'] == 4.0 and len(s['last']) == 4
