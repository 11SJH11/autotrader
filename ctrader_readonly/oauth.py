"""One-use loopback OAuth flow; secrets never enter process args or output.

The documented token endpoint uses GET. Requests/URLs/errors must never be logged.
Only a server-confirmed SCOPE_VIEW grant may be saved.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from .config import SafetyError, private_read, private_write
from .protocol import integer


@dataclass(repr=False)
class Token:
    access: str = field(repr=False)
    expires_at: float
    verified: bool = False

    def require_current(self,now=None):
        if not isinstance(self.access,str) or not self.access or type(self.expires_at) not in (int,float) or not math.isfinite(self.expires_at):
            raise SafetyError('INVALID_TOKEN')
        if (time.time() if now is None else now)+30 >= self.expires_at:
            raise SafetyError('TOKEN_EXPIRED_REAUTHORIZE')

    @classmethod
    def from_response(cls,response,now=None):
        if not isinstance(response,dict) or response.get('errorCode') or response.get('error'):
            raise SafetyError('OAUTH_AUTHORIZATION_FAILED')
        if response.get('scope','accounts') != 'accounts':
            raise SafetyError('VIEW_SCOPE_REQUIRED')
        if not isinstance(response.get('tokenType'),str) or response['tokenType'].lower() != 'bearer':
            raise SafetyError('INVALID_TOKEN_RESPONSE')
        access=response.get('accessToken')
        if not isinstance(access,str) or not access or len(access)>16384:
            raise SafetyError('INVALID_TOKEN_RESPONSE')
        expires=integer(response.get('expiresIn'),31)
        if expires>366*86400:
            raise SafetyError('INVALID_TOKEN_RESPONSE')
        return cls(access,(time.time() if now is None else now)+expires)

    def save(self,path):
        self.require_current()
        if not self.verified:
            raise SafetyError('UNVERIFIED_GRANT')
        private_write(path,{'access_token':self.access,'expires_at':self.expires_at,'scope':'accounts'})

    @classmethod
    def load(cls,path):
        value=private_read(path)
        if set(value) != {'access_token','expires_at','scope'} or value['scope']!='accounts':
            raise SafetyError('VIEW_SCOPE_REQUIRED')
        if (not isinstance(value['access_token'],str) or not value['access_token']
                or type(value['expires_at']) not in (int,float)):
            raise SafetyError('INVALID_TOKEN_FILE')
        if not math.isfinite(value['expires_at']):
            raise SafetyError('INVALID_TOKEN_FILE')
        token=cls(value['access_token'],value['expires_at'])
        token.require_current()
        return token  # Scope must be verified remotely on every connection.


def exchange(config,code):
    config.require_approved()
    # Fixed host, no redirect following, no ambient proxies, no HTTP debug output.
    connection=http.client.HTTPSConnection('openapi.ctrader.com',timeout=10)
    query=urlencode({'grant_type':'authorization_code','code':code,
        'redirect_uri':config.redirect_uri,'client_id':config.client_id,'client_secret':config.client_secret})
    try:
        connection.request('GET','/apps/token?'+query,headers={'Accept':'application/json'})
        response=connection.getresponse()
        if response.status!=200:
            raise SafetyError('OAUTH_EXCHANGE_FAILED')
        data=response.read(65537)
        if len(data)>65536:
            raise SafetyError('OAUTH_EXCHANGE_FAILED')
        return Token.from_response(json.loads(data))
    except SafetyError:
        raise
    except Exception:
        raise SafetyError('OAUTH_EXCHANGE_FAILED') from None
    finally:
        connection.close()


class OAuthFlow:
    def __init__(self,config,verify,exchange_code=exchange,clock=time.monotonic):
        config.require_approved()
        self.config,self.verify,self.exchange_code,self.clock=config,verify,exchange_code,clock
        self._state=secrets.token_urlsafe(32)
        self._created=clock()
        self.done=False
        self.result='PENDING'
        self.failure_stage=None

    def authorization_url(self):
        return 'https://id.ctrader.com/my/settings/openapi/grantingaccess/?'+urlencode({
            'client_id':self.config.client_id,'redirect_uri':self.config.redirect_uri,
            'scope':'accounts','product':'web','state':self._state})

    def callback(self,path):
        if self.done or self.clock()-self._created>180:
            raise SafetyError('OAUTH_CALLBACK_EXPIRED')
        parsed=urlsplit(path)
        params=parse_qs(parsed.query,keep_blank_values=True)
        if parsed.path!='/oauth/callback' or set(params)-{'code','state','error','error_description','scope'}:
            raise SafetyError('OAUTH_CALLBACK_REJECTED')
        if any(len(v)!=1 for v in params.values()):
            raise SafetyError('OAUTH_CALLBACK_REJECTED')
        state=params.get('state',[''])[0]
        if not hmac.compare_digest(hashlib.sha256(state.encode()).digest(),hashlib.sha256(self._state.encode()).digest()):
            raise SafetyError('OAUTH_STATE_MISMATCH')
        self.done=True  # Valid state consumes this attempt, even on denial/failure.
        stage='CALLBACK_AUTHORIZATION'
        try:
            if params.get('scope',['accounts'])[0]!='accounts':
                raise SafetyError('VIEW_SCOPE_REQUIRED')
            code=params.get('code',[''])[0]
            if 'error' in params or not code or len(code)>4096:
                raise SafetyError('OAUTH_AUTHORIZATION_FAILED')
            stage='TOKEN_EXCHANGE'
            token=self.exchange_code(self.config,code)
            stage='GRANT_VERIFICATION'
            self.complete(token)
        except Exception:
            # Fixed stage labels only: never copy provider errors or responses.
            self.failure_stage=stage
            self.result='OAUTH_AUTHORIZATION_FAILED'
            raise SafetyError('OAUTH_AUTHORIZATION_FAILED') from None

    def complete(self,token):
        self.verify(token)  # Must validate scope before any token storage.
        token.save(self.config.token_path)
        self.result='AUTHORIZED_VIEW_ONLY'

    def handler(self):
        flow=self
        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):
                # Do not use send_error: it may interpolate paths or raw messages.
                try:
                    if len(self.path)>8192 or self.headers.get('Host')!=urlsplit(flow.config.redirect_uri).netloc:
                        raise SafetyError('OAUTH_CALLBACK_REJECTED')
                    flow.callback(self.path)
                    code,body=200,b'View-only authorization complete. You may close this window.'
                except Exception:
                    code,body=400,b'Authorization unsuccessful. Restart the local authorization flow.'
                self.send_response(code)
                self.send_header('Content-Type','text/plain; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('Referrer-Policy','no-referrer')
                self.send_header('Content-Security-Policy',"default-src 'none'; frame-ancestors 'none'")
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self,*args):
                pass
        return Callback

    def serve(self,open_browser):
        url=urlsplit(self.config.redirect_uri)
        # Bind successfully before opening a browser; never print the OAuth URL.
        from .__main__ import LocalHTTPServer
        with LocalHTTPServer(('127.0.0.1',url.port),self.handler()) as server:
            server.timeout=0.5
            if not open_browser(self.authorization_url()):
                raise SafetyError('BROWSER_OPEN_FAILED')
            while not self.done and self.clock()-self._created<=180:
                server.handle_request()
        if not self.done or self.result!='AUTHORIZED_VIEW_ONLY':
            raise SafetyError('OAUTH_AUTHORIZATION_FAILED')
