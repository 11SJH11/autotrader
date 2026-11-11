"""Phase 0 durable queue, extended by lifecycle.py without replacing its claims."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import json
import sqlite3
from uuid import uuid4

from .contracts import BrokerObservation, OrderCommand, Quote, RiskDecision, State, TradeIntent, utc

D = Decimal


@dataclass(frozen=True)
class Policy:
    max_quantity: Decimal = D('2')
    max_strategy_quantity: Decimal = D('1')
    max_order_risk: Decimal = D('10')
    max_total_risk: Decimal = D('20')
    max_spread: Decimal = D('1')
    max_slippage: Decimal = D('1')
    max_quote_age: int = 5
    max_signal_age: int = 60
    max_strategy_risk: Decimal = D('15')
    quantity_step: Decimal = D('0.01')
    min_quantity: Decimal = D('0.01')
    max_order_quantity: Decimal = D('2')
    margin_per_unit: Decimal = D('10')
    available_margin: Decimal = D('100')
    strategy_limits: tuple = ()  # (strategy, max_quantity, max_risk)

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == 'strategy_limits':
                continue
            if isinstance(value, Decimal) and (not value.is_finite() or value <= 0):
                raise ValueError('INVALID_POLICY: '+name)
            if isinstance(value, int) and value <= 0:
                raise ValueError('INVALID_POLICY: '+name)
        names = set()
        for name, quantity, risk in self.strategy_limits:
            if name in names or not all(isinstance(v,Decimal) and v.is_finite() and v > 0 for v in (quantity,risk)):
                raise ValueError('INVALID_STRATEGY_POLICY')
            names.add(name)



class BaseFakeBroker:
    """Synthetic units only. No credentials, URLs, sockets or external imports."""
    def __init__(self):
        self.account = 'SIM-DEMO'
        self.environment = 'SIMULATION'
        self.orders = {}
        self.calls = 0
        self.mode = 'normal'

    def guard(self, account):
        if self.environment != 'SIMULATION' or self.account != 'SIM-DEMO' or account != self.account:
            raise ValueError('ACCOUNT_ENVIRONMENT_MISMATCH')

    def submit(self, command, intent, now):
        self.guard(intent.account_scope)  # Hard adapter fence on every call.
        self.calls += 1
        if self.mode == 'timeout_before':
            raise TimeoutError()
        observation = BrokerObservation(command, 'fake-' + command, State.FILLED,
                                        intent.entry_price, intent.quantity, now)
        self.orders[command] = observation
        if self.mode == 'timeout_after':
            raise TimeoutError()
        return observation

    def snapshot(self):
        self.guard('SIM-DEMO')
        return dict(self.orders)


class BaseEngine:
    def __init__(self, path, broker: FakeBroker, policy=Policy()):
        from .lifecycle import FakeBroker
        if type(broker) is not FakeBroker:
            raise TypeError('Only the offline FakeBroker is supported')
        self.broker, self.policy = broker, policy
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS commands (
          id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, intent_id TEXT NOT NULL,
          strategy TEXT NOT NULL, instrument TEXT NOT NULL, side TEXT NOT NULL,
          quantity TEXT NOT NULL, risk TEXT NOT NULL, payload TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('QUEUED','SUBMITTING','UNKNOWN','FILLED','REJECTED')));
        CREATE TABLE IF NOT EXISTS audit (
          seq INTEGER PRIMARY KEY, at TEXT NOT NULL, command_id TEXT,
          kind TEXT NOT NULL, detail TEXT NOT NULL);
        PRAGMA user_version=1;
        ''')
        self.ready = False

    def close(self):
        self.db.close()

    def audit(self, now, command, kind, detail):
        self.db.execute('INSERT INTO audit(at,command_id,kind,detail) VALUES(?,?,?,?)',
                        (now.isoformat(), command, kind, detail))

    def reconcile(self, now, snapshot=None):
        utc(now)
        self.ready = False
        snapshot = self.broker.snapshot() if snapshot is None else snapshot  # Failure closes startup gate.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE commands SET state='UNKNOWN' WHERE state='SUBMITTING'")
            rows = self.db.execute("SELECT * FROM commands WHERE state='UNKNOWN'").fetchall()
            for row in rows:
                obs = snapshot.get(row['id'])
                if obs and obs.state == State.FILLED and obs.executed_volume == D(row['quantity']):
                    self.db.execute("UPDATE commands SET state='FILLED' WHERE id=?", (row['id'],))
                    self.audit(now, row['id'], 'FILLED', json.dumps(asdict(obs), default=str))
            known = {r[0] for r in self.db.execute('SELECT id FROM commands')}
            unknown = self.db.execute("SELECT count(*) FROM commands WHERE state='UNKNOWN'").fetchone()[0]
            self.audit(now, None, 'RECONCILIATION', 'blocked' if unknown or set(snapshot)-known else 'ready')
            self.db.commit()
            self.ready = not unknown and not (set(snapshot)-known)
        except BaseException:
            self.db.rollback()
            raise

    def reason(self, intent, quote, now):
        p = self.policy
        try:
            self.broker.guard(intent.account_scope)
        except ValueError:
            return 'ACCOUNT_ENVIRONMENT_MISMATCH'
        if not self.ready or self.db.execute("SELECT 1 FROM commands WHERE state IN ('UNKNOWN','SUBMITTING')").fetchone():
            return 'RECONCILIATION_REQUIRED'
        nums = [intent.quantity, intent.entry_price, quote.bid, quote.ask]
        if intent.stop_loss is not None:
            nums.append(intent.stop_loss)
        if intent.take_profit is not None:
            nums.append(intent.take_profit)
        if not all(isinstance(n, Decimal) and n.is_finite() for n in nums):
            return 'INVALID_NUMERIC'
        if intent.instrument != 'SIM-XAU' or intent.side not in ('BUY','SELL') or intent.order_type not in ('MARKET', 'LIMIT'):
            return 'UNSUPPORTED_CONTRACT'
        if intent.quantity < p.min_quantity or intent.quantity > p.max_order_quantity or intent.quantity % p.quantity_step or min(intent.entry_price, quote.bid) <= 0 or quote.ask < quote.bid:
            return 'INVALID_CONTRACT'
        if intent.valid_until <= now or not 0 <= (now-intent.created_at).total_seconds() <= p.max_signal_age:
            return 'STALE_SIGNAL'
        if not 0 <= (now-quote.at).total_seconds() <= p.max_quote_age:
            return 'STALE_QUOTE'
        if quote.ask-quote.bid > p.max_spread:
            return 'SPREAD'
        if intent.order_type == 'MARKET' and abs(intent.entry_price-(quote.ask if intent.side == 'BUY' else quote.bid)) > p.max_slippage:
            return 'SLIPPAGE'
        stop = intent.stop_loss
        if stop is None or stop <= 0 or (intent.side == 'BUY' and stop >= min(quote.bid, intent.entry_price)) or (intent.side == 'SELL' and stop <= max(quote.ask, intent.entry_price)):
            return 'PROTECTIVE_STOP'
        if intent.order_type == 'LIMIT' and ((intent.side == 'BUY' and intent.entry_price > quote.ask) or (intent.side == 'SELL' and intent.entry_price < quote.bid)):
            return 'LIMIT_PRICE'
        if intent.take_profit is not None and (not intent.take_profit.is_finite() or intent.take_profit <= 0 or (intent.side == 'BUY' and intent.take_profit <= intent.entry_price) or (intent.side == 'SELL' and intent.take_profit >= intent.entry_price)):
            return 'TAKE_PROFIT'
        risk = abs(intent.entry_price-stop)*intent.quantity
        if risk > p.max_order_risk:
            return 'ORDER_RISK'
        rows = self.db.execute("SELECT * FROM commands WHERE state != 'REJECTED' AND CAST(quantity AS REAL) > 0").fetchall()
        if any(r['instrument'] == intent.instrument and r['side'] != intent.side for r in rows):
            return 'STRATEGY_CONFLICT'
        if sum((D(r['quantity']) for r in rows), D(0))+intent.quantity > p.max_quantity:
            return 'ACCOUNT_EXPOSURE'
        if sum((D(r['risk']) for r in rows), D(0))+risk > p.max_total_risk:
            return 'TOTAL_RISK'
        strategy_qty, strategy_risk = p.max_strategy_quantity, p.max_strategy_risk
        for name, quantity, limit in p.strategy_limits:
            if name == intent.strategy_id:
                strategy_qty, strategy_risk = quantity, limit
        own = [r for r in rows if r['strategy'] == intent.strategy_id]
        if sum((D(r['quantity']) for r in own), D(0))+intent.quantity > strategy_qty:
            return 'STRATEGY_EXPOSURE'
        if sum((D(r['risk']) for r in own), D(0))+risk > strategy_risk:
            return 'STRATEGY_RISK'
        if (sum((D(r['quantity']) for r in rows), D(0))+intent.quantity)*p.margin_per_unit > p.available_margin:
            return 'MARGIN'
        return 'ACCEPTED'

    def enqueue(self, intent: TradeIntent, quote: Quote, now: datetime):
        utc(now)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT * FROM commands WHERE key=?', (intent.key,)).fetchone()
            reason = 'DUPLICATE' if row else self.reason(intent, quote, now)
            decision = RiskDecision(intent.intent_id, reason == 'ACCEPTED', reason, now)
            command = None
            if decision.accepted:
                cid = uuid4().hex
                self.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (cid, intent.key, intent.intent_id, intent.strategy_id, intent.instrument, intent.side,
                     str(intent.quantity), str(abs(intent.entry_price-intent.stop_loss)*intent.quantity),
                     json.dumps(asdict(intent), default=str), State.QUEUED))
                command = OrderCommand(cid, intent.key, intent.intent_id, State.QUEUED)
            self.audit(now, command.command_id if command else None, 'RISK', json.dumps(asdict(decision), default=str))
            self.db.commit()
            return decision, command
        except BaseException:
            self.db.rollback()
            raise

    def dispatch(self, command_id, quote, now):
        utc(now)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT * FROM commands WHERE id=?', (command_id,)).fetchone()
            if not row or row['state'] != State.QUEUED:
                self.db.rollback()
                return
            data = json.loads(row['payload'])
            for field in ('quantity','entry_price','stop_loss','take_profit'):
                data[field] = D(data[field]) if data.get(field) is not None else None
            for field in ('created_at','valid_until'):
                data[field] = datetime.fromisoformat(data[field])
            intent = TradeIntent(**data)
            # Exclude this reservation while rechecking current policy and quote.
            self.db.execute("UPDATE commands SET state='REJECTED' WHERE id=?", (command_id,))
            reason = self.reason(intent, quote, now)
            state = State.SUBMITTING if reason == 'ACCEPTED' else State.REJECTED
            self.db.execute('UPDATE commands SET state=? WHERE id=?', (state, command_id))
            self.audit(now, command_id, state, reason)
            self.db.commit()  # Durable fence BEFORE external side effect.
        except BaseException:
            self.db.rollback()
            raise
        if state != State.SUBMITTING:
            return
        self.checkpoint('entry_claim')
        try:
            obs = self.broker.submit(command_id, intent, now)
            state = obs.state
        except Exception:
            state = State.UNKNOWN
            self.ready = False
        self.checkpoint('entry_broker')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('UPDATE commands SET state=? WHERE id=?', (state, command_id))
            self.audit(now, command_id, state, 'synthetic broker result')
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.ready = False
            raise

