"""Razor DEMO trading transport, separate from the immutable view-only allowlist.

No connection occurs at import/construction. Every mutation requires a private
owner approval AND a durable, once-consumed journal claim. No automatic resend.
"""
from collections import deque
from decimal import Decimal as D
import json
import time
from uuid import uuid4

from ctrader_readonly.client import Reader,rows,one,money,serial_io
from ctrader_readonly.config import SafetyError
from ctrader_readonly.protocol import DemoWebSocket,DEMO_URL,SCHEMAS,integer,enum,validate_frame
from .schema import validate_mutation,KEYS,SymbolSpec,frame,order_frame,RiskLimits
from control_plane.contracts import Quote
from datetime import datetime,timezone
from .approvals import profile,require_role,validate_test,number
from .ledger import digest

REJECT_CODES=frozenset(('TRADING_BAD_VOLUME','TRADING_BAD_STOPS','TRADING_BAD_PRICES',
    'TRADING_DISABLED','TRADING_NOT_ALLOWED','NOT_ENOUGH_MONEY','MAX_EXPOSURE_REACHED',
    'ORDER_NOT_FOUND','POSITION_NOT_FOUND','PROTECTION_IS_TOO_CLOSE_TO_MARKET',
    'ACCOUNT_NOT_AUTHORIZED','OA_AUTH_TOKEN_EXPIRED','ALREADY_SUBSCRIBED'))


class MutationWire(DemoWebSocket):
    endpoint=DEMO_URL
    def __init__(self,account):
        super().__init__();self.account=account;self.symbol=None;self.trade_bound=False;self._once=None

    def bind(self,account,symbol):
        if account!=self.account:raise SafetyError('DEMO_ACCOUNT_MISMATCH')
        self.symbol=integer(symbol,1);self.trade_bound=True

    def permit_once(self,frame,journal):
        if not self.trade_bound:raise SafetyError('REMOTE_TRADING_GRANT_REQUIRED')
        journal.consume_send(frame['clientMsgId'],frame['payloadType'],frame['payload'])
        self._once=(frame['clientMsgId'],digest(frame['payload']))

    def send(self,frame):
        kind=frame.get('payloadType')
        if kind not in KEYS:
            validate_frame(frame)
            if 'ctidTraderAccountId' in frame['payload'] and integer(frame['payload']['ctidTraderAccountId'],1)!=self.account:raise SafetyError('DEMO_ACCOUNT_MISMATCH')
            return super().send(frame)
        validate_mutation(frame)
        p=frame['payload'];permit=(frame['clientMsgId'],digest(p))
        if not self.trade_bound or self._once!=permit:raise SafetyError('DURABLE_MUTATION_PERMIT_REQUIRED')
        self._once=None  # Consume even if the write raises/lost acknowledgement.
        if p['ctidTraderAccountId']!=self.account or ('symbolId' in p and p['symbolId']!=self.symbol):raise SafetyError('DEMO_ACCOUNT_OR_SYMBOL_MISMATCH')
        try:self.socket.send(json.dumps(frame,separators=(',',':'),allow_nan=False))
        except Exception:raise SafetyError('NETWORK_SEND_FAILED') from None

    def close(self):
        self.trade_bound=False;self.symbol=None;self._once=None;super().close()


class TradeClient(Reader):
    """Read-side machinery reused, remote permission check explicitly SCOPE_TRADE.

    This class has no HTTP status server and is never used by the view observer.
    The default wire is fixed-demo TLS. Injected wires are for offline tests.
    """
    def __init__(self,config,authority,transport=None,**kwargs):
        profile(config)
        now=kwargs.get('wall',time.time)()
        if authority.get('role')=='ONE_TEST':validate_test(authority,config,now,management=True)
        else:require_role(authority,config,'OAUTH_ONLY',now)
        wire=transport or MutationWire(config.demo_account_id)
        if getattr(wire,'endpoint',None)!=DEMO_URL:raise SafetyError('DEMO_ENDPOINT_REQUIRED')
        self.authority=authority;self.events=deque(maxlen=1000);self.native_spec=None
        super().__init__(config,wire,**kwargs)

    def _check_authority(self,*,entry=False):
        if self.authority.get('role')=='ONE_TEST':return validate_test(self.authority,self.config,self.wall(),management=not entry)
        return require_role(self.authority,self.config,'OAUTH_ONLY',self.wall())

    def _grant(self,token):
        self._check_authority();self.config.require_approved();self.disconnect('CONNECTING')
        if getattr(token,'scope',None)!='trading':raise SafetyError('SEPARATE_TRADING_TOKEN_REQUIRED')
        if token.account_id is not None and token.account_id!=self.config.demo_account_id:raise SafetyError('TRADING_TOKEN_ACCOUNT_MISMATCH')
        token.require_current(self.wall());token.verified=False;self._token=token
        self._count('connect_attempts');self.transport.connect();self._count('connections')
        with self._lock:
            self._connected=True;self._last_rx=self._last_hb=self.clock()
            self._epoch=uuid4().hex;self._request_seq=0;self._symbol_id=None;self._last_error=None
        self.request('application',clientId=self.config.client_id,clientSecret=self.config.client_secret)
        grant=self.request('accounts',accessToken=token.access)
        if grant.get('accessToken')!=token.access or enum(grant.get('permissionScope'),{'SCOPE_VIEW':0,'SCOPE_TRADE':1})!=1:raise SafetyError('REMOTE_TRADING_SCOPE_REQUIRED')
        accounts=rows(grant,'ctidTraderAccount')
        if any(a.get('isLive') is not False for a in accounts):raise SafetyError('LIVE_OR_UNKNOWN_ACCOUNT_GRANT_REJECTED')
        return grant

    @serial_io
    def authorize(self,token):
        try:
            super().authorize(token)  # Dispatches only this class's _grant.
            identity=super().verify_identity()
            if not identity['broker_name_matches_configuration']:raise SafetyError('BROKER_IDENTITY_MISMATCH')
            if identity['currency']!='USD' or identity['account_mode']!='HEDGED':raise SafetyError('RAZOR_USD_HEDGED_REQUIRED')
            if self.native_spec is not None:
                self._symbol_id=self.native_spec.symbol_id
                self.transport.bind(self.config.demo_account_id,self._symbol_id)
        except Exception:
            token.verified=False;self.disconnect('TRADING_AUTHORIZATION_FAILED');raise

    @serial_io
    def refresh(self,*args,**kwargs):
        self._check_authority();super().refresh(*args,**kwargs)
        if self._symbol.get('minimum_units')!='1' or self._symbol.get('lot_units')!='100':
            self.disconnect('RAZOR_CONTRACT_CHANGED');raise SafetyError('RAZOR_CONTRACT_CHANGED')
        self.transport.bind(self.config.demo_account_id,self._symbol_id)
        s=self._symbol
        self.native_spec=SymbolSpec(self._symbol_id,int(D(s['lot_units'])*100),int(D(s['minimum_units'])*100),int(D(s['maximum_units'])*100),int(D(s['step_units'])*100),s['price_digits'],s['stop_distance'],s['target_distance'],False,s['distance_unit']=='POINTS')
        if self.authority.get('role')=='ONE_TEST':
            ui=self.authority['ui'];spec=self.native_spec
            if any(actual!=number(ui[key]) for actual,key in ((D(spec.min_hundredths)/spec.lot_hundredths,'minimum_lots'),(D(spec.max_hundredths)/spec.lot_hundredths,'maximum_lots'),(D(spec.step_hundredths)/spec.lot_hundredths,'step_lots'),(D(spec.lot_hundredths)/100,'units_per_lot'))):raise SafetyError('API_UI_SPEC_MISMATCH')
            from dataclasses import replace
            self.native_spec=replace(spec,independently_verified=True)
        with self._lock:self._account['token_scope']='TRADE'

    def status(self):
        result=super().status()
        result.update(read_only=False,execution_enabled=False,manual_test_only=True)
        return result

    def _event(self,kind,payload):
        if kind==2132:
            if integer(payload.get('ctidTraderAccountId'),1)!=self.config.demo_account_id:raise SafetyError('RESPONSE_ACCOUNT_MISMATCH')
            self._dirty=True;return True
        return super()._event(kind,payload)

    @serial_io
    def snapshot(self,since_ms):
        self._check_authority()
        for _ in range(3):
            revision=self._event_revision
            before=self._read('reconcile')
            end=int(self.wall()*1000)
            if not 0<=since_ms<=end or end-since_ms>7*86400000:raise SafetyError('BOUNDED_HISTORY_WINDOW_REQUIRED')
            orders=self._history('orders',since_ms,end);deals=self._history('deals',since_ms,end)
            after=self._read('reconcile')
            if before!=after or revision!=self._event_revision:continue
            pending=rows(after,'order');positions=rows(after,'position')
            self._validate_book(pending,positions)
            by_id={integer(o.get('orderId'),1):o for o in orders}
            for order in pending:
                oid=integer(order.get('orderId'),1)
                # Pending reconcile can be a fresher projection than history.
                if oid in by_id and any(by_id[oid].get(k)!=order.get(k) for k in ('clientOrderId','orderType')):raise SafetyError('ORDER_HISTORY_CONFLICT')
                by_id[oid]=order
            return {'ctidTraderAccountId':self.config.demo_account_id,'orders':list(by_id.values()),
                'positions':positions,'deals':deals,'history_complete':True,'at':self.wall()}
        raise SafetyError('SNAPSHOT_CHANGED_RETRY')

    @serial_io
    def mutate(self,frame,journal,*,controls_check):
        validate_mutation(frame);kind=frame['payloadType'];cid=frame['clientMsgId'];p=frame['payload']
        self._check_authority(entry=kind==2106)
        if self.authority.get('role')!='ONE_TEST':raise SafetyError('ORDER_TEST_APPROVAL_REQUIRED')
        if kind not in self.authority['allowed_ops']:raise SafetyError('OPERATION_NOT_APPROVED')
        binding=journal.db.execute('SELECT profile,approval FROM binding').fetchone()
        if str(journal.path)!=self.authority['journal_path'] or tuple(binding)!=(profile(self.config),self.authority['id']):raise SafetyError('JOURNAL_AUTHORITY_MISMATCH')
        if not self._authorized or self._token is None or not self._token.verified:raise SafetyError('REMOTE_TRADING_GRANT_REQUIRED')
        self._token.require_current(self.wall())
        if p['ctidTraderAccountId']!=self.config.demo_account_id:raise SafetyError('DEMO_ACCOUNT_MISMATCH')
        entry=journal.entry()
        if not entry:raise SafetyError('DURABLE_ENTRY_CLAIM_REQUIRED')
        if kind==2106:
            controls_check()
            if cid!=entry['id'] or p['volume']!=100 or p.get('symbolId')!=self._symbol_id:raise SafetyError('BOUNDED_MINIMUM_TEST_REQUIRED')
            expected_side=1 if self.authority['side']=='BUY' else 2
            expected_type=1 if self.authority['order_type']=='MARKET' else 2
            if p['tradeSide']!=expected_side or p['orderType']!=expected_type:raise SafetyError('ENTRY_APPROVAL_MISMATCH')
            if expected_type==1:
                if p['relativeStopLoss']!=int(number(self.authority['stop_distance'])*100000) or p['relativeTakeProfit']!=int(number(self.authority['target_distance'])*100000):raise SafetyError('ENTRY_APPROVAL_MISMATCH')
            else:
                limit=number(self.authority['limit_price']);sign=1 if expected_side==1 else -1
                if D(str(p['limitPrice']))!=limit or D(str(p['stopLoss']))!=limit-sign*number(self.authority['stop_distance']) or D(str(p['takeProfit']))!=limit+sign*number(self.authority['target_distance']):raise SafetyError('ENTRY_APPROVAL_MISMATCH')
            self._entry_risk_check()
            controls_check()
        else:
            book=self.snapshot(max(0,int(entry['at']*1000)-1000))
            order,owned,opened,_=owned_state(book,entry['id'])
            if not order or integer(order['tradeData']['symbolId'],1)!=self._symbol_id or integer(order['tradeData']['volume'],1)!=100 or integer(order['tradeData']['tradeSide'],1)!=(1 if self.authority['side']=='BUY' else 2) or opened>100:raise SafetyError('OWNED_ENTRY_IDENTITY_MISMATCH')
            target=p.get('positionId',p.get('orderId'))
            if kind in (2108,2109):
                if not order or integer(order['orderId'],1)!=target:raise SafetyError('OWNED_ORDER_REQUIRED')
                if kind==2109:
                    if integer(order.get('orderStatus'),1)!=1 or integer(order.get('executedVolume',0)) or integer(order.get('orderType'),1)!=2:raise SafetyError('UNFILLED_LIMIT_AMENDMENT_REQUIRED')
                    if p['volume']>100:raise SafetyError('RISK_INCREASING_AMENDMENT')
                    self.native_spec.volume(D(p['volume'])/100)
                    old=order.get('stopLoss');buy=integer(order['tradeData']['tradeSide'],1)==1
                    if old is None or ((p['stopLoss']<old) if buy else (p['stopLoss']>old)):raise SafetyError('RISK_INCREASING_AMENDMENT')
                    if abs(D(str(p['limitPrice']))-D(str(p['stopLoss'])))>number(self.authority['stop_distance']):raise SafetyError('RISK_INCREASING_AMENDMENT')
                    limit,stop,target=(D(str(p[k])) for k in ('limitPrice','stopLoss','takeProfit'))
                    for price in (limit,stop,target):self.native_spec.price(price)
                    if not (stop<limit<target if buy else target<limit<stop):raise SafetyError('NATIVE_PROTECTION')
                    if abs(limit-stop)<D(self.native_spec.stop_points).scaleb(-self.native_spec.digits) or abs(target-limit)<D(self.native_spec.target_points).scaleb(-self.native_spec.digits):raise SafetyError('NATIVE_PROTECTION')
            else:
                pos=one([x for x in owned if integer(x['positionId'],1)==target])
                current=integer(pos['tradeData']['volume'],1)
                if kind==2111 and p['volume']>current:raise SafetyError('CLOSE_VOLUME_EXCEEDS_POSITION')
                if kind==2111 and p['volume']!=current:
                    if self.authority.get('partial_closures_approved') is not True:raise SafetyError('PARTIAL_CLOSE_NOT_APPROVED')
                    self.native_spec.volume(D(p['volume'])/100)
                    if (current-p['volume'])<self.native_spec.min_hundredths:raise SafetyError('INVALID_CLOSE_RESIDUAL')
                if kind==2110:
                    quote=self.fresh_quote();buy=integer(pos['tradeData']['tradeSide'],1)==1
                    old=D(str(pos.get('stopLoss',0)));new=D(str(p['stopLoss']))
                    if (old>0 and ((new<old) if buy else (new>old))) or ((new>=quote.bid) if buy else (new<=quote.ask)):raise SafetyError('RISK_INCREASING_OR_INVALID_STOP')
                    self.native_spec.price(new);self.native_spec.price(D(str(p['takeProfit'])))
                    target=D(str(p['takeProfit']))
                    if (target<=quote.ask if buy else target>=quote.bid):raise SafetyError('NATIVE_TARGET_DISTANCE')
                    gap=(quote.bid-new) if buy else (new-quote.ask)
                    if gap<D(self.native_spec.stop_points).scaleb(-self.native_spec.digits):raise SafetyError('NATIVE_STOP_DISTANCE')
        self.transport.permit_once(frame,journal)  # Durable BEFORE network write.
        try:
            self.limiter.acquire()
            self._check_authority(entry=kind==2106);self._token.require_current(self.wall())
            if kind==2106:self.fresh_quote();controls_check()
            self.transport.send(frame)
            deadline=self.clock()+5
            while self.clock()<deadline:
                self._heartbeat()
                try:response=self.transport.receive(.5)
                except TimeoutError:continue
                if not isinstance(response,dict) or not isinstance(response.get('payload'),dict):raise SafetyError('UNEXPECTED_MUTATION_RESPONSE')
                body=response['payload'];reply_kind=integer(response.get('payloadType'))
                if 'ctidTraderAccountId' in body and integer(body['ctidTraderAccountId'],1)!=self.config.demo_account_id:raise SafetyError('RESPONSE_ACCOUNT_MISMATCH')
                correlated=response.get('clientMsgId')==cid
                if reply_kind in (2132,2142,50) and correlated:
                    code=body.get('errorCode')
                    if not isinstance(code,str) or code not in REJECT_CODES:raise SafetyError('UNKNOWN_BROKER_REJECTION')
                    journal.outcome(cid,'REJECTED',code);return {'rejected':True,'code':code}
                if reply_kind==2126 and correlated:
                    if integer(body.get('ctidTraderAccountId'),1)!=self.config.demo_account_id:raise SafetyError('RESPONSE_ACCOUNT_MISMATCH')
                    execution=integer(body.get('executionType'),1)
                    if execution==7:
                        code=body.get('errorCode')
                        if code not in REJECT_CODES:raise SafetyError('UNKNOWN_BROKER_REJECTION')
                        journal.outcome(cid,'REJECTED',code);return {'rejected':True,'code':code}
                    if execution not in {2106:{2,3,11},2108:{5},2109:{4},2110:{4},2111:{3,11}}[kind]:raise SafetyError('UNKNOWN_EXECUTION_TYPE')
                    if kind==2106 and body.get('order',{}).get('clientOrderId')!=cid:raise SafetyError('ORDER_IDENTITY_MISMATCH')
                    if kind in (2108,2109) and integer(body.get('order',{}).get('orderId'),1)!=p['orderId']:raise SafetyError('ACTION_TARGET_MISMATCH')
                    if kind in (2110,2111) and integer(body.get('position',{}).get('positionId'),1)!=p['positionId']:raise SafetyError('ACTION_TARGET_MISMATCH')
                    journal.outcome(cid,'ACCEPTED','CORRELATED_EXECUTION_EVENT');return {'rejected':False}
                # Async events cannot be guessed into request acknowledgements.
                if reply_kind in (2126,2132,2131,51,2123,2141,2120):
                    self._event(reply_kind,body);self._last_rx=self.clock();continue
                raise SafetyError('MUTATION_RESPONSE_CORRELATION_MISMATCH')
            raise SafetyError('MUTATION_ACK_TIMEOUT')
        except Exception:
            journal.outcome(cid,'UNKNOWN','UNKNOWN_BROKER_OUTCOME')
            self.disconnect('UNKNOWN_BROKER_OUTCOME');raise SafetyError('UNKNOWN_BROKER_OUTCOME') from None

    def fresh_quote(self):
        value=self.status().get('quote')
        if not value or not 0<=value['server_age_seconds']<=5:raise SafetyError('FRESH_QUOTE_REQUIRED')
        return Quote(D(value['bid']),D(value['ask']),datetime.fromisoformat(value['timestamp_utc']))

    def _entry_risk_check(self):
        if self.native_spec is None or not self.native_spec.independently_verified or self._symbol.get('trading_mode')!=0:raise SafetyError('NATIVE_SPEC_NOT_VERIFIED')
        if not self.status()['snapshot_fresh']:raise SafetyError('FRESH_MARKET_SNAPSHOT_REQUIRED')
        quote=self.fresh_quote()
        stop=number(self.authority['stop_distance']);target=number(self.authority['target_distance'])
        if stop<max(number(self.authority['stop_floor'],zero=True),D(self.native_spec.stop_points).scaleb(-self.native_spec.digits)) or target<D(self.native_spec.target_points).scaleb(-self.native_spec.digits):raise SafetyError('NATIVE_PROTECTION')
        if self.authority['order_type']=='MARKET' and stop<=quote.ask-quote.bid:raise SafetyError('NATIVE_PROTECTION')
        if quote.ask-quote.bid>number(self.authority['max_spread']):raise SafetyError('TEST_SPREAD_LIMIT')
        book=self.snapshot(max(0,int(self.wall()*1000)-86400000))
        if book['positions'] or any(integer(o.get('orderStatus'),1)==1 for o in book['orders']):raise SafetyError('EMPTY_DEMO_BOOK_REQUIRED')
        identity=super().verify_identity()
        if not identity['broker_name_matches_configuration']:raise SafetyError('BROKER_IDENTITY_MISMATCH')
        trader=self._read('trader')['trader']
        if trader.get('isLimitedRisk') is not False:raise SafetyError('STANDARD_STOP_ACCOUNT_NOT_VERIFIED')
        balance=money(trader,'balance')
        if balance!=number(self.authority['confirmed_balance'],zero=True):raise SafetyError('ACTUAL_BALANCE_CHANGED_RECONFIRM_LOCALLY')
        result=self._read('margin',symbolId=self._symbol_id,volume=[100])
        estimate=one([m for m in rows(result,'margin') if integer(m.get('volume'),1)==100])
        required=money(dict(estimate,moneyDigits=result.get('moneyDigits')),'buyMargin' if self.authority['side']=='BUY' else 'sellMargin')
        cap=number(self.authority['risk_cap'])
        if required<=0 or required>number(self.authority['margin_cap']) or required+cap>min(balance,number(self.authority['intended_balance'])):raise SafetyError('TEST_MARGIN_OR_FUNDS_LIMIT')
        policy=RiskLimits(cap,cap,cap,D(1),D(1),number(self.authority['margin_cap']),number(self.authority['ui']['commission_roundtrip_per_lot'],zero=True),number(self.authority['slippage_buffer'],zero=True),number(self.authority['max_spread']),number(self.authority['fee_buffer'],zero=True))
        if number(self.authority['stop_distance'])+policy.slippage_per_unit_usd+policy.fees(D(1),self.native_spec)>cap:raise SafetyError('TEST_PLANNED_RISK_EXCEEDED')
        self.fresh_quote()  # Slow risk reads cannot revive an old quote.

    def submit_order(self,key,intent,journal,controls_check):
        if self.native_spec is None or not self.native_spec.independently_verified:raise SafetyError('NATIVE_SPEC_NOT_VERIFIED')
        cid=uuid4().hex;request=order_frame(self.config.demo_account_id,self.native_spec,cid,intent,self.fresh_quote(),datetime.fromtimestamp(self.wall(),timezone.utc))
        claim,new=journal.claim(key,2106,request['payload'],cid=cid)
        if not new:raise SafetyError('ENTRY_ALREADY_CLAIMED')
        return self.mutate(request,journal,controls_check=controls_check)

    def management(self,key,kind,values,journal,controls_check):
        if kind not in (2108,2109,2110,2111):raise SafetyError('UNSUPPORTED_MUTATION')
        payload={'ctidTraderAccountId':self.config.demo_account_id,**values}
        claim,new=journal.claim(key,kind,payload)
        if not new:return {'prior_state':claim['state']}
        return self.mutate(frame(claim['id'],kind,payload),journal,controls_check=controls_check)


def owned_state(book,cid):
    if book.get('history_complete') is not True:raise SafetyError('HISTORY_INCOMPLETE')
    orders=[o for o in book['orders'] if o.get('clientOrderId')==cid]
    if len(orders)>1:raise SafetyError('AMBIGUOUS_CLIENT_ORDER_ID')
    order=orders[0] if orders else None
    seen=set()
    for deal in book['deals']:
        did=integer(deal.get('dealId'),1)
        if did in seen:raise SafetyError('DUPLICATE_BROKER_DEAL')
        seen.add(did)
    pids=set()
    if order and order.get('positionId') is not None:pids.add(integer(order['positionId'],1))
    opening=[];closing=[]
    for deal in book['deals']:
        if order and integer(deal.get('orderId'),1)==integer(order['orderId'],1) and 'closePositionDetail' not in deal:
            if integer(deal.get('filledVolume'))>0:
                if integer(deal.get('dealStatus'),1) not in (2,3):raise SafetyError('INVALID_ENTRY_DEAL')
                pids.add(integer(deal.get('positionId'),1));opening.append(deal)
    if len(pids)>1:raise SafetyError('FIRST_ORDER_MULTIPLE_POSITIONS')
    owned=[p for p in book['positions'] if integer(p.get('positionId'),1) in pids]
    for deal in book['deals']:
        if integer(deal.get('positionId')) in pids and 'closePositionDetail' in deal and integer(deal.get('filledVolume'))>0:
            if integer(deal.get('dealStatus'),1) not in (2,3):raise SafetyError('INVALID_EXIT_DEAL')
            closing.append(deal)
    opened=sum(integer(d['filledVolume'],1) for d in opening)
    closed=sum(integer(d['filledVolume'],1) for d in closing)
    current=sum(integer(p['tradeData']['volume'],1) for p in owned)
    if order:
        td=order['tradeData'];symbol=integer(td['symbolId'],1);side=integer(td['tradeSide'],1)
        if side not in (1,2):raise SafetyError('OWNED_ENTRY_IDENTITY_MISMATCH')
        for deal in opening+closing:
            expected=side if deal in opening else 3-side
            if integer(deal.get('symbolId'),1)!=symbol or integer(deal.get('tradeSide'),1)!=expected:raise SafetyError('DEAL_IDENTITY_MISMATCH')
            if 'closePositionDetail' in deal and integer(deal['closePositionDetail'].get('closedVolume'),1)!=integer(deal['filledVolume'],1):raise SafetyError('EXIT_VOLUME_MISMATCH')
        for position in owned:
            if integer(position['tradeData']['symbolId'],1)!=symbol or integer(position['tradeData']['tradeSide'],1)!=side:raise SafetyError('POSITION_IDENTITY_MISMATCH')
    if closed>opened or opened-closed!=current:raise SafetyError('POSITION_DEAL_RECONCILIATION_MISMATCH')
    return order,owned,opened,closed


def audit_projection(book):
    def td(value):return {k:value[k] for k in ('symbolId','volume','tradeSide','openTimestamp') if k in value}
    output={k:book[k] for k in ('ctidTraderAccountId','history_complete','at')}
    fields={
        'orders':('orderId','clientOrderId','orderType','orderStatus','positionId','executedVolume','limitPrice','stopLoss','takeProfit','relativeStopLoss','relativeTakeProfit'),
        'positions':('positionId','positionStatus','price','stopLoss','takeProfit','usedMargin','moneyDigits'),
        'deals':('dealId','orderId','positionId','symbolId','volume','filledVolume','executionPrice','tradeSide','dealStatus','executionTimestamp','commission','moneyDigits')}
    for key,allowed in fields.items():
        output[key]=[]
        for raw in book[key]:
            item={k:raw[k] for k in allowed if k in raw}
            if 'tradeData' in raw:item['tradeData']=td(raw['tradeData'])
            if 'closePositionDetail' in raw:item['closePositionDetail']={k:raw['closePositionDetail'][k] for k in ('closedVolume','entryPrice','grossProfit','commission','swap','moneyDigits') if k in raw['closePositionDetail']}
            output[key].append(item)
    return output
