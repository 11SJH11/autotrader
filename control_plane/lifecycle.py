"""Offline lifecycle extension to the Phase 0 queue; Linux local-disk ownership.

The broker journal is independent of the engine database, like an external system.
All units, prices, margin and fills are deliberately synthetic.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal as D
import copy
import fcntl
import json
from pathlib import Path
import sqlite3
import threading

from .contracts import BrokerObservation, State, utc
from .foundation import BaseEngine, BaseFakeBroker, Policy


def encode(value):
    return json.dumps(value, default=str, sort_keys=True)


class FakeBroker(BaseFakeBroker):
    def __init__(self, path=None):
        super().__init__()
        self.lock = threading.RLock()
        self.store = sqlite3.connect(path or ':memory:', isolation_level=None, check_same_thread=False)
        self.store.execute('PRAGMA synchronous=FULL')
        self.store.execute('CREATE TABLE IF NOT EXISTS simulator(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
        self.book = {'orders': {}, 'positions': {}, 'fills': {}, 'receipts': {}}
        row = self.store.execute('SELECT data FROM simulator WHERE id=1').fetchone()
        if row:
            self.book = json.loads(row[0])
        self._observations()

    def close(self):
        self.store.close()

    def _observations(self):
        self.orders = {}
        for cid, item in self.book['orders'].items():
            self.orders[cid] = BrokerObservation(cid, item['id'], State.FILLED,
                D(item['entry']), D(item['initial_quantity']), datetime.fromisoformat(item['at']))

    @contextmanager
    def transaction(self):
        with self.lock:
            self.guard('SIM-DEMO')
            if self.mode == 'disconnected':
                raise ConnectionError('simulated disconnect')
            self.store.execute('BEGIN IMMEDIATE')
            old = copy.deepcopy(self.book)
            try:
                row = self.store.execute('SELECT data FROM simulator WHERE id=1').fetchone()
                if row:
                    self.book = json.loads(row[0])
                yield
                self.store.execute('INSERT OR REPLACE INTO simulator VALUES(1,?)', (encode(self.book),))
                self.store.commit()
                self._observations()
            except BaseException:
                self.store.rollback()
                self.book = old
                raise

    def submit(self, command, intent, now):
        self.guard(intent.account_scope)
        self.calls += 1
        if self.mode == 'timeout_before':
            raise TimeoutError()
        with self.transaction():
            if command not in self.book['orders']:
                item = dict(id='fake-'+command, command_id=command, strategy=intent.strategy_id,
                    instrument=intent.instrument, side=intent.side, entry=str(intent.entry_price),
                    stop=str(intent.stop_loss), take_profit=str(intent.take_profit) if intent.take_profit else None,
                    initial_quantity=str(intent.quantity), quantity=str(intent.quantity),
                    status='PENDING', at=now.isoformat(), campaign_id=intent.campaign_id)
                self.book['orders'][command] = item
                if intent.order_type == 'MARKET':
                    self._fill(command, now)
        if self.mode == 'timeout_after':
            raise TimeoutError()
        return self.orders[command]  # FILLED here means terminal entry-command acknowledgement.

    def _fill(self, cid, now):
        order = self.book['orders'][cid]
        if order['status'] != 'PENDING':
            return
        order['status'] = 'FILLED'
        pid = 'position-'+cid
        self.book['positions'][pid] = dict(order, id=pid, status='OPEN')
        self.book['fills']['entry-'+cid] = dict(id='entry-'+cid, position_id=pid,
            command_id=cid, quantity=order['quantity'], price=order['entry'], at=now.isoformat(), kind='ENTRY')

    def tick(self, quote, now):
        """Synthetic all-or-nothing fills at limit price; no liquidity model."""
        utc(now)
        if not all(x.is_finite() and x > 0 for x in (quote.bid, quote.ask)) or quote.bid > quote.ask:
            raise ValueError('INVALID_QUOTE')
        with self.transaction():
            for cid, order in self.book['orders'].items():
                if order['status'] == 'PENDING' and ((order['side'] == 'BUY' and quote.ask <= D(order['entry'])) or (order['side'] == 'SELL' and quote.bid >= D(order['entry']))):
                    self._fill(cid, now)
            for pid, position in self.book['positions'].items():
                if position['status'] != 'OPEN':
                    continue
                price = quote.bid if position['side'] == 'BUY' else quote.ask
                stop, tp = D(position['stop']), position['take_profit']
                hit = price <= stop if position['side'] == 'BUY' else price >= stop
                target = tp and (price >= D(tp) if position['side'] == 'BUY' else price <= D(tp))
                if hit or target:
                    self._close(pid, position['quantity'], 'protect-'+pid, price, now)

    def _close(self, pid, quantity, cid, price, now):
        position = self.book['positions'][pid]
        remaining = D(position['quantity'])-D(quantity)
        position['quantity'] = str(remaining)
        position['status'] = 'CLOSED' if remaining == 0 else 'OPEN'
        self.book['fills'][cid] = dict(id=cid, position_id=pid, command_id=cid,
            quantity=str(quantity), price=str(price), at=now.isoformat(), kind='EXIT')

    def execute(self, cid, operation, target, values, now):
        self.calls += 1
        if self.mode == 'timeout_before':
            raise TimeoutError()
        with self.transaction():
            if cid not in self.book['receipts']:
                result = 'APPLIED'
                if operation == 'CANCEL':
                    order = self.book['orders'].get(target)
                    if order and order['status'] == 'PENDING':
                        order['status'], order['quantity'] = 'CANCELLED', '0'
                    else:
                        result = 'TOO_LATE'
                elif operation == 'MODIFY':
                    position = self.book['positions'].get(target) or self.book['orders'].get(target)
                    if position and position['status'] in ('OPEN','PENDING'):
                        position.update(values)
                    else:
                        result = 'TOO_LATE'
                elif operation == 'CLOSE':
                    position = self.book['positions'].get(target)
                    if position and position['status'] == 'OPEN' and D(values['quantity']) <= D(position['quantity']):
                        self._close(target, values['quantity'], cid, position['entry'], now)
                    else:
                        result = 'TOO_LATE'
                else:
                    raise ValueError('UNSUPPORTED_OPERATION')
                self.book['receipts'][cid] = result
        if self.mode == 'timeout_after':
            raise TimeoutError()
        return self.book['receipts'][cid]

    def snapshot(self):
        self.guard('SIM-DEMO')
        if self.mode == 'disconnected':
            raise ConnectionError('simulated disconnect')
        with self.transaction():
            pass
        return dict(self.orders)

    def lifecycle_snapshot(self):
        self.snapshot()
        with self.lock:
            return copy.deepcopy(self.book)


class Engine(BaseEngine):
    def __init__(self, path, broker, policy=Policy()):
        if str(path) == ':memory:':
            raise ValueError('Persistent database path required')
        super().__init__(path, broker, policy)
        self.lockfile = open(str(Path(path).resolve())+'.owner', 'a+b')
        self.checkpoint = lambda stage: None
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS lifecycle_orders(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS positions(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS actions(
            id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, operation TEXT NOT NULL,
            target TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS campaigns(id TEXT PRIMARY KEY, strategy TEXT NOT NULL,
            instrument TEXT NOT NULL, level TEXT NOT NULL, direction TEXT NOT NULL,
            state TEXT NOT NULL);
        PRAGMA user_version=2;
        ''')

    @contextmanager
    def owner(self):
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('EXECUTION_OWNER_BUSY') from None
        try:
            yield
        finally:
            fcntl.flock(self.lockfile, fcntl.LOCK_UN)

    def close(self):
        self.lockfile.close()
        super().close()

    def reason(self, intent, quote, now):
        if self.db.execute("SELECT 1 FROM actions WHERE state IN ('SUBMITTING','UNKNOWN')").fetchone():
            return 'RECONCILIATION_REQUIRED'
        if intent.campaign_id:
            row = self.db.execute('SELECT * FROM campaigns WHERE id=?', (intent.campaign_id,)).fetchone()
            if not row or row['state'] != 'ACTIVE' or row['strategy'] != intent.strategy_id or row['instrument'] != intent.instrument:
                return 'CAMPAIGN_INACTIVE'
        return super().reason(intent, quote, now)

    def dispatch(self, command_id, quote, now):
        with self.owner():
            super().dispatch(command_id, quote, now)
            row = self.db.execute('SELECT state FROM commands WHERE id=?', (command_id,)).fetchone()
            if row and row[0] == 'FILLED':
                self._reconcile(now)

    def reconcile(self, now):
        with self.owner():
            self._reconcile(now)

    def _reconcile(self, now):
        self.ready = False
        book = self.broker.lifecycle_snapshot()
        # Base reconciliation handles entry UNKNOWN without ever requeuing it.
        evidence = {cid: BrokerObservation(cid, o['id'], State.FILLED, D(o['entry']),
            D(o['initial_quantity']), datetime.fromisoformat(o['at'])) for cid,o in book['orders'].items()}
        super().reconcile(now, evidence)
        base_ready = self.ready
        self.ready = False
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE actions SET state='UNKNOWN' WHERE state='SUBMITTING'")
            for cid, result in book['receipts'].items():
                self.db.execute('UPDATE actions SET state=? WHERE id=?', (result, cid))
            known = {r['id']: r for r in self.db.execute('SELECT * FROM commands')}
            blocked = set(book['orders'])-set(known)
            if set(book['receipts'])-{r[0] for r in self.db.execute('SELECT id FROM actions')}:
                blocked = True
            reservations = []
            for cid, row in known.items():
                order = book['orders'].get(cid)
                if row['state'] == 'FILLED' and not order:
                    blocked = True
                if not order:
                    continue
                if row['state'] != 'FILLED':
                    blocked = True
                original = json.loads(row['payload'])
                if any(order[field] != original[source] for field, source in
                       [('strategy','strategy_id'),('instrument','instrument'),('side','side'),('entry','entry_price'),('initial_quantity','quantity')]):
                    blocked = True
                if order['status'] == 'FILLED':
                    pid = 'position-'+cid
                    pos = book['positions'].get(pid)
                    related = [f for f in book['fills'].values() if f['position_id'] == pid]
                    entries = sum((D(f['quantity']) for f in related if f['kind']=='ENTRY'), D(0))
                    exits = sum((D(f['quantity']) for f in related if f['kind']=='EXIT'), D(0))
                    if not pos or entries != D(order['initial_quantity']) or entries-exits != D(pos['quantity']):
                        blocked = True
                positions = [p for p in book['positions'].values() if p['command_id'] == cid and p['status'] == 'OPEN']
                quantity = D(order['quantity']) if order['status'] == 'PENDING' else D(0)
                risk = quantity*abs(D(order['entry'])-D(order['stop']))
                for position in positions:
                    if any(position[k] != order[k] for k in ('strategy','instrument','side','entry','initial_quantity')):
                        blocked = True
                    quantity += D(position['quantity'])
                    risk += D(position['quantity'])*abs(D(position['entry'])-D(position['stop']))
                reservations.append((str(quantity),str(risk),cid))
            if any(p['command_id'] not in known for p in book['positions'].values()):
                blocked = True
            for row in self.db.execute('SELECT id,data FROM fills'):
                if row['id'] not in book['fills'] or encode(book['fills'][row['id']]) != row['data']:
                    blocked = True
            if not blocked:
                self.db.executemany('UPDATE commands SET quantity=?,risk=? WHERE id=?',reservations)
            for table, source in [('lifecycle_orders','orders'),('positions','positions'),('fills','fills')]:
                if table != 'fills':
                    self.db.execute('DELETE FROM '+table)
                for item in book[source].values():
                    self.db.execute('INSERT OR IGNORE INTO '+table+' VALUES(?,?)', (item['id'],encode(item)))
            uncertain = self.db.execute("SELECT 1 FROM actions WHERE state='UNKNOWN'").fetchone()
            self.audit(now, None, 'LIFECYCLE_RECONCILIATION', 'blocked' if blocked or uncertain or not base_ready else 'complete')
            self.db.commit()
            self.ready = bool(base_ready and not blocked and not uncertain)
        except BaseException:
            self.db.rollback()
            raise

    def manage(self, key, operation, target, now, *, quantity=None, stop=None, take_profit=None):
        """Explicit idempotency key; duplicate keys return their original durable result.

        MODIFY requires a stop and replaces TP (None clears it). Only risk-reducing
        stop changes are supported in this phase. CLOSE uses synthetic entry price.
        """
        utc(now)
        with self.owner():
            self._reconcile(now)
            self.db.execute('BEGIN IMMEDIATE')
            try:
                prior = self.db.execute('SELECT * FROM actions WHERE key=?', (key,)).fetchone()
                if prior:
                    self.db.rollback()
                    return prior['id'], prior['state']
                if not self.ready:
                    raise ValueError('RECONCILIATION_REQUIRED')
                self.broker.guard('SIM-DEMO')
                values = {}
                local_cancel = False
                if operation == 'CANCEL':
                    entry = self.db.execute('SELECT state FROM commands WHERE id=?', (target,)).fetchone()
                    if not entry:
                        raise ValueError('UNKNOWN_ORDER')
                    local_cancel = entry[0] == 'QUEUED'
                    if local_cancel:
                        self.db.execute("UPDATE commands SET state='REJECTED',quantity='0',risk='0' WHERE id=?",(target,))
                elif operation in ('MODIFY','CLOSE'):
                    row = self.db.execute('SELECT data FROM positions WHERE id=?', (target,)).fetchone()
                    if not row and operation == 'MODIFY':
                        row = self.db.execute('SELECT data FROM lifecycle_orders WHERE id=?',('fake-'+target,)).fetchone()
                    if not row or json.loads(row[0])['status'] not in ('OPEN','PENDING'):
                        raise ValueError('POSITION_NOT_OPEN')
                    position = json.loads(row[0])
                    if operation == 'CLOSE':
                        quantity = D(position['quantity']) if quantity is None else quantity
                        if not isinstance(quantity,D) or not quantity.is_finite() or quantity <= 0 or quantity > D(position['quantity']) or quantity % self.policy.quantity_step:
                            raise ValueError('INVALID_QUANTITY')
                        values = {'quantity':str(quantity)}
                    else:
                        if not isinstance(stop,D) or not stop.is_finite() or stop <= 0:
                            raise ValueError('PROTECTIVE_STOP')
                        entry, old = D(position['entry']), D(position['stop'])
                        if (position['side'] == 'BUY' and not old <= stop < entry) or (position['side'] == 'SELL' and not entry < stop <= old):
                            raise ValueError('RISK_INCREASING_STOP')
                        if take_profit is not None and (not isinstance(take_profit,D) or not take_profit.is_finite() or take_profit <= 0 or (position['side']=='BUY' and take_profit <= entry) or (position['side']=='SELL' and take_profit >= entry)):
                            raise ValueError('TAKE_PROFIT')
                        values = {'stop':str(stop), 'take_profit':str(take_profit) if take_profit else None}
                else:
                    raise ValueError('UNSUPPORTED_OPERATION')
                from uuid import uuid4
                cid = uuid4().hex
                self.db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?)', (cid,key,operation,target,encode(values),'SUBMITTING'))
                self.audit(now,cid,'ACTION_CLAIM',operation)
                if local_cancel:
                    self.db.execute("UPDATE actions SET state='APPLIED' WHERE id=?",(cid,))
                    self.audit(now,cid,'ACTION_RESULT','APPLIED_LOCAL_CANCEL')
                self.db.commit()
                if local_cancel:
                    return cid,'APPLIED'
            except BaseException:
                self.db.rollback()
                raise
            self.checkpoint('action_claim')
            try:
                result = self.broker.execute(cid, operation, target, values, now)
            except Exception:
                result = 'UNKNOWN'
            self.checkpoint('action_broker')
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('UPDATE actions SET state=? WHERE id=?', (result,cid))
                self.audit(now,cid,'ACTION_RESULT',result)
                self.db.commit()
            except BaseException:
                self.db.rollback()
                self.ready = False
                raise
            self.ready = False
            if result != 'UNKNOWN':
                self._reconcile(now)
            return cid,result

    def create_campaign(self, campaign_id, strategy, instrument, level, direction, now):
        utc(now)
        if not isinstance(instrument,str) or not instrument or direction not in ('BELOW','ABOVE') or not isinstance(level,D) or not level.is_finite() or level <= 0:
            raise ValueError('INVALID_CAMPAIGN')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior = self.db.execute('SELECT * FROM campaigns WHERE id=?',(campaign_id,)).fetchone()
            if prior and tuple(prior)[1:5] != (strategy,instrument,str(level),direction):
                raise ValueError('CAMPAIGN_ID_CONFLICT')
            self.db.execute('INSERT OR IGNORE INTO campaigns VALUES(?,?,?,?,?,?)', (campaign_id,strategy,instrument,str(level),direction,'ACTIVE'))
            self.audit(now,None,'CAMPAIGN',campaign_id)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def evaluate_campaigns(self, quote, now):
        """Invalidate first, then cancel broker pending entries; fills win races.

        Restart/repeated evaluation retries cancellation with the same key, never
        an UNKNOWN action. An invalidation does not flatten existing positions.
        """
        utc(now)
        if not all(x.is_finite() and x > 0 for x in (quote.bid,quote.ask)) or quote.bid > quote.ask or not 0 <= (now-quote.at).total_seconds() <= self.policy.max_quote_age:
            raise ValueError('INVALID_QUOTE')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for row in self.db.execute("SELECT * FROM campaigns WHERE state='ACTIVE'").fetchall():
                hit = quote.bid <= D(row['level']) if row['direction']=='BELOW' else quote.ask >= D(row['level'])
                if hit:
                    self.db.execute("UPDATE campaigns SET state='INVALIDATED' WHERE id=?",(row['id'],))
                    self.audit(now,None,'INVALIDATED',row['id'])
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        self.reconcile(now)
        for row in self.db.execute('SELECT id,payload,state FROM commands').fetchall():
            campaign = json.loads(row['payload']).get('campaign_id')
            if campaign and self.db.execute("SELECT 1 FROM campaigns WHERE id=? AND state='INVALIDATED'",(campaign,)).fetchone():
                self.manage('invalidate:'+row['id'],'CANCEL',row['id'],now)
