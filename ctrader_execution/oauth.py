"""Separately approved trading OAuth; never invoked by observers or imports."""
from dataclasses import dataclass,field
import hashlib
import hmac
import http.client
import json
import math
import secrets
import time
from urllib.parse import urlencode,urlsplit,parse_qs
from http.server import BaseHTTPRequestHandler

from ctrader_readonly.config import Config,SafetyError,private_path,private_read,private_write
from ctrader_readonly.protocol import integer
from .approvals import paths,load_approval,profile


@dataclass(repr=False)
class TradingToken:
    access:str=field(repr=False)
    expires_at:float
    account_id:int|None=field(default=None,repr=False)
    verified:bool=False
    scope:str='trading'

    def require_current(self,now=None):
        if self.scope!='trading' or not isinstance(self.access,str) or not self.access or len(self.access)>16384 or type(self.expires_at) not in (int,float) or not math.isfinite(self.expires_at):raise SafetyError('INVALID_TRADING_TOKEN')
        if (time.time() if now is None else now)+30>=self.expires_at:raise SafetyError('TRADING_TOKEN_EXPIRED_REAUTHORIZE')

    @classmethod
    def from_response(cls,value,now=None):
        if not isinstance(value,dict) or value.get('errorCode') or value.get('error') or value.get('scope','trading')!='trading':raise SafetyError('TRADING_OAUTH_EXCHANGE_REJECTED')
        if not isinstance(value.get('tokenType'),str) or value['tokenType'].lower()!='bearer':raise SafetyError('INVALID_TRADING_TOKEN_RESPONSE')
        expiry=integer(value.get('expiresIn'),31)
        if expiry>366*86400:raise SafetyError('INVALID_TRADING_TOKEN_RESPONSE')
        token=cls(value.get('accessToken'),(time.time() if now is None else now)+expiry)
        token.require_current(time.time() if now is None else now);return token

    def save(self,path,view_path):
        if private_path(path).resolve()==private_path(view_path).resolve():raise SafetyError('SEPARATE_TRADING_TOKEN_STORE_REQUIRED')
        self.require_current()
        if self.verified is not True or self.account_id is None:raise SafetyError('UNVERIFIED_TRADING_GRANT')
        private_write(path,{'scope':'trading','access_token':self.access,'expires_at':self.expires_at,'demo_account_id':self.account_id})

    @classmethod
    def load(cls,path,view_path,account):
        if private_path(path).resolve()==private_path(view_path).resolve():raise SafetyError('SEPARATE_TRADING_TOKEN_STORE_REQUIRED')
        value=private_read(path)
        if set(value)!={'scope','access_token','expires_at','demo_account_id'} or value['scope']!='trading' or value['demo_account_id']!=account:raise SafetyError('TRADING_TOKEN_ROLE_OR_ACCOUNT_MISMATCH')
        token=cls(value['access_token'],value['expires_at'],value['demo_account_id']);token.require_current();return token


def exchange_trading(config,code):
    connection=http.client.HTTPSConnection('openapi.ctrader.com',timeout=10);connection.set_debuglevel(0)
    query=urlencode({'grant_type':'authorization_code','code':code,'redirect_uri':config.redirect_uri,
        'client_id':config.client_id,'client_secret':config.client_secret})
    try:
        connection.request('GET','/apps/token?'+query,headers={'Accept':'application/json'})
        response=connection.getresponse();data=response.read(65537)
        if response.status!=200 or len(data)>65536:raise SafetyError('TRADING_OAUTH_EXCHANGE_REJECTED')
        return TradingToken.from_response(json.loads(data))
    except Exception:raise SafetyError('TRADING_OAUTH_EXCHANGE_REJECTED') from None
    finally:connection.close()


class TradingOAuthFlow:
    def __init__(self,config_path,client_factory=None,exchange=exchange_trading,clock=time.monotonic):
        self.config_path=config_path;self.config=Config.load(config_path);profile(self.config)
        self.authority=load_approval(config_path,'OAUTH_ONLY')
        self.paths=paths(config_path);self.exchange=exchange;self.clock=clock
        self.state=secrets.token_urlsafe(32);self.created=clock();self.done=False;self.success=False
        if client_factory is None:
            from .transport import TradeClient
            client_factory=TradeClient
        self.client_factory=client_factory

    def authorization_url(self):
        load_approval(self.config_path,'OAUTH_ONLY')
        return 'https://id.ctrader.com/my/settings/openapi/grantingaccess/?'+urlencode({
            'client_id':self.config.client_id,'redirect_uri':self.config.redirect_uri,'scope':'trading','product':'web','state':self.state})

    def callback(self,path):
        if self.done:raise SafetyError('TRADING_OAUTH_CALLBACK_REPLAYED')
        if self.clock()-self.created>180:raise SafetyError('TRADING_OAUTH_CALLBACK_EXPIRED')
        parts=urlsplit(path);params=parse_qs(parts.query,keep_blank_values=True)
        if parts.path!='/oauth/callback' or set(params)-{'state','code','error','error_description','scope'} or any(len(v)!=1 for v in params.values()):raise SafetyError('TRADING_OAUTH_CALLBACK_REJECTED')
        supplied=params.get('state',[''])[0]
        if not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),hashlib.sha256(self.state.encode()).digest()):raise SafetyError('TRADING_OAUTH_STATE_MISMATCH')
        self.done=True
        if params.get('scope',['trading'])[0]!='trading' or 'error' in params or not params.get('code',[''])[0] or len(params['code'][0])>4096:raise SafetyError('TRADING_OAUTH_DENIED')
        authority=load_approval(self.config_path,'OAUTH_ONLY')
        client=None
        try:
            token=self.exchange(self.config,params['code'][0])
            client=self.client_factory(self.config,authority)
            client.authorize(token)  # Exact remote TRADE + pinned demo + identity.
            token.account_id=self.config.demo_account_id
            token.save(self.paths['token'],self.config.token_path)
            self.success=True
        finally:
            if client:client.disconnect('TRADING_OAUTH_FINISHED')

    def handler(self):
        flow=self;host=urlsplit(self.config.redirect_uri).netloc
        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    if self.headers.get('Host')!=host or len(self.path)>8192:raise SafetyError('TRADING_CALLBACK_HOST_REJECTED')
                    flow.callback(self.path);code=200;body=b'Separate demo trading token verified and saved privately. No order was sent.'
                except Exception:code=400;body=b'Trading authorization unsuccessful; do not share callback URLs.'
                self.send_response(code);self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer')
                self.send_header('Content-Security-Policy',"default-src 'none'; frame-ancestors 'none'")
                self.send_header('Content-Type','text/plain');self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        return Callback

    def serve(self):
        from ctrader_readonly.__main__ import LocalHTTPServer,open_browser_private
        parsed=urlsplit(self.config.redirect_uri)
        with LocalHTTPServer(('127.0.0.1',parsed.port),self.handler()) as server:
            server.timeout=.5
            if not open_browser_private(self.authorization_url()):raise SafetyError('BROWSER_OPEN_FAILED')
            while not self.done and self.clock()-self.created<=180:server.handle_request()
        if not self.success:raise SafetyError('TRADING_OAUTH_NOT_COMPLETED')
