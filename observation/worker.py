"""Persistent observation worker with an isolated, explicitly fictional paper book.

Virtual market time and real process heartbeat/receipt time are distinct. Adapter
claims, observations, paper transitions and source cursor commit atomically.
"""
from dataclasses import asdict,dataclass
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from control_plane.contracts import Quote,utc
from signal_adapters.common import ObservationJournal,ObservationSizing,identity,Decision
from signal_adapters.hurricane import HurricaneAdapter
from signal_adapters.t02 import T02Adapter,T02Policy,Candle,NY,first_setup,session_key

UTC=timezone.utc


def encode(data):return json.dumps(data,default=str,sort_keys=True)


@dataclass(frozen=True)
class ReplayEvent:
    index: int
    candle: Candle
    received_at: datetime
    messages: tuple = ()

    @property
    def at(self):return self.candle.time+timedelta(minutes=1)

    @property
    def digest(self):return hashlib.sha256(encode([self.index,asdict(self.candle),[asdict(m) for m in self.messages]]).encode()).hexdigest()


class Worker:
    def __init__(self,path,source_hash,policies=None,wall=None,mono=time.monotonic,controls_path=None):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.owner=open(str(self.path.resolve())+'.owner','a+b')
        try:fcntl.flock(self.owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.owner.close();raise RuntimeError('WORKER_OWNER_BUSY') from None
        self.wall=wall or (lambda:datetime.now(UTC));self.mono=mono
        self.controls_path=controls_path
        self.last_mono=mono()
        self.policies=tuple(policies or (T02Policy.reference(),T02Policy.long_only()))
        if len({p.strategy_id for p in self.policies})!=len(self.policies):
            self.owner.close();raise ValueError('DUPLICATE_POLICY')
        self.journal=ObservationJournal(self.path);self.db=self.journal.db
        self.db.row_factory=sqlite3.Row
        self.checkpoint=lambda stage:None
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS worker_meta(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS replay_events(idx INTEGER PRIMARY KEY,digest TEXT NOT NULL,at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS replay_candles(at TEXT PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS worker_decisions(id TEXT PRIMARY KEY,event_idx INTEGER,strategy TEXT,at TEXT,reason TEXT,setup TEXT,intents TEXT);
        CREATE TABLE IF NOT EXISTS paper_orders(id TEXT PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_positions(id TEXT PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS worker_errors(seq INTEGER PRIMARY KEY,at TEXT,code TEXT);
        ''')
        configuration=[asdict(p) for p in self.policies]
        controls_identity=identity(str(Path(controls_path).resolve())) if controls_path else None
        row=self.db.execute('SELECT data FROM worker_meta').fetchone()
        if row:
            self.meta=json.loads(row[0])
            if self.meta['source_hash']!=source_hash or encode(self.meta['policies'])!=encode(configuration):
                self.close();raise ValueError('REPLAY_CONFIGURATION_CHANGED')
            if self.meta.get('controls_identity')!=controls_identity:
                self.close();raise ValueError('REPLAY_CONTROLS_CHANGED')
        else:
            self.meta={'mode':'OFFLINE_REPLAY','real_execution_enabled':False,'source_hash':source_hash,
                'policies':configuration,'cursor':-1,'last_signal':None,'last_quote':None,
                'last_event':None,'heartbeat':None,'status':'STARTING','fresh_input':False,'last_error':None}
            self.meta['controls_identity']=controls_identity
        self.adapters=[T02Adapter(self.journal,p) for p in self.policies]
        self.hurricane=HurricaneAdapter(self.journal)
        self.heartbeat('RUNNING')

    def _save_meta(self):
        self.db.execute('INSERT OR REPLACE INTO worker_meta VALUES(1,?)',(encode(self.meta),))

    def heartbeat(self,status=None):
        now=self.wall();utc(now)
        received=self.meta.get('last_received_at')
        if received and (now-datetime.fromisoformat(received)).total_seconds()>5:
            self.meta['fresh_input']=False
            if self.meta['status']=='RUNNING':self.meta['status']='WAITING_FRESH_INPUT'
        self.meta['heartbeat']=now.isoformat()
        if status:self.meta['status']=status
        self._save_meta()

    def error(self,code):
        self.meta.update(status='ATTENTION_REQUIRED',fresh_input=False,last_error=code)
        self.db.execute('INSERT INTO worker_errors(at,code) VALUES(?,?)',(self.wall().isoformat(),code))
        self.heartbeat()

    def close(self):
        if getattr(self,'journal',None):self.journal.close();self.journal=None
        if not self.owner.closed:self.owner.close()

    def stop(self):
        self.meta['fresh_input']=False
        self.heartbeat('STOPPED')

    def _put(self,table,data):
        self.db.execute('INSERT OR REPLACE INTO '+table+' VALUES(?,?)',(data['id'],encode(data)))

    def _paper_step(self,candle):
        # This book is not the central execution queue and has no broker adapter.
        for row in self.db.execute('SELECT data FROM paper_orders').fetchall():
            order=json.loads(row[0])
            if order['status']!='PENDING':continue
            side=order['side'];entry=D(order['entry']);stop=D(order['stop'])
            hit=order['order_type']=='MARKET' or (candle.low<=entry if side=='BUY' else candle.high>=entry)
            if not hit:continue
            fill=candle.open if order['order_type']=='MARKET' else min(candle.open,entry) if side=='BUY' else max(candle.open,entry)
            if (side=='BUY' and stop>=fill) or (side=='SELL' and stop<=fill):
                order['status']='REJECTED_PROTECTION';self._put('paper_orders',order);continue
            order['status']='FILLED';self._put('paper_orders',order)
            target=fill+abs(fill-stop) if side=='BUY' else fill-abs(fill-stop)
            if not order['strategy'].startswith('t02_'):target=D(order['target'])
            self._put('paper_positions',{'id':order['id'],'strategy':order['strategy'],'side':side,
                'entry':str(fill),'stop':str(stop),'target':str(target),'initial_risk':str(abs(fill-stop)),
                'quantity':order['quantity'],'status':'OPEN','opened_at':candle.time.isoformat(),'be_pending':False,'be_armed':False})
        for row in self.db.execute('SELECT data FROM paper_positions').fetchall():
            p=json.loads(row[0])
            if p['status']!='OPEN':continue
            entry=D(p['entry']);stop=D(p['stop']);target=D(p['target']);buy=p['side']=='BUY'
            if p['be_pending']:
                stop=entry;p['stop']=str(entry);p['be_pending']=False;p['be_armed']=True
            exit=None
            if buy:
                if candle.open<=stop:exit=candle.open
                elif candle.open>=target:exit=candle.open
                elif candle.low<=stop:exit=stop
                elif candle.high>=target:exit=target
            else:
                if candle.open>=stop:exit=candle.open
                elif candle.open<=target:exit=candle.open
                elif candle.high>=stop:exit=stop
                elif candle.low<=target:exit=target
            if exit is not None:
                p.update(status='CLOSED',exit=str(exit),closed_at=candle.time.isoformat(),
                    pnl=str((exit-entry)*(1 if buy else -1)*D(p['quantity'])))
            elif p['strategy'].startswith('t02_') and not p['be_armed']:
                threshold=entry+D('.68')*D(p['initial_risk'])*(1 if buy else -1)
                if candle.high>=threshold if buy else candle.low<=threshold:p['be_pending']=True
            self._put('paper_positions',p)

    def process(self,event: ReplayEvent):
        now=self.wall();utc(now);utc(event.received_at)
        previous_wall=self.meta.get('heartbeat')
        if self.mono()<self.last_mono or (previous_wall and now<datetime.fromisoformat(previous_wall)-timedelta(seconds=2)):
            self.error('CLOCK_REGRESSION');return 'CLOCK_REGRESSION'
        self.last_mono=self.mono()
        if not 0<=(now-event.received_at).total_seconds()<=5:
            self.error('STALE_INPUT');return 'STALE_INPUT'
        prior=self.db.execute('SELECT digest FROM replay_events WHERE idx=?',(event.index,)).fetchone()
        if prior:
            if prior[0]!=event.digest:self.error('EVENT_ID_CONFLICT');return 'EVENT_ID_CONFLICT'
            self.heartbeat();return 'DUPLICATE_EVENT'
        if event.index!=self.meta['cursor']+1:
            self.error('OUT_OF_ORDER_EVENT');return 'OUT_OF_ORDER_EVENT'
        if self.meta['last_event'] and event.at<=datetime.fromisoformat(self.meta['last_event']):
            self.error('MARKET_CLOCK_REGRESSION');return 'MARKET_CLOCK_REGRESSION'
        old_meta=json.loads(encode(self.meta))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._paper_step(event.candle)
            self.db.execute('INSERT INTO replay_candles VALUES(?,?)',(event.candle.time.isoformat(),encode(asdict(event.candle))))
            start=(event.at-timedelta(hours=8)).isoformat()
            self.db.execute('DELETE FROM replay_candles WHERE at<?',(start,))
            history=[json.loads(r[0]) for r in self.db.execute('SELECT data FROM replay_candles ORDER BY at')]
            candles=[Candle(datetime.fromisoformat(c['time']),*(D(c[k]) for k in ('open','high','low','close'))) for c in history]
            quote=Quote(event.candle.close,event.candle.close,event.at)
            controls=None;controls_error=False
            if self.controls_path:
                try:
                    from control_plane.operations import read_controls
                    controls=read_controls(self.controls_path)
                except Exception:controls_error=True
            def blocked(strategy):
                if controls_error:return 'CONTROLS_UNAVAILABLE'
                if controls is not None:
                    from control_plane.operations import entry_reason
                    return entry_reason(controls,strategy)
                return None
            for adapter in self.adapters:
                reason=blocked(adapter.policy.strategy_id)
                result=Decision(reason) if reason else adapter.evaluate(candles,quote,event.at,ObservationSizing(D('.01'))) if event.at.minute%15==0 else Decision('NO_CONFIRMATION_BOUNDARY')
                setup=first_setup(candles,session_key(event.at)) if event.at.minute%15==0 and session_key(event.at) else None
                self._decision(event,adapter.policy.strategy_id,result,detected=asdict(setup) if setup else None)
            for message in event.messages:
                reason=blocked(self.hurricane.strategy_id)
                result=Decision(reason) if reason and message.event=='NEW' else self.hurricane.evaluate(message,quote,event.at,ObservationSizing(D('.01')))
                self._decision(event,self.hurricane.strategy_id,result,identity(message.setup_key))
                if result.invalidated:
                    # Only local pending paper projections are invalidated.
                    for row in self.db.execute('SELECT data FROM paper_orders').fetchall():
                        order=json.loads(row[0])
                        if order.get('source_key')==identity(message.setup_key) and order['status']=='PENDING':
                            order['status']='CANCELLED_OBSERVATION_EDIT';self._put('paper_orders',order)
            self.db.execute('INSERT INTO replay_events VALUES(?,?,?)',(event.index,event.digest,event.at.isoformat()))
            self.meta.update(cursor=event.index,last_event=event.at.isoformat(),last_quote={'bid':str(quote.bid),'ask':str(quote.ask),'at':quote.at.isoformat()},
                heartbeat=now.isoformat(),last_received_at=event.received_at.isoformat(),status='RUNNING',fresh_input=True,last_error=None)
            self._save_meta();self.checkpoint('before_commit');self.db.commit()
            return 'PROCESSED'
        except BaseException:
            self.db.rollback();self.meta=old_meta
            raise

    def _decision(self,event,strategy,result,source_key=None,detected=None):
        # One record per policy/event (or stable Hurricane message key).
        key=identity(event.index,strategy,source_key)
        setup=detected
        if detected:self.meta["last_signal"]=str(detected["confirmed_at"])
        if result.intents:
            self.meta['last_signal']=event.at.isoformat()
            if setup is None:setup={'side':result.intents[0].side,'stop':str(result.intents[0].stop_loss)}
        intents=[asdict(i) for i in result.intents]
        self.db.execute('INSERT INTO worker_decisions VALUES(?,?,?,?,?,?,?)',
            (key,event.index,strategy,event.at.isoformat(),result.reason,encode(setup),encode(intents)))
        for proposal in result.intents:
            if not proposal.observation_only:raise ValueError('OBSERVATION_FENCE_REQUIRED')
            self._put('paper_orders',{'id':proposal.intent_id,'strategy':strategy,'side':proposal.side,
                'entry':str(proposal.entry_price),'stop':str(proposal.stop_loss),'target':str(proposal.take_profit),
                'quantity':str(proposal.quantity),'order_type':proposal.order_type,'status':'PENDING',
                'created_at':event.at.isoformat(),'source_key':source_key,'mode':'FICTIONAL_PAPER_ORDER'})
