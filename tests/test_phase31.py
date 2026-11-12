"""Phase 3.1 transport tests. All identities/tokens/frames are synthetic; no network."""
import copy
from dataclasses import replace,asdict
from decimal import Decimal as D
import json
import sqlite3
from urllib.parse import parse_qs,urlsplit,urlencode

import pytest
from test_ctrader import ACCOUNT,SYMBOL,SECRET,ACCESS,Clock,MockTransport,private_dir
from ctrader_readonly.config import Config,SafetyError,private_write,private_read
from ctrader_readonly.protocol import SCHEMAS,DEMO_URL
from ctrader_readonly.oauth import Token
from ctrader_execution.approvals import profile,validate_test,require_baseline,paths,local_confirmation
from ctrader_execution.ledger import Journal
from ctrader_execution.transport import MutationWire,TradeClient,owned_state,audit_projection
from ctrader_execution.oauth import TradingToken,TradingOAuthFlow
from ctrader_execution.offline import OfflinePeer
from ctrader_execution.schema import frame,order_frame
from ctrader_execution.harness import OneTest,market_state,prepare,collect_baseline,protection_ok


class Wire(MutationWire):
    """Runs the actual outbound wire fence against an in-memory socket."""
    def __init__(self,clock):
        super().__init__(ACCOUNT);self.clock=clock;self.mock=MockTransport(clock)
        self.peer=OfflinePeer(ACCOUNT,SYMBOL);self.inbox=[];self.calls=[]
        self.change=lambda name,result:result;self.mutation_change=lambda response:response
        self.failure=None;self.opens=0;self.subscribed=False
    def connect(self):
        self.opens+=1;self.socket=self
    def close(self):
        # Avoid self-as-socket recursion in DemoWebSocket.close.
        self.socket=None;self.trade_bound=False;self._once=None;self.symbol=None;self.inbox=[];self.subscribed=False
    def send(self,value):
        if isinstance(value,dict):return super().send(value)
        value=json.loads(value);self.calls.append(copy.deepcopy(value));kind=value['payloadType']
        if kind in (2106,2108,2109,2110,2111):
            if self.failure=='before':self.failure=None;raise OSError(SECRET)
            try:result=self.peer.send(value)
            finally:
                for order in self.peer.orders.values():order['utcLastUpdateTimestamp']=int(self.clock.wall()*1000)
                for deal in self.peer.deals.values():deal['executionTimestamp']=int(self.clock.wall()*1000)
            if self.failure=='after':self.failure=None;raise OSError(SECRET)
            result=self.mutation_change(result)
        else:
            name=next(k for k,v in SCHEMAS.items() if v[0]==kind)
            if name=='spots':self.subscribed=True
            result=self.change(name,self.response(name,value['payload']))
            if result is None:return
            result={'payloadType':SCHEMAS[name][1],'clientMsgId':value['clientMsgId'],'payload':result}
        if result is not None:
            if self.subscribed and kind!=51:self.inbox.append(self.mock.quote())
            self.inbox.append(result)
    def response(self,name,p):
        base={'ctidTraderAccountId':ACCOUNT}
        if name=='accounts':return {'accessToken':ACCESS,'permissionScope':1,'ctidTraderAccount':[dict(base,isLive=False,brokerTitleShort='Pepperstone - Europe',traderLogin=7654321)]}
        if name=='trader':return dict(base,trader=dict(base,balance=1000000,moneyDigits=2,depositAssetId=1,accessRights=0,accountType=0,brokerName='pepperstoneuk',registrationTimestamp=0,isLimitedRisk=False))
        if name=='symbol':
            result=self.mock.response(name,p);result['symbol'][0].update(maxVolume=500000,slDistance=0,tpDistance=0);return result
        if name=='reconcile':
            orders=[copy.deepcopy(o) for o in self.peer.orders.values() if o['orderStatus']==1]
            positions=[dict(copy.deepcopy(x),swap=0,usedMargin=15000,moneyDigits=2) for x in self.peer.positions.values() if x['positionStatus']==1]
            return dict(base,order=orders,position=positions)
        if name in ('orders','deals'):
            key='order' if name=='orders' else 'deal';source=self.peer.orders if key=='order' else self.peer.deals
            stamp='utcLastUpdateTimestamp' if key=='order' else 'executionTimestamp'
            items=[copy.deepcopy(x) for x in source.values() if p['fromTimestamp']<=x[stamp]<=p['toTimestamp']]
            return dict(base,**{key:items},hasMore=True if self.failure=='history' else False)
        if name=='pnl':return dict(base,moneyDigits=2,positionUnrealizedPnL=[{'positionId':p['positionId'],'netUnrealizedPnL':0,'grossUnrealizedPnL':0} for p in self.peer.positions.values() if p['positionStatus']==1])
        if name=='margin':return dict(base,moneyDigits=2,margin=[{'volume':100,'buyMargin':15000,'sellMargin':15000}])
        return self.mock.response(name,p)
    def receive(self,timeout):
        if self.inbox:return self.inbox.pop(0)
        self.clock.sleep(timeout);raise TimeoutError()


@pytest.fixture
def native(private_dir):
    clock=Clock();config=Config('SYNTHETIC_CLIENT',SECRET,'http://127.0.0.1:18768/oauth/callback',ACCOUNT,
        'Pepperstone - Europe',private_dir/'view.json',application_approved=True,
        expected_demo_login=7654321,expected_broker_name='pepperstoneuk',expected_currency='USD',expected_account_mode='HEDGED')
    now=clock.wall();approval={'id':'a'*32,'profile':profile(config),'role':'ONE_TEST','approved':True,'issued_at':now,'expires_at':now+600,'management_until':now+3600,
        'entry_limit':1,'raw_volume':100,'side':'BUY','order_type':'MARKET','limit_price':None,
        'stop_distance':'2','target_distance':'4','risk_cap':'5','margin_cap':'250','slippage_buffer':'.25','fee_buffer':'.50','max_spread':'.50','stop_floor':'0',
        'ui':{'same_account_confirmed':True,'commission_basis_confirmed':True,'minimum_lots':'.01','maximum_lots':'50','step_lots':'.01','units_per_lot':'100','commission_roundtrip_per_lot':'7'},
        'emergency_close':True,'cancel_remainder':True,'stop_uncertainty_acknowledged':True,'allowed_ops':[2106,2108,2109,2110,2111],
        'journal_path':str(private_dir/'first-test.sqlite'),'confirmed_balance':'10000','intended_balance':'10000'}
    baseline={'profile':profile(config),'confirmed':True,'confirmed_at':now,'balance':'10000','intended_balance':'10000'}
    wire=Wire(clock);client=TradeClient(config,approval,wire,clock=clock.monotonic,wall=clock.wall,sleep=clock.sleep)
    token=TradingToken(ACCESS,now+7200,ACCOUNT)
    journal=Journal(approval['journal_path'],profile(config),approval['id'],clock=clock.wall)
    yield config,clock,approval,baseline,wire,client,token,journal
    client.disconnect('TEST_DONE');journal.close()


def ready(native):
    c,clock,a,b,w,client,token,j=native
    client.authorize(token);client.refresh();return client


def entry(native):
    c,clock,a,b,w,client,token,j=native
    market=market_state(client);intent,spec,proposal=prepare(c,a,b,market,clock.wall())
    request=order_frame(ACCOUNT,spec,'e'*32,intent,market['quote'],intent.created_at)
    j.claim('entry',2106,request['payload'],cid=request['clientMsgId'])
    return request


def test_real_wire_market_relative_protection_and_reconciliation(native):
    client=ready(native);request=entry(native);j=native[-1];w=native[4]
    client.mutate(request,j,controls_check=lambda:None)
    assert request['payload']['relativeStopLoss']==200000
    assert request['payload']['relativeTakeProfit']==400000
    assert 'stopLoss' not in request['payload']
    book=client.snapshot(int(j.entry()['at']*1000)-1000)
    order,positions,opened,closed=owned_state(book,request['clientMsgId'])
    assert opened==100 and closed==0 and protection_ok(positions[0],native[2],client.native_spec)
    client.management('close',2111,{'positionId':positions[0]['positionId'],'volume':100},j,lambda:None)
    assert owned_state(client.snapshot(int(j.entry()['at']*1000)-1000),request['clientMsgId'])[2:]==(100,100)
    assert [f['payloadType'] for f in w.calls if f['payloadType'] in (2106,2111)]==[2106,2111]
    assert SECRET not in json.dumps(j.summary()) and ACCESS not in json.dumps(j.summary())


@pytest.mark.parametrize('field,value',[('permissionScope',0),('live',True),('live',None),('login',333),('broker','Other')])
def test_remote_scope_demo_and_identity_fail_closed(native,field,value):
    w=native[4];client=native[5]
    def change(name,result):
        if name=='accounts':
            if field=='permissionScope':result[field]=value
            else:result['ctidTraderAccount'][0][{'live':'isLive','login':'traderLogin','broker':'brokerTitleShort'}[field]]=value
        return result
    w.change=change
    with pytest.raises(SafetyError):client.authorize(native[6])
    assert not w.trade_bound and not native[6].verified
    assert not any(x['payloadType']==2106 for x in w.calls)


@pytest.mark.parametrize('field,value',[('brokerName','Other'),('accountType',1),('depositAssetId',2)])
def test_remote_trader_identity_fail_closed(native,field,value):
    w=native[4]
    def change(name,result):
        if name=='trader':result['trader'][field]=value
        return result
    w.change=change
    with pytest.raises(SafetyError):native[5].authorize(native[6])
    assert not w.trade_bound


def test_view_token_and_expired_trade_token_never_connect(native):
    client=native[5];clock=native[1]
    for token in (Token(ACCESS,clock.wall()+7200),TradingToken(ACCESS,clock.wall()+29,ACCOUNT)):
        with pytest.raises(SafetyError):client.authorize(token)
    assert native[4].opens==0


@pytest.mark.parametrize('failure',['before','after'])
def test_lost_ack_is_unknown_and_durable_never_resends(native,failure):
    ready(native);request=entry(native);w=native[4];client=native[5];j=native[-1]
    w.failure=failure
    with pytest.raises(SafetyError,match='UNKNOWN_BROKER_OUTCOME'):client.mutate(request,j,controls_check=lambda:None)
    assert j.entry()['state']=='UNKNOWN' and j.entry()['sent']==1
    assert len(w.peer.orders)==(failure=='after')
    client.authorize(native[6]);client.refresh()
    with pytest.raises(SafetyError):client.mutate(request,j,controls_check=lambda:None)
    assert sum(f['payloadType']==2106 for f in w.calls)==1


@pytest.mark.parametrize('corrupt',['correlation','account','client','type','error'])
def test_unexpected_mutation_response_cannot_mean_success(native,corrupt):
    ready(native);request=entry(native)
    def change(response):
        if corrupt=='correlation':response['clientMsgId']='wrong'
        elif corrupt=='account':response['payload']['ctidTraderAccountId']=333
        elif corrupt=='client':response['payload']['order']['clientOrderId']='wrong'
        elif corrupt=='type':response['payload']['executionType']=999
        else:return {'payloadType':2132,'clientMsgId':request['clientMsgId'],'payload':{'ctidTraderAccountId':ACCOUNT,'errorCode':SECRET}}
        return response
    native[4].mutation_change=change
    with pytest.raises(SafetyError,match='UNKNOWN_BROKER_OUTCOME') as error:native[5].mutate(request,native[-1],controls_check=lambda:None)
    assert SECRET not in str(error.value) and native[-1].entry()['state']=='UNKNOWN'


def test_wire_requires_once_consumed_durable_permit(native):
    ready(native);request=entry(native);wire=native[4];j=native[-1]
    with pytest.raises(SafetyError,match='DURABLE_MUTATION_PERMIT_REQUIRED'):wire.send(request)
    wire.permit_once(request,j);wire.send(request)
    with pytest.raises(SafetyError):wire.send(request)
    with pytest.raises(SafetyError):wire.permit_once(request,j)
    assert wire.peer.calls==1


def test_restart_recovers_claim_as_unknown_and_prevents_second_entry(native):
    ready(native);request=entry(native);j=native[-1];path=j.path
    j.close();restored=Journal(path,profile(native[0]),native[2]['id'])
    try:
        assert restored.entry()['state']=='UNKNOWN'
        with pytest.raises(SafetyError):restored.consume_send(request['clientMsgId'],2106,request['payload'])
        with pytest.raises(SafetyError,match='FIRST_ENTRY_ALREADY_CLAIMED'):restored.claim('other',2106,request['payload'])
    finally:restored.close()


def test_journal_exclusive_owner_binding_and_idempotency(native):
    j=native[-1]
    with pytest.raises(BlockingIOError):Journal(j.path,profile(native[0]),native[2]['id'])
    payload={'ctidTraderAccountId':ACCOUNT,'orderId':7}
    claim,new=j.claim('cancel',2108,payload)
    assert new and not j.claim('cancel',2108,payload)[1]
    with pytest.raises(SafetyError,match='IDEMPOTENCY_PAYLOAD_CONFLICT'):j.claim('cancel',2108,dict(payload,orderId=8))
    j.close()
    with pytest.raises(SafetyError,match='FIRST_TEST_JOURNAL_ALREADY_BOUND'):Journal(j.path,profile(native[0]),'b'*32)


def test_database_lock_prevents_claim_before_send(native):
    j=native[-1];other=sqlite3.connect(j.path,isolation_level=None)
    other.execute('BEGIN IMMEDIATE');j.db.execute('PRAGMA busy_timeout=10')
    try:
        with pytest.raises(sqlite3.OperationalError):j.claim('entry',2106,{'x':1})
    finally:other.rollback();other.close()
    assert native[4].peer.calls==0 and j.entry() is None


@pytest.mark.parametrize('missing_protection,partial',[(False,False),(True,False),(False,True)])
def test_one_order_harness_ends_flat_and_verifies_native_state(native,missing_protection,partial):
    c,clock,a,b,w,client,token,j=native
    w.peer.protection_missing=missing_protection
    if partial:w.peer.fill_fraction=D('.5')
    confirmations=[]
    result=OneTest(c,a,b,client,j,confirm=lambda *args:confirmations.append(args),controls=lambda:None).run(token)
    assert result.endswith('CLOSED_RECONCILED') and len(confirmations)==1
    assert j.entry()['state']=='CLOSED'
    assert sum(f['payloadType']==2106 for f in w.calls)==1
    assert not any(p['positionStatus']==1 for p in w.peer.positions.values())
    codes=j.summary()['audit_codes']
    assert ('CRITICAL_NATIVE_PROTECTION_MISSING_OR_MISMATCHED' in codes)==missing_protection
    assert ('NATIVE_POSITION_PROTECTION_VERIFIED' in codes)==(not missing_protection)
    if partial:assert any(f['payloadType']==2108 for f in w.calls)


def test_limit_amend_cancel_and_no_fill(native):
    native[2].update(order_type='LIMIT',limit_price='1999')
    ready(native);request=entry(native);client=native[5];j=native[-1]
    assert request['payload']['stopLoss']==1997 and 'relativeStopLoss' not in request['payload']
    client.mutate(request,j,controls_check=lambda:None)
    oid=next(iter(native[4].peer.orders))
    client.management('amend',2109,{'orderId':oid,'volume':100,'limitPrice':1999,'stopLoss':1998,'takeProfit':2003},j,lambda:None)
    client.management('cancel',2108,{'orderId':oid},j,lambda:None)
    assert native[4].peer.orders[oid]['orderStatus']==5


def test_position_modification_rejects_looser_stop_and_partial_dust(native):
    ready(native);request=entry(native);client=native[5];j=native[-1]
    client.mutate(request,j,controls_check=lambda:None);pid=next(iter(native[4].peer.positions))
    client.management('protect',2110,{'positionId':pid,'stopLoss':1999,'takeProfit':2004},j,lambda:None)
    with pytest.raises(SafetyError):client.management('loosen',2110,{'positionId':pid,'stopLoss':1998,'takeProfit':2004},j,lambda:None)
    with pytest.raises(SafetyError):client.management('partial',2111,{'positionId':pid,'volume':50},j,lambda:None)
    assert native[4].peer.positions[pid]['tradeData']['volume']==100


@pytest.mark.parametrize('mode',['timeout_before','timeout_after','reject'])
def test_harness_unknown_entry_and_rejection_do_not_resubmit(native,mode):
    c,clock,a,b,w,client,token,j=native
    # One fault on entry; management must still be allowed for lost ACK recovery.
    original=w.peer.send
    def send(request):
        w.peer.mode=mode if request['payloadType']==2106 else 'normal'
        return original(request)
    w.peer.send=send
    runner=OneTest(c,a,b,client,j,confirm=lambda *args:None,controls=lambda:None)
    if mode=='timeout_before':
        with pytest.raises(SafetyError,match='UNKNOWN_ENTRY_NOT_RESUBMITTED'):runner.run(token)
    else:assert runner.run(token) in ('CLOSED_RECONCILED','REJECTED_CONFIRMED')
    assert sum(f['payloadType']==2106 for f in w.calls)==1


def test_harness_unprotected_close_failure_is_critical(native):
    c,clock,a,b,w,client,token,j=native;w.peer.protection_missing=True
    original=w.peer.send
    def send(request):
        if request['payloadType']==2111:raise TimeoutError()
        return original(request)
    w.peer.send=send
    with pytest.raises(SafetyError,match='CRITICAL_EXPOSURE_REQUIRES_MANUAL_BROKER_UI'):
        OneTest(c,a,b,client,j,confirm=lambda *args:None,controls=lambda:None).run(token)
    assert 'CRITICAL_EXPOSURE_REQUIRES_MANUAL_BROKER_UI' in j.summary()['audit_codes']
    assert sum(f['payloadType']==2111 for f in w.calls)==1


@pytest.mark.parametrize('problem',['balance','margin','stale','spread','ui','risk','stop'])
def test_preflight_risk_and_data_limits(native,problem):
    client=ready(native);market=market_state(client);a=native[2];b=native[3]
    if problem=='balance':market['balance']=D(1000)
    if problem=='margin':market['buy_margin']=D(251)
    if problem=='stale':market['at']-=10
    if problem=='spread':a['max_spread']='.1'
    if problem=='ui':a['ui']['step_lots']='.02'
    if problem=='risk':a['risk_cap']='1'
    if problem=='stop':a['stop_floor']='3'
    with pytest.raises(SafetyError):prepare(native[0],a,b,market,native[1].wall())
    assert native[4].peer.calls==0


def test_actual_baseline_not_hardcoded_and_confirmation_required(native):
    client=ready(native)
    record=collect_baseline(native[0],client,'10000',native[1].wall)
    assert record['balance']=='10000' and record['confirmed'] is False
    with pytest.raises(SafetyError,match='LOCAL_BASELINE_CONFIRMATION_REQUIRED'):require_baseline(record,native[0],native[1].wall())
    # A consciously confirmed differing budget is allowed, never edits the account.
    record.update(confirmed=True,confirmed_at=native[1].wall(),intended_balance='9000')
    assert require_baseline(record,native[0],native[1].wall())['balance']=='10000'


def test_history_missing_and_identity_conflicts_fail_closed(native):
    client=ready(native);request=entry(native);j=native[-1]
    client.mutate(request,j,controls_check=lambda:None)
    book=client.snapshot(int(j.entry()['at']*1000)-1000)
    for mutation in ('missing','duplicate','symbol','volume'):
        bad=copy.deepcopy(book)
        if mutation=='missing':bad['deals']=[]
        elif mutation=='duplicate':bad['deals']*=2
        elif mutation=='symbol':bad['deals'][0]['symbolId']=999
        else:bad['positions'][0]['tradeData']['volume']=50
        with pytest.raises(SafetyError):owned_state(bad,request['clientMsgId'])
    native[4].change=lambda name,result:dict(result,hasMore=None) if name=='deals' else result
    with pytest.raises(SafetyError):client.snapshot(int(j.entry()['at']*1000)-1000)


def test_approval_expiry_scope_operation_and_journal_fences(native):
    a=native[2];c=native[0];clock=native[1]
    for key,value in [('raw_volume',200),('emergency_close',False),('journal_path',str(c.token_path.parent/'other.sqlite')),('approved',False),('expires_at',clock.wall()-1)]:
        bad=copy.deepcopy(a);bad[key]=value
        with pytest.raises(SafetyError):validate_test(bad,c,clock.wall())
    ready(native);request=entry(native);a['allowed_ops']=[2106,2108,2111]
    with pytest.raises(SafetyError):native[5].management('foreign',2110,{'positionId':999,'stopLoss':1998,'takeProfit':2004},native[-1],lambda:None)
    assert native[4].peer.calls==0


def test_separate_token_store_remote_verified_and_redacted(native,private_dir):
    token=native[6];c=native[0]
    assert ACCESS not in repr(token)
    with pytest.raises(SafetyError):token.save(private_dir/'trade.json',c.token_path)
    ready(native);token.save(private_dir/'trade.json',c.token_path)
    assert TradingToken.load(private_dir/'trade.json',c.token_path,ACCOUNT).scope=='trading'
    assert not c.token_path.exists()
    with pytest.raises(SafetyError):token.save(c.token_path,c.token_path)
    with pytest.raises(SafetyError):TradingToken.load(private_dir/'trade.json',c.token_path,999)


def test_trading_oauth_separate_approval_csrf_replay_and_no_orders(native,private_dir):
    c,clock,a,b,w,client,token,j=native
    cp=private_dir/'config.json';private_write(cp,dict({k:v for k,v in asdict(c).items() if k!='discovery_only'},token_path=str(c.token_path)))
    # Config JSON excludes internal runtime fields if present.
    with pytest.raises(Exception):TradingOAuthFlow(cp)
    authority={'id':'b'*32,'role':'OAUTH_ONLY','profile':profile(c),'approved':True,'issued_at':clock.wall(),'expires_at':clock.wall()+900}
    private_write(paths(cp)['oauth'],authority)
    def factory(config,auth):return TradeClient(config,auth,w,clock=clock.monotonic,wall=clock.wall,sleep=clock.sleep)
    flow=TradingOAuthFlow(cp,client_factory=factory,exchange=lambda *args:TradingToken(ACCESS,clock.wall()+7200),clock=clock.monotonic)
    query=parse_qs(urlsplit(flow.authorization_url()).query);assert query['scope']==['trading']
    with pytest.raises(SafetyError,match='STATE_MISMATCH'):flow.callback('/oauth/callback?state=bad&code=synthetic')
    path='/oauth/callback?'+urlencode({'state':flow.state,'code':'synthetic'})
    flow.callback(path);assert flow.success
    with pytest.raises(SafetyError,match='REPLAYED'):flow.callback(path)
    assert private_read(paths(cp)['token'])['scope']=='trading'
    assert w.peer.calls==0 and not c.token_path.exists()
    second=TradingOAuthFlow(cp,client_factory=factory,clock=clock.monotonic)
    with pytest.raises(SafetyError,match='STATE_MISMATCH'):second.callback(path)
    clock.sleep(181)
    with pytest.raises(SafetyError,match='EXPIRED'):second.callback('/oauth/callback?'+urlencode({'state':second.state,'code':'synthetic'}))


def test_cancellation_vs_fill_race_closes_only_owned_position(native):
    c,clock,a,b,w,client,token,j=native;a.update(order_type='LIMIT',limit_price='1999')
    original=w.peer.send
    def send(request):
        if request['payloadType']==2108:
            oid=request['payload']['orderId'];w.peer.fill(oid,100)
        return original(request)
    w.peer.send=send
    assert OneTest(c,a,b,client,j,confirm=lambda *args:None,controls=lambda:None).run(token)=='CLOSED_RECONCILED'
    assert sum(f['payloadType']==2106 for f in w.calls)==1
    assert sum(f['payloadType']==2111 for f in w.calls)==1


def test_crash_after_write_before_ack_recovery_never_enters_again(native):
    c,clock,a,b,w,client,token,j=native;ready(native);request=entry(native)
    # Simulated process crash after bytes accepted, before an outcome is committed.
    w.permit_once(request,j);w.send(request);j.close();client.disconnect('CRASH')
    restored=Journal(a['journal_path'],profile(c),a['id'],clock=clock.wall)
    try:
        assert restored.entry()['state']=='UNKNOWN'
        assert OneTest(c,a,b,client,restored,confirm=lambda *args:None,controls=lambda:None).run(token,resume=True)=='CLOSED_RECONCILED'
        assert sum(f['payloadType']==2106 for f in w.calls)==1
    finally:restored.close()


def test_direct_entry_boundary_rechecks_margin_and_controls(native):
    ready(native);request=entry(native);w=native[4]
    def change(name,result):
        if name=='margin':result['margin'][0]['buyMargin']=99999999
        return result
    w.change=change
    with pytest.raises(SafetyError,match='TEST_MARGIN_OR_FUNDS_LIMIT'):native[5].mutate(request,native[-1],controls_check=lambda:None)
    assert native[-1].entry()['sent']==0 and w.peer.calls==0
    w.change=lambda name,result:result
    def suspended():raise SafetyError('CONTROLS_CHANGED')
    with pytest.raises(SafetyError,match='CONTROLS_CHANGED'):native[5].mutate(request,native[-1],controls_check=suspended)
    assert native[-1].entry()['sent']==0 and w.peer.calls==0


def test_stale_quote_cannot_submit_directly(native):
    ready(native);request=entry(native);native[1].sleep(11)
    with pytest.raises(SafetyError):native[5].mutate(request,native[-1],controls_check=lambda:None)
    assert native[4].peer.calls==0


def test_foreign_position_cannot_be_closed(native):
    ready(native);request=entry(native);client=native[5];j=native[-1]
    client.mutate(request,j,controls_check=lambda:None)
    with pytest.raises(SafetyError):client.management('foreign-close',2111,{'positionId':98765,'volume':100},j,lambda:None)
    assert native[4].peer.calls==1


def test_view_allowlist_and_demo_endpoint_remain_unchanged(native):
    from ctrader_readonly.protocol import DemoWebSocket
    with pytest.raises(SafetyError):DemoWebSocket().send({'clientMsgId':'x','payloadType':2106,'payload':{}})
    wire=native[4];wire.endpoint='wss://live.ctraderapi.com:5036'
    with pytest.raises(SafetyError,match='DEMO_ENDPOINT_REQUIRED'):TradeClient(native[0],native[2],wire)


def test_no_controlling_terminal_means_no_owner_confirmation(monkeypatch):
    import builtins
    def denied(*args,**kwargs):raise OSError('No controlling terminal')
    monkeypatch.setattr(builtins,'open',denied)
    with pytest.raises(SafetyError,match='LOCAL_OWNER_TTY_REQUIRED'):local_confirmation('Review locally','EXACT')


def test_expired_management_approval_requires_manual_ui_not_more_requests(native):
    ready(native);request=entry(native);client=native[5];j=native[-1]
    client.mutate(request,j,controls_check=lambda:None)
    native[1].sleep(3601)
    with pytest.raises(SafetyError,match='OWNER_APPROVAL_EXPIRED'):client.management('close',2111,{'positionId':next(iter(native[4].peer.positions)),'volume':100},j,lambda:None)
    assert native[4].peer.calls==1
