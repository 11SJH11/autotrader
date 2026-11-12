"""Synthetic protocol fixtures only: no credentials, real accounts or broker I/O."""
import copy
from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from ctrader_readonly.client import Reader
from ctrader_readonly.config import Config, SafetyError, private_read, private_write
from ctrader_readonly.oauth import OAuthFlow, Token, exchange
from ctrader_readonly.protocol import DEMO_URL, DemoWebSocket, RateLimiter, SCHEMAS, validate_frame
from ctrader_readonly.__main__ import status_handler, main

ACCOUNT=900001  # Invented mock identity, not an observed account.
SYMBOL=10001
SECRET='SYNTHETIC_DO_NOT_LEAK_SECRET'
ACCESS='SYNTHETIC_DO_NOT_LEAK_TOKEN'
PRIVATE='SYNTHETIC_PRIVATE_LABEL'


class Clock:
    def __init__(self):
        self.mono=1000.0
        self.epoch=time.time()
    def monotonic(self):
        return self.mono
    def wall(self):
        return self.epoch+self.mono-1000
    def sleep(self,seconds):
        self.mono+=seconds


class MockTransport:
    def __init__(self,clock):
        self.clock=clock
        self.sent=[]
        self.inbox=[]
        self.opened=0
        self.closed=0
        self.change=lambda name,result:result
        self.inject=None
        self.failure=None
        trade={'symbolId':SYMBOL,'volume':100,'tradeSide':1}
        self.book={'order':[{'orderId':70001,'tradeData':trade,'orderStatus':1,'orderType':2,'label':PRIVATE}],
            'position':[{'positionId':80001,'tradeData':trade,'positionStatus':1,'swap':0,
                         'usedMargin':1234,'moneyDigits':2,'comment':PRIVATE}]}
    def connect(self):
        self.opened+=1
        if self.failure=='connect':
            raise OSError(SECRET)
    def close(self):
        self.closed+=1
        self.inbox=[]
    def send(self,frame):
        validate_frame(frame)
        self.sent.append((self.clock.monotonic(),copy.deepcopy(frame)))
        if self.failure=='send':
            raise OSError(SECRET)
        name=next(k for k,v in SCHEMAS.items() if v[0]==frame['payloadType'])
        result=self.response(name,frame['payload'])
        result=self.change(name,result)
        if result is None:
            return
        response={'payloadType':SCHEMAS[name][1],'clientMsgId':frame['clientMsgId'],'payload':result}
        if self.inject:
            response=self.inject(name,response)
        if response is not None:
            self.inbox.append(response)
    def response(self,name,payload):
        base={'ctidTraderAccountId':ACCOUNT}
        data={
            'application':{},
            'heartbeat':{},
            'accounts':{'accessToken':ACCESS,'permissionScope':0,'ctidTraderAccount':[
                {'ctidTraderAccountId':ACCOUNT,'isLive':False,'brokerTitleShort':'Example Broker'}]},
            'account':base,
            'trader':dict(base,trader=dict(base,balance=100000,moneyDigits=2,depositAssetId=1,
                accessRights=0,accountType=0,brokerName='Example Broker',registrationTimestamp=0)),
            'assets':dict(base,asset=[{'assetId':1,'name':'USD'},{'assetId':2,'name':'XAU'}]),
            'symbols':dict(base,symbol=[{'symbolId':SYMBOL,'symbolName':'XAUUSD','baseAssetId':2,'quoteAssetId':1}]),
            'symbol':dict(base,symbol=[{'symbolId':SYMBOL,'digits':2,'pipPosition':1,'minVolume':100,
                'maxVolume':100000,'stepVolume':100,'lotSize':10000,'distanceSetIn':1,
                'slDistance':10,'tpDistance':20,'tradingMode':0}]),
            'reconcile':dict(base,**copy.deepcopy(self.book)),
            'orders':dict(base,order=[{'orderId':70002,'utcLastUpdateTimestamp':payload.get('toTimestamp',0),'label':PRIVATE}],hasMore=False),
            'deals':dict(base,deal=[{'dealId':60001,'executionTimestamp':payload.get('toTimestamp',0),'comment':PRIVATE}],hasMore=False),
            'pnl':dict(base,moneyDigits=2,positionUnrealizedPnL=[{'positionId':80001,'netUnrealizedPnL':123,'grossUnrealizedPnL':124}]),
            'margin':dict(base,moneyDigits=2,margin=[{'volume':100,'buyMargin':1234,'sellMargin':1256}]),
            'spots':base,
        }
        if name=='spots':
            self.inbox.append(self.quote())
        return copy.deepcopy(data[name])
    def quote(self,**change):
        body={'ctidTraderAccountId':ACCOUNT,'symbolId':SYMBOL,'bid':200000000,
              'ask':200020000,'timestamp':int(self.clock.wall()*1000)}
        body.update(change)
        return {'payloadType':2131,'payload':body}
    def receive(self,timeout):
        if self.failure=='receive':
            raise OSError(SECRET)
        if self.inbox:
            return self.inbox.pop(0)
        self.clock.sleep(timeout)
        raise TimeoutError()


@pytest.fixture
def private_dir():
    # Outside Git as required; all contents are synthetic and removed on teardown.
    with tempfile.TemporaryDirectory(prefix='autotrader-oauth-test-') as directory:
        yield Path(directory)


@pytest.fixture
def rig(private_dir):
    clock=Clock()
    config=Config('SYNTHETIC_CLIENT',SECRET,'http://127.0.0.1:18768/oauth/callback',ACCOUNT,
        'Example Broker',private_dir/'tokens.json',application_approved=True)
    transport=MockTransport(clock)
    reader=Reader(config,transport,clock.monotonic,clock.wall,clock.sleep)
    token=Token(ACCESS,clock.wall()+3600)
    return config,clock,transport,reader,token


def opened(rig):
    _,clock,transport,reader,token=rig
    reader.authorize(token)
    reader.refresh()
    return reader


def test_complete_read_only_snapshot_and_redaction(rig):
    reader=opened(rig)
    status=reader.status()
    assert status['health']=='HEALTHY'
    assert status['symbol']['minimum_units']=='1'
    assert status['symbol']['lot_units']=='100'
    assert status['account']['token_scope']=='VIEW_ONLY'
    assert status['account']['account_access']=='FULL_ACCESS'  # Account rights != OAuth scope.
    assert status['pending_count']==status['position_count']==1
    assert status['order_history_count']==status['deal_history_count']==1
    assert status['margin_information_available']
    assert not status['free_margin_authoritative']
    assert str(reader._margin['used'])=='12.34'
    assert str(reader._margin['free_margin_estimate'])=='988.89'
    result=json.dumps(status)
    for secret in (SECRET,ACCESS,PRIVATE,str(ACCOUNT),str(SYMBOL),'balance','usedMargin','positionId','orderId'):
        assert secret not in result
    assert all(frame['payloadType'] in {s[0] for s in SCHEMAS.values()} for _,frame in rig[2].sent)
    assert not (set(frame['payloadType'] for _,frame in rig[2].sent) & {2106,2108,2109,2110,2111})


def test_approval_gate_has_no_network(rig):
    config,clock,t,_,token=rig
    reader=Reader(replace(config,application_approved=False),t,clock.monotonic,clock.wall,clock.sleep)
    with pytest.raises(SafetyError,match='APPLICATION_APPROVAL_REQUIRED'):
        reader.authorize(token)
    assert not t.opened
    with pytest.raises(SafetyError,match='VIEW_SCOPE_REQUIRED'):
        replace(config,scope='trading')


@pytest.mark.parametrize('scope',[1,'SCOPE_TRADE',None,False,'trading'])
def test_scope_rejected_before_account_auth_and_persistence(rig,scope):
    config,clock,t,reader,token=rig
    def change(name,response):
        if name=='accounts':
            response['permissionScope']=scope
        return response
    t.change=change
    with pytest.raises(SafetyError):
        reader.authorize(token)
    assert not token.verified
    assert not config.token_path.exists()
    assert not any(f['payloadType']==2102 for _,f in t.sent)
    assert not reader.status()['view_authorized']


@pytest.mark.parametrize('field,value,code',[
    ('isLive',True,'DEMO_ACCOUNT_REQUIRED'),('isLive',None,'DEMO_ACCOUNT_REQUIRED'),
    ('ctidTraderAccountId',999999,'MISSING_OR_AMBIGUOUS_METADATA'),
    ('brokerTitleShort','Wrong Broker','BROKER_IDENTITY_MISMATCH')])
def test_wrong_demo_or_broker(rig,field,value,code):
    _,_,t,reader,token=rig
    def change(name,response):
        if name=='accounts':
            response['ctidTraderAccount'][0][field]=value
        return response
    t.change=change
    with pytest.raises(SafetyError,match=code):
        reader.authorize(token)
    assert not any(f['payloadType']==2102 for _,f in t.sent)


def test_error_response_never_leaks(rig):
    _,_,t,reader,token=rig
    t.inject=lambda name,result: {'payloadType':2142,'payload':{'errorCode':'INVALID_TOKEN','description':SECRET+ACCESS}}
    with pytest.raises(SafetyError,match='BROKER_REQUEST_REJECTED') as error:
        reader.authorize(token)
    assert SECRET not in str(error.value)
    assert SECRET not in json.dumps(reader.status())


@pytest.mark.parametrize('issue',['type','correlation','account','missing_payload'])
def test_unexpected_response(rig,issue):
    _,_,t,reader,token=rig
    reader.authorize(token)
    def inject(name,response):
        if issue=='type':
            response['payloadType']=9999
        elif issue=='correlation':
            response['clientMsgId']='wrong'
        elif issue=='account':
            response['payload']['ctidTraderAccountId']=11111
        else:
            del response['payload']
        return response
    t.inject=inject
    with pytest.raises(SafetyError):
        reader.refresh()
    assert not reader.status()['connected']


def test_expired_token_rejected_before_connect_and_during_session(rig):
    _,clock,t,reader,token=rig
    token.expires_at=clock.wall()+29
    with pytest.raises(SafetyError,match='TOKEN_EXPIRED'):
        reader.authorize(token)
    assert t.opened==0
    token.expires_at=clock.wall()+3600
    reader.authorize(token)
    clock.sleep(3600)
    with pytest.raises(SafetyError,match='TOKEN_EXPIRED'):
        reader.poll()
    assert not reader.status()['connected']


@pytest.mark.parametrize('failure',['connect','send','receive'])
def test_network_failure_redacted(rig,failure):
    _,_,t,reader,token=rig
    t.failure=failure
    with pytest.raises(SafetyError) as error:
        reader.authorize(token)
    assert SECRET not in str(error.value)
    assert not reader.status()['connected']


def test_timeout_and_heartbeat(rig):
    _,clock,t,reader,token=rig
    reader.authorize(token)
    t.change=lambda name,result:None if name=='trader' else result
    with pytest.raises(SafetyError,match='REQUEST_TIMEOUT'):
        reader.refresh()
    assert not reader.status()['connected']
    t.change=lambda name,result:result
    reader.authorize(token)
    clock.sleep(10)
    reader.poll()
    assert t.sent[-1][1]['payloadType']==51
    clock.sleep(31)
    with pytest.raises(SafetyError,match='HEARTBEAT_TIMEOUT'):
        reader.poll()


@pytest.mark.parametrize('change',[{'timestamp':0},{'symbolId':999},{'ctidTraderAccountId':999},
                                    {'bid':200040000},{'timestamp':'bad'},{'bid':True}])
def test_bad_quote_disconnects(rig,change):
    reader=opened(rig)
    t=rig[2]
    t.inbox.append(t.quote(**change))
    with pytest.raises(SafetyError):
        reader.poll()
    assert not reader.status()['quote_fresh']
    assert not reader.status()['connected']


def test_independent_quote_side_and_snapshot_staleness(rig):
    reader=opened(rig)
    _,clock,t,_,_=rig
    clock.sleep(11)
    t.inbox.append(t.quote(bid=200000000))
    del t.inbox[-1]['payload']['ask']
    reader.poll()
    assert not reader.status()['quote_fresh']  # New bid cannot freshen old ask.
    clock.sleep(20)
    assert not reader.status()['snapshot_fresh']
    assert reader.status()['health']=='DEGRADED'


def test_execution_event_marks_snapshot_dirty(rig):
    reader=opened(rig)
    t=rig[2]
    t.inbox.append({'payloadType':2126,'payload':{'ctidTraderAccountId':ACCOUNT,'executionType':3}})
    reader.poll()
    assert not reader.status()['snapshot_fresh']
    reader.refresh()
    assert reader.status()['health']=='HEALTHY'


def test_history_bisects_full_pages(rig):
    _,_,t,reader,token=rig
    reader.authorize(token)
    calls=[]
    original=t.send
    def send(frame):
        if frame['payloadType']==2175:
            lo,hi=frame['payload']['fromTimestamp'],frame['payload']['toTimestamp']
            calls.append((lo,hi))
            t.inbox.append({'payloadType':2176,'clientMsgId':frame['clientMsgId'],
                'payload':{'ctidTraderAccountId':ACCOUNT,'hasMore':hi-lo>1,
                'order':[] if hi-lo>1 else [{'orderId':lo+1,'utcLastUpdateTimestamp':lo}]}})
        else:
            original(frame)
    t.send=send
    result=reader._history('orders',10,13)
    assert len(result)==2
    assert calls==[(10,13),(12,13),(10,11)]


@pytest.mark.parametrize('kind',['missing_has_more','saturated','missing_timestamp'])
def test_incomplete_history_never_healthy(rig,kind):
    _,_,t,reader,token=rig
    reader.authorize(token)
    def change(name,response):
        if name=='orders':
            if kind=='missing_has_more':
                response.pop('hasMore')
            elif kind=='saturated':
                response.update(hasMore=True,order=[])
            else:
                del response['order'][0]['utcLastUpdateTimestamp']
        return response
    t.change=change
    with pytest.raises(SafetyError):
        reader.refresh(history_seconds=1)
    assert not reader.status()['history_complete']
    assert reader.status()['health']=='DISCONNECTED'


def test_rate_limit_all_and_history():
    clock=Clock()
    limiter=RateLimiter(clock.monotonic,clock.sleep)
    all_times=[]
    history=[]
    for index in range(35):
        historical=index%3==0
        limiter.acquire(historical)
        all_times.append(clock.monotonic())
        if historical:
            history.append(clock.monotonic())
    for times,limit in ((all_times,10),(history,2)):
        for instant in times:
            assert sum(instant<=at<instant+1 for at in times)<=limit


@pytest.mark.parametrize('payload',[2106,2108,2109,2110,2111,2173,9999])
def test_transport_blocks_trade_and_unlisted_requests_before_socket(payload):
    transport=DemoWebSocket()
    class Socket:
        def send(self,*args):
            pytest.fail('Forbidden request reached socket')
    transport.socket=Socket()
    with pytest.raises(SafetyError,match='OUTBOUND_NOT_ALLOWED'):
        transport.send({'clientMsgId':'x','payloadType':payload,'payload':{}})


def test_outbound_unknown_fields_and_wrong_account(rig):
    reader=opened(rig)
    with pytest.raises(SafetyError,match='DEMO_ACCOUNT_MISMATCH'):
        reader.request('trader',ctidTraderAccountId=1)
    with pytest.raises(SafetyError,match='OUTBOUND_SCHEMA_REJECTED'):
        validate_frame({'clientMsgId':'x','payloadType':2121,'payload':{'ctidTraderAccountId':ACCOUNT,'volume':100}})


def test_real_transport_fixed_endpoint_no_redirects_or_proxy(monkeypatch):
    import websocket
    calls=[]
    class Socket:
        def close(self):
            pass
        def getstatus(self):
            return 101
    def create(url,**kwargs):
        calls.append((url,kwargs))
        return Socket()
    monkeypatch.setattr(websocket,'create_connection',create)
    transport=DemoWebSocket()
    transport.connect()
    assert calls[0][0]=='wss://demo.ctraderapi.com:5036'
    assert calls[0][1]['redirect_limit']==0
    assert calls[0][1]['http_no_proxy']==['*']
    assert 'sslopt' not in calls[0][1]  # Certificate verification uses secure library defaults.
    transport.close()


def test_secure_config_and_atomic_verified_tokens(rig,private_dir):
    config,_,_,reader,token=rig
    data={'client_id':config.client_id,'client_secret':SECRET,'redirect_uri':config.redirect_uri,
        'demo_account_id':ACCOUNT,'expected_broker':'Example Broker','token_path':str(config.token_path),
        'application_approved':True}
    path=private_dir/'config.json'
    private_write(path,data)
    assert path.stat().st_mode & 0o777==0o600
    assert Config.load(path).scope=='accounts'
    assert SECRET not in repr(config)
    with pytest.raises(SafetyError,match='UNVERIFIED_GRANT'):
        token.save(config.token_path)
    reader.authorize(token)
    token.save(config.token_path)
    assert config.token_path.stat().st_mode & 0o777==0o600
    restored=Token.load(config.token_path)
    assert not restored.verified
    assert ACCESS not in repr(restored)
    os.chmod(path,0o644)
    with pytest.raises(SafetyError,match='PRIVATE_FILE_MODE_REQUIRED'):
        Config.load(path)


def test_private_storage_rejects_repo_and_symlinks(rig,private_dir):
    with pytest.raises(SafetyError,match='OUTSIDE_REPOSITORY'):
        private_write(Path.cwd()/'secrets'/'tokens.json',{})
    link=private_dir/'link.json'
    target=private_dir/'real.json'
    private_write(target,{'safe':True})
    link.symlink_to(target)
    with pytest.raises(SafetyError,match='SYMLINK'):
        private_read(link)


def make_flow(rig,**kwargs):
    config,_,_,reader,_=rig
    return OAuthFlow(config,reader.authorize,
        exchange_code=lambda config,code:Token(ACCESS,time.time()+3600),**kwargs)


def callback_path(flow,**params):
    state=parse_qs(urlsplit(flow.authorization_url()).query)['state'][0]
    return '/oauth/callback?'+urlencode(dict(state=state,code='SYNTHETIC_CODE',**params))


def test_oauth_url_state_callback_and_replay(rig):
    flow=make_flow(rig)
    query=parse_qs(urlsplit(flow.authorization_url()).query)
    assert query['scope']==['accounts']
    assert 'client_secret' not in query
    flow.callback(callback_path(flow))
    assert flow.done and flow.result=='AUTHORIZED_VIEW_ONLY'
    assert rig[0].token_path.exists()
    with pytest.raises(SafetyError,match='CALLBACK_EXPIRED'):
        flow.callback(callback_path(flow))


def test_oauth_wrong_state_does_not_exchange(rig):
    flow=make_flow(rig)
    flow.exchange_code=lambda *args:pytest.fail('invalid state exchanged')
    with pytest.raises(SafetyError,match='STATE_MISMATCH'):
        flow.callback('/oauth/callback?state=wrong&code=bad')
    assert not flow.done
    assert not rig[0].token_path.exists()


@pytest.mark.parametrize('query',['scope=trading','error=denied','code=x&code=y'])
def test_oauth_denial_scope_and_duplicate_parameters(rig,query):
    flow=make_flow(rig)
    state=parse_qs(urlsplit(flow.authorization_url()).query)['state'][0]
    with pytest.raises(SafetyError):
        flow.callback('/oauth/callback?state='+state+'&'+query)
    assert not rig[0].token_path.exists()


def test_oauth_callback_expiry(rig):
    clock=Clock()
    flow=make_flow(rig,clock=clock.monotonic)
    clock.sleep(181)
    with pytest.raises(SafetyError,match='CALLBACK_EXPIRED'):
        flow.callback(callback_path(flow))


def test_oauth_trading_token_not_saved(rig):
    _,_,transport,_,_=rig
    def change(name,response):
        if name=='accounts':
            response['permissionScope']=1
        return response
    transport.change=change
    flow=make_flow(rig)
    with pytest.raises(SafetyError,match='OAUTH_AUTHORIZATION_FAILED'):
        flow.callback(callback_path(flow))
    assert not rig[0].token_path.exists()


@pytest.mark.parametrize('response',[{'errorCode':'SECRET_ERROR','description':SECRET},
    {'accessToken':ACCESS,'tokenType':'bearer','expiresIn':3600,'scope':'trading'},
    {'accessToken':ACCESS,'tokenType':'bearer','expiresIn':0},
    {'accessToken':ACCESS,'tokenType':9,'expiresIn':3600}])
def test_invalid_oauth_response(response):
    with pytest.raises(SafetyError) as error:
        Token.from_response(response)
    assert SECRET not in str(error.value)


def test_token_exchange_fixed_https_no_redirect_or_error_disclosure(rig,monkeypatch):
    from ctrader_readonly import oauth
    calls=[]
    class HTTP:
        def __init__(self,host,timeout):
            calls.append(host)
        def request(self,method,path,headers):
            calls.append((method,path))
        def getresponse(self):
            class Response:
                status=302
                def read(self,*args):
                    pytest.fail('Redirect body read')
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(oauth.http.client,'HTTPSConnection',HTTP)
    with pytest.raises(SafetyError,match='OAUTH_EXCHANGE_FAILED'):
        exchange(rig[0],'SYNTHETIC_CODE')
    assert calls[0]=='openapi.ctrader.com'
    assert calls[1][0]=='GET'
    assert calls[1][1].startswith('/apps/token?')


class HTTPConnection:
    def __init__(self,request):
        self.request=BytesIO(request)
        self.response=b''
    def makefile(self,*args):
        return self.request
    def sendall(self,data):
        self.response+=data


def test_status_http_get_and_no_write_methods(rig):
    reader=opened(rig)
    for request,code in [(b'GET /status HTTP/1.0\r\n\r\n',b'200'),
                          (b'POST /status HTTP/1.0\r\n\r\n',b'501'),
                          (b'GET /tokens HTTP/1.0\r\n\r\n',b'404')]:
        connection=HTTPConnection(request)
        status_handler(reader)(connection,('127.0.0.1',0),None)
        assert code in connection.response.split(b'\r\n')[0]
        for secret in (SECRET,ACCESS,PRIVATE,str(ACCOUNT)):
            assert secret.encode() not in connection.response


def test_callback_http_redacted_and_host_checked(rig):
    flow=make_flow(rig)
    path=callback_path(flow)
    request=('GET '+path+' HTTP/1.0\r\nHost: attacker.invalid\r\n\r\n').encode()
    connection=HTTPConnection(request)
    flow.handler()(connection,('127.0.0.1',0),None)
    assert b'400' in connection.response.split(b'\r\n')[0]
    assert b'SYNTHETIC_CODE' not in connection.response
    assert not rig[0].token_path.exists()


def test_reconnect_reauthorizes_and_resnapshots(rig):
    _,clock,t,reader,token=rig
    class Stop:
        stopped=False
        waits=[]
        def is_set(self):
            return self.stopped
        def wait(self,delay):
            self.waits.append(delay)
            clock.sleep(delay)
            if len(self.waits)==2:
                self.stopped=True
    stop=Stop()
    def load():
        if stop.waits:
            t.failure=None
        return token
    original=reader.poll
    def poll(timeout=0.5):
        if t.opened==1:
            t.failure='receive'
        elif reader.status()['history_complete']:
            stop.stopped=True
        original(timeout)
    reader.poll=poll
    reader.run(load,stop,jitter=lambda a,b:0)
    assert t.opened==2
    assert stop.waits==[1]
    assert sum(f['payloadType']==2149 for _,f in t.sent)==2
    assert sum(f['payloadType']==2124 for _,f in t.sent)==4
    assert reader.status()['health']=='DISCONNECTED'


def test_backoff_bounded_and_no_browser(rig):
    _,clock,t,reader,token=rig
    t.failure='connect'
    class Stop:
        waits=[]
        def is_set(self):
            return len(self.waits)>=9
        def wait(self,delay):
            self.waits.append(delay)
            clock.sleep(delay)
    stop=Stop()
    reader.run(lambda:token,stop,jitter=lambda a,b:0)
    assert stop.waits==[1,2,4,8,16,32,60,60,60]


def test_cli_failure_has_no_traceback_or_config_data(capsys,private_dir):
    assert main(['observe','--config',str(private_dir/'not-present.json')])==1
    output=capsys.readouterr()
    assert 'UNAVAILABLE' in output.out
    assert str(private_dir) not in output.out
    assert not output.err


def test_demo_transport_rejects_redirect_before_any_authorization(monkeypatch):
    import websocket
    class Redirect:
        closed=False
        def getstatus(self):
            return 302
        def close(self):
            self.closed=True
        def send(self,*args):
            pytest.fail('Credentials sent before WebSocket upgrade')
    socket=Redirect()
    monkeypatch.setattr(websocket,'create_connection',lambda *args,**kwargs:socket)
    transport=DemoWebSocket()
    with pytest.raises(SafetyError,match='NETWORK_CONNECT_FAILED'):
        transport.connect()
    assert socket.closed
    assert transport.socket is None


@pytest.mark.parametrize('name,field,value',[
    ('trader','accountType',None),('trader','accessRights',3),
    ('trader','brokerName','Other Broker'),('trader','depositAssetId',999),
    ('symbol','minVolume',0),('symbol','stepVolume',0),('symbol','digits',20),
    ('symbol','distanceSetIn',None),('symbol','slDistance',None),
    ('symbol','symbolId',999),('symbol','tradingMode',99),
])
def test_missing_or_invalid_metadata_blocks_health(rig,name,field,value):
    _,_,t,reader,token=rig
    reader.authorize(token)
    def change(kind,response):
        if kind==name:
            metadata=response['trader'] if name=='trader' else response['symbol'][0]
            if value is None:
                metadata.pop(field,None)
            else:
                metadata[field]=value
        return response
    t.change=change
    with pytest.raises(SafetyError):
        reader.refresh()
    assert reader.status()['health']=='DISCONNECTED'


def test_snapshot_changes_are_not_published(rig):
    _,_,t,reader,token=rig
    reader.authorize(token)
    seen=0
    def change(name,response):
        nonlocal seen
        if name=='reconcile':
            seen+=1
            if seen==2:
                response['position']=[]
        return response
    t.change=change
    with pytest.raises(SafetyError,match='SNAPSHOT_CHANGED_RETRY'):
        reader.refresh()
    assert not reader.status()['snapshot_fresh']
    assert not reader.status()['margin_information_available']


def test_server_token_revocation_disconnects(rig):
    reader=opened(rig)
    t=rig[2]
    t.inbox.append({'payloadType':2147,'payload':{'ctidTraderAccountIds':[ACCOUNT]}})
    with pytest.raises(SafetyError,match='BROKER_SESSION_INVALIDATED'):
        reader.poll()
    assert not reader.status()['view_authorized']


def test_reconnect_rejects_old_epoch_response(rig):
    _,_,t,reader,token=rig
    reader.authorize(token)
    old=t.sent[-1][1]['clientMsgId']
    reader.authorize(token)
    def inject(name,response):
        response['clientMsgId']=old
        return response
    t.inject=inject
    with pytest.raises(SafetyError,match='CORRELATION_MISMATCH'):
        reader.refresh()


def test_callback_success_http_never_returns_tokens(rig):
    flow=make_flow(rig)
    path=callback_path(flow)
    request=('GET '+path+' HTTP/1.0\r\nHost: 127.0.0.1:18768\r\n\r\n').encode()
    connection=HTTPConnection(request)
    flow.handler()(connection,('127.0.0.1',0),None)
    assert b'200' in connection.response.split(b'\r\n')[0]
    assert b'no-store' in connection.response
    for secret in (SECRET,ACCESS,PRIVATE,'SYNTHETIC_CODE'):
        assert secret.encode() not in connection.response
    assert rig[0].token_path.exists()


def test_load_token_rejects_expiry_and_trading_scope(rig):
    config,_,_,_,_=rig
    private_write(config.token_path,{'scope':'trading','access_token':ACCESS,'expires_at':time.time()+3600})
    with pytest.raises(SafetyError,match='VIEW_SCOPE_REQUIRED'):
        Token.load(config.token_path)
    private_write(config.token_path,{'scope':'accounts','access_token':ACCESS,'expires_at':time.time()-1})
    with pytest.raises(SafetyError,match='TOKEN_EXPIRED'):
        Token.load(config.token_path)


@pytest.mark.parametrize('kind,payload',[
    (2116,{'ctidTraderAccountId':ACCOUNT,'symbolId':SYMBOL}),
    (2139,{'ctidTraderAccountId':ACCOUNT,'symbolId':[SYMBOL],'volume':[100]}),
    (2175,{'ctidTraderAccountId':ACCOUNT,'fromTimestamp':20,'toTimestamp':10}),
])
def test_outbound_documented_field_shapes(kind,payload):
    with pytest.raises(SafetyError,match='OUTBOUND_SCHEMA_REJECTED'):
        validate_frame({'payloadType':kind,'clientMsgId':'synthetic','payload':payload})
