"""Offline cTrader schemas/coordinator tests. All identities and economics invented."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
import json
import sqlite3
import pytest

from ctrader_readonly.config import SafetyError
from ctrader_readonly.protocol import validate_frame,DemoWebSocket
from ctrader_execution.schema import SymbolSpec,RiskLimits,order_frame,frame,validate_mutation
from ctrader_execution.offline import OfflinePeer,OfflineAdapter,production_execution
from control_plane.native import NativeEngine,MarginEvidence
from control_plane.contracts import TradeIntent,Quote

NOW=datetime(2026,11,12,12,tzinfo=timezone.utc)
QUOTE=Quote(D('1999.9'),D('2000'),NOW)
SPEC=SymbolSpec(456,10000,100,500000,100,2,0,0,True)
LIMITS=RiskLimits(D(100),D(200),D(150),D(20),D(10),D(500),D(7),D('.5'),D('.5'),D('.50'))


def intent(key='one',**changes):
    data=dict(intent_id=key,source_event_id=key,strategy_id='t02_reference',setup_id=key,leg='1',
        account_scope='RAZOR-DEMO',instrument='XAUUSD',side='BUY',quantity=D(2),entry_price=D(2000),
        stop_loss=D(1990),take_profit=D(2010),created_at=NOW,valid_until=NOW+timedelta(seconds=60))
    data.update(changes);return TradeIntent(**data)


def margin(units=D(2),**changes):
    return replace(MarginEvidence(units,D(100),D(1000),NOW,True,123,SPEC.symbol_id),**changes)


@pytest.fixture
def rig(tmp_path):
    peer=OfflinePeer();adapter=OfflineAdapter(peer)
    engine=NativeEngine(tmp_path/'native.sqlite',adapter,SPEC,LIMITS)
    engine.reconcile(NOW);engine.control('t02_reference','RUNNING',NOW);engine.suspend(False,NOW)
    try:yield engine,peer,tmp_path/'native.sqlite'
    finally:engine.close()


def enter(engine,peer,**changes):
    i=intent(**changes);decision,ref=engine.enqueue(i,QUOTE,margin(i.quantity),NOW)
    assert decision.accepted,decision.reason_code
    engine.dispatch(ref.command_id,QUOTE,margin(i.quantity),NOW)
    return ref.command_id


def test_native_market_protection_and_lot_scaling(rig):
    e,p,_=rig;cid=enter(e,p)
    wire=p.last_frame['payload']
    assert wire['volume']==200 and wire['relativeStopLoss']==1000000
    assert 'stopLoss' not in wire and 'takeProfit' not in wire
    assert e.status()['commands'][0]['state']=='FILLED'
    assert e.status()['positions'][0]['stop']=='1990.0'
    assert SPEC.units_for_lots(D('.01'))==1
    assert SPEC.units_for_lots(D(50))==5000
    assert not e.status()['execution_enabled']


def test_limit_acceptance_amendment_and_cancel(rig):
    e,p,_=rig;cid=enter(e,p,order_type='LIMIT',entry_price=D(1998),stop_loss=D(1990),take_profit=D(2008))
    assert e.status()['commands'][0]['state']=='ACCEPTED'
    oid=next(iter(p.orders));before=D(e.status()['commands'][0]['risk'])
    amended=e.manage('amend','AMEND_PENDING',oid,QUOTE,NOW,entry=D(1997),margin=margin())
    assert amended.state=='APPLIED' and D(e.status()['commands'][0]['risk'])<before
    calls=p.calls
    assert e.manage('amend','AMEND_PENDING',oid,QUOTE,NOW,entry=D(1997),margin=margin()).command_id==amended.command_id
    assert p.calls==calls
    cancelled=e.manage('cancel','CANCEL',oid,QUOTE,NOW)
    assert cancelled.state=='APPLIED' and e.status()['commands'][0]['quantity']=='0'
    assert e.status()['commands'][0]['margin']=='0'


def test_partial_fill_pending_risk_cancel_and_partial_full_close(rig):
    e,p,_=rig;cid=enter(e,p,order_type='LIMIT',entry_price=D(1998),stop_loss=D(1990),take_profit=D(2008),quantity=D(4))
    oid=next(iter(p.orders));response=p.fill(oid,200)
    e.ingest(cid,response,NOW)
    assert e.status()['commands'][0]['state']=='PARTIAL'
    assert e.status()['commands'][0]['quantity']=='4'
    e.manage('cancel-remnant','CANCEL',oid,QUOTE,NOW)
    assert e.status()['commands'][0]['quantity']=='2'
    pid=next(iter(p.positions))
    e.manage('half','CLOSE',pid,QUOTE,NOW,units=D(1))
    assert e.status()['positions'][0]['quantity']=='1'
    assert e.status()['commands'][0]['margin']=='100'
    e.manage('all','CLOSE',pid,QUOTE,NOW)
    assert e.status()['positions'][0]['status']=='CLOSED'
    assert D(e.status()['commands'][0]['risk'])==0
    assert e.status()['commands'][0]['margin']=='0'
    assert len(e.status()['actions'])==3


def test_protection_amend_preserves_target_and_reduces_risk(rig):
    e,p,_=rig;enter(e,p)
    pid=next(iter(p.positions));quote=Quote(D(2005),D('2005.1'),NOW)
    e.manage('be','MODIFY_POSITION',pid,quote,NOW,stop=D(2000))
    pos=e.status()['positions'][0]
    assert D(pos['stop'])==2000 and D(pos['target'])==2010
    assert D(e.status()['commands'][0]['risk'])<2
    with pytest.raises(SafetyError):e.manage('loosen','MODIFY_POSITION',pid,quote,NOW,stop=D(1990))


@pytest.mark.parametrize('mode',['timeout_before','timeout_after'])
def test_unknown_submission_never_resends_and_recovery_uses_positive_evidence(rig,mode):
    e,p,_=rig;p.mode=mode;cid=enter(e,p)
    assert e.status()['commands'][0]['state']=='UNKNOWN'
    calls=p.calls;e.dispatch(cid,QUOTE,margin(),NOW);assert p.calls==calls
    p.mode='normal';e.reconcile(NOW)
    assert e.status()['commands'][0]['state']==('UNKNOWN' if mode=='timeout_before' else 'FILLED')
    assert e.ready==(mode=='timeout_after')


def test_duplicate_entries_and_events_are_idempotent(rig):
    e,p,_=rig;cid=enter(e,p)
    decision,ref=e.enqueue(intent(),QUOTE,margin(),NOW)
    assert not decision.accepted and ref.command_id==cid
    e.dispatch(cid,QUOTE,margin(),NOW);assert p.calls==1
    snapshot=e.status();e.reconcile(NOW);e.reconcile(NOW)
    assert len(e.status()['positions'])==len(snapshot['positions'])==1
    assert e.db.execute('SELECT count(*) FROM native_fills').fetchone()[0]==1


def test_broker_rejection_releases_reservation(rig):
    e,p,_=rig;p.mode='reject';enter(e,p)
    assert e.status()['commands'][0]['state']=='REJECTED'
    assert e.status()['commands'][0]['risk']=='0'


@pytest.mark.parametrize('stage',['native_claim','native_broker'])
def test_crash_between_entry_stages_is_fenced_on_restart(tmp_path,stage):
    p=OfflinePeer();path=tmp_path/'crash.sqlite';e=NativeEngine(path,OfflineAdapter(p),SPEC,LIMITS)
    e.reconcile(NOW);e.control('t02_reference','RUNNING',NOW);e.suspend(False,NOW)
    _,ref=e.enqueue(intent(),QUOTE,margin(),NOW)
    def crash(checkpoint):
        if checkpoint==stage:raise SystemExit()
    e.checkpoint=crash
    with pytest.raises(SystemExit):e.dispatch(ref.command_id,QUOTE,margin(),NOW)
    e.close();calls=p.calls
    e=NativeEngine(path,OfflineAdapter(p),SPEC,LIMITS)
    try:
        assert e.status()['commands'][0]['state']=='UNKNOWN'
        e.dispatch(ref.command_id,QUOTE,margin(),NOW);assert p.calls==calls
        e.reconcile(NOW)
        assert e.ready==(stage=='native_broker')
    finally:e.close()


def test_action_lost_ack_is_not_retried_without_correlated_receipt(rig):
    e,p,_=rig;enter(e,p);pid=next(iter(p.positions));p.mode='timeout_after'
    action=e.manage('close','CLOSE',pid,QUOTE,NOW)
    assert action.state=='UNKNOWN';calls=p.calls;p.mode='normal'
    e.reconcile(NOW)
    assert not e.ready  # Closed position does not prove this close request's attribution.
    assert e.manage('close','CLOSE',pid,QUOTE,NOW).state=='UNKNOWN'
    assert p.calls==calls


def test_missing_broker_protection_fails_closed_and_keeps_risk(rig):
    e,p,_=rig;p.protection_missing=True
    with pytest.raises(SafetyError,match='PROTECTION'):enter(e,p)
    assert not e.ready
    assert D(e.status()['commands'][0]['risk'])>0


def test_external_or_conflicting_state_blocks_reconciliation(rig):
    e,p,_=rig;enter(e,p);pid=next(iter(p.positions));p.positions[pid]['tradeData']['label']='unowned'
    with pytest.raises(SafetyError):e.reconcile(NOW)
    assert not e.ready


@pytest.mark.parametrize('change,reason',[
    ({'observation_only':True},'OBSERVATION_ONLY'),
    ({'account_scope':'LIVE'},'NATIVE_ACCOUNT_FENCE'),
    ({'quantity':D('.5')},'NATIVE_VOLUME_LIMIT'),
    ({'stop_loss':None},'INVALID_NATIVE_PRICE'),
    ({'created_at':NOW-timedelta(seconds=100)},'STALE_SIGNAL'),
])
def test_entry_guards(rig,change,reason):
    e,p,_=rig;i=intent(**change)
    decision,_=e.enqueue(i,QUOTE,margin(i.quantity),NOW)
    assert decision.reason_code==reason and not p.calls


def test_native_margin_is_explicit_authoritative_and_fresh(rig):
    e,p,_=rig
    for evidence in [margin(authoritative=False),margin(at=NOW-timedelta(seconds=10)),margin(units=D(1)),margin(available_usd=D(10))]:
        decision,_=e.enqueue(intent(),QUOTE,evidence,NOW)
        assert not decision.accepted
    assert not p.calls


def test_global_and_strategy_reservations_include_pending_orders(rig):
    e,p,_=rig
    for index in range(5):
        i=intent(str(index),order_type='LIMIT',entry_price=D(1998),stop_loss=D(1990),take_profit=D(2008))
        decision,ref=e.enqueue(i,QUOTE,margin(),NOW);assert decision.accepted
    decision,_=e.enqueue(intent('six'),QUOTE,margin(),NOW)
    assert decision.reason_code=='STRATEGY_EXPOSURE'
    assert p.calls==0


def test_owner_lock_is_account_scoped_across_databases(rig,tmp_path):
    e,p,_=rig
    with pytest.raises(SafetyError,match='OWNER_BUSY'):NativeEngine(tmp_path/'other.sqlite',OfflineAdapter(p),SPEC,LIMITS)


def test_db_lock_cannot_leak_an_external_side_effect(rig):
    e,p,path=rig;e.db.execute('PRAGMA busy_timeout=1')
    other=sqlite3.connect(path);other.execute('BEGIN IMMEDIATE')
    with pytest.raises(sqlite3.OperationalError):e.enqueue(intent(),QUOTE,margin(),NOW)
    other.rollback();other.close();assert p.calls==0


def test_read_only_transport_still_rejects_all_mutations_and_production_is_disabled():
    request=order_frame(123,SPEC,'synthetic',intent(),QUOTE,NOW)
    with pytest.raises(SafetyError):validate_frame(request)
    with pytest.raises(SafetyError):DemoWebSocket().send(request)
    with pytest.raises(SafetyError):production_execution(approved=True)
    with pytest.raises(SafetyError):OfflineAdapter(DemoWebSocket())


def test_protection_and_size_schema_rejects_unsafe_wire_shapes():
    request=order_frame(123,SPEC,'test',intent(),QUOTE,NOW)
    request['payload']['stopLoss']=1990.
    with pytest.raises(SafetyError):validate_mutation(request)
    with pytest.raises(SafetyError):SPEC.units_for_lots(D('50.01'))
    with pytest.raises(SafetyError):order_frame(123,replace(SPEC,independently_verified=False),'test',intent(),QUOTE,NOW)


def test_cancel_vs_fill_race_keeps_position_and_reports_too_late(rig):
    e,p,_=rig;enter(e,p,order_type='LIMIT',entry_price=D(1998),stop_loss=D(1990),take_profit=D(2008))
    oid=next(iter(p.orders));original=p.send
    def raced(request):
        if request['payloadType']==2108:return p.fill(oid,200,cid=request['clientMsgId'])
        return original(request)
    p.send=raced
    action=e.manage('raced-cancel','CANCEL',oid,QUOTE,NOW)
    assert action.state=='TOO_LATE'
    assert e.status()['positions'][0]['status']=='OPEN'
    assert e.status()['commands'][0]['quantity']=='2'


def test_sell_relative_stop_and_target_are_directionally_correct(rig):
    e,p,_=rig;enter(e,p,side='SELL',stop_loss=D(2010),take_profit=D(1990))
    pos=e.status()['positions'][0]
    assert D(pos['stop'])==2010 and D(pos['target'])==1990


def test_snapshot_and_margin_identity_cannot_be_reused_stale_or_cross_account(rig):
    e,p,_=rig
    for evidence in (margin(account_id=999),margin(symbol_id=999),margin(currency='GBP')):
        decision,_=e.enqueue(intent(),QUOTE,evidence,NOW)
        assert decision.reason_code=='NATIVE_MARGIN_IDENTITY'
    later=NOW+timedelta(seconds=6)
    decision,_=e.enqueue(intent(created_at=later,valid_until=later+timedelta(seconds=60)),replace(QUOTE,at=later),margin(at=later),later)
    assert decision.reason_code=='STALE_RECONCILIATION'


def test_new_external_exposure_before_dispatch_is_detected(rig):
    e,p,_=rig;_,ref=e.enqueue(intent(),QUOTE,margin(),NOW)
    p.positions[999]={'positionId':999,'tradeData':{'symbolId':456,'label':'foreign','volume':100,'tradeSide':1},'positionStatus':1,'price':2000.,'stopLoss':1990.,'takeProfit':2010.}
    with pytest.raises(SafetyError):e.dispatch(ref.command_id,QUOTE,margin(),NOW)
    assert p.calls==0 and not e.ready


def test_unknown_entry_absence_cannot_release_risk(rig):
    e,p,_=rig;p.mode='timeout_before';cid=enter(e,p);before=e.status()['commands'][0]['risk']
    p.history_complete=False
    with pytest.raises(SafetyError):e.reconcile(NOW)
    p.history_complete=True;e.reconcile(NOW)
    assert e.status()['commands'][0]['state']=='UNKNOWN' and e.status()['commands'][0]['risk']==before


def test_missing_exit_history_cannot_infer_close_from_absent_position(rig):
    e,p,_=rig;enter(e,p);pid=next(iter(p.positions));p.positions.pop(pid)
    with pytest.raises(SafetyError,match='MISSING_OPEN_POSITION'):e.reconcile(NOW)
    assert not e.ready and D(e.status()['commands'][0]['risk'])>0


def test_risk_caps_do_not_net_hedged_directions(rig):
    e,p,_=rig;enter(e,p)
    e.control('t02_long_only','RUNNING',NOW)
    other=intent('second',strategy_id='t02_long_only',side='SELL',stop_loss=D(2010),take_profit=D(1990))
    decision,_=e.enqueue(other,QUOTE,margin(),NOW);assert decision.accepted
    assert sum(D(r['quantity']) for r in e.status()['commands'])==4
    assert sum(D(r['risk']) for r in e.status()['commands'])>40


def test_broker_volume_or_deal_history_conflict_fail_closed(rig):
    e,p,_=rig;enter(e,p);did=next(iter(p.deals));p.deals[did]['executionPrice']=2001.
    with pytest.raises(SafetyError,match='HISTORY_CONFLICT'):e.reconcile(NOW)
    assert not e.ready


def test_global_risk_and_margin_caps_across_strategies(rig):
    e,p,_=rig;e.limits=replace(LIMITS,total_risk_usd=D(30))
    enter(e,p);e.control('t02_long_only','RUNNING',NOW)
    decision,_=e.enqueue(intent('second',strategy_id='t02_long_only'),QUOTE,margin(),NOW)
    assert decision.reason_code=='TOTAL_RISK'
    e.limits=replace(LIMITS,margin_usd=D(150))
    decision,_=e.enqueue(intent('third',strategy_id='t02_long_only'),QUOTE,margin(),NOW)
    assert decision.reason_code=='NATIVE_MARGIN'


@pytest.mark.parametrize('stage',['native_action_claim','native_action_broker'])
def test_management_crash_fences_replay(rig,stage):
    e,p,_=rig;enter(e,p);pid=next(iter(p.positions))
    def crash(value):
        if value==stage:raise SystemExit()
    e.checkpoint=crash
    with pytest.raises(SystemExit):e.manage('crash-close','CLOSE',pid,QUOTE,NOW)
    calls=p.calls
    assert e.manage('crash-close','CLOSE',pid,QUOTE,NOW).state=='SUBMITTING'
    assert p.calls==calls


def test_partial_reject_keeps_filled_position_and_logs_zero_fill_reject(rig):
    e,p,_=rig;cid=enter(e,p,order_type='LIMIT',entry_price=D(1998),stop_loss=D(1990),take_profit=D(2008))
    oid=next(iter(p.orders));p.fill(oid,100);p.orders[oid]['orderStatus']=3
    did=p.next_id();p.deals[did]={'dealId':did,'orderId':oid,'positionId':0,'symbolId':456,'volume':100,'filledVolume':0,'dealStatus':4,'tradeSide':1}
    e.reconcile(NOW)
    assert e.status()['commands'][0]['state']=='REJECTED'
    assert D(e.status()['commands'][0]['quantity'])==1
    assert D(e.status()['commands'][0]['risk'])>0
    e.reconcile(NOW)
    assert e.db.execute('SELECT count(*) FROM native_rejected_deals').fetchone()[0]==1
    assert e.db.execute("SELECT count(*) FROM audit WHERE kind='REJECTED_DEAL'").fetchone()[0]==1


def test_malformed_response_becomes_durable_unknown(rig):
    e,p,_=rig;send=p.send
    def malformed(request):
        result=send(request);result['clientMsgId']='wrong';return result
    p.send=malformed
    with pytest.raises(SafetyError):enter(e,p)
    assert e.status()['commands'][0]['state']=='UNKNOWN' and not e.ready
    assert e.db.execute("SELECT count(*) FROM audit WHERE kind='NATIVE_DISPATCH_ATTENTION'").fetchone()[0]==1
    calls=p.calls;e.dispatch(e.status()['commands'][0]['id'],QUOTE,margin(),NOW);assert p.calls==calls
    e.reconcile(NOW);assert e.ready  # Fresh positive broker book can resolve entry.


def test_minimum_lot_commission_reserves_usd_cent_rounding_and_explicit_buffer():
    assert LIMITS.fees(D(1),SPEC)==D('.08')+LIMITS.fee_buffer_usd


def test_default_native_controls_do_not_activate_strategies(tmp_path):
    p=OfflinePeer();e=NativeEngine(tmp_path/'stopped.sqlite',OfflineAdapter(p),SPEC,LIMITS)
    try:
        e.reconcile(NOW)
        assert e.status()['entry_suspended']
        decision,_=e.enqueue(intent(),QUOTE,margin(),NOW)
        assert decision.reason_code=='ENTRY_SUSPENDED' and not p.calls
    finally:e.close()


def test_native_spec_requires_fresh_matching_ui_and_actual_api_fields():
    from ctrader_execution.schema import UISpecEvidence
    numbers={'lotSize':10000,'minVolume':100,'maxVolume':500000,'stepVolume':100,'digits':2,'slDistance':0,'tpDistance':0,'distanceSetIn':1}
    report={'checked_at_utc':NOW.isoformat(),'identity':{'environment':'DEMO','currency':'USD','account_mode':'HEDGED','pinned_account_id_matches':True,'view_authorized':True,'broker_name_matches_configuration':True},
        'fields':{k:{'present':True,'raw_integer':str(v)} for k,v in numbers.items()}}
    ui=UISpecEvidence(D('.01'),D(50),D('.01'),D(100),NOW,True)
    spec=SymbolSpec.from_report(456,report,ui,NOW)
    assert spec.min_hundredths==100 and spec.max_hundredths==500000 and spec.independently_verified
    for bad in (replace(ui,same_account_confirmed=False),replace(ui,step_lots=D('.1'))):
        with pytest.raises(SafetyError):SymbolSpec.from_report(456,report,bad,NOW)
    with pytest.raises(SafetyError):SymbolSpec.from_report(456,report,ui,NOW+timedelta(minutes=10))


def _native_contender(path,queue):
    try:
        e=NativeEngine(path,OfflineAdapter(OfflinePeer()),SPEC,LIMITS)
    except SafetyError as exc:queue.put(str(exc))
    else:queue.put('ACQUIRED');e.close()


def test_native_owner_fence_blocks_another_process(rig,tmp_path):
    import multiprocessing
    ctx=multiprocessing.get_context('spawn');queue=ctx.Queue()
    child=ctx.Process(target=_native_contender,args=(tmp_path/'other-process.sqlite',queue))
    child.start();child.join(10)
    if child.is_alive():child.terminate();child.join();pytest.fail('contender timeout')
    assert child.exitcode==0 and queue.get(timeout=2)=='NATIVE_EXECUTION_OWNER_BUSY'


def _save_offline_broker(peer,path):
    import os
    data={k:getattr(peer,k) for k in ('orders','positions','deals','sequence','calls')}
    temp=str(path)+'.new'
    with open(temp,'w') as stream:json.dump(data,stream);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)


def _load_offline_broker(path):
    p=OfflinePeer();data=json.loads(path.read_text())
    for key in ('orders','positions','deals'):setattr(p,key,{int(k):v for k,v in data[key].items()})
    p.sequence=data['sequence'];p.calls=data['calls'];return p


def _native_killed_worker(path,broker_file,cid,stage):
    import os
    p=_load_offline_broker(broker_file);original=p.send
    def persistent_send(request):
        result=original(request);_save_offline_broker(p,broker_file);return result
    p.send=persistent_send
    e=NativeEngine(path,OfflineAdapter(p),SPEC,LIMITS)
    e.reconcile(NOW)
    def checkpoint(value):
        if value==stage:os._exit(72)
    e.checkpoint=checkpoint;e.dispatch(cid,QUOTE,margin(),NOW)


@pytest.mark.parametrize('stage',['native_claim','native_broker'])
def test_real_process_death_preserves_native_unknown_fence(tmp_path,stage):
    import multiprocessing
    path=tmp_path/'killed.sqlite';broker_file=tmp_path/'fictional-broker.json';p=OfflinePeer()
    e=NativeEngine(path,OfflineAdapter(p),SPEC,LIMITS)
    e.reconcile(NOW);e.control('t02_reference','RUNNING',NOW);e.suspend(False,NOW)
    _,ref=e.enqueue(intent(),QUOTE,margin(),NOW);e.close();_save_offline_broker(p,broker_file)
    ctx=multiprocessing.get_context('spawn')
    child=ctx.Process(target=_native_killed_worker,args=(path,broker_file,ref.command_id,stage))
    child.start();child.join(10)
    if child.is_alive():child.terminate();child.join();pytest.fail('crash worker timeout')
    assert child.exitcode==72
    p=_load_offline_broker(broker_file);calls=p.calls
    e=NativeEngine(path,OfflineAdapter(p),SPEC,LIMITS)
    try:
        assert e.status()['commands'][0]['state']=='UNKNOWN'
        e.dispatch(ref.command_id,QUOTE,margin(),NOW);assert p.calls==calls
        e.reconcile(NOW)
        assert e.ready==(stage=='native_broker')
    finally:e.close()


def test_linked_campaign_invalidation_cancels_pending_without_flattening_fill(rig):
    e,p,_=rig;e.create_campaign('campaign','t02_reference',D(2005),'ABOVE',NOW)
    enter(e,p,campaign_id='campaign',order_type='LIMIT',entry_price=D(1998),stop_loss=D(1990),take_profit=D(2010))
    oid=next(iter(p.orders));p.fill(oid,100);e.reconcile(NOW)
    trigger=Quote(D(2005),D('2005.1'),NOW)
    e.evaluate_campaigns(trigger,NOW);calls=p.calls
    e.evaluate_campaigns(trigger,NOW)
    assert p.calls==calls and e.status()['positions'][0]['status']=='OPEN'
    assert e.status()['commands'][0]['quantity']=='1'
    decision,_=e.enqueue(intent('new',campaign_id='campaign'),QUOTE,margin(),NOW)
    assert decision.reason_code=='CAMPAIGN_INACTIVE'


def test_duplicate_deal_price_presentation_does_not_change_business_identity(rig):
    e,p,_=rig;enter(e,p);did=next(iter(p.deals))
    p.deals[did]['executionPrice']=2000
    e.reconcile(NOW);assert e.ready
    assert e.db.execute('SELECT count(*) FROM native_fills').fetchone()[0]==1
