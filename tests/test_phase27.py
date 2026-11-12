from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
from io import BytesIO
import json
import sqlite3
from pathlib import Path

import pytest

from audit_phase27.validate import validate,trade_metrics,SCENARIOS
from signal_adapters.hurricane import HurricaneMessage
from signal_adapters.t02 import Candle
from observation.worker import Worker,ReplayEvent
from observation.status import snapshot
from control_plane.diagnostics import handler
from test_phase25 import synthetic_minutes
from test_ctrader import private_dir

UTC=timezone.utc
WALL=datetime(2026,11,12,12,tzinfo=UTC)


def candles():
    return [Candle(r['time'],*(D(str(r[k])) for k in ('open','high','low','close'))) for r in synthetic_minutes()]


@pytest.fixture
def clock():return [WALL,1000.]


def worker(path,clock):return Worker(path,'synthetic-fixture',wall=lambda:clock[0],mono=lambda:clock[1])


def feed(w,values,clock,start=0):
    for index,c in enumerate(values,start):assert w.process(ReplayEvent(index,c,clock[0]))=='PROCESSED'


def test_atomic_restart_duplicate_and_multistrategy_paper_state(tmp_path,clock):
    path=tmp_path/'worker.sqlite';w=worker(path,clock);bars=candles()
    feed(w,bars[:60],clock)
    assert w.db.execute("SELECT count(*) FROM worker_decisions WHERE reason='OBSERVATION_ACCEPTED'").fetchone()[0]==2
    assert w.db.execute('SELECT count(*) FROM paper_orders').fetchone()[0]==2
    w.stop();w.close()
    w=worker(path,clock)
    try:
        assert w.process(ReplayEvent(59,bars[59],clock[0]))=='DUPLICATE_EVENT'
        assert w.db.execute("SELECT count(*) FROM worker_decisions WHERE reason='OBSERVATION_ACCEPTED'").fetchone()[0]==2
        feed(w,bars[60:61],clock,start=60)
        states=[json.loads(r[0]) for r in w.db.execute('SELECT data FROM paper_positions')]
        assert len(states)==2 and all(p['status']=='OPEN' for p in states)
        result=snapshot(path,clock[0]);assert result['reconciliation']=='CONSISTENT_LOCAL_PAPER_BOOK'
        assert result['real_execution_enabled'] is False
    finally:w.close()


def test_crash_before_commit_rolls_back_adapter_claims_and_cursor(tmp_path,clock):
    path=tmp_path/'worker.sqlite';bars=candles();w=worker(path,clock)
    feed(w,bars[:59],clock)
    class Crash(BaseException):pass
    def fail(stage):raise Crash()
    w.checkpoint=fail
    with pytest.raises(Crash):w.process(ReplayEvent(59,bars[59],clock[0]))
    assert w.meta['cursor']==58
    assert w.db.execute('SELECT count(*) FROM adapter_decisions').fetchone()[0]==0
    w.close();w=worker(path,clock)
    try:
        feed(w,bars[59:60],clock,start=59)
        assert w.db.execute('SELECT count(*) FROM paper_orders').fetchone()[0]==2
    finally:w.close()


def test_stale_input_stops_new_decisions_without_advancing_cursor(tmp_path,clock):
    path=tmp_path/'worker.sqlite';w=worker(path,clock)
    try:
        c=candles()[0]
        assert w.process(ReplayEvent(0,c,clock[0]-timedelta(seconds=6)))=='STALE_INPUT'
        assert w.meta['cursor']==-1
        assert w.db.execute('SELECT count(*) FROM worker_decisions').fetchone()[0]==0
        assert w.process(ReplayEvent(0,c,clock[0]))=='PROCESSED'
        clock[0]+=timedelta(seconds=6);w.heartbeat()
        assert not snapshot(path,clock[0])['fresh_input']
    finally:w.close()


def test_wall_and_market_clock_regressions_and_conflicting_duplicates(tmp_path,clock):
    w=worker(tmp_path/'w.sqlite',clock)
    try:
        bars=candles();feed(w,bars[:1],clock)
        assert w.process(ReplayEvent(0,replace(bars[0],close=bars[0].open),clock[0]))=='DUPLICATE_EVENT'
        assert w.process(ReplayEvent(0,replace(bars[0],high=bars[0].high+D(1)),clock[0]))=='EVENT_ID_CONFLICT'
        assert w.process(ReplayEvent(2,bars[2],clock[0]))=='OUT_OF_ORDER_EVENT'
        clock[0]-=timedelta(seconds=10)
        assert w.process(ReplayEvent(1,bars[1],clock[0]))=='CLOCK_REGRESSION'
        clock[0]+=timedelta(seconds=10)
        assert w.process(ReplayEvent(1,bars[0],clock[0]))=='MARKET_CLOCK_REGRESSION'
    finally:w.close()


def test_exclusive_owner_and_dataset_pin(tmp_path,clock):
    path=tmp_path/'worker.sqlite';w=worker(path,clock)
    with pytest.raises(RuntimeError,match='OWNER_BUSY'):worker(path,clock)
    w.close()
    with pytest.raises(ValueError,match='CONFIGURATION_CHANGED'):
        Worker(path,'different',wall=lambda:clock[0])
    w=worker(path,clock);w.close()


def test_hurricane_adapter_messages_and_edits_are_observations_only(tmp_path,clock):
    w=worker(tmp_path/'w.sqlite',clock);bars=candles()
    now=bars[0].time+timedelta(minutes=1)
    message=HurricaneMessage('synthetic-only',now,'utc',now,'BUY',D(110),D(112),D(100),(D(120),D(125),D(130)))
    try:
        assert w.process(ReplayEvent(0,bars[0],clock[0],(message,)))=='PROCESSED'
        assert w.db.execute('SELECT count(*) FROM paper_orders').fetchone()[0]==3
        # Limit proposals or unfilled market proposals can be tombstoned without broker I/O.
        assert w.process(ReplayEvent(1,bars[1],clock[0],(replace(message,event='EDIT'),)))=='PROCESSED'
        reasons=[r[0] for r in w.db.execute('SELECT reason FROM worker_decisions')]
        assert 'EDIT_NEVER_ENTERS' in reasons
        assert all(json.loads(r[0])[0]['observation_only'] for r in w.db.execute("SELECT intents FROM worker_decisions WHERE reason='OBSERVATION_ACCEPTED'"))
    finally:w.close()


def test_worker_database_lock_fails_before_cursor_commit(tmp_path,clock):
    path=tmp_path/'w.sqlite';w=worker(path,clock);lock=sqlite3.connect(path)
    try:
        lock.execute('BEGIN IMMEDIATE');w.db.execute('PRAGMA busy_timeout=1')
        with pytest.raises(sqlite3.OperationalError):w.process(ReplayEvent(0,candles()[0],clock[0]))
        assert w.meta['cursor']==-1
    finally:lock.rollback();lock.close();w.close()


class HTTPConnection:
    def __init__(self,request):self.request=BytesIO(request);self.response=b''
    def makefile(self,*args):return self.request
    def sendall(self,data):self.response+=data


def test_dashboard_worker_api_readonly_and_broker_view_separation(tmp_path,clock):
    path=tmp_path/'w.sqlite';w=worker(path,clock)
    feed(w,candles()[:1],clock)
    try:
        serve=handler(path,path,lambda:{'read_only':True,'environment':'DEMO','health':'BLOCKED_CONFIGURATION'})
        for route,code in [('/',b'200'),('/dashboard.js',b'200'),('/diagnostics',b'200'),('/nope',b'404')]:
            c=HTTPConnection(f'GET {route} HTTP/1.0\r\n\r\n'.encode());serve(c,('127.0.0.1',0),None)
            assert code in c.response.split(b'\r\n')[0]
            if route=='/':assert b'REAL ORDER EXECUTION DISABLED' in c.response
            if route=='/diagnostics':
                result=json.loads(c.response.split(b'\r\n\r\n',1)[1])
                assert result['worker']['mode']=='OFFLINE_REPLAY'
                assert result['broker_observer']['health']=='BLOCKED_CONFIGURATION'
        c=HTTPConnection(b'POST /diagnostics HTTP/1.0\r\n\r\n');serve(c,('127.0.0.1',0),None)
        assert b'501' in c.response.split(b'\r\n')[0]
    finally:w.close()


@pytest.fixture(scope='module')
def investigation():return validate()


def test_development_classification_and_no_unseen_claim(investigation):
    assert investigation['classification']=='development'
    assert not investigation['genuinely_unseen_validation']
    assert investigation['causality']['prefix_cases']==109
    assert not investigation['causality']['failures']


def test_exact_metrics_stress_and_breakdowns(investigation):
    baseline=investigation['scenarios']['zero_cost']['policies']
    ref=baseline['t02_reference'];long=baseline['t02_long_only']
    assert ref['metrics']['average_winner']==pytest.approx(110.74527476626358)
    assert ref['metrics']['average_loser']==pytest.approx(-112.72949478006146)
    assert long['metrics']['trade_count']==30
    assert ref['breakdown']['side']['SELL']['trade_count']==34
    for name,scenario in investigation['scenarios'].items():
        for value in scenario['policies'].values():
            for breakdown in value['breakdown'].values():
                assert sum(r['trade_count'] for r in breakdown.values())==value['metrics']['trade_count']
    assert investigation['scenarios']['severe']['policies']['t02_reference']['metrics']['net_pnl']<0
    assert investigation['scenarios']['severe']['policies']['t02_long_only']['metrics']['net_pnl']<0


def test_config_preflight_redacts_and_flags_account_selection(private_dir):
    from ctrader_readonly.config import private_write
    from ctrader_readonly.check_config import check
    path=private_dir/'configuration.json'
    private_write(path,{'client_id':'SYNTHETIC_CLIENT','client_secret':'SYNTHETIC_SECRET',
        'application_approved':True,'scope':'accounts','redirect_uri':'http://127.0.0.1:18768/oauth/callback',
        'expected_broker':'Example Broker','demo_account_id':None,'token_path':str(private_dir/'tokens.json')})
    result=check(path)
    assert result['blockers']==['DEMO_ACCOUNT_ALLOWLIST_REQUIRED']
    assert 'SYNTHETIC_SECRET' not in json.dumps(result)
