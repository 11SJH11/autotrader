"""Central native-unit command preparation, using the shared durable store.

OFFLINE ONLY: constructor requires the exact in-memory cTrader OfflineAdapter.
The Phase 0/1 FakeBroker engine remains fenced and unchanged. No token or network
trade path is accepted here. Risk/quantities use explicit USD/native evidence.
"""
from contextlib import contextmanager
from dataclasses import asdict,dataclass,replace,field
from datetime import datetime,timedelta
from decimal import Decimal as D
import fcntl
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .storage import open_database
from .contracts import RiskDecision,TradeIntent,utc
from .lifecycle import encode
from ctrader_readonly.config import SafetyError,REPOSITORY
from ctrader_readonly.protocol import integer
from ctrader_execution.offline import OfflineAdapter
from ctrader_execution.schema import SymbolSpec,RiskLimits,order_frame,frame,protected


@dataclass(frozen=True)
class MarginEvidence:
    units:D
    required_usd:D
    available_usd:D
    at:datetime
    authoritative:bool=False
    account_id:int|None=field(default=None,repr=False)
    symbol_id:int|None=None
    currency:str='USD'

    def validate(self,units,now,account,symbol):
        utc(self.at)
        if self.authoritative is not True or not 0<=(now-self.at).total_seconds()<=5:raise SafetyError('NATIVE_MARGIN_NOT_VERIFIED')
        if self.account_id!=account or self.symbol_id!=symbol or self.currency!='USD':raise SafetyError('NATIVE_MARGIN_IDENTITY')
        if not isinstance(self.units,D) or not self.units.is_finite() or self.units<=0 or self.required_usd<=0:raise SafetyError('NATIVE_MARGIN_NOT_VERIFIED')
        if self.units!=units or any(not isinstance(v,D) or not v.is_finite() or v<0 for v in (self.required_usd,self.available_usd)):
            raise SafetyError('NATIVE_MARGIN_NOT_VERIFIED')


@dataclass(frozen=True)
class CommandRef:
    command_id:str
    state:str


def decode_intent(data):
    v=json.loads(data)
    for key in ('quantity','entry_price','stop_loss','take_profit'):v[key]=D(v[key]) if v[key] is not None else None
    for key in ('created_at','valid_until'):v[key]=datetime.fromisoformat(v[key])
    return TradeIntent(**v)


class NativeEngine:
    def __init__(self,path,adapter,spec,limits,*,account_alias='RAZOR-DEMO',environment='DEMO',currency='USD',mode='HEDGED'):
        if type(adapter) is not OfflineAdapter:raise SafetyError('OFFLINE_ADAPTER_REQUIRED')
        if environment!='DEMO' or currency!='USD' or mode!='HEDGED' or account_alias!='RAZOR-DEMO':raise SafetyError('NATIVE_ACCOUNT_FENCE')
        if adapter.peer.symbol_id!=spec.symbol_id:raise SafetyError('RESPONSE_SYMBOL_MISMATCH')
        self.adapter,self.spec,self.limits=adapter,spec,limits
        self.account=adapter.peer.account_id;self.alias=account_alias;self.ready=False;self.last_reconciled=None
        self.checkpoint=lambda _:None
        directory=REPOSITORY/'.phase3'/'owners';directory.mkdir(parents=True,exist_ok=True)
        self.ownerfile=open(directory/(hashlib.sha256(str(self.account).encode()).hexdigest()+'.owner'),'a+b')
        try:fcntl.flock(self.ownerfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:self.ownerfile.close();raise SafetyError('NATIVE_EXECUTION_OWNER_BUSY') from None
        try:
            self.db=open_database(path)
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS native_meta(id INTEGER PRIMARY KEY, fingerprint TEXT);
                CREATE TABLE IF NOT EXISTS native_commands(id TEXT PRIMARY KEY,key TEXT UNIQUE,strategy TEXT,payload TEXT,state TEXT,quantity TEXT,risk TEXT,margin TEXT);
                CREATE TABLE IF NOT EXISTS native_actions(id TEXT PRIMARY KEY,key TEXT UNIQUE,command_id TEXT,operation TEXT,target INTEGER,payload TEXT,state TEXT);
                CREATE TABLE IF NOT EXISTS native_orders(id INTEGER PRIMARY KEY,command_id TEXT,data TEXT);
                CREATE TABLE IF NOT EXISTS native_positions(id INTEGER PRIMARY KEY,command_id TEXT,data TEXT);
                CREATE TABLE IF NOT EXISTS native_fills(id INTEGER PRIMARY KEY,command_id TEXT,data TEXT);
                CREATE TABLE IF NOT EXISTS native_rejected_deals(id INTEGER PRIMARY KEY,command_id TEXT,reason TEXT);
                CREATE TABLE IF NOT EXISTS native_configurations(fingerprint TEXT PRIMARY KEY,spec TEXT,policy TEXT);
                CREATE TABLE IF NOT EXISTS native_protection(id INTEGER PRIMARY KEY,stop TEXT,target TEXT);
                CREATE TABLE IF NOT EXISTS native_campaigns(id TEXT PRIMARY KEY,strategy TEXT,level TEXT,direction TEXT,state TEXT);
                CREATE TABLE IF NOT EXISTS native_controls(strategy TEXT PRIMARY KEY,state TEXT);
                CREATE TABLE IF NOT EXISTS native_switch(id INTEGER PRIMARY KEY,suspended INTEGER);
                CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,at TEXT,command_id TEXT,kind TEXT,detail TEXT);
                INSERT OR IGNORE INTO native_switch VALUES(1,1);
            ''')
            digest=hashlib.sha256(encode([asdict(spec),asdict(limits),account_alias,self.account]).encode()).hexdigest()
            prior=self.db.execute('SELECT fingerprint FROM native_meta WHERE id=1').fetchone()
            if prior and prior[0]!=digest:raise SafetyError('NATIVE_CONFIGURATION_CHANGED')
            self.db.execute('INSERT OR IGNORE INTO native_meta VALUES(1,?)',(digest,))
            self.db.execute('INSERT OR IGNORE INTO native_configurations VALUES(?,?,?)',(digest,encode(asdict(spec)),encode(asdict(limits))))
            with self.transaction():
                self.db.execute("UPDATE native_commands SET state='UNKNOWN' WHERE state='SUBMITTING'")
                self.db.execute("UPDATE native_actions SET state='UNKNOWN' WHERE state='SUBMITTING'")
        except Exception:
            if hasattr(self,'db'):self.db.close()
            self.ownerfile.close();raise

    def close(self):self.db.close();self.ownerfile.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:yield;self.db.commit()
        except BaseException:self.db.rollback();self.ready=False;raise

    def audit(self,now,cid,kind,detail):
        utc(now)
        self.db.execute('INSERT INTO audit(at,command_id,kind,detail) VALUES(?,?,?,?)',(now.isoformat(),cid,kind,detail))

    def control(self,strategy,state,now):
        if state not in ('STOPPED','PAUSED','RUNNING') or strategy not in ('t02_reference','t02_long_only','hurricane_observation'):raise SafetyError('INVALID_BOT_CONTROL')
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO native_controls VALUES(?,?)',(strategy,state))
            self.audit(now,None,'BOT_CONTROL',strategy+':'+state)

    def suspend(self,active,now):
        if type(active) is not bool:raise SafetyError('INVALID_ENTRY_SWITCH')
        with self.transaction():
            self.db.execute('UPDATE native_switch SET suspended=?',(int(active),))
            self.audit(now,None,'ENTRY_SUSPENSION','ON' if active else 'OFF_OFFLINE_ONLY')

    def reason(self,intent,quote,margin,now,exclude=None):
        try:
            utc(now)
            if intent.observation_only:return 'OBSERVATION_ONLY'
            if intent.account_scope!=self.alias or intent.instrument!='XAUUSD':return 'NATIVE_ACCOUNT_FENCE'
            if intent.campaign_id:
                campaign=self.db.execute('SELECT strategy,state FROM native_campaigns WHERE id=?',(intent.campaign_id,)).fetchone()
                if not campaign or campaign['strategy']!=intent.strategy_id or campaign['state']!='ACTIVE':return 'CAMPAIGN_INACTIVE'
            if self.db.execute('SELECT suspended FROM native_switch').fetchone()[0]:return 'ENTRY_SUSPENDED'
            state=self.db.execute('SELECT state FROM native_controls WHERE strategy=?',(intent.strategy_id,)).fetchone()
            if not state or state[0]!='RUNNING':return 'STRATEGY_NOT_RUNNING'
            if not self.ready or self.db.execute("SELECT 1 FROM native_commands WHERE state IN ('UNKNOWN','SUBMITTING')").fetchone() or self.db.execute("SELECT 1 FROM native_actions WHERE state IN ('UNKNOWN','SUBMITTING')").fetchone():return 'RECONCILIATION_REQUIRED'
            if self.last_reconciled is None or not 0<=(now-self.last_reconciled).total_seconds()<=5:return 'STALE_RECONCILIATION'
            if not 0<=(now-quote.at).total_seconds()<=self.limits.max_quote_age:return 'STALE_QUOTE'
            if not 0<=(now-intent.created_at).total_seconds()<=90 or now>=intent.valid_until:return 'STALE_SIGNAL'
            if quote.bid<=0 or quote.ask<quote.bid or quote.ask-quote.bid>self.limits.max_spread:return 'SPREAD'
            if intent.order_type=='MARKET' and abs(intent.entry_price-(quote.ask if intent.side=='BUY' else quote.bid))>self.limits.slippage_per_unit_usd:return 'SLIPPAGE'
            protected(intent,self.spec,quote,now);margin.validate(intent.quantity,now,self.account,self.spec.symbol_id)
            risk=self.limits.risk(intent,self.spec)
            if risk>self.limits.order_risk_usd:return 'ORDER_RISK'
            rows=[r for r in self.db.execute('SELECT * FROM native_commands') if r['id']!=exclude and D(r['quantity'])>0]
            units=sum((D(r['quantity']) for r in rows),D(0))+intent.quantity
            total=sum((D(r['risk']) for r in rows),D(0))+risk
            if units>self.limits.total_units:return 'ACCOUNT_EXPOSURE'
            if total>self.limits.total_risk_usd:return 'TOTAL_RISK'
            own=[r for r in rows if r['strategy']==intent.strategy_id]
            max_units,max_risk=self.limits.strategy_units,self.limits.strategy_risk_usd
            for strategy,qty,cap in self.limits.strategies:
                if strategy==intent.strategy_id:max_units,max_risk=qty,cap
            if sum((D(r['quantity']) for r in own),D(0))+intent.quantity>max_units:return 'STRATEGY_EXPOSURE'
            if sum((D(r['risk']) for r in own),D(0))+risk>max_risk:return 'STRATEGY_RISK'
            required=sum((D(r['margin']) for r in rows),D(0))+margin.required_usd
            if required>min(margin.available_usd,self.limits.margin_usd):return 'NATIVE_MARGIN'
            return 'ACCEPTED'
        except (SafetyError,ValueError,TypeError,ArithmeticError) as exc:
            return str(exc) if isinstance(exc,SafetyError) else 'INVALID_NATIVE_INPUT'

    def enqueue(self,intent,quote,margin,now):
        with self.transaction():
            row=self.db.execute('SELECT * FROM native_commands WHERE key=?',(intent.key,)).fetchone()
            reason='DUPLICATE_KEY' if row else self.reason(intent,quote,margin,now)
            ref=CommandRef(row['id'],row['state']) if row else None
            if reason=='ACCEPTED':
                cid=uuid4().hex
                self.db.execute('INSERT INTO native_commands VALUES(?,?,?,?,?,?,?,?)',(cid,intent.key,intent.strategy_id,
                    encode(asdict(intent)),'QUEUED',str(intent.quantity),str(self.limits.risk(intent,self.spec)),str(margin.required_usd)))
                ref=CommandRef(cid,'QUEUED')
            self.audit(now,ref.command_id if ref else None,'NATIVE_RISK',reason)
        return RiskDecision(intent.intent_id,reason=='ACCEPTED',reason,now,'native-usd-offline-1'),ref

    def dispatch(self,cid,quote,margin,now):
        try:return self._dispatch(cid,quote,margin,now)
        except Exception:
            self.ready=False
            with self.transaction():
                self.db.execute("UPDATE native_commands SET state='UNKNOWN' WHERE id=? AND state='SUBMITTING'",(cid,))
                self.audit(now,cid,'NATIVE_DISPATCH_ATTENTION','UNCONFIRMED_RESULT_OR_RECONCILIATION')
            raise

    def _dispatch(self,cid,quote,margin,now):
        current=self.db.execute('SELECT state FROM native_commands WHERE id=?',(cid,)).fetchone()
        if not current or current[0]!='QUEUED':return
        self.reconcile(now)
        with self.transaction():
            row=self.db.execute('SELECT * FROM native_commands WHERE id=?',(cid,)).fetchone()
            if not row or row['state']!='QUEUED':return
            intent=decode_intent(row['payload']);reason=self.reason(intent,quote,margin,now,exclude=cid)
            if reason!='ACCEPTED':
                self.db.execute("UPDATE native_commands SET state='REJECTED',quantity='0',risk='0',margin='0' WHERE id=?",(cid,))
                self.audit(now,cid,'DISPATCH_REJECTED',reason);return
            outbound=order_frame(self.account,self.spec,cid,intent,quote,now)
            self.db.execute("UPDATE native_commands SET state='SUBMITTING' WHERE id=?",(cid,))
            self.audit(now,cid,'NATIVE_CLAIM','NEW_ORDER_OFFLINE')
        self.checkpoint('native_claim')
        try:response=self.adapter.send(outbound)
        except Exception:response=None
        self.checkpoint('native_broker')
        with self.transaction():
            self.db.execute("UPDATE native_commands SET state='UNKNOWN' WHERE id=?",(cid,))
            if response is not None:self._ack(response,cid,now,entry=True)
            self.audit(now,cid,'NATIVE_RESULT','UNKNOWN' if response is None else 'ACK_RECEIVED')
        self.ready=False
        if response is not None:self.reconcile(now)

    def _ack(self,response,cid,now,entry=False):
        if not isinstance(response,dict) or response.get('clientMsgId')!=cid:raise SafetyError('NATIVE_RESPONSE_CORRELATION')
        p=response.get('payload')
        if not isinstance(p,dict) or integer(p.get('ctidTraderAccountId'),1)!=self.account:raise SafetyError('RESPONSE_ACCOUNT_MISMATCH')
        if response.get('payloadType')==2132:
            table='native_commands' if entry else 'native_actions'
            self.db.execute('UPDATE '+table+" SET state='REJECTED' WHERE id=?",(cid,))
            if entry:self.db.execute("UPDATE native_commands SET quantity='0',risk='0',margin='0' WHERE id=?",(cid,))
            allowed={'TRADING_BAD_VOLUME','TRADING_BAD_STOPS','TRADING_BAD_PRICES','TRADING_DISABLED',
                'TRADING_NOT_ALLOWED','NOT_ENOUGH_MONEY','MAX_EXPOSURE_REACHED','ORDER_NOT_FOUND',
                'POSITION_NOT_FOUND','PROTECTION_IS_TOO_CLOSE_TO_MARKET','REQUEST_FREQUENCY_EXCEEDED'}
            code=p.get('errorCode')
            self.audit(now,cid,'BROKER_REJECTED',code if code in allowed else 'BROKER_REJECTED');return
        if response.get('payloadType')!=2126 or integer(p.get('executionType'),1) not in (2,3,4,5,6,7,11):raise SafetyError('UNEXPECTED_EXECUTION_EVENT')
        if entry:
            if not p.get('order') or p['order'].get('clientOrderId')!=cid:raise SafetyError('ORDER_IDENTITY_MISMATCH')
            if p['executionType'] not in (2,3,7,11):raise SafetyError('UNEXPECTED_ENTRY_EVENT')
            state={2:'ACCEPTED',3:'FILLED',7:'REJECTED',11:'PARTIAL'}[p['executionType']]
            self.db.execute('UPDATE native_commands SET state=? WHERE id=?',(state,cid))
        else:
            action=self.db.execute('SELECT * FROM native_actions WHERE id=?',(cid,)).fetchone()
            if not action:raise SafetyError('UNCLAIMED_EXECUTION_EVENT')
            values=json.loads(action['payload']);wire=values['wire'];op=action['operation']
            expected={'CANCEL':5,'AMEND_PENDING':4,'MODIFY_POSITION':4,'CLOSE':3}[op]
            effect='APPLIED'
            if p['executionType']!=expected:
                if op in ('CANCEL','AMEND_PENDING') and p['executionType'] in (3,11):effect='TOO_LATE'
                else:raise SafetyError('UNEXPECTED_ACTION_OUTCOME')
            if op in ('CANCEL','AMEND_PENDING'):
                if integer(p.get('order',{}).get('orderId'),1)!=action['target']:raise SafetyError('ACTION_TARGET_MISMATCH')
            else:
                if integer(p.get('position',{}).get('positionId'),1)!=action['target']:raise SafetyError('ACTION_TARGET_MISMATCH')
            if op=='MODIFY_POSITION' and any(D(str(p['position'].get(k,0)))!=D(str(wire[k])) for k in ('stopLoss','takeProfit')):raise SafetyError('NATIVE_PROTECTION_NOT_CONFIRMED')
            if op=='CLOSE' and integer(p.get('deal',{}).get('filledVolume'),1)!=wire['volume']:raise SafetyError('PARTIAL_CLOSE_OUTCOME_UNKNOWN')
            self.db.execute('UPDATE native_actions SET state=? WHERE id=?',(effect,cid))

    def reconcile(self,now):
        self.ready=False
        try:
            book=self.adapter.snapshot()
            if integer(book.get('ctidTraderAccountId'),1)!=self.account or book.get('history_complete') is not True:raise SafetyError('NATIVE_RECONCILIATION_INCOMPLETE')
            with self.transaction():self._book(book,now)
        except Exception:
            self.ready=False
            with self.transaction():self.audit(now,None,'NATIVE_RECONCILIATION','BLOCKED')
            raise
        unknown=self.db.execute("SELECT 1 FROM native_commands WHERE state IN ('UNKNOWN','SUBMITTING') UNION SELECT 1 FROM native_actions WHERE state IN ('UNKNOWN','SUBMITTING')").fetchone()
        self.ready=not unknown
        self.last_reconciled=now if self.ready else None
        with self.transaction():self.audit(now,None,'NATIVE_RECONCILIATION','COMPLETE' if self.ready else 'UNKNOWN_OUTCOME')

    def _book(self,book,now):
        commands={r['id']:r for r in self.db.execute('SELECT * FROM native_commands')}
        orders={};positions={};fills={};by_cid={};oid_cid={}
        for o in book['orders']:
            oid=integer(o.get('orderId'),1);cid=o.get('clientOrderId');td=o.get('tradeData',{})
            if oid in orders or cid not in commands or cid in by_cid:raise SafetyError('UNOWNED_OR_DUPLICATE_ORDER')
            intent=decode_intent(commands[cid]['payload'])
            if integer(td.get('symbolId'),1)!=self.spec.symbol_id or integer(td.get('tradeSide'),1)!=(1 if intent.side=='BUY' else 2):raise SafetyError('ORDER_IDENTITY_MISMATCH')
            total=integer(td.get('volume'),1);executed=integer(o.get('executedVolume',0));status=integer(o.get('orderStatus'),1)
            if total!=self.spec.volume(intent.quantity) or executed>total or status not in (1,2,3,4,5):raise SafetyError('NATIVE_ORDER_VOLUME_MISMATCH')
            if status==2 and executed!=total:raise SafetyError('ORDER_TERMINAL_VOLUME_MISMATCH')
            if integer(o.get('orderType'),1)!=(1 if intent.order_type=='MARKET' else 2):raise SafetyError('NATIVE_ORDER_TYPE_MISMATCH')
            expected_fields={'limitPrice':intent.entry_price,'stopLoss':intent.stop_loss,'takeProfit':intent.take_profit} if intent.order_type=='LIMIT' else {
                'relativeStopLoss':abs(intent.entry_price-intent.stop_loss)*100000,'relativeTakeProfit':abs(intent.take_profit-intent.entry_price)*100000}
            if status==1 and any(D(str(o.get(k,0)))!=v for k,v in expected_fields.items()):raise SafetyError('PENDING_PROTECTION_MISMATCH')
            orders[oid]={'command':cid,'volume':total,'executed':executed,'status':status,'order_id':oid}
            by_cid[cid]=orders[oid];oid_cid[oid]=cid
        for p in book['positions']:
            pid=integer(p.get('positionId'),1);td=p.get('tradeData',{});cid=td.get('label')
            if pid in positions or cid not in commands:raise SafetyError('UNOWNED_OR_DUPLICATE_POSITION')
            if integer(td.get('symbolId'),1)!=self.spec.symbol_id:raise SafetyError('RESPONSE_SYMBOL_MISMATCH')
            volume=integer(td.get('volume'));status=integer(p.get('positionStatus'),1)
            if status not in (1,2) or (status==1)!=(volume>0):raise SafetyError('POSITION_VOLUME_MISMATCH')
            intent=decode_intent(commands[cid]['payload']);entry=D(str(p['price']))
            if integer(td.get('tradeSide'),1)!=(1 if intent.side=='BUY' else 2):raise SafetyError('POSITION_SIDE_MISMATCH')
            stop=D(str(p.get('stopLoss',0)));tp=D(str(p.get('takeProfit',0)))
            override=self.db.execute('SELECT stop,target FROM native_protection WHERE id=?',(pid,)).fetchone()
            sign=1 if intent.side=='BUY' else -1
            expected_stop=D(override[0]) if override else intent.stop_loss if intent.order_type=='LIMIT' else entry-sign*abs(intent.entry_price-intent.stop_loss)
            expected_tp=D(override[1]) if override else intent.take_profit if intent.order_type=='LIMIT' else entry+sign*abs(intent.take_profit-intent.entry_price)
            if status==1 and (not entry.is_finite() or stop!=expected_stop or tp!=expected_tp):raise SafetyError('NATIVE_PROTECTION_NOT_CONFIRMED')
            positions[pid]={'command':cid,'position_id':pid,'quantity':str(D(volume)/100),'entry':str(entry),'stop':str(stop),'target':str(tp),'status':'OPEN' if status==1 else 'CLOSED','side':intent.side}
        # Real reconcile responses omit closed positions. History, not absence,
        # must prove closure. Reconstruct only a fully balanced closed position.
        missing={integer(d.get('positionId')) for d in book['deals'] if integer(d.get('filledVolume'))>0}-set(positions)
        for pid in missing:
            related=[d for d in book['deals'] if integer(d.get('positionId'))==pid and integer(d.get('filledVolume'))>0]
            opening=[d for d in related if 'closePositionDetail' not in d];closing=[d for d in related if 'closePositionDetail' in d]
            cids={oid_cid.get(integer(d.get('orderId'),1)) for d in opening}
            opened=sum(integer(d['filledVolume'],1) for d in opening);closed=sum(integer(d['filledVolume'],1) for d in closing)
            if pid<=0 or not opened or opened!=closed or len(cids)!=1 or None in cids:raise SafetyError('MISSING_OPEN_POSITION')
            cid=cids.pop();original=decode_intent(commands[cid]['payload'])
            entry=sum((D(str(d['executionPrice']))*integer(d['filledVolume'],1) for d in opening),D(0))/opened
            positions[pid]={'command':cid,'position_id':pid,'quantity':'0','entry':str(entry),'stop':'0','target':'0',
                'status':'CLOSED','side':original.side}
        entry_volume={};exit_volume={};rejected_ids=set()
        for d in book['deals']:
            did=integer(d.get('dealId'),1);oid=integer(d.get('orderId'),1)
            deal_status=integer(d.get('dealStatus'),1)
            if deal_status in (4,5,6,7) and integer(d.get('filledVolume'))==0:
                if oid not in oid_cid or integer(d.get('symbolId'),1)!=self.spec.symbol_id:raise SafetyError('UNOWNED_REJECTED_DEAL')
                if did in rejected_ids:raise SafetyError('DUPLICATE_REJECTED_DEAL')
                rejected_ids.add(did)
                reason={4:'LP_REJECTED',5:'INTERNALLY_REJECTED',6:'DEAL_ERROR',7:'DEAL_MISSED'}[deal_status]
                inserted=self.db.execute('INSERT OR IGNORE INTO native_rejected_deals VALUES(?,?,?)',(did,oid_cid[oid],reason)).rowcount
                if inserted:self.audit(now,oid_cid[oid],'REJECTED_DEAL',reason)
                continue
            pid=integer(d.get('positionId'),1)
            if did in fills or pid not in positions or integer(d.get('symbolId'),1)!=self.spec.symbol_id:raise SafetyError('UNOWNED_OR_DUPLICATE_DEAL')
            qty=integer(d.get('filledVolume'),1);kind='EXIT' if 'closePositionDetail' in d else 'ENTRY'
            cid=positions[pid]['command']
            expected_side=1 if positions[pid]['side']=='BUY' else 2
            if kind=='EXIT':expected_side=3-expected_side
            price=D(str(d['executionPrice']))
            if deal_status not in (2,3) or not price.is_finite() or price<=0 or integer(d.get('volume'),1)<qty or integer(d.get('tradeSide'),1)!=expected_side:raise SafetyError('INVALID_EXECUTED_DEAL')
            if kind=='ENTRY' and oid_cid.get(oid)!=cid:raise SafetyError('DEAL_ORDER_MISMATCH')
            mapping=exit_volume if kind=='EXIT' else entry_volume
            mapping[pid]=mapping.get(pid,0)+qty
            fills[did]={'command':cid,'position_id':pid,'quantity':str(D(qty)/100),'price':str(price.normalize()),'kind':kind,'order_id':oid}
        for pid,p in positions.items():
            if D(entry_volume.get(pid,0)-exit_volume.get(pid,0))/100!=D(p['quantity']):raise SafetyError('DEAL_POSITION_BALANCE_MISMATCH')
        for row in self.db.execute('SELECT id,data FROM native_fills'):
            if row['id'] not in fills or encode(fills[row['id']])!=row['data']:raise SafetyError('DEAL_HISTORY_CONFLICT')
        if {r[0] for r in self.db.execute('SELECT id FROM native_rejected_deals')}-rejected_ids:raise SafetyError('REJECTED_HISTORY_CONFLICT')
        for cid,command in commands.items():
            order=by_cid.get(cid)
            if order is None:
                if command['state'] not in ('QUEUED','UNKNOWN','SUBMITTING','REJECTED'):raise SafetyError('MISSING_BROKER_ORDER')
                continue  # Absence never resolves UNKNOWN or releases its reserve.
            pos=[p for p in positions.values() if p['command']==cid and p['status']=='OPEN']
            intent=decode_intent(command['payload'])
            executed_deals=sum((int(D(f['quantity'])*100) for f in fills.values() if f['command']==cid and f['kind']=='ENTRY'),0)
            if executed_deals!=order['executed']:raise SafetyError('ORDER_DEAL_BALANCE_MISMATCH')
            pending=D(order['volume']-order['executed'])/100 if order['status']==1 else D(0)
            units=pending+sum((D(p['quantity']) for p in pos),D(0))
            risk=pending*abs(intent.entry_price-intent.stop_loss)+sum((D(p['quantity'])*abs(D(p['entry'])-D(p['stop'])) for p in pos),D(0))
            risk+=units*self.limits.slippage_per_unit_usd+self.limits.fees(units,self.spec)
            state={1:'PARTIAL' if order['executed'] else 'ACCEPTED',2:'FILLED' if units else 'CLOSED',3:'REJECTED',4:'CANCELLED',5:'CANCELLED'}[order['status']]
            # Conservative full original margin reserve until all exposure exits.
            margin=command['margin'] if units else '0'
            self.db.execute('UPDATE native_commands SET state=?,quantity=?,risk=?,margin=? WHERE id=?',(state,str(units),str(risk),margin,cid))
        for table,items in (('native_orders',orders),('native_positions',positions),('native_fills',fills)):
            if table!='native_fills':self.db.execute('DELETE FROM '+table)
            for bid,item in items.items():self.db.execute('INSERT OR IGNORE INTO '+table+' VALUES(?,?,?)',(bid,item['command'],encode(item)))

    def manage(self,key,operation,target,quote,now,**values):
        try:return self._manage(key,operation,target,quote,now,**values)
        except Exception:
            self.ready=False
            with self.transaction():
                self.db.execute("UPDATE native_actions SET state='UNKNOWN' WHERE key=? AND state='SUBMITTING'",(key,))
                self.audit(now,None,'NATIVE_MANAGEMENT_ATTENTION',operation if operation in ('CANCEL','AMEND_PENDING','MODIFY_POSITION','CLOSE') else 'UNSUPPORTED_OPERATION')
            raise

    def _manage(self,key,operation,target,quote,now,*,units=None,entry=None,stop=None,target_price=None,margin=None):
        utc(now)
        prior=self.db.execute('SELECT id,state FROM native_actions WHERE key=?',(key,)).fetchone()
        if prior:return CommandRef(prior['id'],prior['state'])
        self.reconcile(now)
        if not self.ready:raise SafetyError('RECONCILIATION_REQUIRED')
        if not 0<=(now-quote.at).total_seconds()<=self.limits.max_quote_age:raise SafetyError('STALE_QUOTE')
        with self.transaction():
            if operation in ('CANCEL','AMEND_PENDING'):
                row=self.db.execute('SELECT command_id,data FROM native_orders WHERE id=?',(target,)).fetchone()
                if not row:raise SafetyError('UNKNOWN_OWNED_ORDER')
                order=json.loads(row['data'])
                if order['status']!=1:raise SafetyError('ORDER_NOT_PENDING')
                cid=row['command_id'];intent=decode_intent(self.db.execute('SELECT payload FROM native_commands WHERE id=?',(cid,)).fetchone()[0])
                if operation=='CANCEL':kind=2108;body={'ctidTraderAccountId':self.account,'orderId':target};values={}
                else:
                    if order['executed']:raise SafetyError('PARTIAL_ORDER_AMEND_REQUIRES_REVIEW')
                    updated=replace(intent,quantity=intent.quantity if units is None else units,
                        entry_price=intent.entry_price if entry is None else entry,
                        stop_loss=intent.stop_loss if stop is None else stop,
                        take_profit=intent.take_profit if target_price is None else target_price)
                    protected(updated,self.spec,quote,now)
                    if updated.order_type!='LIMIT':raise SafetyError('ORDER_NOT_LIMIT')
                    if margin is None:raise SafetyError('NATIVE_MARGIN_NOT_VERIFIED')
                    margin.validate(updated.quantity,now,self.account,self.spec.symbol_id)
                    original=self.db.execute('SELECT risk,margin FROM native_commands WHERE id=?',(cid,)).fetchone()
                    if self.limits.risk(updated,self.spec)>D(original['risk']) or margin.required_usd>D(original['margin']):raise SafetyError('RISK_INCREASING_AMENDMENT')
                    kind=2109;body={'ctidTraderAccountId':self.account,'orderId':target,'volume':self.spec.volume(updated.quantity),
                        'limitPrice':self.spec.price(updated.entry_price),'stopLoss':self.spec.price(updated.stop_loss),'takeProfit':self.spec.price(updated.take_profit)}
                    values={'intent':asdict(updated),'margin':str(margin.required_usd)}
            elif operation in ('MODIFY_POSITION','CLOSE'):
                row=self.db.execute('SELECT command_id,data FROM native_positions WHERE id=?',(target,)).fetchone()
                if not row:raise SafetyError('UNKNOWN_OWNED_POSITION')
                pos=json.loads(row['data']);cid=row['command_id']
                if pos['status']!='OPEN':raise SafetyError('POSITION_NOT_OPEN')
                if operation=='CLOSE':
                    units=D(pos['quantity']) if units is None else units
                    raw=self.spec.volume(units,close=True)
                    remaining=D(pos['quantity'])-units
                    if remaining<0 or (remaining>0 and remaining*100<self.spec.min_hundredths):raise SafetyError('INVALID_CLOSE_RESIDUAL')
                    kind=2111;body={'ctidTraderAccountId':self.account,'positionId':target,'volume':raw};values={}
                else:
                    stop=D(pos['stop']) if stop is None else stop
                    target_price=D(pos['target']) if target_price is None else target_price
                    buy=pos['side']=='BUY';old=D(pos['stop'])
                    self.spec.price(stop);self.spec.price(target_price)
                    if (stop<old or stop>=quote.bid) if buy else (stop>old or stop<=quote.ask):raise SafetyError('RISK_INCREASING_OR_INVALID_STOP')
                    if (target_price<=quote.ask) if buy else (target_price>=quote.bid):raise SafetyError('INVALID_NATIVE_TARGET')
                    distance=(quote.bid-stop) if buy else (stop-quote.ask)
                    if distance<D(self.spec.stop_points).scaleb(-self.spec.digits):raise SafetyError('NATIVE_STOP_DISTANCE')
                    kind=2110;body={'ctidTraderAccountId':self.account,'positionId':target,'stopLoss':self.spec.price(stop),'takeProfit':self.spec.price(target_price)}
                    values={'stop':str(stop),'target':str(target_price)}
            else:raise SafetyError('UNSUPPORTED_NATIVE_MANAGEMENT')
            aid=uuid4().hex;outbound=frame(aid,kind,body)
            values['wire']=body
            self.db.execute('INSERT INTO native_actions VALUES(?,?,?,?,?,?,?)',(aid,key,cid,operation,target,encode(values),'SUBMITTING'))
            self.audit(now,aid,'NATIVE_ACTION_CLAIM',operation+'_OFFLINE')
        self.checkpoint('native_action_claim')
        try:response=self.adapter.send(outbound)
        except Exception:response=None
        self.checkpoint('native_action_broker')
        with self.transaction():
            self.db.execute("UPDATE native_actions SET state='UNKNOWN' WHERE id=?",(aid,))
            if response is not None:
                self._ack(response,aid,now)
                self._apply_action(aid)
            self.audit(now,aid,'NATIVE_ACTION_RESULT','UNKNOWN' if response is None else 'ACK_RECEIVED')
        self.ready=False
        if response is not None:self.reconcile(now)
        return CommandRef(aid,self.db.execute('SELECT state FROM native_actions WHERE id=?',(aid,)).fetchone()[0])

    def _apply_action(self,aid):
        action=self.db.execute('SELECT * FROM native_actions WHERE id=?',(aid,)).fetchone()
        if action['state']!='APPLIED':return
        values=json.loads(action['payload'])
        if action['operation']=='AMEND_PENDING':
            self.db.execute('UPDATE native_commands SET payload=?,margin=? WHERE id=?',(encode(values['intent']),values['margin'],action['command_id']))
        elif action['operation']=='MODIFY_POSITION':
            self.db.execute('INSERT OR REPLACE INTO native_protection VALUES(?,?,?)',(action['target'],values['stop'],values['target']))

    def ingest(self,cid,response,now):
        command=self.db.execute('SELECT state FROM native_commands WHERE id=?',(cid,)).fetchone()
        action=self.db.execute('SELECT state FROM native_actions WHERE id=?',(cid,)).fetchone()
        if not command and not action:raise SafetyError('UNCLAIMED_EXECUTION_EVENT')
        if command and command['state'] in ('QUEUED','REJECTED'):raise SafetyError('UNCLAIMED_EXECUTION_EVENT')
        with self.transaction():
            self._ack(response,cid,now,entry=bool(command))
            if action:self._apply_action(cid)
            self.audit(now,cid,'EXECUTION_EVENT','CORRELATED_OFFLINE')
        self.reconcile(now)

    def status(self):
        return {'mode':'OFFLINE_CTRADER_PROTOCOL_PREPARATION','execution_enabled':False,
            'network_execution_implemented':False,'ready_for_offline_decisions':self.ready,
            'entry_suspended':bool(self.db.execute('SELECT suspended FROM native_switch').fetchone()[0]),
            'bots':[dict(r) for r in self.db.execute('SELECT strategy,state FROM native_controls')],
            'commands':[dict(r) for r in self.db.execute('SELECT id,strategy,state,quantity,risk,margin FROM native_commands ORDER BY rowid DESC LIMIT 100')],
            'actions':[dict(r) for r in self.db.execute('SELECT id,operation,state FROM native_actions ORDER BY rowid DESC LIMIT 100')],
            'positions':[json.loads(r[0]) for r in self.db.execute('SELECT data FROM native_positions')],
            'pending_orders':[json.loads(r[0]) for r in self.db.execute('SELECT data FROM native_orders') if json.loads(r[0])['status']==1],
            'recent_audit':[dict(r) for r in self.db.execute('SELECT at,kind,detail FROM audit ORDER BY seq DESC LIMIT 50')]}

    def create_campaign(self,key,strategy,level,direction,now):
        if strategy not in ('hurricane_observation','t02_reference','t02_long_only') or direction not in ('BELOW','ABOVE') or not isinstance(level,D) or not level.is_finite() or level<=0:raise SafetyError('INVALID_NATIVE_CAMPAIGN')
        with self.transaction():
            old=self.db.execute('SELECT strategy,level,direction FROM native_campaigns WHERE id=?',(key,)).fetchone()
            if old and tuple(old)!=(strategy,str(level),direction):raise SafetyError('CAMPAIGN_KEY_CONFLICT')
            self.db.execute("INSERT OR IGNORE INTO native_campaigns VALUES(?,?,?,?,'ACTIVE')",(key,strategy,str(level),direction))
            self.audit(now,None,'NATIVE_CAMPAIGN','REGISTERED')

    def evaluate_campaigns(self,quote,now):
        utc(now)
        if not 0<=(now-quote.at).total_seconds()<=self.limits.max_quote_age:raise SafetyError('STALE_QUOTE')
        if not quote.bid.is_finite() or not quote.ask.is_finite() or quote.bid<=0 or quote.ask<quote.bid:raise SafetyError('INVALID_NATIVE_QUOTE')
        with self.transaction():
            for row in self.db.execute("SELECT * FROM native_campaigns WHERE state='ACTIVE'").fetchall():
                hit=quote.bid<=D(row['level']) if row['direction']=='BELOW' else quote.ask>=D(row['level'])
                if hit:
                    self.db.execute("UPDATE native_campaigns SET state='INVALIDATED' WHERE id=?",(row['id'],))
                    self.audit(now,None,'NATIVE_CAMPAIGN','INVALIDATED')
        self.reconcile(now)
        for row in self.db.execute("SELECT id,data,command_id FROM native_orders").fetchall():
            order=json.loads(row['data'])
            if order['status']!=1:continue
            payload=json.loads(self.db.execute('SELECT payload FROM native_commands WHERE id=?',(row['command_id'],)).fetchone()[0])
            campaign=payload.get('campaign_id')
            if campaign and self.db.execute("SELECT 1 FROM native_campaigns WHERE id=? AND state='INVALIDATED'",(campaign,)).fetchone():
                self.manage('invalidate:'+row['command_id'],'CANCEL',row['id'],quote,now)
