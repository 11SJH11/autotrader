"""One-use local demo-account selection after view-only OAuth discovery.

No account auth occurs until an explicit radio selection, matching typed display
login and confirmation. Account IDs remain server-side and in private config.
"""
from dataclasses import replace
import hashlib
import hmac
from html import escape
import json
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs,urlsplit

from .config import Config,SafetyError,private_read,private_write
from .oauth import OAuthFlow,exchange
from .client import Reader


def digest(value):return hashlib.sha256(value.encode()).digest()
def equal(a,b):return hmac.compare_digest(digest(a),digest(b))
def serialized(value):return json.dumps(value,sort_keys=True,separators=(',',':'))


class DiscoveryFlow(OAuthFlow):
    def __init__(self,path,reader=None,exchange_code=exchange,clock=time.monotonic):
        self.path=path
        self.original=private_read(path)
        config=Config.load(path,discovery=True)
        self.reader=reader or Reader(config)
        self.reader.config=config
        self.candidates={};self.pending=None;self.selection_done=False
        self.selection_created=None
        self.selection_consumed=False
        self.last_failure=None
        self.discovery_failure=None
        self.selection_stage='LOCAL_CONFIRMATION'
        self._launch=secrets.token_urlsafe(32)
        self._selection=secrets.token_urlsafe(32)
        self._browser=secrets.token_urlsafe(32)
        super().__init__(config,lambda token:None,exchange_code,clock)

    def complete(self,token):
        try:
            candidates=self.reader.discover(token)
        except SafetyError as exc:
            allowed={'NO_MATCHING_DEMO_ACCOUNTS','AMBIGUOUS_ACCOUNT_LIST','VIEW_SCOPE_REQUIRED',
                'NO_AUTHORIZED_DEMO_ACCOUNTS','BROKER_IDENTITY_MISMATCH','DISPLAY_LOGIN_MISMATCH',
                'NETWORK_DISCONNECTED','REQUEST_TIMEOUT','BROKER_REQUEST_REJECTED',
                'UNEXPECTED_RESPONSE_SCHEMA','UNEXPECTED_RESPONSE_TYPE','RESPONSE_CORRELATION_MISMATCH',
                'HEARTBEAT_TIMEOUT'}
            self.discovery_failure=str(exc) if str(exc) in allowed else 'GRANT_DISCOVERY_FAILED'
            raise
        self.candidates={secrets.token_urlsafe(24):a for a in candidates}
        self.pending=token
        self.selection_created=self.clock()
        self.result='EXPLICIT_DEMO_SELECTION_REQUIRED'

    def form(self):
        rows=[]
        for key,candidate in self.candidates.items():
            # Deliberately no checked/preselected radio, even for one candidate.
            rows.append('<label><input required type="radio" name="choice" value="'+key+'"> DEMO · '+
                escape(candidate['broker'])+' · displayed login '+str(candidate['traderLogin'])+'</label><br>')
        return ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Confirm demo account</title>'
            '<h1>Confirm your cTrader demo account</h1><p>Read-only accounts scope. No trading permission.</p>'
            '<p>Match the displayed login and broker to your intended demo account. No account is selected automatically.</p>'
            '<form method="post" action="/discovery/select"><input type="hidden" name="csrf" value="'+self._selection+'">'+''.join(rows)+
            '<p><label>Type the selected displayed login: <input required name="display_login" inputmode="numeric" autocomplete="off"></label></p>'
            '<label><input required type="checkbox" name="confirm" value="yes"> I confirm this broker and DEMO account; authorize view-only reads.</label>'
            '<p><button type="submit">Confirm selected demo account</button></p></form></html>').encode()

    def select(self,params):
        if self.selection_done or self.selection_consumed:
            raise SafetyError('SELECTION_TOKEN_REPLAYED')
        if self.pending is None or self.selection_created is None or self.clock()-self.selection_created>180:
            raise SafetyError('DEMO_SELECTION_EXPIRED')
        if not params.get('csrf') or params['csrf']==['']:
            raise SafetyError('SELECTION_TOKEN_MISSING')
        if set(params)!={'csrf','choice','display_login','confirm'} or any(len(v)!=1 for v in params.values()):
            raise SafetyError('INVALID_DEMO_SELECTION')
        if not equal(params['csrf'][0],self._selection):raise SafetyError('SELECTION_TOKEN_MISMATCH')
        selected=self.candidates.get(params['choice'][0])
        if not selected or selected['isLive'] is not False or selected['broker']!=self.config.expected_broker:
            raise SafetyError('DEMO_SELECTION_REJECTED')
        supplied=params['display_login'][0]
        if not supplied.isascii() or not supplied.isdigit() or int(supplied)!=selected['traderLogin'] or params['confirm'][0]!='yes':
            raise SafetyError('DISPLAY_LOGIN_CONFIRMATION_REQUIRED')
        current=private_read(self.path)
        if not equal(serialized(current),serialized(self.original)):
            raise SafetyError('PRIVATE_CONFIGURATION_CHANGED')
        self.selection_consumed=True  # A valid confirmation gets one broker attempt.
        pinned=replace(self.config,demo_account_id=selected['ctidTraderAccountId'],
            expected_demo_login=selected['traderLogin'],discovery_only=False)
        self.reader.config=pinned
        # Re-probe view grant, broker/display login/isLive and authorize only this
        # explicit demo selection. A changed/revoked grant prevents persistence.
        self.selection_stage='DEMO_REAUTHORIZATION'
        self.reader.authorize(self.pending)
        if pinned.expected_currency is not None or pinned.expected_account_mode is not None:
            # New account profiles can pin currency/mode before private storage.
            # This reads trader/assets only and cannot grant trading permission.
            self.selection_stage='DEMO_PROFILE_VERIFICATION'
            self.reader.verify_identity()
        current['demo_account_id']=selected['ctidTraderAccountId']
        current['expected_demo_login']=selected['traderLogin']
        self.selection_stage='PRIVATE_CONFIGURATION_SAVE'
        private_write(self.path,current)
        self.selection_stage='PRIVATE_TOKEN_SAVE'
        self.pending.save(pinned.token_path)
        self.selection_done=True;self.result='DEMO_SELECTED_VIEW_ONLY'
        self.pending=None;self.candidates={}

    def handler(self):
        from http.server import BaseHTTPRequestHandler
        flow=self
        origin=urlsplit(flow.config.redirect_uri)
        base=origin.scheme+'://'+origin.netloc
        class LocalSelection(BaseHTTPRequestHandler):
            def headers_safe(self):return self.headers.get('Host')==origin.netloc
            def browser_safe(self):
                cookie=SimpleCookie()
                try:cookie.load(self.headers.get('Cookie',''))
                except Exception:return False
                value=cookie.get('autotrader_discovery')
                return bool(value and equal(value.value,flow._browser))
            def validate_browser(self):
                if not self.headers_safe():raise SafetyError('SELECTION_HOST_MISMATCH')
                cookie=SimpleCookie()
                try:cookie.load(self.headers.get('Cookie',''))
                except Exception:raise SafetyError('SELECTION_SESSION_MISMATCH') from None
                value=cookie.get('autotrader_discovery')
                if value is None:raise SafetyError('SELECTION_SESSION_MISSING')
                if not equal(value.value,flow._browser):raise SafetyError('SELECTION_SESSION_MISMATCH')
                supplied=self.headers.get('Origin')
                if supplied is None:raise SafetyError('SELECTION_ORIGIN_MISSING')
                if supplied=='null':raise SafetyError('SELECTION_ORIGIN_NULL')
                if supplied!=base:raise SafetyError('SELECTION_ORIGIN_MISMATCH')
            def respond(self,code,body,*,cookie=False,location=None,selection_form=False):
                self.send_response(code)
                self.send_header('Cache-Control','no-store')
                # no-referrer also turns a browser form POST's Origin into null.
                # origin preserves strict Origin validation while excluding the
                # callback path/query (including code/state) from Referer.
                self.send_header('Referrer-Policy','origin' if selection_form else 'no-referrer')
                self.send_header('Content-Security-Policy',"default-src 'none'; form-action 'self'; frame-ancestors 'none'")
                self.send_header('Content-Type','text/html; charset=utf-8')
                if cookie:self.send_header('Set-Cookie','autotrader_discovery='+flow._browser+'; HttpOnly; SameSite=Strict; Path=/discovery')
                if location:self.send_header('Location',location)
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_GET(self):
                try:
                    if not self.headers_safe() or len(self.path)>8192:raise SafetyError('LOCAL_REQUEST_REJECTED')
                    if self.path=='/discovery/start/'+flow._launch and not flow.done and flow.clock()-flow._created<=180:
                        return self.respond(302,b'',location=flow.authorization_url())
                    if urlsplit(self.path).path=='/oauth/callback':
                        flow.callback(self.path)
                        return self.respond(200,flow.form(),cookie=True,selection_form=True)
                    if self.path=='/discovery/choose' and self.browser_safe() and flow.pending and not flow.selection_done:
                        return self.respond(200,flow.form(),selection_form=True)
                    raise SafetyError('LOCAL_REQUEST_REJECTED')
                except Exception as exc:
                    flow.last_failure=str(exc) if isinstance(exc,SafetyError) and str(exc) in {'OAUTH_STATE_MISMATCH','OAUTH_CALLBACK_EXPIRED','OAUTH_AUTHORIZATION_FAILED','OAUTH_CALLBACK_REJECTED','NO_MATCHING_DEMO_ACCOUNTS'} else 'DISCOVERY_FAILED'
                    body=b'Authorization or discovery failed. Restart locally; do not share callback URLs.'
                    safe_code=flow.discovery_failure or flow.last_failure or 'DISCOVERY_FAILED'
                    body+=('<p>Diagnostic code: '+escape(safe_code)+'</p>').encode()
                    if flow.discovery_failure=='BROKER_IDENTITY_MISMATCH':
                        body=('<h1>Broker identity mismatch</h1><p>No account was selected or authorized.</p><p>Configured broker: '+
                            escape(flow.config.expected_broker)+'</p><p>Authorized demo broker names: '+
                            ', '.join(escape(s) for s in getattr(flow.reader,'discovery_brokers',[]))+
                            '</p><p>Verify the intended broker locally and correct expected_broker in your private configuration, then restart discovery. No automatic substitution is allowed.</p>').encode()
                    self.respond(400,body)
            def do_POST(self):
                try:
                    flow.selection_stage='LOCAL_CONFIRMATION'
                    if self.path!='/discovery/select':raise SafetyError('LOCAL_REQUEST_REJECTED')
                    self.validate_browser()
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=4096:raise SafetyError('LOCAL_REQUEST_REJECTED')
                    params=parse_qs(self.rfile.read(size).decode('ascii'),keep_blank_values=True)
                    flow.select(params)
                    self.respond(200,b'Demo account selected and pinned privately. View-only authorization complete. No trades were sent.')
                except Exception as exc:
                    allowed={'DEMO_SELECTION_EXPIRED','DISPLAY_LOGIN_CONFIRMATION_REQUIRED','PRIVATE_CONFIGURATION_CHANGED','DISPLAY_LOGIN_MISMATCH','BROKER_IDENTITY_MISMATCH','DEMO_ACCOUNT_REQUIRED','ACCOUNT_CURRENCY_MISMATCH','ACCOUNT_MODE_MISMATCH','INVALID_SCHEMA_INTEGER','UNEXPECTED_RESPONSE_SCHEMA','RESPONSE_ACCOUNT_MISMATCH','RESPONSE_CORRELATION_MISMATCH','BROKER_REQUEST_REJECTED','VIEW_SCOPE_REQUIRED','TOKEN_EXPIRED_REAUTHORIZE','NETWORK_CONNECT_FAILED','NETWORK_RECEIVE_FAILED','NETWORK_SEND_FAILED','REQUEST_TIMEOUT','SELECTION_TOKEN_MISSING','SELECTION_TOKEN_MISMATCH','SELECTION_TOKEN_REPLAYED','SELECTION_HOST_MISMATCH','SELECTION_SESSION_MISSING','SELECTION_SESSION_MISMATCH','SELECTION_ORIGIN_MISSING','SELECTION_ORIGIN_NULL','SELECTION_ORIGIN_MISMATCH'}
                    flow.last_failure=str(exc) if isinstance(exc,SafetyError) and str(exc) in allowed else 'SELECTION_FAILED'
                    self.respond(400,('Selection not accepted. Diagnostic code: '+flow.last_failure+'; stage: '+flow.selection_stage+'. Return to the local selection page or restart; do not share private values.').encode())
            def log_message(self,*args):pass
        return LocalSelection

    def serve(self,open_browser):
        from .__main__ import LocalHTTPServer
        parsed=urlsplit(self.config.redirect_uri)
        try:
            with LocalHTTPServer(('127.0.0.1',parsed.port),self.handler()) as server:
                server.timeout=.5
                if not open_browser(f'http://{parsed.netloc}/discovery/start/'+self._launch):
                    raise SafetyError('BROWSER_OPEN_FAILED')
                while not self.selection_done:
                    deadline=(self.selection_created if self.selection_created is not None else self._created)+180
                    if self.clock()>deadline or self.result=='OAUTH_AUTHORIZATION_FAILED':
                        raise SafetyError('DISCOVERY_OR_SELECTION_INCOMPLETE')
                    server.handle_request()
        finally:
            self.pending=None;self.candidates={}
            self.reader.disconnect('DISCOVERY_FINISHED')
