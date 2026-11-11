from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class State(StrEnum):
    QUEUED = 'QUEUED'
    SUBMITTING = 'SUBMITTING'
    UNKNOWN = 'UNKNOWN'
    FILLED = 'FILLED'
    REJECTED = 'REJECTED'


def utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise ValueError('UTC-aware timestamp required')


@dataclass(frozen=True)
class SourceEvent:
    source: str
    channel: str
    message_id: str
    revision: int
    event_type: str
    occurred_at: datetime
    received_at: datetime
    payload_hash: str
    payload_ref: str

    def __post_init__(self):
        utc(self.occurred_at)
        utc(self.received_at)


@dataclass(frozen=True)
class TradeIntent:
    intent_id: str
    source_event_id: str
    strategy_id: str
    setup_id: str
    leg: str
    account_scope: str
    instrument: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    stop_loss: Decimal | None
    created_at: datetime
    valid_until: datetime
    strategy_version: str = '1'
    order_type: str = 'MARKET'
    take_profit: Decimal | None = None
    campaign_id: str | None = None

    @property
    def key(self) -> str:
        # Revision and random intent IDs deliberately do not create new entries.
        import json
        return json.dumps([self.account_scope, self.strategy_id, self.setup_id, self.leg])

    def __post_init__(self):
        utc(self.created_at)
        utc(self.valid_until)


@dataclass(frozen=True)
class Quote:
    bid: Decimal
    ask: Decimal
    at: datetime

    def __post_init__(self):
        utc(self.at)


@dataclass(frozen=True)
class RiskDecision:
    intent_id: str
    accepted: bool
    reason_code: str
    evaluated_at: datetime
    policy_version: str = 'simulation-1'


@dataclass(frozen=True)
class OrderCommand:
    command_id: str
    idempotency_key: str
    intent_id: str
    state: State


@dataclass(frozen=True)
class BrokerObservation:
    command_id: str
    broker_order_id: str
    state: State
    executed_price: Decimal
    executed_volume: Decimal
    timestamp: datetime
