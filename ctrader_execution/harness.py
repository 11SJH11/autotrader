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
from .ledger import Journal
from .schema import SymbolSpec,order_frame,frame,RiskLimits,protected
from .transport import TradeClient,owned_state,audit_projection

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
        self.spec=None;self.since_ms=None

    def connect(self,token):
        self.client.authorize(token);self.client.refresh()

    def _book(self):
        entry=self.journal.entry()
        since=max(0,int(entry['at']*1000)-1000)
        book=self.client.snapshot(since);self.journal.record(audit_projection(book))
        if book['ctidTraderAccountId']!=self.config.demo_account_id:raise SafetyError('DEMO_ACCOUNT_MISMATCH')
        order,positions,opened,closed=owned_state(book,entry['id'])
        if order:
            td=order['tradeData']
            if integer(td['symbolId'],1)!=self.spec.symbol_id or integer(td['volume'],1)!=100 or integer(td['tradeSide'],1)!=(1 if self.approval['side']=='BUY' else 2):raise SafetyError('OWNED_ENTRY_IDENTITY_MISMATCH')
        if opened>100:raise SafetyError('ENTRY_VOLUME_EXCEEDED_APPROVAL')
        if any(integer(p['tradeData']['symbolId'],1)!=self.spec.symbol_id for p in positions):raise SafetyError('RESPONSE_SYMBOL_MISMATCH')
        return order,positions,opened,closed

    def _action(self,key,kind,body):
        claim,new=self.journal.claim(key,kind,body)
        if not new:return claim['state']
        self.client.mutate(frame(claim['id'],kind,body),self.journal,controls_check=self.controls)
        return self.journal.db.execute('SELECT state FROM mutations WHERE id=?',(claim['id'],)).fetchone()[0]

    def _recover_connection(self,token):
        # A fresh authenticated read session, never a resend of an old claim.
        if not self.client._connected:
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
        order,positions,opened,closed=self._book()
        pending=order is not None and integer(order.get('orderStatus'),1)==1
        if positions or pending:
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
        self.controls();self.connect(token)
        approval=self.approval;validate_test(approval,self.config,self.client.wall(),management=resume)
        entry=self.journal.entry()
        if entry:
            if not resume:raise SafetyError('FIRST_ENTRY_ALREADY_CLAIMED_USE_RESUME')
            self.spec=market_spec(self.client)
            self.confirm('Recovery only: no new entry. Verify the pinned demo account locally. Type RESUME DEMO '+str(self.config.expected_demo_login),
                'RESUME DEMO '+str(self.config.expected_demo_login))
            return self.cancel_and_close(token,'OWNER_CONFIRMED_RECOVERY')
        if resume:raise SafetyError('NO_ENTRY_TO_RECOVER')
        before=market_state(self.client)
        intent,self.spec,proposal=prepare(self.config,approval,self.baseline,before,self.client.wall())
        message='DEMO manual test only. Broker '+self.config.expected_broker+'; displayed login '+str(self.config.expected_demo_login)+'.\n'+json.dumps(proposal,indent=2)+'\nOne entry maximum. Includes residual cancellation and full emergency/normal closure. No guaranteed loss cap. Type ONE DEMO '+str(self.config.expected_demo_login)
        self.confirm(message,'ONE DEMO '+str(self.config.expected_demo_login))
        self.controls()
        after=market_state(self.client)
        intent,self.spec,_=prepare(self.config,approval,self.baseline,after,self.client.wall())
        cid=uuid4().hex;request=order_frame(self.config.demo_account_id,self.spec,cid,intent,after['quote'],intent.created_at)
        claim,new=self.journal.claim('first-entry',2106,request['payload'],cid=cid)
        if not new:raise SafetyError('FIRST_ENTRY_ALREADY_CLAIMED')
        try:self.client.mutate(request,self.journal,controls_check=self.controls)
        except SafetyError:self.journal.audit(cid,'ENTRY_UNKNOWN_RECONCILING_WITHOUT_RESEND');self._recover_connection(token)
        # Bounded polling: accepted is not filled and presence is not protection.
        deadline=self.client.clock()+15
        while self.client.clock()<deadline:
            order,positions,opened,closed=self._book()
            if positions:
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


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('baseline','confirm-baseline','approve-oauth','authorize-trading','approve-test','preflight','run','resume','status'))
    parser.add_argument('--config',required=True);parser.add_argument('--intended-balance')
    parser.add_argument('--controls-database',default='.phase3/ops.sqlite')
    parser.add_argument('--execute',action='store_true',help='Still requires private approval and controlling-terminal confirmation')
    parser.add_argument('--side',choices=('BUY','SELL'),default='BUY');parser.add_argument('--order-type',choices=('MARKET','LIMIT'),default='MARKET');parser.add_argument('--limit-price')
    args=parser.parse_args(argv);client=None;journal=None
    try:
        config=Config.load(args.config);profile(config);p=paths(args.config)
        if args.command=='baseline':
            if args.intended_balance is None:raise SafetyError('INTENDED_BALANCE_REQUIRED')
            client=view_reader(config);record=collect_baseline(config,client,args.intended_balance)
            private_write(p['pending'],record)
            print(json.dumps({'pinned_demo_verified':True,'currency':'USD','balance_matches_intended':D(record['balance'])==D(record['intended_balance']),'actual_balance_saved_privately':True,'local_confirmation_pending':True,'execution_enabled':False}));return 0
        if args.command=='confirm-baseline':
            pending=private_read(p['pending']);client=view_reader(config)
            fresh=collect_baseline(config,client,pending['intended_balance'])
            text='Confirm pinned Razor DEMO displayed login '+str(config.expected_demo_login)+'; actual USD balance '+fresh['balance']+'; intended demo budget '+fresh['intended_balance']+'. No configuration or balance will change.\nType CONFIRM '+str(config.expected_demo_login)+' '+fresh['balance']+' '+fresh['intended_balance']
            local_confirmation(text,'CONFIRM '+str(config.expected_demo_login)+' '+fresh['balance']+' '+fresh['intended_balance'])
            fresh.update(confirmed=True,confirmed_at=time.time());private_write(p['baseline'],fresh)
            print('BASELINE_CONFIRMED_PRIVATELY; NO_EXECUTION_PERMISSION');return 0
        if args.command=='approve-oauth':
            text='Separate permission for DEMO TRADING OAuth only, not orders. Select ONLY the pinned Razor demo account. Type ALLOW TRADING OAUTH '+str(config.expected_demo_login)
            local_confirmation(text,'ALLOW TRADING OAUTH '+str(config.expected_demo_login))
            now=time.time();private_write(p['oauth'],{'id':uuid4().hex,'role':'OAUTH_ONLY','profile':profile(config),'approved':True,'issued_at':now,'expires_at':now+900})
            print('SEPARATE_OAUTH_APPROVAL_SAVED; NO_ORDER_PERMISSION');return 0
        if args.command=='authorize-trading':TradingOAuthFlow(args.config).serve();print('SEPARATE_DEMO_TRADING_TOKEN_SAVED; NO_ORDER_SENT');return 0
        baseline=require_baseline(private_read(p['baseline']),config)
        if args.command=='approve-test':
            stopped_controls(args.controls_database)
            if p['journal'].exists():
                import sqlite3
                with sqlite3.connect(p['journal'].as_uri()+'?mode=ro',uri=True) as db:
                    if db.execute('SELECT 1 FROM mutations WHERE entry_slot=1').fetchone():raise SafetyError('FIRST_ENTRY_ALREADY_CLAIMED')
            client=view_reader(config);market=market_state(client)
            # Owner enters independent same-account UI facts, not API defaults.
            from .approvals import RISK_KEYS
            import getpass
            with open('/dev/tty','r+') as terminal:
                terminal.write('Enter matching Razor UI min lots, max lots, step lots, units/lot, round-trip commission USD/lot, reviewed minimum stop price distance, comma-separated. No credentials.\n');terminal.flush()
                fields=getpass.getpass('UI specification facts: ',stream=terminal).split(',')
            if len(fields)!=6:raise SafetyError('SIX_UI_FACTS_REQUIRED')
            stop_floor=str(number(fields[5],zero=True))
            ui=dict(zip(('minimum_lots','maximum_lots','step_lots','units_per_lot','commission_roundtrip_per_lot'),[str(number(v,zero=i==4)) for i,v in enumerate(fields[:5])]))
            ui.update(same_account_confirmed=True,commission_basis_confirmed=True)
            now=time.time();approval={'id':uuid4().hex,'profile':profile(config),'role':'ONE_TEST','approved':True,'issued_at':now,'expires_at':now+600,'management_until':now+3600,
                'entry_limit':1,'raw_volume':100,'side':args.side,'order_type':args.order_type,'limit_price':args.limit_price,
                'stop_distance':'2.00','target_distance':'4.00','risk_cap':'5.00','margin_cap':'250.00','slippage_buffer':'0.25','fee_buffer':'0.50','max_spread':'0.50','stop_floor':stop_floor,
                'ui':ui,'emergency_close':True,'cancel_remainder':True,'stop_uncertainty_acknowledged':True,
                'allowed_ops':[2106,2108,2111],'controls_database':str(Path(args.controls_database).resolve()),
                'journal_path':str(p['journal']),'confirmed_balance':baseline['balance'],'intended_balance':baseline['intended_balance']}
            _,_,proposal=prepare(config,approval,baseline,market,now)
            message='One DEMO entry, residual cancellation and full normal/emergency close only. Standard stop may gap. Zero displayed minimum-stop distance does not guarantee acceptance.\n'+json.dumps(proposal,indent=2)+'\nType APPROVE ONE TEST '+str(config.expected_demo_login)
            local_confirmation(message,'APPROVE ONE TEST '+str(config.expected_demo_login));private_write(p['test'],approval)
            print('ONE_TEST_APPROVAL_SAVED_PRIVATELY; NO_ORDER_SENT');return 0
        approval=validate_test(private_read(p['test']),config,management=args.command in ('resume','status'))
        if args.command=='preflight' or (args.command=='run' and not args.execute):
            stopped_controls(approval['controls_database']);client=view_reader(config)
            _,_,proposal=prepare(config,approval,baseline,market_state(client),time.time())
            print(json.dumps({'execution_enabled':False,'preflight':'PASS','proposal':proposal}));return 0
        journal=Journal(p['journal'],profile(config),approval['id'])
        if args.command=='status':print(json.dumps(journal.summary(),indent=2));return 0
        if not args.execute:raise SafetyError('EXECUTE_FLAG_AND_LOCAL_APPROVAL_REQUIRED')
        token=TradingToken.load(p['token'],config.token_path,config.demo_account_id)
        client=TradeClient(config,approval)
        result=OneTest(config,approval,baseline,client,journal).run(token,resume=args.command=='resume')
        print(result+'; ENTRY_SLOT_PERMANENTLY_SPENT');return 2 if result.startswith(('PARTIAL_ENTRY_','PROTECTION_FAILURE_','REJECTED_')) else 0
    except KeyboardInterrupt:
        if journal:journal.audit(None,'OWNER_INTERRUPT_REQUIRES_RESUME_NO_RESEND')
        print('STOPPED; POSSIBLE_EXPOSURE_IF_ENTRY_CLAIM_EXISTS: inspect demo broker UI and use recovery; no resend');return 2
    except Exception:
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
