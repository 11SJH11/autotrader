"""Discovery tests use fabricated credentials/accounts and no network/browser."""
from dataclasses import replace
import json
from urllib.parse import parse_qs,urlsplit,urlencode
import pytest

from ctrader_readonly.config import Config,SafetyError,private_write,private_read
from ctrader_readonly.discovery import DiscoveryFlow
from ctrader_readonly.oauth import Token
from test_ctrader import private_dir,rig,MockTransport,Clock,ACCOUNT,ACCESS,SECRET,HTTPConnection

LOGIN=12345001  # Fabricated displayed login; distinct from fabricated API ID.


def setup(rig):
    config,clock,t,reader,token=rig
    path=config.token_path.parent/'config.json'
    data={'client_id':config.client_id,'client_secret':SECRET,'redirect_uri':config.redirect_uri,
        'demo_account_id':None,'expected_broker':config.expected_broker,
        'token_path':str(config.token_path),'application_approved':True,'scope':'accounts'}
    private_write(path,data)
    original=t.change
    def change(name,response):
        if name=='accounts':
            response['ctidTraderAccount'][0]['traderLogin']=LOGIN
            response['ctidTraderAccount'].extend([
                {'ctidTraderAccountId':999001,'traderLogin':LOGIN,'brokerTitleShort':'Example Broker','isLive':True},
                {'ctidTraderAccountId':999002,'traderLogin':LOGIN,'brokerTitleShort':'Other Broker','isLive':False}])
        return original(name,response)
    t.change=change
    flow=DiscoveryFlow(path,reader,exchange_code=lambda *_:token,clock=clock.monotonic)
    return flow,path,t,reader,token


def callback(flow):
    state=parse_qs(urlsplit(flow.authorization_url()).query)['state'][0]
    flow.callback('/oauth/callback?'+urlencode({'state':state,'code':'SYNTHETIC_CODE'}))


def choice(flow):
    return {'csrf':[flow._selection],'choice':[next(iter(flow.candidates))],
            'display_login':[str(LOGIN)],'confirm':['yes']}


def test_discovery_probes_only_view_grant_and_never_selects_automatically(rig):
    flow,path,t,reader,token=setup(rig)
    callback(flow)
    assert len(flow.candidates)==1
    assert not token.verified and not reader.status()['view_authorized']
    assert not reader.config.token_path.exists()
    assert private_read(path)['demo_account_id'] is None
    assert [f['payloadType'] for _,f in t.sent]==[2100,2149]
    html=flow.form().decode()
    assert 'checked' not in html
    assert str(LOGIN) in html and str(ACCOUNT) not in html
    assert SECRET not in html and ACCESS not in html


def test_explicit_selection_pins_api_id_and_display_login_privately(rig):
    flow,path,t,reader,token=setup(rig);callback(flow)
    params=choice(flow)
    flow.select(params)
    data=private_read(path)
    assert data['demo_account_id']==ACCOUNT and data['expected_demo_login']==LOGIN
    assert data['client_secret']==SECRET
    assert path.stat().st_mode & 0o777==0o600
    assert reader.config.token_path.stat().st_mode & 0o777==0o600
    assert flow.selection_done and token.verified
    auth=[f for _,f in t.sent if f['payloadType']==2102]
    assert len(auth)==1 and auth[0]['payload']['ctidTraderAccountId']==ACCOUNT
    with pytest.raises(SafetyError,match='REPLAYED'):flow.select(params)


@pytest.mark.parametrize('field,value', [('csrf','wrong'),('choice','forged-live-account'),('display_login','wrong'),('display_login','12345002'),('confirm','no')])
def test_invalid_local_confirmation_never_persists_or_authorizes(rig,field,value):
    flow,path,t,reader,token=setup(rig);callback(flow)
    params=choice(flow);params[field]=[value]
    with pytest.raises(SafetyError):flow.select(params)
    assert private_read(path)['demo_account_id'] is None
    assert not reader.config.token_path.exists()
    assert not any(f['payloadType']==2102 for _,f in t.sent)


def test_configuration_change_and_selection_timeout_fail_closed(rig):
    flow,path,t,reader,token=setup(rig);callback(flow)
    data=private_read(path);data['expected_broker']='Changed';private_write(path,data)
    with pytest.raises(SafetyError,match='CONFIGURATION_CHANGED'):flow.select(choice(flow))
    rig[1].sleep(181)
    with pytest.raises(SafetyError,match='EXPIRED'):flow.select(choice(flow))
    assert not reader.config.token_path.exists()


@pytest.mark.parametrize('scope',[1,'SCOPE_TRADE',None])
def test_trade_or_unknown_scope_never_discovered_or_saved(rig,scope):
    flow,path,t,reader,token=setup(rig)
    prior=t.change
    def change(name,response):
        response=prior(name,response)
        if name=='accounts':response['permissionScope']=scope
        return response
    t.change=change
    with pytest.raises(SafetyError):callback(flow)
    assert not flow.candidates and not token.verified
    assert not reader.config.token_path.exists()
    assert not any(f['payloadType']==2102 for _,f in t.sent)


def test_missing_pin_only_allowed_in_explicit_discovery_mode(rig):
    flow,path,t,reader,token=setup(rig)
    with pytest.raises(SafetyError,match='ALLOWLIST'):Config.load(path)
    assert Config.load(path,discovery=True).discovery_only
    with pytest.raises(SafetyError,match='EXPLICIT_DEMO_SELECTION'):reader.authorize(token)
    assert not t.sent


def test_account_changed_to_live_between_probe_and_confirmation(rig):
    flow,path,t,reader,token=setup(rig);callback(flow)
    prior=t.change
    def change(name,response):
        response=prior(name,response)
        if name=='accounts':response['ctidTraderAccount'][0]['isLive']=True
        return response
    t.change=change
    with pytest.raises(SafetyError,match='DEMO_ACCOUNT_REQUIRED'):flow.select(choice(flow))
    assert private_read(path)['demo_account_id'] is None
    assert not reader.config.token_path.exists()
    assert not any(f['payloadType']==2102 for _,f in t.sent)


def test_discovery_http_selection_requires_origin_host_cookie_and_csrf(rig):
    flow,path,t,reader,token=setup(rig);callback(flow)
    body=urlencode({k:v[0] for k,v in choice(flow).items()})
    serve=flow.handler()
    for cookie,origin in [('', 'http://127.0.0.1:18768'),('autotrader_discovery=wrong','http://127.0.0.1:18768'),('autotrader_discovery='+flow._browser,'https://attacker.invalid')]:
        request=f'POST /discovery/select HTTP/1.0\r\nHost: 127.0.0.1:18768\r\nOrigin: {origin}\r\nCookie: {cookie}\r\nContent-Length: {len(body)}\r\n\r\n{body}'
        connection=HTTPConnection(request.encode());serve(connection,('127.0.0.1',0),None)
        assert b'400' in connection.response.split(b'\r\n')[0]
        assert SECRET.encode() not in connection.response and ACCESS.encode() not in connection.response
    # Other local clients cannot even retrieve the selection form/capability.
    c=HTTPConnection(b'GET /discovery/choose HTTP/1.0\r\nHost: 127.0.0.1:18768\r\n\r\n')
    serve(c,('127.0.0.1',0),None)
    assert b'400' in c.response.split(b'\r\n')[0] and str(LOGIN).encode() not in c.response
    assert private_read(path)['demo_account_id'] is None


def test_valid_browser_confirmation_success_response_excludes_ids_and_tokens(rig):
    flow,path,t,reader,token=setup(rig);callback(flow)
    body=urlencode({k:v[0] for k,v in choice(flow).items()})
    request=f'POST /discovery/select HTTP/1.0\r\nHost: 127.0.0.1:18768\r\nOrigin: http://127.0.0.1:18768\r\nCookie: autotrader_discovery={flow._browser}\r\nContent-Length: {len(body)}\r\n\r\n{body}'
    connection=HTTPConnection(request.encode());flow.handler()(connection,('127.0.0.1',0),None)
    assert b'200' in connection.response.split(b'\r\n')[0]
    for private in (SECRET,ACCESS,str(ACCOUNT),str(LOGIN)):
        assert private.encode() not in connection.response


def test_discovery_failure_diagnostics_are_fixed_codes_only(rig):
    flow,path,t,reader,token=setup(rig)
    def fail(_):raise SafetyError('private-provider-text '+ACCESS)
    reader.discover=fail
    with pytest.raises(SafetyError):callback(flow)
    assert flow.failure_stage=='GRANT_VERIFICATION'
    assert flow.discovery_failure=='GRANT_DISCOVERY_FAILED'
    assert not reader.config.token_path.exists()


def test_empty_application_ack_may_omit_payload_but_requires_correlation(rig):
    flow,path,t,reader,token=setup(rig)
    def inject(name,response):
        if name=='application':response.pop('payload')
        return response
    t.inject=inject
    callback(flow)
    assert flow.candidates and not any(f['payloadType']==2102 for _,f in t.sent)


def test_omitted_application_ack_with_wrong_correlation_rejects(rig):
    flow,path,t,reader,token=setup(rig)
    def inject(name,response):
        if name=='application':
            response.pop('payload');response['clientMsgId']='wrong'
        return response
    t.inject=inject
    with pytest.raises(SafetyError):callback(flow)
    assert not flow.candidates and not token.verified
    assert not reader.config.token_path.exists()


@pytest.mark.parametrize('problem,code',[
    ('live','NO_AUTHORIZED_DEMO_ACCOUNTS'),
    ('broker','BROKER_IDENTITY_MISMATCH'),
    ('login','DISPLAY_LOGIN_MISMATCH'),
])
def test_discovery_filter_failures_are_distinct_and_never_authorize(rig,problem,code):
    flow,path,t,reader,token=setup(rig)
    prior=t.change
    def change(name,response):
        response=prior(name,response)
        if name=='accounts':
            response['ctidTraderAccount']=response['ctidTraderAccount'][:1]
            a=response['ctidTraderAccount'][0]
            if problem=='live':a['isLive']=True
            if problem=='broker':a['brokerTitleShort']='Different Demo Broker'
            if problem=='login':a['traderLogin']=LOGIN+1
        return response
    t.change=change
    if problem=='login':reader.config=replace(reader.config,expected_demo_login=LOGIN)
    with pytest.raises(SafetyError):callback(flow)
    assert flow.discovery_failure==code
    assert not token.verified and not reader.config.token_path.exists()
    assert not any(f['payloadType']==2102 for _,f in t.sent)


def test_minimal_identity_report_checks_pin_currency_and_reports_broker_without_mutation(rig):
    config,clock,t,reader,token=rig
    reader.authorize(token)
    reader.config=replace(config,expected_broker_name='Different Expected Name')
    report=reader.verify_identity()
    assert report['pinned_account_id_matches']
    assert report['currency']=='USD'
    assert not report['broker_name_matches_configuration']
    assert reader.config.expected_broker_name=='Different Expected Name'
    assert str(ACCOUNT) not in json.dumps(report)
    assert ACCESS not in json.dumps(report) and SECRET not in json.dumps(report)
    assert [f['payloadType'] for _,f in t.sent][-2:]==[2121,2112]


def post_selection(flow,params=None,*,cookie=None,origin='http://127.0.0.1:18768',host='127.0.0.1:18768'):
    body=urlencode({k:v[0] for k,v in (choice(flow) if params is None else params).items()})
    headers=f'Host: {host}\r\n'
    if origin is not None:headers+=f'Origin: {origin}\r\n'
    if cookie is not None:headers+=f'Cookie: {cookie}\r\n'
    request=f'POST /discovery/select HTTP/1.0\r\n{headers}Content-Length: {len(body)}\r\n\r\n{body}'
    c=HTTPConnection(request.encode());flow.handler()(c,('127.0.0.1',0),None)
    return c.response


def test_selection_form_origin_policy_excludes_callback_query(rig):
    flow,path,t,reader,token=setup(rig)
    state=parse_qs(urlsplit(flow.authorization_url()).query)['state'][0]
    query=urlencode({'state':state,'code':'SYNTHETIC_CODE'})
    c=HTTPConnection(f'GET /oauth/callback?{query} HTTP/1.0\r\nHost: 127.0.0.1:18768\r\n\r\n'.encode())
    flow.handler()(c,('127.0.0.1',0),None)
    headers,html=c.response.split(b'\r\n\r\n',1)
    assert b'Referrer-Policy: origin' in headers
    assert b'HttpOnly; SameSite=Strict; Path=/discovery' in headers
    assert b'Cache-Control: no-store' in headers
    assert b'SYNTHETIC_CODE' not in html and state.encode() not in html
    assert ('name="csrf" value="'+flow._selection+'"').encode() in html
    # Re-render another tab without rotating the attempt's token.
    before=flow._selection
    assert flow.form()==html and flow._selection==before
    response=post_selection(flow,cookie='autotrader_discovery='+flow._browser)
    assert b'200' in response.split(b'\r\n')[0]
    assert flow.selection_done


@pytest.mark.parametrize('origin,code',[(None,'SELECTION_ORIGIN_MISSING'),('null','SELECTION_ORIGIN_NULL'),
    ('http://localhost:18768','SELECTION_ORIGIN_MISMATCH'),('https://attacker.invalid','SELECTION_ORIGIN_MISMATCH')])
def test_origin_failure_diagnostics_preserve_strict_validation(rig,origin,code):
    flow,path,t,reader,token=setup(rig);callback(flow)
    response=post_selection(flow,cookie='autotrader_discovery='+flow._browser,origin=origin)
    assert code.encode() in response and flow.last_failure==code
    assert not flow.selection_consumed and not token.verified
    assert not any(f['payloadType']==2102 for _,f in t.sent)


@pytest.mark.parametrize('cookie,code',[(None,'SELECTION_SESSION_MISSING'),
    ('autotrader_discovery=old-attempt','SELECTION_SESSION_MISMATCH')])
def test_missing_or_other_attempt_cookie_rejects(rig,cookie,code):
    flow,path,t,reader,token=setup(rig);callback(flow)
    assert code.encode() in post_selection(flow,cookie=cookie)
    assert not reader.config.token_path.exists()


def test_multiple_tabs_same_attempt_one_success_then_replay(rig):
    flow,path,t,reader,token=setup(rig);callback(flow)
    tab1=choice(flow);tab2=choice(flow)
    cookie='autotrader_discovery='+flow._browser
    assert b'200' in post_selection(flow,tab1,cookie=cookie).split(b'\r\n')[0]
    assert b'SELECTION_TOKEN_REPLAYED' in post_selection(flow,tab2,cookie=cookie)
    assert len([f for _,f in t.sent if f['payloadType']==2102])==1


def test_stale_tab_new_attempt_cookie_does_not_validate_old_form(rig):
    flow,path,t,reader,token=setup(rig);callback(flow)
    old_params=choice(flow);old_cookie='autotrader_discovery='+flow._browser
    new=DiscoveryFlow(path,reader,exchange_code=lambda *_:token,clock=rig[1].monotonic)
    callback(new)
    assert new._selection!=flow._selection and new._browser!=flow._browser
    assert b'SELECTION_SESSION_MISMATCH' in post_selection(new,old_params,cookie=old_cookie)
    # A newer tab overwrites the cookie in the same browser; old hidden token still rejects.
    assert b'SELECTION_TOKEN_MISMATCH' in post_selection(new,old_params,cookie='autotrader_discovery='+new._browser)
    assert not new.selection_consumed and not reader.config.token_path.exists()


def test_expired_and_missing_tokens_have_distinct_codes(rig):
    flow,path,t,reader,token=setup(rig);callback(flow)
    params=choice(flow);params.pop('csrf')
    cookie='autotrader_discovery='+flow._browser
    assert b'SELECTION_TOKEN_MISSING' in post_selection(flow,params,cookie=cookie)
    rig[1].sleep(181)
    assert b'DEMO_SELECTION_EXPIRED' in post_selection(flow,cookie=cookie)
    assert not any(f['payloadType']==2102 for _,f in t.sent)


def test_valid_confirmation_consumes_token_even_if_broker_recheck_fails(rig):
    flow,path,t,reader,token=setup(rig);callback(flow)
    def fail(_):raise SafetyError('BROKER_REQUEST_REJECTED')
    reader.authorize=fail
    cookie='autotrader_discovery='+flow._browser
    assert b'BROKER_REQUEST_REJECTED' in post_selection(flow,cookie=cookie)
    assert flow.selection_stage=='DEMO_REAUTHORIZATION'
    assert b'SELECTION_TOKEN_REPLAYED' in post_selection(flow,cookie=cookie)
    assert not reader.config.token_path.exists()


def test_refresh_keeps_one_spot_subscription_per_connection_and_resubscribes_after_reconnect(rig):
    config,clock,t,reader,token=rig
    subscription_seen=False
    def reject_duplicate(name,response):
        nonlocal subscription_seen
        if name=='application':subscription_seen=False
        if name=='spots':
            if subscription_seen:return {'payloadType':2142,'payload':{'errorCode':'ALREADY_SUBSCRIBED'}}
            subscription_seen=True
        return response
    t.inject=reject_duplicate
    reader.authorize(token)
    reader.refresh()
    clock.sleep(15)
    reader.refresh()
    assert len([f for _,f in t.sent if f['payloadType']==2127])==1
    reader.disconnect('TEST_RECONNECT')
    reader.authorize(token)
    reader.refresh()
    assert len([f for _,f in t.sent if f['payloadType']==2127])==2
