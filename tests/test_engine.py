from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import sqlite3

import pytest
from control_plane.contracts import Quote, TradeIntent
from control_plane.engine import Engine, FakeBroker, Policy

NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
Q = Quote(D('100'), D('100.2'), NOW)
I = TradeIntent('i','event','hurricane','setup','1','SIM-DEMO','SIM-XAU','BUY',
                D('0.5'),D('100.2'),D('99'),NOW,NOW+timedelta(seconds=50))


@pytest.fixture
def rig(tmp_path):
    path = tmp_path/'state.sqlite'
    broker = FakeBroker()
    engine = Engine(path, broker)
    engine.reconcile(NOW)
    yield path, broker, engine
    engine.close()


def state(e, c):
    return e.db.execute('SELECT state FROM commands WHERE id=?',(c.command_id,)).fetchone()[0]


def test_duplicate_and_restart(rig):
    path,b,e = rig
    _,c = e.enqueue(I,Q,NOW)
    e.dispatch(c.command_id,Q,NOW)
    other = Engine(path,b)
    other.reconcile(NOW)
    assert other.enqueue(replace(I,intent_id='replay'),Q,NOW)[0].reason_code == 'DUPLICATE'
    other.dispatch(c.command_id,Q,NOW)
    assert b.calls == 1
    other.close()


@pytest.mark.parametrize('mode,expected', [('timeout_after','FILLED'),('timeout_before','UNKNOWN')])
def test_lost_ack_and_unknown(rig, mode, expected):
    path,b,e = rig
    b.mode = mode
    _,c = e.enqueue(I,Q,NOW)
    e.dispatch(c.command_id,Q,NOW)
    assert state(e,c) == 'UNKNOWN'
    e.reconcile(NOW)
    assert state(e,c) == expected
    e.dispatch(c.command_id,Q,NOW)
    assert b.calls == 1
    assert e.ready == (expected == 'FILLED')


@pytest.mark.parametrize('change,quote,reason', [
    ({},replace(Q,at=NOW-timedelta(seconds=6)),'STALE_QUOTE'),
    ({},replace(Q,at=NOW+timedelta(seconds=1)),'STALE_QUOTE'),
    ({},replace(Q,ask=D('102')),'SPREAD'),
    ({'account_scope':'LIVE'},Q,'ACCOUNT_ENVIRONMENT_MISMATCH'),
    ({'stop_loss':None},Q,'PROTECTIVE_STOP'),
    ({'stop_loss':D('101')},Q,'PROTECTIVE_STOP'),
    ({'quantity':D('NaN')},Q,'INVALID_NUMERIC'),
    ({'quantity':D('0.001')},Q,'INVALID_CONTRACT'),
    ({'entry_price':D('104')},Q,'SLIPPAGE'),
    ({'created_at':NOW-timedelta(seconds=61)},Q,'STALE_SIGNAL'),
    ({'valid_until':NOW},Q,'STALE_SIGNAL'),
    ({'stop_loss':D('1')},Q,'ORDER_RISK'),
    ({'quantity':D('1.1')},Q,'STRATEGY_EXPOSURE'),
])
def test_gates(rig,change,quote,reason):
    _,b,e = rig
    assert e.enqueue(replace(I,**change),quote,NOW)[0].reason_code == reason
    assert b.calls == 0


def test_adapter_hard_lock(rig):
    _,b,e = rig
    _,c = e.enqueue(I,Q,NOW)
    b.environment = 'LIVE'
    e.dispatch(c.command_id,Q,NOW)
    assert state(e,c) == 'REJECTED'
    with pytest.raises(ValueError):
        b.submit('bypass',I,NOW)
    assert b.calls == 0


def test_dispatch_rechecks_quote(rig):
    _,b,e = rig
    _,c = e.enqueue(I,Q,NOW)
    e.dispatch(c.command_id,Q,NOW+timedelta(seconds=6))
    assert state(e,c) == 'REJECTED'
    assert b.calls == 0


def test_startup_and_crash_fence(rig):
    path,b,e = rig
    _,c = e.enqueue(I,Q,NOW)
    e.db.execute("UPDATE commands SET state='SUBMITTING'")
    other = Engine(path,b)
    assert other.enqueue(replace(I,setup_id='new'),Q,NOW)[0].reason_code == 'RECONCILIATION_REQUIRED'
    other.reconcile(NOW)
    assert state(other,c) == 'UNKNOWN'
    other.dispatch(c.command_id,Q,NOW)
    assert b.calls == 0
    other.close()


def test_database_lock_fails_before_submission(rig):
    path,b,e = rig
    _,c = e.enqueue(I,Q,NOW)
    lock = sqlite3.connect(path)
    lock.execute('BEGIN IMMEDIATE')
    e.db.execute('PRAGMA busy_timeout=1')
    with pytest.raises(sqlite3.OperationalError):
        e.dispatch(c.command_id,Q,NOW)
    lock.rollback()
    lock.close()
    assert b.calls == 0
    restarted = Engine(path,b)
    restarted.reconcile(NOW)
    restarted.dispatch(c.command_id,Q,NOW)
    assert b.calls == 1
    restarted.close()


def test_simultaneous_strategies(rig):
    path,b,e = rig
    def reserve(n):
        local = Engine(path,b,Policy(max_quantity=D('0.5')))
        local.reconcile(NOW)
        result = local.enqueue(replace(I,strategy_id=str(n),setup_id=str(n)),Q,NOW)[0]
        local.close()
        return result.reason_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve,range(2)))
    assert sorted(results) == ['ACCEPTED','ACCOUNT_EXPOSURE']


def test_opposite_strategies(rig):
    _,_,e = rig
    e.enqueue(I,Q,NOW)
    decision,_ = e.enqueue(replace(I,setup_id='other',strategy_id='t02',side='SELL',
                                   entry_price=D('100'),stop_loss=D('101')),Q,NOW)
    assert decision.reason_code == 'STRATEGY_CONFLICT'


def test_unowned_broker_evidence_blocks(rig):
    _,b,e = rig
    b.submit('external',I,NOW)
    e.reconcile(NOW)
    assert not e.ready


def test_total_risk(rig):
    _,_,e = rig
    e.policy = Policy(max_total_risk=D('0.7'))
    e.enqueue(I,Q,NOW)
    assert e.enqueue(replace(I,setup_id='2',strategy_id='t02'),Q,NOW)[0].reason_code == 'TOTAL_RISK'
