from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal as D
import json
import multiprocessing
import sqlite3
import threading

import pytest
from control_plane.engine import Engine, FakeBroker, Policy
from control_plane.diagnostics import snapshot
from test_engine import NOW, Q, I


@pytest.fixture
def rig(tmp_path):
    broker = FakeBroker(tmp_path/'broker.sqlite')
    engine = Engine(tmp_path/'engine.sqlite',broker)
    engine.reconcile(NOW)
    yield tmp_path,broker,engine
    engine.close()
    broker.close()


def entry(engine, intent=I):
    decision, command = engine.enqueue(intent,Q,NOW)
    assert decision.accepted, decision.reason_code
    engine.dispatch(command.command_id,Q,NOW)
    return command.command_id


def position(engine):
    return json.loads(engine.db.execute('SELECT data FROM positions').fetchone()[0])


def test_full_lifecycle_restart(rig):
    path,b,e = rig
    cid = entry(e,replace(I,order_type='LIMIT',entry_price=D('100')))
    assert snapshot(path/'engine.sqlite')['pending_orders'][0]['quantity']=='0.5'
    b.tick(replace(Q,bid=D('99.8'),ask=D('100')),NOW)
    e.reconcile(NOW)
    pid = position(e)['id']
    assert e.manage('modify','MODIFY',pid,NOW,stop=D('99.5'),take_profit=D('103'))[1]=='APPLIED'
    assert e.manage('partial','CLOSE',pid,NOW,quantity=D('0.2'))[1]=='APPLIED'
    assert position(e)['quantity']=='0.3'
    restarted_b = FakeBroker(path/'broker.sqlite')
    restarted = Engine(path/'engine.sqlite',restarted_b)
    assert not restarted.ready
    restarted.reconcile(NOW)
    calls = restarted_b.calls
    assert restarted.manage('partial','CLOSE',pid,NOW,quantity=D('0.2'))[1]=='APPLIED'
    assert restarted_b.calls == calls
    restarted.manage('full','CLOSE',pid,NOW)
    assert position(restarted)['status']=='CLOSED'
    assert tuple(map(D,restarted.db.execute('SELECT quantity,risk FROM commands WHERE id=?',(cid,)).fetchone())) == (D(0),D(0))
    assert restarted.db.execute('SELECT COUNT(*) FROM fills').fetchone()[0]==3
    restarted.close()
    restarted_b.close()


def test_cancel_pending_and_queued(rig):
    _,b,e = rig
    cid = entry(e,replace(I,order_type='LIMIT',entry_price=D('100')))
    result = e.manage('cancel','CANCEL',cid,NOW)
    calls = b.calls
    assert e.manage('cancel','CANCEL',cid,NOW)==result
    assert b.calls==calls
    b.tick(replace(Q,bid=D('99.8'),ask=D('100')),NOW)
    e.reconcile(NOW)
    assert not e.db.execute('SELECT 1 FROM positions').fetchone()
    _,cmd = e.enqueue(replace(I,setup_id='second'),Q,NOW)
    e.manage('cancel-queue','CANCEL',cmd.command_id,NOW)
    e.dispatch(cmd.command_id,Q,NOW)
    assert b.calls==calls


@pytest.mark.parametrize('mode,expected',[('timeout_before','UNKNOWN'),('timeout_after','APPLIED')])
def test_action_lost_ack(rig,mode,expected):
    path,b,e=rig
    entry(e)
    pid=position(e)['id']
    b.mode=mode
    cid,result=e.manage('close','CLOSE',pid,NOW)
    assert result=='UNKNOWN'
    assert not e.ready
    assert e.enqueue(replace(I,setup_id='blocked'),Q,NOW)[0].reason_code=='RECONCILIATION_REQUIRED'
    b.mode='normal'
    e.reconcile(NOW)
    assert e.db.execute('SELECT state FROM actions WHERE id=?',(cid,)).fetchone()[0]==expected
    calls=b.calls
    e.manage('close','CLOSE',pid,NOW)
    assert b.calls==calls


class Crash(BaseException):
    pass


@pytest.mark.parametrize('stage', ['entry_claim','entry_broker','action_claim','action_broker'])
def test_crash_boundaries(rig,stage):
    path,b,e=rig
    if stage.startswith('action'):
        entry(e)
        pid=position(e)['id']
    def crash(at):
        if at==stage:
            raise Crash()
    e.checkpoint=crash
    with pytest.raises(Crash):
        if stage.startswith('entry'):
            entry(e)
        else:
            e.manage('crash','CLOSE',pid,NOW)
    other=Engine(path/'engine.sqlite',b)
    other.reconcile(NOW)
    assert other.ready == stage.endswith('broker')
    calls=b.calls
    if stage.startswith('action'):
        other.manage('crash','CLOSE',pid,NOW)
    else:
        cid=other.db.execute('SELECT id FROM commands').fetchone()[0]
        other.dispatch(cid,Q,NOW)
    assert b.calls==calls
    other.close()


def test_cancel_fill_race(rig):
    _,b,e=rig
    for n in range(12):
        cid=entry(e,replace(I,setup_id=str(n),quantity=D('0.01'),order_type='LIMIT',entry_price=D('100')))
        barrier=threading.Barrier(2)
        def cancel():
            barrier.wait()
            return b.execute('race-'+cid,'CANCEL',cid,{},NOW)
        def fill():
            barrier.wait()
            b.tick(replace(Q,bid=D('99.8'),ask=D('100')),NOW)
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(cancel)
            f=pool.submit(fill)
            result=a.result()
            f.result()
        book=b.lifecycle_snapshot()
        order=book['orders'][cid]
        assert (order['status']=='CANCELLED') == (result=='APPLIED')
        assert ('position-'+cid in book['positions']) == (result=='TOO_LATE')
        # The injected external receipt deliberately blocks reconciliation.
        with b.transaction():
            del b.book['receipts']['race-'+cid]
        e.reconcile(NOW)


def test_risk_pending_margin_and_named_strategy(rig):
    _,b,e=rig
    e.policy=Policy(max_total_risk=D('0.9'))
    entry(e,replace(I,order_type='LIMIT',entry_price=D('100')))
    assert e.enqueue(replace(I,setup_id='2'),Q,NOW)[0].reason_code=='TOTAL_RISK'
    e.policy=Policy(available_margin=D('5'))
    assert e.enqueue(replace(I,setup_id='3'),Q,NOW)[0].reason_code=='MARGIN'
    e.policy=Policy(strategy_limits=(('hurricane',D('2'),D('0.6')),))
    assert e.enqueue(replace(I,setup_id='4',quantity=D('0.1')),Q,NOW)[0].reason_code=='STRATEGY_RISK'


def test_campaign_linked_entries_invalidation_and_restart(rig):
    path,b,e=rig
    e.create_campaign('campaign','hurricane','SIM-XAU',D('99.5'),'BELOW',NOW)
    for leg in ('1','2'):
        entry(e,replace(I,leg=leg,quantity=D('0.1'),campaign_id='campaign',order_type='LIMIT',entry_price=D('100')))
    _,queued=e.enqueue(replace(I,leg='3',quantity=D('0.1'),campaign_id='campaign'),Q,NOW)
    e.evaluate_campaigns(replace(Q,bid=D('99.4'),ask=D('99.6')),NOW)
    other=Engine(path/'engine.sqlite',b)
    other.reconcile(NOW)
    other.evaluate_campaigns(Q,NOW)
    assert not snapshot(path/'engine.sqlite')['pending_orders']
    assert other.enqueue(replace(I,leg='4',campaign_id='campaign'),Q,NOW)[0].reason_code=='CAMPAIGN_INACTIVE'
    calls=b.calls
    other.dispatch(queued.command_id,Q,NOW)
    assert b.calls==calls
    other.close()


def test_disconnect_missing_and_foreign_state(rig):
    _,b,e=rig
    entry(e)
    b.mode='disconnected'
    with pytest.raises(ConnectionError):
        e.reconcile(NOW)
    assert not e.ready
    b.mode='normal'
    e.reconcile(NOW)
    assert e.ready
    with b.transaction():
        b.book['orders'].clear()
        b.book['positions'].clear()
    e.reconcile(NOW)
    assert not e.ready


def contender(path, broker_path, queue):
    b=FakeBroker(broker_path)
    e=Engine(path,b)
    try:
        e.reconcile(NOW)
        queue.put('unsafe')
    except RuntimeError as exc:
        queue.put(str(exc))
    finally:
        e.close()
        b.close()


def test_process_ownership(rig):
    path,b,e=rig
    context=multiprocessing.get_context('spawn')
    queue=context.Queue()
    with e.owner():
        process=context.Process(target=contender,args=(path/'engine.sqlite',path/'broker.sqlite',queue))
        process.start()
        assert queue.get(timeout=10)=='EXECUTION_OWNER_BUSY'
        process.join(10)
        assert process.exitcode==0
    e.reconcile(NOW)
    assert e.ready


def test_lock_during_result_persistence(rig):
    path,b,e=rig
    lock=sqlite3.connect(path/'engine.sqlite')
    e.db.execute('PRAGMA busy_timeout=1')
    def checkpoint(stage):
        if stage=='entry_broker':
            lock.execute('BEGIN IMMEDIATE')
    e.checkpoint=checkpoint
    _,cmd=e.enqueue(I,Q,NOW)
    with pytest.raises(sqlite3.OperationalError):
        e.dispatch(cmd.command_id,Q,NOW)
    lock.rollback()
    lock.close()
    e.reconcile(NOW)
    assert e.ready
    e.dispatch(cmd.command_id,Q,NOW)
    assert b.calls==1


def test_native_synthetic_protection(rig):
    _,b,e=rig
    entry(e,replace(I,take_profit=D('101')))
    b.tick(replace(Q,bid=D('101'),ask=D('101.2')),NOW)
    e.reconcile(NOW)
    assert position(e)['status']=='CLOSED'


def test_diagnostics_excludes_source_data(rig):
    path,b,e=rig
    entry(e,replace(I,source_event_id='PRIVATE',setup_id='PRIVATE',intent_id='PRIVATE'))
    e.enqueue(replace(I,stop_loss=None),Q,NOW)
    data=snapshot(path/'engine.sqlite')
    assert data['positions']
    assert data['recent_rejections'][0]['reason_code']=='PROTECTIVE_STOP'
    assert 'PRIVATE' not in json.dumps(data)
    assert 'SIM-DEMO' not in json.dumps(data)


def test_missing_fill_evidence_preserves_reservation(rig):
    _,b,e=rig
    entry(e)
    with b.transaction():
        b.book['positions'].clear()
        b.book['fills'].clear()
    e.reconcile(NOW)
    assert not e.ready
    assert D(e.db.execute('SELECT quantity FROM commands').fetchone()[0])==I.quantity
    assert e.db.execute('SELECT COUNT(*) FROM fills').fetchone()[0]==1


def dispatch_contender(path,broker_path,cid,queue):
    b=FakeBroker(broker_path)
    e=Engine(path,b)
    try:
        e.reconcile(NOW)
        e.dispatch(cid,Q,NOW)
        queue.put(b.calls)
    except RuntimeError:
        queue.put(0)
    finally:
        e.close()
        b.close()


def test_duplicate_dispatch_across_processes(rig):
    path,b,e=rig
    _,command=e.enqueue(I,Q,NOW)
    ctx=multiprocessing.get_context('spawn')
    queue=ctx.Queue()
    workers=[ctx.Process(target=dispatch_contender,args=(path/'engine.sqlite',path/'broker.sqlite',command.command_id,queue)) for _ in range(2)]
    for worker in workers:
        worker.start()
    assert sum(queue.get(timeout=10) for _ in workers)==1
    for worker in workers:
        worker.join(10)
        assert worker.exitcode==0
    e.reconcile(NOW)
    assert len(b.lifecycle_snapshot()['positions'])==1


def killed_worker(path,broker_path,cid,stage):
    import os
    b=FakeBroker(broker_path)
    e=Engine(path,b)
    e.reconcile(NOW)
    def die(at):
        if at==stage:
            os._exit(71)
    e.checkpoint=die
    e.dispatch(cid,Q,NOW)


@pytest.mark.parametrize('stage,ready',[('entry_claim',False),('entry_broker',True)])
def test_actual_process_death_releases_owner_without_resend(rig,stage,ready):
    path,b,e=rig
    _,command=e.enqueue(I,Q,NOW)
    ctx=multiprocessing.get_context('spawn')
    worker=ctx.Process(target=killed_worker,args=(path/'engine.sqlite',path/'broker.sqlite',command.command_id,stage))
    worker.start()
    worker.join(10)
    assert worker.exitcode==71
    e.reconcile(NOW)
    assert e.ready==ready
    e.dispatch(command.command_id,Q,NOW)
    assert b.calls==0
    assert len(b.lifecycle_snapshot()['positions'])==int(ready)


def test_invalid_stop_modification_and_policy(rig):
    _,b,e=rig
    entry(e)
    pid=position(e)['id']
    calls=b.calls
    with pytest.raises(ValueError,match='RISK_INCREASING_STOP'):
        e.manage('widen','MODIFY',pid,NOW,stop=D('98'))
    assert b.calls==calls
    with pytest.raises(ValueError,match='INVALID_POLICY'):
        Policy(quantity_step=D('NaN'))


def test_modify_pending_protection(rig):
    _,b,e=rig
    cid=entry(e,replace(I,order_type='LIMIT',entry_price=D('100')))
    e.manage('pending-modify','MODIFY',cid,NOW,stop=D('99.5'),take_profit=D('103'))
    assert D(e.db.execute('SELECT risk FROM commands WHERE id=?',(cid,)).fetchone()[0])==D('0.25')
    b.tick(replace(Q,bid=D('99.8'),ask=D('100')),NOW)
    e.reconcile(NOW)
    assert position(e)['stop']=='99.5'
    assert position(e)['take_profit']=='103'


def test_engine_cancel_after_fill_race(rig):
    _,b,e=rig
    cid=entry(e,replace(I,order_type='LIMIT',entry_price=D('100')))
    def checkpoint(stage):
        if stage=='action_claim':
            with ThreadPoolExecutor(1) as pool:
                pool.submit(b.tick,replace(Q,bid=D('99.8'),ask=D('100')),NOW).result()
    e.checkpoint=checkpoint
    assert e.manage('race','CANCEL',cid,NOW)[1]=='TOO_LATE'
    assert position(e)['status']=='OPEN'
    assert D(e.db.execute('SELECT quantity FROM commands WHERE id=?',(cid,)).fetchone()[0])==I.quantity


def test_http_read_only_routes(rig):
    from io import BytesIO
    from control_plane.diagnostics import handler
    path,_,e=rig
    entry(e)
    class Connection:
        def __init__(self,request):
            self.request=BytesIO(request)
            self.response=b''
        def makefile(self,*args):
            return self.request
        def sendall(self,data):
            self.response+=data
    for request,code in [(b'GET /diagnostics HTTP/1.0\r\n\r\n',b'200'),
                         (b'POST /diagnostics HTTP/1.0\r\n\r\n',b'501'),
                         (b'GET /secret HTTP/1.0\r\n\r\n',b'404')]:
        connection=Connection(request)
        handler(path/'engine.sqlite')(connection,('127.0.0.1',0),None)
        assert code in connection.response.split(b'\r\n')[0]


def test_phase0_queued_payload_upgrade(rig):
    path,b,e=rig
    _,command=e.enqueue(I,Q,NOW)
    data=json.loads(e.db.execute('SELECT payload FROM commands').fetchone()[0])
    del data['take_profit']
    del data['campaign_id']
    e.db.execute('UPDATE commands SET payload=?',(json.dumps(data),))
    reopened=Engine(path/'engine.sqlite',b)
    reopened.reconcile(NOW)
    reopened.dispatch(command.command_id,Q,NOW)
    assert b.calls==1
    assert position(reopened)['status']=='OPEN'
    reopened.close()
