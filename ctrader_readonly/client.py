"""Synchronous single-owner observer; broker responses stay in memory only.

No execution engine imports. No broker mutations. No raw response/log persistence.
"""
import copy
from functools import wraps
from decimal import Decimal
from datetime import datetime, timezone
import random
import re
import threading
import time
from uuid import uuid4

from .config import SafetyError
from .protocol import DemoWebSocket, RateLimiter, SCHEMAS, enum, integer, validate_frame

ACCOUNT_MODES={'HEDGED':0,'NETTED':1,'SPREAD_BETTING':2}
ACCESS={'FULL_ACCESS':0,'CLOSE_ONLY':1,'NO_TRADING':2,'NO_LOGIN':3}
VIEW={'SCOPE_VIEW':0,'SCOPE_TRADE':1}
DISTANCE={'SYMBOL_DISTANCE_IN_POINTS':1,'SYMBOL_DISTANCE_IN_PERCENTAGE':2}


def rows(payload,key):
    result=payload.get(key,[])
    if not isinstance(result,list) or len(result)>100000 or not all(isinstance(x,dict) for x in result):
        raise SafetyError('UNEXPECTED_RESPONSE_SCHEMA')
    return result


def one(items):
    if len(items)!=1:
        raise SafetyError('MISSING_OR_AMBIGUOUS_METADATA')
    return items[0]


def money(item,key):
    # Optional fields are not silently treated as zero/default precision.
    digits=integer(item.get('moneyDigits'))
    if digits>12:
        raise SafetyError('INVALID_MONEY_PRECISION')
    return Decimal(integer(item.get(key),-(2**63)))/(Decimal(10)**digits)


def serial_io(method):
    @wraps(method)
    def wrapped(self,*args,**kwargs):
        with self._io:
            return method(self,*args,**kwargs)
    return wrapped


class Reader:
    def __init__(self,config,transport=None,clock=time.monotonic,wall=time.time,sleep=time.sleep):
        self.config=config
        self.transport=transport or DemoWebSocket()
        self.clock,self.wall,self.sleep=clock,wall,sleep
        self.limiter=RateLimiter(clock,sleep)
        self._lock=threading.RLock()
        self._io=threading.RLock()
        self._token=None
        self._connected=False
        self._authorized=False
        self._last_rx=None
        self._last_hb=None
        self._snapshot_at=None
        self._dirty=True
        self._event_revision=0
        self._symbol_id=None
        self._subscribed_symbol=None
        self._quotes={}
        self._account={}
        self._symbol={}
        self._pending=[]
        self._positions=[]
        self._orders=[]
        self._deals=[]
        self._margin={}
        self._history_complete=False
        self._last_error=None
        self._epoch=None
        self._request_seq=0
        self._operations={}
        self._metrics={key:0 for key in ('connect_attempts','connections','authorizations',
            'disconnects','recoveries','requests','history_requests','heartbeats_sent',
            'heartbeats_received','quote_events','api_errors','rate_limit_errors',
            'request_timeouts','history_errors','stale_quote_errors','snapshots')}
        self._recovery_pending=False

    def _count(self,key):
        with self._lock:self._metrics[key]+=1

    @serial_io
    def disconnect(self,reason='DISCONNECTED'):
        self.transport.close()
        with self._lock:
            if self._connected:self._metrics['disconnects']+=1
            self._connected=False
            self._authorized=False
            self._token=None
            self._dirty=True
            self._history_complete=False
            self._quotes={}
            self._subscribed_symbol=None
            self._last_error=reason

    def _send(self,name,payload):
        self._token.require_current(self.wall())
        self.limiter.acquire(name in ('orders','deals'))
        self._request_seq+=1
        cid=f'{self._epoch}:{self._request_seq}'
        frame={'clientMsgId':cid,'payloadType':SCHEMAS[name][0],'payload':payload}
        validate_frame(frame)
        self.transport.send(frame)
        self._count('requests')
        if name in ('orders','deals'):self._count('history_requests')
        if name=='heartbeat':
            self._count('heartbeats_sent')
            self._last_hb=self.clock()
        return cid

    def _heartbeat(self):
        if self.clock()-self._last_hb>=10:
            self._send('heartbeat',{})
        if self.clock()-self._last_rx>30:
            raise SafetyError('HEARTBEAT_TIMEOUT')

    def _event(self,kind,payload):
        if kind==51:
            self._count('heartbeats_received')
            return True
        if kind in (2147,2148,2164):
            raise SafetyError('BROKER_SESSION_INVALIDATED')
        if kind in (2131,2126,2123,2120,2141,2107):
            if integer(payload.get('ctidTraderAccountId'),1)!=self.config.demo_account_id:
                raise SafetyError('RESPONSE_ACCOUNT_MISMATCH')
            if not self._authorized:
                raise SafetyError('EVENT_BEFORE_AUTHORIZATION')
            if kind==2131:
                if integer(payload.get('symbolId'),1)!=self._symbol_id:
                    raise SafetyError('RESPONSE_SYMBOL_MISMATCH')
                timestamp=integer(payload.get('timestamp'))/1000
                if not -2 <= self.wall()-timestamp <= 10:
                    self._count('stale_quote_errors')
                    raise SafetyError('STALE_QUOTE')
                self._count('quote_events')
                with self._lock:
                    for side in ('bid','ask'):
                        if side in payload:
                            price=Decimal(integer(payload[side],1))/Decimal(100000)
                            old=self._quotes.get(side)
                            if old and timestamp<old[2]:
                                raise SafetyError('OUT_OF_ORDER_QUOTE')
                            self._quotes[side]=(price,self.clock(),timestamp)
                    if all(s in self._quotes for s in ('bid','ask')) and self._quotes['bid'][0]>self._quotes['ask'][0]:
                        raise SafetyError('CROSSED_QUOTE')
            else:
                with self._lock:
                    self._dirty=True
                    self._event_revision+=1
            return True
        return False

    @serial_io
    def request(self,name,**payload):
        if name not in SCHEMAS or name=='heartbeat':
            raise SafetyError('OUTBOUND_NOT_ALLOWED')
        if not self._connected:
            raise SafetyError('NOT_CONNECTED')
        if name not in ('application','accounts','account') and not self._authorized:
            raise SafetyError('VIEW_AUTHORIZATION_REQUIRED')
        if name=='account' and not self._token.verified:
            raise SafetyError('VIEW_AUTHORIZATION_REQUIRED')
        if name not in ('application','accounts'):
            if payload.get('ctidTraderAccountId')!=self.config.demo_account_id:
                raise SafetyError('DEMO_ACCOUNT_MISMATCH')
        try:
            self._heartbeat()
            cid=self._send(name,payload)
            expected=SCHEMAS[name][1]
            deadline=self.clock()+5
            while self.clock()<deadline:
                self._heartbeat()
                try:
                    frame=self.transport.receive(min(0.5,max(0.001,deadline-self.clock())))
                except TimeoutError:
                    continue
                if not isinstance(frame,dict):
                    raise SafetyError('UNEXPECTED_RESPONSE_SCHEMA')
                kind=integer(frame.get('payloadType'))
                # The demo JSON gateway omits payload on the empty application
                # acknowledgement. Only that documented empty response permits
                # omission; its expected type and correlation are still checked.
                if kind==2101 and 'payload' not in frame:
                    body={}
                elif isinstance(frame.get('payload'),dict):
                    body=frame['payload']
                else:
                    raise SafetyError('UNEXPECTED_RESPONSE_SCHEMA')
                if kind in (50,2142):
                    self._count('api_errors')
                    if body.get('errorCode') in ('REQUEST_FREQUENCY_EXCEEDED',108,'108'):
                        self._count('rate_limit_errors')
                    # Descriptions and response bodies may contain private information.
                    raise SafetyError('BROKER_REQUEST_REJECTED')
                if kind!=expected:
                    if self._event(kind,body):
                        self._last_rx=self.clock()
                        continue
                    raise SafetyError('UNEXPECTED_RESPONSE_TYPE')
                if frame.get('clientMsgId')!=cid:
                    raise SafetyError('RESPONSE_CORRELATION_MISMATCH')
                if 'ctidTraderAccountId' in payload and integer(body.get('ctidTraderAccountId'),1)!=self.config.demo_account_id:
                    raise SafetyError('RESPONSE_ACCOUNT_MISMATCH')
                self._last_rx=self.clock()
                self._operations[name]='OK'
                return body
            raise SafetyError('REQUEST_TIMEOUT')
        except Exception as exc:
            reason=str(exc) if isinstance(exc,SafetyError) else 'NETWORK_DISCONNECTED'
            if reason=='REQUEST_TIMEOUT':self._count('request_timeouts')
            if name in ('orders','deals'):self._count('history_errors')
            self._operations[name]='FAILED:'+reason
            self.disconnect(reason)
            raise SafetyError(reason) from None

    def _grant(self,token):
        self.config.require_approved()
        self.disconnect('CONNECTING')
        token.require_current(self.wall())
        self._token=token
        token.verified=False
        self._count('connect_attempts')
        self.transport.connect()
        self._count('connections')
        with self._lock:
            self._connected=True
            self._last_rx=self._last_hb=self.clock()
            self._epoch=uuid4().hex
            self._request_seq=0
            self._symbol_id=None
            self._last_error=None
        self.request('application',clientId=self.config.client_id,clientSecret=self.config.client_secret)
        grant=self.request('accounts',accessToken=token.access)
        if grant.get('accessToken')!=token.access or enum(grant.get('permissionScope'),VIEW)!=0:
            raise SafetyError('VIEW_SCOPE_REQUIRED')
        return grant

    @serial_io
    def discover(self,token):
        """Read grant metadata only. Never authorize an account or select one."""
        try:
            grant=self._grant(token)
            candidates=[]
            demos=[a for a in rows(grant,'ctidTraderAccount') if a.get('isLive') is False]
            self.discovery_brokers=sorted({a['brokerTitleShort'] for a in demos
                if isinstance(a.get('brokerTitleShort'),str) and 0<len(a['brokerTitleShort'])<=128})
            if not demos:raise SafetyError('NO_AUTHORIZED_DEMO_ACCOUNTS')
            matching=[a for a in demos if a.get('brokerTitleShort')==self.config.expected_broker]
            if not matching:raise SafetyError('BROKER_IDENTITY_MISMATCH')
            for account in matching:
                login=integer(account.get('traderLogin'),1)
                if self.config.expected_demo_login is not None and login!=self.config.expected_demo_login:
                    continue
                candidates.append({'ctidTraderAccountId':integer(account.get('ctidTraderAccountId'),1),
                    'traderLogin':login,'broker':account['brokerTitleShort'],'isLive':False})
            if not candidates:raise SafetyError('DISPLAY_LOGIN_MISMATCH')
            if len({a['ctidTraderAccountId'] for a in candidates})!=len(candidates):
                raise SafetyError('AMBIGUOUS_ACCOUNT_LIST')
            # Remain unverified for storage/account auth until the user's choice.
            token.verified=False
            return candidates
        except Exception as exc:
            token.verified=False
            reason=str(exc) if isinstance(exc,SafetyError) else 'DISCOVERY_FAILED'
            self.disconnect(reason)
            raise SafetyError(reason) from None

    @serial_io
    def authorize(self,token):
        """View-only grant and pinned demo/display-login validation before account auth."""
        if self.config.discovery_only or self.config.demo_account_id is None:
            raise SafetyError('EXPLICIT_DEMO_SELECTION_REQUIRED')
        try:
            grant=self._grant(token)
            selected=one([a for a in rows(grant,'ctidTraderAccount') if integer(a.get('ctidTraderAccountId'),1)==self.config.demo_account_id])
            if selected.get('isLive') is not False:
                raise SafetyError('DEMO_ACCOUNT_REQUIRED')
            if selected.get('brokerTitleShort')!=self.config.expected_broker:
                raise SafetyError('BROKER_IDENTITY_MISMATCH')
            if self.config.expected_demo_login is not None and integer(selected.get('traderLogin'),1)!=self.config.expected_demo_login:
                raise SafetyError('DISPLAY_LOGIN_MISMATCH')
            token.verified=True
            self.request('account',ctidTraderAccountId=self.config.demo_account_id,accessToken=token.access)
            self._authorized=True
            self._count('authorizations')
            self._recovery_pending=self._metrics['authorizations']>1
            self._grant_broker=selected['brokerTitleShort']
        except Exception as exc:
            token.verified=False
            reason=str(exc) if isinstance(exc,SafetyError) else 'AUTHENTICATION_FAILED'
            self.disconnect(reason)
            raise SafetyError(reason) from None

    def _read(self,name,**payload):
        return self.request(name,ctidTraderAccountId=self.config.demo_account_id,**payload)

    def _history(self,name,start,end,budget=64):
        key,identity,timestamp=('order','orderId','utcLastUpdateTimestamp') if name=='orders' else ('deal','dealId','executionTimestamp')
        windows=[(start,end)]
        found={}
        while windows:
            if budget<=0:
                raise SafetyError('HISTORY_INCOMPLETE')
            budget-=1
            lo,hi=windows.pop()
            args={'fromTimestamp':lo,'toTimestamp':hi}
            if name=='deals':
                args['maxRows']=1000
            result=self._read(name,**args)
            more=result.get('hasMore')
            if type(more) is not bool:
                raise SafetyError('HISTORY_COMPLETENESS_UNKNOWN')
            items=rows(result,key)
            if more:
                # No undocumented offset/cursor. Split until every inclusive window
                # is complete. A saturated single millisecond is explicitly incomplete.
                if lo==hi:
                    raise SafetyError('HISTORY_INCOMPLETE')
                mid=(lo+hi)//2
                windows.extend([(lo,mid),(mid+1,hi)])
                continue
            for item in items:
                identifier=integer(item.get(identity),1)
                at=integer(item.get(timestamp))
                if not lo<=at<=hi:
                    raise SafetyError('HISTORY_TIMESTAMP_MISMATCH')
                if identifier in found and found[identifier]!=item:
                    raise SafetyError('HISTORY_CONFLICT')
                found[identifier]=item
        return list(found.values())

    @serial_io
    def verify_identity(self):
        """Minimal read-only identity report; no IDs or financial values exposed.

        A differing trader brokerName is reported for owner confirmation, never
        accepted as a replacement configuration or a healthy full snapshot.
        """
        trader=self._read('trader').get('trader')
        if not isinstance(trader,dict) or integer(trader.get('ctidTraderAccountId'),1)!=self.config.demo_account_id:
            raise SafetyError('RESPONSE_ACCOUNT_MISMATCH')
        broker=trader.get('brokerName')
        if not isinstance(broker,str) or not broker.strip() or len(broker)>128 or not broker.isprintable():
            raise SafetyError('BROKER_IDENTITY_MISSING')
        assets=rows(self._read('assets'),'asset')
        by_id={integer(a.get('assetId'),1):a for a in assets}
        if len(by_id)!=len(assets):raise SafetyError('AMBIGUOUS_ASSETS')
        currency=by_id.get(integer(trader.get('depositAssetId'),1),{}).get('name')
        if not isinstance(currency,str) or not re.fullmatch('[A-Z]{3,12}',currency):
            raise SafetyError('ACCOUNT_CURRENCY_UNKNOWN')
        mode=enum(trader.get('accountType'),ACCOUNT_MODES)
        if mode not in ACCOUNT_MODES.values():raise SafetyError('ACCOUNT_PERMISSIONS_UNKNOWN')
        mode_name=next(k for k,v in ACCOUNT_MODES.items() if v==mode)
        if self.config.expected_currency is not None and currency!=self.config.expected_currency:
            raise SafetyError('ACCOUNT_CURRENCY_MISMATCH')
        if self.config.expected_account_mode is not None and mode_name!=self.config.expected_account_mode:
            raise SafetyError('ACCOUNT_MODE_MISMATCH')
        return {'environment':'DEMO','read_only':True,'view_authorized':self._authorized,
            'pinned_account_id_matches':True,'broker_name':broker,'currency':currency,
            'account_mode':mode_name,
            'broker_name_matches_configuration':broker==(self.config.expected_broker_name or self.config.expected_broker)}

    @serial_io
    def refresh(self,history_seconds=86400):
        if type(history_seconds) is not int or not 1<=history_seconds<=7*86400:
            raise SafetyError('INVALID_HISTORY_WINDOW')
        with self._lock:
            self._dirty=True
            self._history_complete=False
        try:
            revision=self._event_revision
            trader=self._read('trader').get('trader')
            if not isinstance(trader,dict) or integer(trader.get('ctidTraderAccountId'),1)!=self.config.demo_account_id:
                raise SafetyError('RESPONSE_ACCOUNT_MISMATCH')
            mode=enum(trader.get('accountType'),ACCOUNT_MODES)
            access=enum(trader.get('accessRights'),ACCESS)
            if mode not in ACCOUNT_MODES.values() or access not in (0,1,2):
                raise SafetyError('ACCOUNT_PERMISSIONS_UNKNOWN')
            broker=trader.get('brokerName')
            if not isinstance(broker,str) or not broker.strip() or len(broker)>128:
                raise SafetyError('BROKER_IDENTITY_MISSING')
            expected=self.config.expected_broker_name or self.config.expected_broker
            if broker!=expected:
                raise SafetyError('BROKER_IDENTITY_MISMATCH')
            assets=rows(self._read('assets'),'asset')
            by_id={integer(a.get('assetId'),1):a for a in assets}
            if len(by_id)!=len(assets):
                raise SafetyError('AMBIGUOUS_ASSETS')
            currency=by_id.get(integer(trader.get('depositAssetId'),1),{}).get('name')
            if not isinstance(currency,str) or not re.fullmatch('[A-Z]{3,12}',currency):
                raise SafetyError('ACCOUNT_CURRENCY_UNKNOWN')
            if self.config.expected_currency is not None and currency!=self.config.expected_currency:
                raise SafetyError('ACCOUNT_CURRENCY_MISMATCH')
            if self.config.expected_account_mode is not None and ACCOUNT_MODES[self.config.expected_account_mode]!=mode:
                raise SafetyError('ACCOUNT_MODE_MISMATCH')
            light=one([s for s in rows(self._read('symbols',includeArchivedSymbols=False),'symbol')
                       if s.get('symbolName','').replace('/','')=='XAUUSD'])
            if by_id.get(integer(light.get('baseAssetId'),1),{}).get('name')!='XAU' or by_id.get(integer(light.get('quoteAssetId'),1),{}).get('name')!='USD':
                raise SafetyError('SYMBOL_ASSET_MISMATCH')
            sid=integer(light.get('symbolId'),1)
            symbol=one(rows(self._read('symbol',symbolId=[sid]),'symbol'))
            if integer(symbol.get('symbolId'),1)!=sid:
                raise SafetyError('RESPONSE_SYMBOL_MISMATCH')
            digits=integer(symbol.get('digits'))
            pip=integer(symbol.get('pipPosition'))
            if digits>12 or pip>digits:
                raise SafetyError('INVALID_SYMBOL_PRECISION')
            minimum,step,maximum=(integer(symbol.get(k),1) for k in ('minVolume','stepVolume','maxVolume'))
            if minimum>maximum or step>maximum:
                raise SafetyError('INVALID_SYMBOL_VOLUME')
            distance=enum(symbol.get('distanceSetIn'),DISTANCE)
            if distance not in (1,2):
                raise SafetyError('STOP_CONSTRAINTS_UNKNOWN')
            spec={'name':'XAUUSD','price_digits':digits,'pip_position':pip,
                'minimum_units':str(Decimal(minimum)/100),'step_units':str(Decimal(step)/100),
                'maximum_units':str(Decimal(maximum)/100),
                'stop_distance':integer(symbol.get('slDistance')),
                'target_distance':integer(symbol.get('tpDistance')),
                'distance_unit':'POINTS' if distance==1 else 'PERCENTAGE',
                'lot_units':str(Decimal(integer(symbol['lotSize'],1))/100) if 'lotSize' in symbol else None,
                'trading_mode':enum(symbol.get('tradingMode'),{'ENABLED':0,'DISABLED_WITHOUT_PENDINGS_EXECUTION':1,'DISABLED_WITH_PENDINGS_EXECUTION':2,'CLOSE_ONLY_MODE':3})}
            if spec['trading_mode'] not in (0,1,2,3):
                raise SafetyError('SYMBOL_TRADING_MODE_UNKNOWN')
            before=self._read('reconcile')
            pending,positions=rows(before,'order'),rows(before,'position')
            self._validate_book(pending,positions)
            end=int(self.wall()*1000)
            start=max(0,end-history_seconds*1000,integer(trader.get('registrationTimestamp',0)))
            orders=self._history('orders',start,end)
            deals=self._history('deals',start,end)
            pnl=self._read('pnl')
            estimate=self._read('margin',symbolId=sid,volume=[minimum])
            margins=rows(estimate,'margin')
            match=one([m for m in margins if integer(m.get('volume'),1)==minimum])
            buy=money(dict(match,moneyDigits=estimate.get('moneyDigits')),'buyMargin')
            sell=money(dict(match,moneyDigits=estimate.get('moneyDigits')),'sellMargin')
            # Validate monetary precision before any derived available-margin estimate.
            balance=money(trader,'balance')
            used=sum((money(p,'usedMargin') for p in positions),Decimal(0))
            pnls=rows(pnl,'positionUnrealizedPnL')
            open_ids={integer(p['positionId'],1) for p in positions}
            if {integer(p.get('positionId'),1) for p in pnls}!=open_ids or len(pnls)!=len(open_ids):
                raise SafetyError('MARGIN_SNAPSHOT_INCOMPLETE')
            net=sum((money(dict(p,moneyDigits=pnl.get('moneyDigits')),'netUnrealizedPnL') for p in pnls),Decimal(0))
            after=self._read('reconcile')
            if before!=after or revision!=self._event_revision:
                raise SafetyError('SNAPSHOT_CHANGED_RETRY')
            with self._lock:
                self._symbol_id=sid
            if self._subscribed_symbol is None:
                self._read('spots',symbolId=[sid],subscribeToSpotTimestamp=True)
                self._subscribed_symbol=sid
            elif self._subscribed_symbol!=sid:
                raise SafetyError('RESPONSE_SYMBOL_MISMATCH')
            with self._lock:
                if revision!=self._event_revision:
                    raise SafetyError('SNAPSHOT_CHANGED_RETRY')
                self._account={'currency':currency,'broker':self._grant_broker,
                    'mode':next(k for k,v in ACCOUNT_MODES.items() if v==mode),
                    'account_access':next(k for k,v in ACCESS.items() if v==access),
                    'token_scope':'VIEW_ONLY','environment':'DEMO'}
                self._symbol=spec
                self._pending,self._positions=pending,positions
                self._orders,self._deals=orders,deals
                # Account finances are memory-only, excluded from every public status.
                self._margin={'balance':balance,'used':used,'net_pnl':net,
                    'free_margin_estimate':balance+net-used,'expected_buy':buy,'expected_sell':sell,
                    'authoritative_free_margin':False}
                self._snapshot_at=self.clock()
                self._history_complete=True
                self._dirty=False
                self._last_error=None
                self._count('snapshots')
                if self._recovery_pending:
                    self._count('recoveries');self._recovery_pending=False
        except Exception as exc:
            reason=str(exc) if isinstance(exc,SafetyError) else 'UNEXPECTED_RESPONSE_SCHEMA'
            self.disconnect(reason)
            raise SafetyError(reason) from None

    @staticmethod
    def _validate_book(pending,positions):
        for items,identity in ((pending,'orderId'),(positions,'positionId')):
            seen=set()
            for item in items:
                key=integer(item.get(identity),1)
                if key in seen:
                    raise SafetyError('DUPLICATE_BROKER_ENTITY')
                seen.add(key)
                data=item.get('tradeData')
                if not isinstance(data,dict):
                    raise SafetyError('UNEXPECTED_RESPONSE_SCHEMA')
                integer(data.get('symbolId'),1)
                integer(data.get('volume'),1)
                if enum(data.get('tradeSide'),{'BUY':1,'SELL':2}) not in (1,2):
                    raise SafetyError('UNEXPECTED_RESPONSE_SCHEMA')

    @serial_io
    def poll(self,timeout=0.5):
        if not self._connected:
            raise SafetyError('NOT_CONNECTED')
        try:
            self._token.require_current(self.wall())
            self._heartbeat()
            try:
                frame=self.transport.receive(timeout)
            except TimeoutError:
                return
            if not isinstance(frame,dict) or not isinstance(frame.get('payload'),dict) or not self._event(integer(frame.get('payloadType')),frame['payload']):
                raise SafetyError('UNEXPECTED_RESPONSE_TYPE')
            self._last_rx=self.clock()
        except Exception as exc:
            reason=str(exc) if isinstance(exc,SafetyError) else 'NETWORK_DISCONNECTED'
            self.disconnect(reason)
            raise SafetyError(reason) from None

    def status(self):
        with self._lock:
            now=self.clock()
            quote_fresh=(len(self._quotes)==2 and all(0<=now-q[1]<=10 and -2<=self.wall()-q[2]<=10 for q in self._quotes.values()))
            data_fresh=self._snapshot_at is not None and 0<=now-self._snapshot_at<=30 and not self._dirty
            heartbeat=self._connected and self._last_rx is not None and 0<=now-self._last_rx<=30
            token_current=self._token is not None and self._token.expires_at>self.wall()+30
            data_fresh=bool(data_fresh and self._connected and self._authorized)
            healthy=bool(self._connected and self._authorized and data_fresh and quote_fresh and heartbeat and token_current and self._history_complete)
            return {'read_only':True,'environment':'DEMO',
                'health':'HEALTHY' if healthy else ('DEGRADED' if self._connected else 'DISCONNECTED'),
                'connected':self._connected,'view_authorized':self._authorized,
                'heartbeat_fresh':heartbeat,'snapshot_fresh':data_fresh,'quote_fresh':quote_fresh,
                'history_complete':self._history_complete,
                'quote':{'bid':str(self._quotes['bid'][0]),'ask':str(self._quotes['ask'][0]),'timestamp_utc':datetime.fromtimestamp(min(q[2] for q in self._quotes.values()),timezone.utc).isoformat(),'server_age_seconds':self.wall()-min(q[2] for q in self._quotes.values())} if len(self._quotes)==2 else None,
                'read_operations':dict(self._operations),
                'execution_enabled':False,'execution_readiness':'NO_GO',
                'volume_independently_verified':False,
                'token_seconds_remaining':max(0,int(self._token.expires_at-self.wall())) if self._token else None,
                'reauthorization_required':self._token is None or not token_current,
                'metrics':dict(self._metrics),
                'rate_limiter':{'wait_count':self.limiter.wait_count,'wait_seconds':self.limiter.wait_seconds,
                    'peak_requests_per_second':self.limiter.peak_requests_per_second,
                    'peak_history_per_second':self.limiter.peak_history_per_second},
                'last_error':self._last_error,'account':copy.deepcopy(self._account),
                'symbol':copy.deepcopy(self._symbol),
                'pending_count':len(self._pending),'position_count':len(self._positions),
                'order_history_count':len(self._orders),'deal_history_count':len(self._deals),
                'margin_information_available':bool(self._margin) and data_fresh,
                'free_margin_authoritative':False}

    def run(self,load_token,stop,jitter=random.uniform):
        """Bounded backoff, fresh auth and complete resnapshot on every reconnect."""
        delay=1
        while not stop.is_set():
            try:
                self.authorize(load_token())
                self.refresh()
                stable_since=self.clock()
                while not stop.is_set():
                    self.poll()
                    if self._dirty or self.clock()-self._snapshot_at>=15:
                        self.refresh()
                    if self.clock()-stable_since>=60:
                        delay=1
            except (SafetyError,OSError,ValueError) as exc:
                # Loading an expired token can fail before request telemetry.
                # Preserve only finite local auth reason codes, never file/HTTP
                # exception text. Reauthorization remains deliberate/manual.
                auth_reason=str(exc) if isinstance(exc,SafetyError) and str(exc) in {
                    'TOKEN_EXPIRED_REAUTHORIZE','VIEW_SCOPE_REQUIRED','INVALID_TOKEN_FILE',
                    'INVALID_TOKEN','APPLICATION_APPROVAL_REQUIRED'} else None
                self.disconnect(auth_reason or self._last_error or 'LOCAL_AUTHORIZATION_UNAVAILABLE')
                # No automatic browser, token refresh or trading fallback.
                stop.wait(min(60,delay)+jitter(0,min(1,delay/4)))
                delay=min(60,delay*2)
        self.disconnect('STOPPED')
