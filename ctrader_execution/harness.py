"""Attended, one-entry Razor DEMO CLI. Default commands are read-only.

Trading OAuth and run --execute require separate private owner approvals. No
command is run at import. Public output is fixed codes/sanitized summaries only.
"""
import argparse
from dataclasses import asdict,replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
import json
from pathlib import Path
import secrets
import time
from uuid import uuid4

from control_plane.contracts import Quote,TradeIntent
from control_plane.operations import read_controls,STRATEGIES
from ctrader_readonly.client import Reader,money,rows,one
from ctrader_readonly.config import Config,SafetyError,private_read,private_write
from ctrader_readonly.oauth import Token
from ctrader_readonly.protocol import integer
from .approvals import paths,profile,number,require_baseline,validate_test,require_role,local_confirmation
from .oauth import TradingToken,TradingOAuthFlow
from .ledger import Journal,read_status,inspect_private_file
from .schema import SymbolSpec,order_frame,frame,RiskLimits,protected
from .transport import TradeClient,owned_state,audit_projection,observed_exposure

UTC=timezone.utc


def stopped_controls(path):
    value=read_controls(path)
    if value['entry_suspended'] is not True or {s['strategy'] for s in value['strategies']}!=set(STRATEGIES) or any(s['state']!='STOPPED' for s in value['strategies']):
        raise SafetyError('ALL_STRATEGIES_MUST_BE_STOPPED_AND_SUSPENDED')


def collect_baseline(config,reader,intended,now=time.time):
    identity=reader.verify_identity()
    if not identity['broker_name_matches_configuration']:raise SafetyError('BROKER_IDENTITY_MISMATCH')
    trader=reader._read('trader')['trader']
    return {'profile':profile(config),'account_id':config.demo_account_id,
        'display_login':config.expected_demo_login,'broker_name':identity['broker_name'],
        'currency':identity['currency'],'mode':identity['account_mode'],
        'balance':str(money(trader,'balance')),'intended_balance':str(number(intended)),
        'observed_at':now(),'confirmed':False}


def market_state(client):
    client.refresh()
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        client.poll();status=client.status()
        if status['quote_fresh'] and status['snapshot_fresh']:break
    else:raise SafetyError('FRESH_MARKET_SNAPSHOT_REQUIRED')
    if status['pending_count'] or status['position_count']:raise SafetyError('EMPTY_DEMO_BOOK_REQUIRED')
    q=status['quote'];quote=Quote(D(q['bid']),D(q['ask']),datetime.fromisoformat(q['timestamp_utc']))
    spec=status['symbol']
    native=SymbolSpec(client._symbol_id,int(D(spec['lot_units'])*100),int(D(spec['minimum_units'])*100),
        int(D(spec['maximum_units'])*100),int(D(spec['step_units'])*100),spec['price_digits'],
        spec['stop_distance'],spec['target_distance'],False,spec['distance_unit']=='POINTS')
    trader=client._read('trader')['trader']
    return {'spec':native,'quote':quote,'balance':money(trader,'balance'),
        'buy_margin':client._margin['expected_buy'],'sell_margin':client._margin['expected_sell'],
        'at':client.wall(),'money_digits':integer(trader.get('moneyDigits'))}


def prepare(config,approval,baseline,market,now):
    validate_test(approval,config,now);require_baseline(baseline,config,now)
    if market['balance']!=number(baseline['balance'],zero=True):raise SafetyError('ACTUAL_BALANCE_CHANGED_RECONFIRM_LOCALLY')
    ui=approval['ui'];spec=market['spec']
    pairs=((D(spec.min_hundredths)/spec.lot_hundredths,number(ui['minimum_lots'])),
        (D(spec.max_hundredths)/spec.lot_hundredths,number(ui['maximum_lots'])),
        (D(spec.step_hundredths)/spec.lot_hundredths,number(ui['step_lots'])),
        (D(spec.lot_hundredths)/100,number(ui['units_per_lot'])))
    if any(actual!=expected for actual,expected in pairs):raise SafetyError('API_UI_SPEC_MISMATCH')
    spec=replace(spec,independently_verified=True)
    if spec.volume(spec.units_for_lots(D('.01')))!=100:raise SafetyError('MINIMUM_RAZOR_VOLUME_MISMATCH')
    quote=market['quote'];at=datetime.fromtimestamp(now,UTC)
    if not 0<=(at-quote.at).total_seconds()<=5 or now-market['at']>5:raise SafetyError('STALE_TEST_PREFLIGHT')
    if quote.ask-quote.bid>number(approval['max_spread']):raise SafetyError('TEST_SPREAD_LIMIT')
    side=approval['side'];sign=1 if side=='BUY' else -1
    price=(quote.ask if sign==1 else quote.bid) if approval['order_type']=='MARKET' else number(approval['limit_price'])
    stop_distance=number(approval['stop_distance']);target_distance=number(approval['target_distance'])
    if stop_distance<number(approval['stop_floor'],zero=True):raise SafetyError('OWNER_STOP_FLOOR')
    intent=TradeIntent('manual-test','manual-test','manual_test','manual-test','1','RAZOR-DEMO','XAUUSD',side,D(1),price,
        price-sign*stop_distance,at,at+timedelta(seconds=30),order_type=approval['order_type'],take_profit=price+sign*target_distance)
    protected(intent,spec,quote,at)
    risk_policy=RiskLimits(number(approval['risk_cap']),number(approval['risk_cap']),number(approval['risk_cap']),
        D(1),D(1),number(approval['margin_cap']),number(ui['commission_roundtrip_per_lot'],zero=True),
        number(approval['slippage_buffer'],zero=True),number(approval['max_spread']),number(approval['fee_buffer'],zero=True))
    risk=risk_policy.risk(intent,spec)
    if risk>number(approval['risk_cap']):raise SafetyError('TEST_PLANNED_RISK_EXCEEDED')
    margin=market['buy_margin'] if side=='BUY' else market['sell_margin']
    capital=min(market['balance'],number(baseline['intended_balance']))
    if margin<=0 or margin>number(approval['margin_cap']) or margin+number(approval['risk_cap'])>capital:
        raise SafetyError('TEST_MARGIN_OR_FUNDS_LIMIT')
    return intent,spec,{'direction':side,'lots':'0.01','units':'1','entry_reference':str(price),
        'stop_reference':str(intent.stop_loss),'target_reference':str(intent.take_profit),
        'planned_risk_usd':str(risk),'risk_ceiling_usd':approval['risk_cap'],
        'estimated_margin_usd':str(margin),'margin_ceiling_usd':approval['margin_cap']}


def protection_ok(position,approval,spec):
    try:
        td=position['tradeData'];side=integer(td['tradeSide'],1);entry=D(str(position['price']))
        stop=D(str(position.get('stopLoss',0)));target=D(str(position.get('takeProfit',0)))
        if side!=(1 if approval['side']=='BUY' else 2):return False
        if not all(x.is_finite() and x>0 for x in (entry,stop,target)):return False
        if not (stop<entry<target if side==1 else target<entry<stop):return False
        if approval['order_type']=='MARKET':
            expected_stop=entry-(1 if side==1 else -1)*number(approval['stop_distance'])
            expected_target=entry+(1 if side==1 else -1)*number(approval['target_distance'])
        else:
            limit=number(approval['limit_price']);sign=1 if side==1 else -1
            expected_stop=limit-sign*number(approval['stop_distance']);expected_target=limit+sign*number(approval['target_distance'])
        tolerance=D(1).scaleb(-spec.digits)
        return abs(stop-expected_stop)<=tolerance and abs(target-expected_target)<=tolerance
    except (KeyError,ValueError,ArithmeticError,TypeError):return False


class OneTest:
    def __init__(self,config,approval,baseline,client,journal,*,confirm=local_confirmation,controls=None):
        self.config=config;self.approval=approval;self.baseline=baseline
        self.client=client;self.journal=journal;self.confirm=confirm
        self.controls=controls or (lambda:stopped_controls(approval['controls_database']))
        self.spec=None;self.since_ms=None;self.stage='INITIALIZATION';self.submission_error=None;self.exposure=None

    def connect(self,token,phase='INITIAL'):
        self.stage=phase+'_BROKER_AUTHORIZATION'
        self.client.authorize(token)
        self.stage=phase+'_BROKER_SNAPSHOT'
        self.client.refresh()

    def _book(self):
        self.stage='BROKER_RECONCILIATION'
        entry=self.journal.entry()
        since=max(0,int(entry['at']*1000)-1000)
        try:book=self.client.snapshot(since)
        except Exception:
            # A failed history request must not conceal a known open account
            # position. Observation only: incomplete facts never authorize close.
            try:
                current=self.client._read('reconcile')
                self.exposure=observed_exposure({'orders':rows(current,'order'),
                    'positions':rows(current,'position'),'deals':[]},entry['id'],
                    symbol_id=self.spec.symbol_id,side=1 if self.approval['side']=='BUY' else 2,raw_volume=100)
            except Exception:pass
            raise
        self.exposure=observed_exposure(book,entry['id'],symbol_id=self.spec.symbol_id,
            side=1 if self.approval['side']=='BUY' else 2,raw_volume=100)
        self.journal.record(audit_projection(book))
        if book['ctidTraderAccountId']!=self.config.demo_account_id:raise SafetyError('DEMO_ACCOUNT_MISMATCH')
        order,positions,opened,closed=owned_state(book,entry['id'])
        if order:
            td=order['tradeData']
            if integer(td['symbolId'],1)!=self.spec.symbol_id or integer(td['volume'],1)!=100 or integer(td['tradeSide'],1)!=(1 if self.approval['side']=='BUY' else 2):raise SafetyError('OWNED_ENTRY_IDENTITY_MISMATCH')
        if opened>100:raise SafetyError('ENTRY_VOLUME_EXCEEDED_APPROVAL')
        if any(integer(p['tradeData']['symbolId'],1)!=self.spec.symbol_id for p in positions):raise SafetyError('RESPONSE_SYMBOL_MISMATCH')
        return order,positions,opened,closed

    def _action(self,key,kind,body):
        self.stage='EMERGENCY_CLOSE' if kind==2111 else 'PENDING_REMAINDER_CANCELLATION'
        claim,new=self.journal.claim(key,kind,body)
        if not new:return claim['state']
        try:self.journal.reference(claim['id'],self.client.fresh_quote())
        except SafetyError:pass  # Missing quote cannot prevent an approved emergency exit.
        self.client.mutate(frame(claim['id'],kind,body),self.journal,controls_check=self.controls)
        return self.journal.db.execute('SELECT state FROM mutations WHERE id=?',(claim['id'],)).fetchone()[0]

    def _recover_connection(self,token):
        # A fresh authenticated read session, never a resend of an old claim.
        if not self.client._connected:
            self.stage='RECOVERY_BROKER_AUTHORIZATION'
            self.client.authorize(token)
            if self.client.native_spec is None:self.client.refresh()

    def cancel_and_close(self,token,reason):
        self._recover_connection(token)
        order,positions,opened,closed=self._book()
        self.journal.audit(None,reason)
        if order and integer(order.get('orderStatus'),1)==1:
            try:self._action('cancel-remainder',2108,{'ctidTraderAccountId':self.config.demo_account_id,'orderId':integer(order['orderId'],1)})
            except SafetyError:
                self.journal.audit(None,'CRITICAL_CANCEL_REMAINDER_UNKNOWN')
                self._recover_connection(token)
            order,positions,opened,closed=self._book()
        for position in positions:
            pid=integer(position['positionId'],1);raw=integer(position['tradeData']['volume'],1)
            # Full native remaining volume, never round dust up or touch foreign IDs.
            try:self._action('close:'+str(pid),2111,{'ctidTraderAccountId':self.config.demo_account_id,'positionId':pid,'volume':raw})
            except SafetyError:
                self.journal.audit(None,'CRITICAL_CLOSE_OUTCOME_UNKNOWN');self._recover_connection(token)
        deadline=self.client.clock()+10
        while True:
            try:
                order,positions,opened,closed=self._book()
                pending=order is not None and integer(order.get('orderStatus'),1)==1
                if not positions and not pending:break
            except SafetyError as exc:
                if str(exc) not in ('SNAPSHOT_CHANGED_RETRY','PROTECTION_ORDER_REMAINS_AFTER_CLOSE','POSITION_DEAL_RECONCILIATION_MISMATCH') or self.client.clock()>=deadline:raise
            if self.client.clock()>=deadline:break
            self.client.sleep(.25)  # Read-only settlement polling, never another close.
        if positions or pending:
            self.stage='EMERGENCY_CLOSE_RECONCILIATION'
            self.journal.audit(None,'CRITICAL_EXPOSURE_REQUIRES_MANUAL_BROKER_UI')
            raise SafetyError('CRITICAL_EXPOSURE_REQUIRES_MANUAL_BROKER_UI')
        if opened and opened==closed:
            self.journal.outcome(self.journal.entry()['id'],'CLOSED','HISTORY_CONFIRMED_FLAT')
            return 'CLOSED_RECONCILED'
        if order and integer(order.get('orderStatus'),1) in (3,4,5) and not opened:
            self.journal.outcome(self.journal.entry()['id'],'RECONCILED','TERMINAL_NO_FILL_CONFIRMED');return 'NO_FILL_RECONCILED'
        if not order and not opened and self.journal.entry()['state']=='REJECTED':return 'REJECTED_CONFIRMED'
        self.journal.audit(None,'UNKNOWN_ENTRY_NOT_RESUBMITTED');raise SafetyError('UNKNOWN_ENTRY_NOT_RESUBMITTED')

    def run(self,token,*,resume=False):
        self.stage='STRATEGY_CONTROLS'
        self.controls();self.connect(token)
        self.stage='TEST_APPROVAL_VALIDATE'
        approval=self.approval;validate_test(approval,self.config,self.client.wall(),management=resume)
        entry=self.journal.entry()
        if entry:
            if not resume:raise SafetyError('FIRST_ENTRY_ALREADY_CLAIMED_USE_RESUME')
            self.spec=market_spec(self.client)
            self.stage='RECOVERY_LOCAL_CONFIRMATION'
            self.client.disconnect('AWAITING_RECOVERY_CONFIRMATION')
            self.confirm('Recovery only: no new entry. Verify the pinned demo account locally. Type RESUME DEMO '+str(self.config.expected_demo_login),
                'RESUME DEMO '+str(self.config.expected_demo_login))
            self.connect(token,phase='RECOVERY')
            return self.cancel_and_close(token,'OWNER_CONFIRMED_RECOVERY')
        if resume:raise SafetyError('NO_ENTRY_TO_RECOVER')
        self.stage='INITIAL_MARKET_VALIDATION'
        before=market_state(self.client)
        self.stage='INITIAL_RISK_VALIDATION'
        intent,self.spec,proposal=prepare(self.config,approval,self.baseline,before,self.client.wall())
        message='DEMO manual test only. Broker '+self.config.expected_broker+'; displayed login '+str(self.config.expected_demo_login)+'.\n'+json.dumps(proposal,indent=2)+'\nOne entry maximum. Includes residual cancellation and full emergency/normal closure. No guaranteed loss cap. Type ONE DEMO '+str(self.config.expected_demo_login)
        # No quote receiver or heartbeat runs during controlling-terminal input.
        # Reauthorize afresh afterward; retain every risk/ownership/send gate.
        self.client.disconnect('AWAITING_OWNER_CONFIRMATION')
        self.stage='LOCAL_CONFIRMATION'
        self.confirm(message,'ONE DEMO '+str(self.config.expected_demo_login))
        self.stage='STRATEGY_CONTROLS'
        self.controls()
        self.connect(token,phase='POST_CONFIRMATION')
        self.stage='POST_CONFIRMATION_MARKET_VALIDATION'
        after=market_state(self.client)
        self.stage='POST_CONFIRMATION_RISK_VALIDATION'
        intent,self.spec,_=prepare(self.config,approval,self.baseline,after,self.client.wall())
        cid=uuid4().hex;request=order_frame(self.config.demo_account_id,self.spec,cid,intent,after['quote'],intent.created_at)
        self.stage='DURABLE_ENTRY_CLAIM'
        claim,new=self.journal.claim('first-entry',2106,request['payload'],cid=cid)
        if not new:raise SafetyError('FIRST_ENTRY_ALREADY_CLAIMED')
        self.journal.reference(cid,after['quote'])
        self.stage='SUBMISSION'
        try:self.client.mutate(request,self.journal,controls_check=self.controls)
        except SafetyError as exc:
            self.submission_error=exc
            self.journal.audit(cid,'ENTRY_UNKNOWN_RECONCILING_WITHOUT_RESEND');self._recover_connection(token)
        # Bounded polling: accepted is not filled and presence is not protection.
        deadline=self.client.clock()+15
        while self.client.clock()<deadline:
            order,positions,opened,closed=self._book()
            if positions:
                self.stage='NATIVE_PROTECTION_VERIFY'
                if any(not protection_ok(p,approval,self.spec) for p in positions):
                    return 'PROTECTION_FAILURE_'+self.cancel_and_close(token,'CRITICAL_NATIVE_PROTECTION_MISSING_OR_MISMATCHED')
                self.journal.audit(cid,'NATIVE_POSITION_PROTECTION_VERIFIED')
                if opened<100:
                    result=self.cancel_and_close(token,'PARTIAL_ENTRY_CANCEL_AND_CLOSE')
                    return 'PARTIAL_ENTRY_'+result
                # This attended first test always ends flat; no strategy activation.
                return self.cancel_and_close(token,'BOUNDED_TEST_NORMAL_CLOSE')
            if opened and closed==opened:return self.cancel_and_close(token,'NATIVE_EXIT_ALREADY_CONFIRMED')
            if order and integer(order.get('orderStatus'),1) in (3,4,5):return self.cancel_and_close(token,'TERMINAL_ENTRY_NO_FILL')
            self.client.sleep(.25)
        return self.cancel_and_close(token,'BOUNDED_FILL_WAIT_EXPIRED')


def market_spec(client):
    s=client.status()['symbol']
    return SymbolSpec(client._symbol_id,int(D(s['lot_units'])*100),int(D(s['minimum_units'])*100),
        int(D(s['maximum_units'])*100),int(D(s['step_units'])*100),s['price_digits'],s['stop_distance'],s['target_distance'],True,s['distance_unit']=='POINTS')


def view_reader(config):
    client=Reader(config);client.authorize(Token.load(config.token_path));return client


def read_ui_facts():
    """Hidden terminal input, explicit format errors; never store raw answers."""
    import getpass
    try:
        with open('/dev/tty','w') as terminal:
            terminal.write('Six comma-separated numbers required: min lots, max lots, step lots, units/lot, VERIFIED round-trip commission USD/lot, reviewed minimum stop PRICE distance. No currency signs. Unknown commission basis/minimum charge or stop evidence: cancel, do not guess.\n')
            terminal.flush()
            answer=getpass.getpass('UI specification facts: ',stream=terminal)
    except EOFError:raise SafetyError('UI_INPUT_EOF') from None
    except OSError:raise SafetyError('LOCAL_OWNER_TTY_REQUIRED') from None
    if not answer.strip():raise SafetyError('UI_INPUT_EMPTY')
    fields=[v.strip() for v in answer.split(',')]
    if len(fields)!=6:raise SafetyError('UI_INPUT_FIELD_COUNT')
    if any(not v for v in fields):raise SafetyError('UI_INPUT_MISSING_VALUE')
    try:values=[str(number(v,zero=i>=4)) for i,v in enumerate(fields)]
    except SafetyError:raise SafetyError('UI_INPUT_INVALID_NUMBER') from None
    ui=dict(zip(('minimum_lots','maximum_lots','step_lots','units_per_lot','commission_roundtrip_per_lot'),values[:5]))
    return ui,values[5]


def execution_failure(config,private_paths,stage,exc,exposure=None):
    """Read-only evidence classification; a consumed send is not proof of bytes.

    Never manufacture a no-send conclusion if inspection fails. Never expose
    arbitrary exception strings or private row contents in diagnostics.
    """
    safe={'LOCAL_OWNER_TTY_REQUIRED','LOCAL_CONFIRMATION_NOT_ACCEPTED','OWNER_APPROVAL_EXPIRED',
        'BASELINE_CONFIRMATION_STALE','EXPLICIT_OWNER_APPROVAL_REQUIRED','FIRST_ENTRY_ALREADY_CLAIMED',
        'FIRST_ENTRY_ALREADY_CLAIMED_USE_RESUME','NO_ENTRY_TO_RECOVER','TOKEN_EXPIRED_REAUTHORIZE',
        'TRADING_TOKEN_EXPIRED_REAUTHORIZE','TRADING_TOKEN_ROLE_OR_ACCOUNT_MISMATCH',
        'INVALID_TRADING_TOKEN','REMOTE_TRADING_SCOPE_REQUIRED','TRADING_TOKEN_ACCOUNT_MISMATCH',
        'LIVE_OR_UNKNOWN_ACCOUNT_GRANT_REJECTED','BROKER_IDENTITY_MISMATCH','DISPLAY_LOGIN_MISMATCH',
        'DEMO_ACCOUNT_REQUIRED','ACCOUNT_CURRENCY_MISMATCH','ACCOUNT_MODE_MISMATCH',
        'RAZOR_USD_HEDGED_REQUIRED','PRIVATE_FILE_MODE_REQUIRED','PRIVATE_DIRECTORY_MODE_REQUIRED',
        'FIRST_TEST_JOURNAL_ALREADY_BOUND','STALE_QUOTE','HEARTBEAT_TIMEOUT','STALE_TEST_PREFLIGHT',
        'SNAPSHOT_CHANGED_RETRY','TEST_SPREAD_LIMIT','TEST_MARGIN_OR_FUNDS_LIMIT','TEST_PLANNED_RISK_EXCEEDED',
        'FRESH_MARKET_SNAPSHOT_REQUIRED','EMPTY_DEMO_BOOK_REQUIRED','API_UI_SPEC_MISMATCH',
        'ACTUAL_BALANCE_CHANGED_RECONFIRM_LOCALLY','ALL_STRATEGIES_MUST_BE_STOPPED_AND_SUSPENDED',
        'NETWORK_CONNECT_FAILED','NETWORK_SEND_FAILED','NETWORK_RECEIVE_FAILED','REQUEST_TIMEOUT',
        'UNKNOWN_BROKER_OUTCOME','UNKNOWN_ENTRY_NOT_RESUBMITTED','HISTORY_INCOMPLETE',
        'HISTORY_COMPLETENESS_UNKNOWN','POSITION_DEAL_RECONCILIATION_MISMATCH',
        'CRITICAL_EXPOSURE_REQUIRES_MANUAL_BROKER_UI','OWNER_INTERRUPTED',
        'AMBIGUOUS_CLIENT_ORDER_ID','ENTRY_ORDER_EVIDENCE_MISSING','UNSUPPORTED_LINKED_ORDER_TYPE',
        'INVALID_LINKED_ORDER_ROLE','INVALID_LINKED_PROTECTION_ORDER','PROTECTION_ORDER_REMAINS_AFTER_CLOSE',
        'DUPLICATE_BROKER_DEAL','INVALID_ENTRY_DEAL','INVALID_EXIT_DEAL','FIRST_ORDER_MULTIPLE_POSITIONS',
        'OWNED_ENTRY_IDENTITY_MISMATCH','DEAL_IDENTITY_MISMATCH','EXIT_VOLUME_MISMATCH',
        'POSITION_IDENTITY_MISMATCH','HISTORY_INCOMPLETE','HISTORY_COMPLETENESS_UNKNOWN',
        'INVALID_SCHEMA_INTEGER','ENTRY_VOLUME_EXCEEDED_APPROVAL'}
    from .diagnostics import CODES
    safe.update(CODES)
    code=str(exc) if isinstance(exc,SafetyError) and str(exc) in safe else (
        'PRIVATE_FILE_MISSING' if isinstance(exc,FileNotFoundError) else
        'PRIVATE_FILE_ACCESS_DENIED' if isinstance(exc,PermissionError) else
        'JOURNAL_OWNERSHIP_BUSY' if isinstance(exc,BlockingIOError) else 'EXECUTION_VALIDATION_FAILED')
    result={'stage':stage,'code':code,'execution_enabled':False}
    try:
        history=read_status(private_paths['journal'],profile(config))
        result['entry_claims']=history['entry_claims']
        if not history['entry_claims']:
            result.update(state='LOCAL_FAILURE_BEFORE_DISPATCH',dispatch='NOT_CLAIMED',no_order_attempted=True)
        else:
            entry=next(x for x in history['mutations'] if x['kind']==2106)
            result['dispatch']='CONSUMED_NOT_PROOF_OF_TRANSMISSION' if entry['sent'] else 'CLAIMED_NOT_PROVEN_SENT'
            result['state']=history['state']
            result['journal_state']=history['state']
            result['no_blind_resend']=True
            result['operator_action']='Inspect pinned demo UI and reconcile before any recovery'
    except Exception:
        result.update(state='UNKNOWN',dispatch='EVIDENCE_UNAVAILABLE',no_blind_resend=True,
            operator_action='Inspect pinned demo UI immediately; journal could not be verified')
    if exposure is not None:
        result['observed_exposure']=exposure
        if (exposure.get('account_open_positions') or 0)>0 or (exposure.get('account_pending_orders') or 0)>0:
            result.update(state='CRITICAL_OBSERVED_EXPOSURE',
                operator_action='Inspect pinned demo UI immediately; observed exposure requires attention; no blind resend')
    if result.get('state')=='SUBMITTED':
        result.update(state='UNKNOWN_RECONCILIATION',operator_action='Inspect pinned demo UI immediately; fill/protection/closure not confirmed')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('baseline','confirm-baseline','approve-oauth','authorize-trading','approve-test','renew-test','preflight','run','resume','status'))
    parser.add_argument('--config',required=True);parser.add_argument('--intended-balance')
    parser.add_argument('--controls-database',default='.phase3/ops.sqlite')
    parser.add_argument('--execute',action='store_true',help='Still requires private approval and controlling-terminal confirmation')
    parser.add_argument('--side',choices=('BUY','SELL'),default='BUY');parser.add_argument('--order-type',choices=('MARKET','LIMIT'),default='MARKET');parser.add_argument('--limit-price')
    args=parser.parse_args(argv);client=None;journal=None;runner=None;stage='CONFIGURATION'
    try:
        config=Config.load(args.config);profile(config);p=paths(args.config)
        if args.command=='status':
            stage='STATUS_PRIVATE_INSPECTION'
            # Inspect history even after approvals/baseline expire or disappear.
            result=read_status(p['journal'],profile(config))
            result['test_approval_present']=inspect_private_file(p['test'])
            result['trading_token_store_present']=inspect_private_file(p['token'])
            if result['state']=='NOT_STARTED' and not result['test_approval_present'] and result['trading_token_store_present']:
                result['state']='AWAITING_TEST_APPROVAL'
            print(json.dumps(result,indent=2));return 0
        if args.command=='baseline':
            if args.intended_balance is None:raise SafetyError('INTENDED_BALANCE_REQUIRED')
            client=view_reader(config);record=collect_baseline(config,client,args.intended_balance)
            private_write(p['pending'],record)
            print(json.dumps({'pinned_demo_verified':True,'currency':'USD','balance_matches_intended':D(record['balance'])==D(record['intended_balance']),'actual_balance_saved_privately':True,'local_confirmation_pending':True,'execution_enabled':False}));return 0
        if args.command=='confirm-baseline':
            stage='BASELINE_PENDING_READ'
            pending=private_read(p['pending'])
            if pending.get('profile')!=profile(config):raise SafetyError('BASELINE_PROFILE_MISMATCH')
            number(pending.get('intended_balance'))
            stage='BASELINE_READ_ONLY_VERIFY';client=view_reader(config)
            fresh=collect_baseline(config,client,pending['intended_balance'])
            stage='BASELINE_LOCAL_CONFIRMATION'
            text='Confirm pinned Razor DEMO displayed login '+str(config.expected_demo_login)+'; actual USD balance '+fresh['balance']+'; intended demo budget '+fresh['intended_balance']+'. No configuration or balance will change.\nType CONFIRM '+str(config.expected_demo_login)+' '+fresh['balance']+' '+fresh['intended_balance']
            local_confirmation(text,'CONFIRM '+str(config.expected_demo_login)+' '+fresh['balance']+' '+fresh['intended_balance'])
            stage='BASELINE_PRIVATE_PERSISTENCE'
            fresh.update(confirmed=True,confirmed_at=time.time());require_baseline(fresh,config)
            private_write(p['baseline'],fresh)
            require_baseline(private_read(p['baseline']),config)
            print('BASELINE_CONFIRMED_PRIVATELY; NO_EXECUTION_PERMISSION');return 0
        if args.command=='approve-oauth':
            text='Separate permission for DEMO TRADING OAuth only, not orders. Select ONLY the pinned Razor demo account. Type ALLOW TRADING OAUTH '+str(config.expected_demo_login)
            local_confirmation(text,'ALLOW TRADING OAUTH '+str(config.expected_demo_login))
            now=time.time();private_write(p['oauth'],{'id':uuid4().hex,'role':'OAUTH_ONLY','profile':profile(config),'approved':True,'issued_at':now,'expires_at':now+900})
            print('SEPARATE_OAUTH_APPROVAL_SAVED; NO_ORDER_PERMISSION');return 0
        if args.command=='authorize-trading':TradingOAuthFlow(args.config).serve();print('SEPARATE_DEMO_TRADING_TOKEN_SAVED; NO_ORDER_SENT');return 0
        preview=args.command=='preflight' or (args.command=='run' and not args.execute)
        if preview:
            stage='TEST_APPROVAL_READ'
            if not inspect_private_file(p['test']):
                history=read_status(p['journal'],profile(config))
                state='AWAITING_TEST_APPROVAL' if not history['entry_claims'] else history['state']
                print(json.dumps({'state':state,'stage':stage,'code':'TEST_APPROVAL_MISSING',
                    'execution_enabled':False,'no_order_attempted':True}));return 1
        stage='BASELINE_VALIDATE'
        baseline=require_baseline(private_read(p['baseline']),config)
        if args.command=='renew-test':
            from .renewal import renew_test
            def renewing_stage(value):
                nonlocal stage
                stage=value
            renew_test(args.config,config,baseline,view_reader=view_reader,market_state=market_state,
                prepare=prepare,stopped_controls=stopped_controls,confirm=local_confirmation,
                clock=time.time,stage=renewing_stage)
            print('SAME_TEST_APPROVAL_RENEWED_PRIVATELY; JOURNAL_PRESERVED; NO_ORDER_SENT');return 0
        if args.command=='approve-test':
            stage='STRATEGY_CONTROLS'
            stopped_controls(args.controls_database)
            stage='JOURNAL_GUARD'
            prior=read_status(p['journal'],profile(config))
            if prior['entry_claims']:raise SafetyError('FIRST_ENTRY_ALREADY_CLAIMED')
            if prior['journal']!='ABSENT':raise SafetyError('RENEWAL_REQUIRED_FOR_EXISTING_JOURNAL')
            stage='READ_ONLY_MARKET_CHECK'
            client=view_reader(config);market=market_state(client)
            # No receiver/heartbeat runs while a human is entering facts. Close
            # this read session rather than retain queued, aging spot events.
            client.disconnect('AWAITING_LOCAL_SPECIFICATIONS');client=None
            stage='UI_INPUT'
            ui,stop_floor=read_ui_facts()
            stage='UI_EVIDENCE_CONFIRMATION'
            local_confirmation('Review verified commission-rate and configured minimum-charge evidence, the round-trip basis and the stop-distance floor. Actual broker rounding/posting are UNOBSERVED, not certified by this confirmation. For a verified $7/lot round-trip basis at 0.01 lots, the proposal budgets $0.08 rounded commission plus the separate $0.50 fee buffer. Confirm only if this conservative allowance and its uncertainty are acceptable; cancel if rate/minimum evidence is missing or the allowance is insufficient. Type REVIEWED COST ALLOWANCE AND STOPS','REVIEWED COST ALLOWANCE AND STOPS')
            ui.update(same_account_confirmed=True,commission_basis_confirmed=True)
            ui['actual_rounding_posting_observed']=False
            # Human input can exceed the five-second freshness budget. Obtain
            # new evidence; never extend the expiry or reuse the pre-prompt quote.
            stage='READ_ONLY_MARKET_REFRESH'
            client=view_reader(config)
            market=market_state(client)
            now=time.time();approval={'id':uuid4().hex,'profile':profile(config),'role':'ONE_TEST','approved':True,'issued_at':now,'expires_at':now+600,'management_until':now+3600,
                'entry_limit':1,'raw_volume':100,'side':args.side,'order_type':args.order_type,'limit_price':args.limit_price,
                'stop_distance':'2.00','target_distance':'4.00','risk_cap':'5.00','margin_cap':'250.00','slippage_buffer':'0.25','fee_buffer':'0.50','max_spread':'0.50','stop_floor':stop_floor,
                'ui':ui,'emergency_close':True,'cancel_remainder':True,'stop_uncertainty_acknowledged':True,
                'allowed_ops':[2106,2108,2111],'controls_database':str(Path(args.controls_database).resolve()),
                'journal_path':str(p['journal']),'confirmed_balance':baseline['balance'],'intended_balance':baseline['intended_balance']}
            stage='UI_API_RISK_VALIDATION'
            _,_,proposal=prepare(config,approval,baseline,market,now)
            message='One DEMO entry, residual cancellation and full normal/emergency close only. Standard stop may gap. Zero displayed minimum-stop distance does not guarantee acceptance.\n'+json.dumps(proposal,indent=2)+'\nType APPROVE ONE TEST '+str(config.expected_demo_login)
            stage='TEST_LOCAL_CONFIRMATION'
            local_confirmation(message,'APPROVE ONE TEST '+str(config.expected_demo_login))
            stage='TEST_APPROVAL_PERSISTENCE'
            validate_test(approval,config);private_write(p['test'],approval)
            print('ONE_TEST_APPROVAL_SAVED_PRIVATELY; NO_ORDER_SENT');return 0
        stage='TEST_APPROVAL_VALIDATE'
        approval=validate_test(private_read(p['test']),config,management=args.command in ('resume','status'))
        if args.command=='preflight' or (args.command=='run' and not args.execute):
            stage='STRATEGY_CONTROLS'
            stopped_controls(approval['controls_database']);client=view_reader(config)
            stage='PREFLIGHT_MARKET_RISK'
            _,_,proposal=prepare(config,approval,baseline,market_state(client),time.time())
            print(json.dumps({'execution_enabled':False,'preflight':'PASS','proposal':proposal}));return 0
        stage='JOURNAL_OWNERSHIP'
        journal=Journal(p['journal'],profile(config),approval['id'])
        if not args.execute:raise SafetyError('EXECUTE_FLAG_AND_LOCAL_APPROVAL_REQUIRED')
        stage='TRADING_TOKEN_VALIDATE'
        token=TradingToken.load(p['token'],config.token_path,config.demo_account_id)
        stage='CLIENT_AUTHORITY_VALIDATE'
        client=TradeClient(config,approval)
        runner=OneTest(config,approval,baseline,client,journal)
        result=runner.run(token,resume=args.command=='resume')
        print(result+'; ENTRY_SLOT_PERMANENTLY_SPENT');return 2 if result.startswith(('PARTIAL_ENTRY_','PROTECTION_FAILURE_','REJECTED_')) else 0
    except KeyboardInterrupt:
        if args.command=='renew-test':
            print(json.dumps({'state':'BLOCKED','stage':stage,'code':'OWNER_INTERRUPTED','execution_enabled':False,'no_order_attempted':True}));return 2
        if args.command in ('run','resume') and args.execute:
            try:failure=execution_failure(config,p,runner.stage if runner else stage,SafetyError('OWNER_INTERRUPTED'),runner.exposure if runner else None)
            except Exception:failure={'state':'UNKNOWN','stage':stage,'code':'LOCAL_EVIDENCE_UNAVAILABLE','execution_enabled':False}
            print(json.dumps(failure));return 2
        if args.command in ('approve-test','preflight') or (args.command=='run' and not args.execute):
            print(json.dumps({'state':'BLOCKED','stage':stage,'code':'OWNER_INTERRUPTED','execution_enabled':False,'no_order_attempted':True}));return 2
        if args.command=='status':
            print(json.dumps({'state':'UNAVAILABLE','code':'OWNER_INTERRUPTED','execution_enabled':False}));return 2
        if args.command=='confirm-baseline':
            print('BASELINE_CONFIRMATION_FAILED stage='+stage+' code=OWNER_INTERRUPTED; NO_ORDER_ATTEMPTED');return 2
        if journal:journal.audit(None,'OWNER_INTERRUPT_REQUIRES_RESUME_NO_RESEND')
        print('STOPPED; POSSIBLE_EXPOSURE_IF_ENTRY_CLAIM_EXISTS: inspect demo broker UI and use recovery; no resend');return 2
    except Exception as exc:
        if args.command=='renew-test':
            safe={'RENEWAL_RECEIPT_INVALID','RENEWAL_RECEIPT_MISSING','RENEWAL_RECEIPT_MISMATCH',
                'RENEWAL_RECEIPT_UNAVAILABLE','RENEWAL_JOURNAL_REQUIRED','RENEWAL_OWNER_FILE_REQUIRED',
                'RENEWAL_OWNER_BUSY','RENEWAL_BINDING_MISMATCH','RENEWAL_MUTATION_EVIDENCE_PRESENT',
                'RENEWAL_EXECUTION_EVIDENCE_PRESENT','RENEWAL_UNEXPLAINED_AUDIT_EVIDENCE',
                'RENEWAL_APPROVAL_CHANGED','RENEWAL_POLICY_CHANGE_FORBIDDEN','RENEWAL_PRIVATE_READBACK_FAILED',
                'RENEWAL_APPROVAL_INVALID','RENEWAL_NOT_REQUIRED','RENEWAL_BASELINE_CHANGE_FORBIDDEN',
                'RENEWAL_BROKER_HISTORY_REVIEW_REQUIRED','RENEWAL_COMMISSION_EVIDENCE_MISSING',
                'RENEWAL_COMMISSION_EVIDENCE_CHANGED','LOCAL_CONFIRMATION_NOT_ACCEPTED','LOCAL_OWNER_TTY_REQUIRED',
                'BASELINE_CONFIRMATION_STALE','LOCAL_BASELINE_CONFIRMATION_REQUIRED','PRIVATE_FILE_MODE_REQUIRED',
                'PRIVATE_DIRECTORY_MODE_REQUIRED','OWNER_APPROVAL_EXPIRED','STALE_QUOTE','HEARTBEAT_TIMEOUT',
                'STALE_TEST_PREFLIGHT','TEST_SPREAD_LIMIT','TEST_MARGIN_OR_FUNDS_LIMIT','TEST_PLANNED_RISK_EXCEEDED',
                'API_UI_SPEC_MISMATCH','ACTUAL_BALANCE_CHANGED_RECONFIRM_LOCALLY','EMPTY_DEMO_BOOK_REQUIRED',
                'BROKER_IDENTITY_MISMATCH','DISPLAY_LOGIN_MISMATCH','ACCOUNT_CURRENCY_MISMATCH',
                'ACCOUNT_MODE_MISMATCH','FRESH_MARKET_SNAPSHOT_REQUIRED','NETWORK_CONNECT_FAILED',
                'NETWORK_SEND_FAILED','NETWORK_RECEIVE_FAILED','SNAPSHOT_CHANGED_RETRY','TOKEN_EXPIRED_REAUTHORIZE',
                'ALL_STRATEGIES_MUST_BE_STOPPED_AND_SUSPENDED','NATIVE_PROTECTION','OWNER_STOP_FLOOR'}
            code=str(exc) if isinstance(exc,SafetyError) and str(exc) in safe else (
                'PRIVATE_FILE_MISSING' if isinstance(exc,FileNotFoundError) else
                'PRIVATE_FILE_ACCESS_DENIED' if isinstance(exc,PermissionError) else 'RENEWAL_VALIDATION_FAILED')
            print(json.dumps({'state':'BLOCKED','stage':stage,'code':code,'execution_enabled':False,'no_order_attempted':True}));return 1
        if args.command in ('run','resume') and args.execute:
            if journal:
                try:journal.audit(None,'CLI_ATTENTION_REQUIRED')
                except Exception:pass
            try:failure=execution_failure(config,p,runner.stage if runner else stage,exc,runner.exposure if runner else None)
            except Exception:failure={'state':'UNKNOWN','stage':stage,'code':'LOCAL_EVIDENCE_UNAVAILABLE','execution_enabled':False}
            print(json.dumps(failure));return 1
        if args.command in ('approve-test','preflight') or (args.command=='run' and not args.execute):
            safe={'UI_INPUT_EOF','UI_INPUT_EMPTY','UI_INPUT_FIELD_COUNT','UI_INPUT_MISSING_VALUE',
                'UI_INPUT_INVALID_NUMBER','LOCAL_OWNER_TTY_REQUIRED','LOCAL_CONFIRMATION_NOT_ACCEPTED',
                'API_UI_SPEC_MISMATCH','RAZOR_MINIMUM_CONTRACT_MISMATCH','OWNER_APPROVAL_EXPIRED',
                'EXPLICIT_OWNER_APPROVAL_REQUIRED','BASELINE_CONFIRMATION_STALE','LOCAL_BASELINE_CONFIRMATION_REQUIRED',
                'PRIVATE_FILE_MODE_REQUIRED','PRIVATE_DIRECTORY_MODE_REQUIRED','PRIVATE_PATH_SYMLINK',
                'FIRST_ENTRY_ALREADY_CLAIMED','RENEWAL_REQUIRED_FOR_EXISTING_JOURNAL','STATUS_JOURNAL_UNREADABLE','STATUS_JOURNAL_BUSY',
                'STATUS_JOURNAL_SCHEMA_INVALID','STATUS_JOURNAL_PROFILE_MISMATCH',
                'STALE_TEST_PREFLIGHT','TEST_SPREAD_LIMIT','TEST_MARGIN_OR_FUNDS_LIMIT','TEST_PLANNED_RISK_EXCEEDED',
                'ACTUAL_BALANCE_CHANGED_RECONFIRM_LOCALLY','OWNER_STOP_FLOOR','NATIVE_PROTECTION',
                'NATIVE_STOP_DISTANCE','NATIVE_TARGET_DISTANCE','PRICE_PRECISION','INVALID_APPROVAL_NUMBER',
                'ALL_STRATEGIES_MUST_BE_STOPPED_AND_SUSPENDED','EMPTY_DEMO_BOOK_REQUIRED',
                'FRESH_MARKET_SNAPSHOT_REQUIRED','BROKER_IDENTITY_MISMATCH','TOKEN_EXPIRED',
                'NETWORK_CONNECT_FAILED','NETWORK_SEND_FAILED','NETWORK_RECEIVE_FAILED','REQUEST_TIMEOUT',
                'STALE_QUOTE','OUT_OF_ORDER_QUOTE','CROSSED_QUOTE','HEARTBEAT_TIMEOUT',
                'SNAPSHOT_CHANGED_RETRY','BROKER_REQUEST_REJECTED','NETWORK_DISCONNECTED',
                'NOT_CONNECTED','TOKEN_EXPIRED_REAUTHORIZE','RESPONSE_ACCOUNT_MISMATCH',
                'DEMO_ACCOUNT_MISMATCH','DISPLAY_LOGIN_MISMATCH','DEMO_ACCOUNT_REQUIRED',
                'ACCOUNT_CURRENCY_MISMATCH','ACCOUNT_MODE_MISMATCH','RESPONSE_SYMBOL_MISMATCH',
                'UNEXPECTED_RESPONSE_SCHEMA','RESPONSE_CORRELATION_MISMATCH',
                'INVALID_SYMBOL_VOLUME','STOP_CONSTRAINTS_UNKNOWN','SYMBOL_TRADING_MODE_UNKNOWN',
                'MARGIN_SNAPSHOT_INCOMPLETE','HISTORY_INCOMPLETE','HISTORY_COMPLETENESS_UNKNOWN',
                'RENEWAL_RECEIPT_INVALID','RENEWAL_RECEIPT_MISSING','RENEWAL_RECEIPT_MISMATCH','RENEWAL_RECEIPT_UNAVAILABLE'}
            code=str(exc) if isinstance(exc,SafetyError) and str(exc) in safe else (
                'PRIVATE_FILE_MISSING' if isinstance(exc,FileNotFoundError) else
                'PRIVATE_FILE_ACCESS_DENIED' if isinstance(exc,PermissionError) else 'APPROVAL_OR_PREFLIGHT_VALIDATION_FAILED')
            print(json.dumps({'state':'BLOCKED','stage':stage,'code':code,'execution_enabled':False,'no_order_attempted':True}));return 1
        if args.command=='status':
            safe={'STATUS_JOURNAL_SCHEMA_INVALID','STATUS_JOURNAL_PROFILE_MISMATCH',
                'STATUS_JOURNAL_BUSY','STATUS_JOURNAL_UNREADABLE','PRIVATE_FILE_MODE_REQUIRED',
                'PRIVATE_DIRECTORY_MODE_REQUIRED','PRIVATE_PATH_SYMLINK'}
            code=str(exc) if isinstance(exc,SafetyError) and str(exc) in safe else 'STATUS_INSPECTION_FAILED'
            print(json.dumps({'state':'UNAVAILABLE','code':code,'execution_enabled':False}));return 1
        if args.command=='confirm-baseline':
            safe_codes={'LOCAL_OWNER_TTY_REQUIRED','LOCAL_CONFIRMATION_NOT_ACCEPTED',
                'PRIVATE_FILE_MODE_REQUIRED','PRIVATE_DIRECTORY_MODE_REQUIRED','PRIVATE_PATH_SYMLINK',
                'BASELINE_PROFILE_MISMATCH','INVALID_APPROVAL_NUMBER','BASELINE_CONFIRMATION_STALE',
                'LOCAL_BASELINE_CONFIRMATION_REQUIRED','BROKER_IDENTITY_MISMATCH','DISPLAY_LOGIN_MISMATCH',
                'DEMO_ACCOUNT_REQUIRED','ACCOUNT_CURRENCY_MISMATCH','ACCOUNT_MODE_MISMATCH',
                'TOKEN_EXPIRED_REAUTHORIZE','TOKEN_EXPIRED','NETWORK_CONNECT_FAILED',
                'NETWORK_SEND_FAILED','NETWORK_RECEIVE_FAILED','REQUEST_TIMEOUT'}
            code=str(exc) if isinstance(exc,SafetyError) and str(exc) in safe_codes else (
                'PRIVATE_FILE_MISSING' if isinstance(exc,FileNotFoundError) else
                'PRIVATE_FILE_ACCESS_DENIED' if isinstance(exc,PermissionError) else 'BASELINE_VALIDATION_FAILED')
            print('BASELINE_CONFIRMATION_FAILED stage='+stage+' code='+code+'; NO_ORDER_ATTEMPTED');return 1
        if journal:
            try:journal.audit(None,'CLI_ATTENTION_REQUIRED')
            except Exception:pass
        if journal and journal.entry() and journal.entry()['state'] not in ('CLOSED','REJECTED','RECONCILED'):
            print('CRITICAL_POSSIBLE_EXPOSURE: inspect Razor demo broker UI and private journal; no blind resend')
        else:print('DEMO_TEST_BLOCKED_OR_UNCERTAIN; inspect private journal; no blind resend')
        return 1
    finally:
        if client:client.disconnect('CLI_FINISHED')
        if journal:journal.close()

if __name__=='__main__':raise SystemExit(main())
