"""Observation controls, strategy previews and auth gates: no broker connectivity."""
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from decimal import Decimal as D
import json,re
from urllib.parse import urlencode
import pytest

from control_plane.operations import OpsStore,read_controls,entry_reason,handler
from signal_adapters.native_preview import preview
from signal_adapters.management import RegisteredLeg,TP1Evidence,hurricane_break_even
from signal_adapters.hurricane import HurricaneMessage,HurricaneAdapter
from signal_adapters.common import ObservationJournal,ObservationSizing
from observation.worker import Worker,ReplayEvent
from ctrader_readonly.config import SafetyError
from ctrader_execution.authorization import TradingPreparation
from test_phase27 import candles,WALL
from test_phase3_native import SPEC,intent,NOW,QUOTE
from test_ctrader import HTTPConnection,private_dir,rig as read_rig


def test_controls_default_stopped_suspended_and_idempotent(tmp_path):
    store=OpsStore(tmp_path/'ops.sqlite')
    try:
        assert store.snapshot()['entry_suspended']
        assert {r['state'] for r in store.snapshot()['strategies']}=={'STOPPED'}
        assert store.command('start','START','t02_reference')=='APPLIED'
        assert store.command('start','START','t02_reference')=='DUPLICATE'
        with pytest.raises(SafetyError):store.command('start','STOP','t02_reference')
        store.command('clear','CLEAR_SUSPENSION')
        assert entry_reason(store.snapshot(),'t02_reference') is None
        assert entry_reason(store.snapshot(),'t02_long_only')=='STRATEGY_NOT_RUNNING'
        store.command('pause','PAUSE','t02_reference')
        assert entry_reason(store.snapshot(),'t02_reference')=='STRATEGY_NOT_RUNNING'
        assert not store.snapshot()['execution_enabled']
        store.command('size','SIZE','t02_reference','0.02')
    finally:store.close()
    state=read_controls(tmp_path/'ops.sqlite')
    assert state['strategies'][1]['lots']=='0.02'
    assert state['strategies'][1]['state']=='PAUSED'


def test_native_preview_preserves_timestamps_ids_and_observation_fence(tmp_path):
    store=OpsStore(tmp_path/'ops.sqlite');store.command('run','START','t02_reference');store.command('clear','CLEAR_SUSPENSION')
    store.command('lots','SIZE','t02_reference','0.02')
    original=intent(observation_only=True,account_scope='SIM-DEMO',instrument='SIM-XAU')
    plan=preview(original,SPEC,store.snapshot())
    assert plan.quantity==2 and plan.account_scope=='RAZOR-DEMO' and plan.observation_only
    assert plan.created_at==original.created_at and plan.source_event_id==original.source_event_id and plan.key!=original.key
    assert plan.strategy_version==original.strategy_version and plan.valid_until==original.valid_until
    with pytest.raises(SafetyError):preview(replace(original,observation_only=False),SPEC,store.snapshot())
    with pytest.raises(SafetyError):preview(original,replace(SPEC,independently_verified=False),store.snapshot())
    store.close()


def test_replay_controls_do_not_activate_bots_and_cannot_be_dropped_on_restart(tmp_path):
    controls=tmp_path/'ops.sqlite';store=OpsStore(controls)
    path=tmp_path/'worker.sqlite';bars=candles();clock=lambda:WALL
    w=Worker(path,'fixture',wall=clock,controls_path=controls)
    try:
        for index,c in enumerate(bars[:60]):assert w.process(ReplayEvent(index,c,WALL))=='PROCESSED'
        assert w.db.execute('SELECT count(*) FROM paper_orders').fetchone()[0]==0
        assert w.db.execute("SELECT count(*) FROM worker_decisions WHERE reason='ENTRY_SUSPENDED'").fetchone()[0]>0
    finally:w.close();store.close()
    with pytest.raises(ValueError,match='CONTROLS_CHANGED'):Worker(path,'fixture',wall=clock)


def test_replay_one_policy_running_other_stopped(tmp_path):
    controls=tmp_path/'ops.sqlite';store=OpsStore(controls)
    store.command('run','START','t02_reference');store.command('clear','CLEAR_SUSPENSION')
    w=Worker(tmp_path/'worker.sqlite','fixture',wall=lambda:WALL,controls_path=controls)
    try:
        for index,c in enumerate(candles()[:60]):w.process(ReplayEvent(index,c,WALL))
        assert w.db.execute('SELECT count(*) FROM paper_orders').fetchone()[0]==1
        assert json.loads(w.db.execute('SELECT data FROM paper_orders').fetchone()[0])['strategy']=='t02_reference'
    finally:w.close();store.close()


def test_cancel_tombstone_never_enters_after_restart(tmp_path):
    path=tmp_path/'journal.sqlite';journal=ObservationJournal(path);adapter=HurricaneAdapter(journal)
    m=HurricaneMessage('opaque',NOW,'telegram_utc',NOW,'BUY',D(1999),D(2001),D(1990),(D(2010),D(2020),D(2030)),event='CANCEL')
    result=adapter.evaluate(m,QUOTE,NOW,ObservationSizing(D('.01')))
    assert result.invalidated and not result.intents
    journal.close();journal=ObservationJournal(path)
    result=HurricaneAdapter(journal).evaluate(replace(m,event='NEW'),QUOTE,NOW,ObservationSizing(D('.01')))
    assert not result.intents and result.reason=='DUPLICATE_OR_INVALIDATED'
    journal.close()


def test_tp1_price_touch_or_partial_exit_cannot_move_stops():
    legs=[RegisteredLeg('campaign',i,str(i),'BUY',D(2000),D(1990),D(2010+i),D(1),True) for i in (1,2,3)]
    evidence=TP1Evidence('campaign','1',D(1),D(1),True,True,True,True)
    quote=replace(QUOTE,bid=D(2011),ask=D('2011.1'))
    actions=hurricane_break_even(legs,evidence,quote,NOW,D('.01'))
    assert len(actions)==2 and all(a.stop==2000 and a.observation_only for a in actions)
    assert actions[0].take_profit==2012
    for bad in [replace(evidence,closed_units=D('.5')),replace(evidence,all_exit_reasons_tp=False),replace(evidence,complete_history=False),replace(evidence,positive_profit=False)]:
        assert not hurricane_break_even(legs,bad,quote,NOW,D('.01'))
    with pytest.raises(SafetyError):hurricane_break_even(legs,replace(evidence,position_ref='other'),quote,NOW,D('.01'))


def test_local_ops_origin_cookie_csrf_and_no_network_authorization(tmp_path):
    store=OpsStore(tmp_path/'ops.sqlite');serve=handler(store,18772)
    request=HTTPConnection(b'GET / HTTP/1.0\r\nHost: 127.0.0.1:18772\r\n\r\n');serve(request,('127.0.0.1',0),None)
    raw=request.response;headers,html=raw.split(b'\r\n\r\n',1)
    assert b'Referrer-Policy: origin' in headers and b'Cache-Control: no-store' in headers
    assert b'REAL EXECUTION DISABLED' in html
    csrf=re.search(b'name="csrf" value="([^"]+)"',html).group(1).decode()
    cookie=re.search(b'Set-Cookie: ([^;]+)',headers).group(1).decode()
    for origin,token,browser_cookie,code in [('null',csrf,cookie,400),('http://localhost:18772',csrf,cookie,400),('http://127.0.0.1:18772','wrong',cookie,400),('http://127.0.0.1:18772',csrf,'wrong=1',400),('http://127.0.0.1:18772',csrf,cookie,200)]:
        body=urlencode(dict(csrf=token,key='operator',operation='START',strategy='t02_reference'))
        c=HTTPConnection((f'POST /control HTTP/1.0\r\nHost: 127.0.0.1:18772\r\nOrigin: {origin}\r\nCookie: {browser_cookie}\r\nContent-Length: {len(body)}\r\n\r\n'+body).encode())
        serve(c,('127.0.0.1',0),None)
        assert str(code).encode() in c.response.split(b'\r\n')[0]
    assert not store.snapshot()['execution_enabled'] and store.snapshot()['entry_suspended']
    store.close()


def test_trading_preparation_never_requests_authorization(private_dir):
    plan=TradingPreparation(private_dir/'trade-config.json',private_dir/'trade-token.json',private_dir/'view-token.json')
    manifest=plan.manifest();assert not manifest['authorization_enabled'] and not manifest['execution_enabled']
    with pytest.raises(SafetyError):plan.authorize(approved=True)
    with pytest.raises(SafetyError):TradingPreparation(private_dir/'cfg',private_dir/'same',private_dir/'same').manifest()
    assert not (private_dir/'trade-token.json').exists()


def test_new_currency_and_mode_pins_fail_closed(read_rig):
    config,clock,transport,reader,token=read_rig;reader.authorize(token)
    reader.config=replace(config,expected_currency='GBP')
    with pytest.raises(SafetyError,match='CURRENCY_MISMATCH'):reader.verify_identity()
    reader.config=replace(config,expected_account_mode='SPREAD_BETTING')
    with pytest.raises(SafetyError,match='MODE_MISMATCH'):reader.verify_identity()


@pytest.mark.parametrize('field,value,code',[('expected_currency','GBP','CURRENCY_MISMATCH'),('expected_account_mode','NETTED','MODE_MISMATCH')])
def test_discovery_profile_checks_precede_private_pinning(read_rig,field,value,code):
    from test_discovery import setup,callback,choice
    from ctrader_readonly.config import private_read,private_write
    from ctrader_readonly.discovery import DiscoveryFlow
    original,path,transport,reader,token=setup(read_rig)
    data=private_read(path);data[field]=value;private_write(path,data)
    flow=DiscoveryFlow(path,reader,exchange_code=lambda *_:token,clock=read_rig[1].monotonic)
    callback(flow)
    with pytest.raises(SafetyError,match=code):flow.select(choice(flow))
    assert flow.selection_stage=='DEMO_PROFILE_VERIFICATION'
    assert private_read(path)['demo_account_id'] is None and not reader.config.token_path.exists()
    assert flow.selection_consumed


def test_hurricane_management_rejects_mixed_or_duplicate_leg_identity():
    legs=[RegisteredLeg('campaign',i,str(i),'BUY',D(2000),D(1990),D(2010+i),D(1),True) for i in (1,2,3)]
    evidence=TP1Evidence('campaign','1',D(1),D(1),True,True,True,True)
    quote=replace(QUOTE,bid=D(2011),ask=D('2011.1'))
    with pytest.raises(SafetyError):hurricane_break_even([legs[0],replace(legs[1],side='SELL')],evidence,quote,NOW,D('.01'))
    with pytest.raises(SafetyError):hurricane_break_even([legs[0],legs[1],legs[1]],evidence,quote,NOW,D('.01'))
    assert not hurricane_break_even(legs,replace(evidence,all_exit_reasons_tp='yes'),quote,NOW,D('.01'))
