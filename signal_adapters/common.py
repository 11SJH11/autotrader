from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import sqlite3

from control_plane.contracts import Quote, TradeIntent, utc


@dataclass(frozen=True)
class Decision:
    reason: str
    intents: tuple[TradeIntent, ...] = ()
    invalidated: bool = False

    @property
    def accepted(self):
        return bool(self.intents)


@dataclass(frozen=True)
class ObservationSizing:
    """Caller-supplied synthetic units; never inferred from a strategy signal."""
    quantity_per_leg: Decimal

    def __post_init__(self):
        positive(self.quantity_per_leg)


def positive(*values):
    if not all(isinstance(v,Decimal) and v.is_finite() and v>0 for v in values):
        raise ValueError('INVALID_PRICE_OR_QUANTITY')


def quote_reason(quote: Quote,now: datetime,max_age=5):
    utc(now)
    utc(quote.at)
    try:
        positive(quote.bid,quote.ask)
    except ValueError:
        return 'INVALID_QUOTE'
    if quote.ask<quote.bid:
        return 'INVALID_QUOTE'
    if not 0<=(now-quote.at).total_seconds()<=max_age:
        return 'STALE_QUOTE'
    return None


def identity(*parts):
    return hashlib.sha256(json.dumps(parts,separators=(',',':')).encode()).hexdigest()


class ObservationJournal:
    """Durable observation decisions/tombstones, never an order queue.

    No source text or external identifiers are persisted. One caller transaction
    emits a proposal; duplicates after restart emit nothing. Edits invalidate the
    observation record only and cannot cancel broker orders.
    """
    def __init__(self,path):
        self.db=sqlite3.connect(path,isolation_level=None,timeout=5)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS adapter_decisions(
            identity TEXT PRIMARY KEY, reason TEXT NOT NULL, at TEXT NOT NULL)''')

    def close(self):
        self.db.close()

    def record(self,key,reason,now,*,edit=False):
        utc(now)
        own_transaction=not self.db.in_transaction
        if own_transaction:self.db.execute('BEGIN IMMEDIATE')
        try:
            prior=self.db.execute('SELECT reason FROM adapter_decisions WHERE identity=?',(key,)).fetchone()
            if edit:
                self.db.execute('INSERT INTO adapter_decisions VALUES(?,?,?) ON CONFLICT(identity) DO UPDATE SET reason=excluded.reason,at=excluded.at',
                                (key,'INVALIDATED_EDIT',now.isoformat()))
            elif prior:
                if own_transaction:self.db.rollback()
                return False
            else:
                self.db.execute('INSERT INTO adapter_decisions VALUES(?,?,?)',(key,reason,now.isoformat()))
            if own_transaction:self.db.commit()
            return True
        except BaseException:
            if own_transaction:self.db.rollback()
            raise


def intent(strategy,setup,leg,side,entry,stop,target,at,ttl,sizing,*,order_type='MARKET',version='1'):
    """Existing central contract, with an explicit engine-enforced observation fence."""
    utc(at)
    positive(entry,stop,target)
    if (side=='BUY' and not stop<entry<target) or (side=='SELL' and not target<entry<stop) or side not in ('BUY','SELL'):
        raise ValueError('INVALID_PROTECTION')
    key=identity(strategy,setup,leg)
    return TradeIntent(key,identity(strategy,setup),strategy,setup,leg,'SIM-DEMO','SIM-XAU',side,
        sizing.quantity_per_leg,entry,stop,at,at+timedelta(seconds=ttl),
        strategy_version=version,order_type=order_type,take_profit=target,observation_only=True)
