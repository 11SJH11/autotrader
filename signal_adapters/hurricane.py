"""Structured Hurricane adapter; no Telegram client, private text or broker API.

The caller supplies parsed levels and an explicit publication-time convention.
Edits produce observation tombstones only. Eligibility uses a current UTC quote;
publication-time market-path eligibility is not inferred.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from control_plane.contracts import Quote,utc
from .common import Decision,ObservationJournal,ObservationSizing,identity,intent,positive,quote_reason
from .timestamps import timestamp


@dataclass(frozen=True)
class HurricaneMessage:
    setup_key: str  # Opaque source-stable key, not a random delivery ID.
    published: str | datetime
    convention: str
    received: datetime
    side: str
    zone_low: Decimal
    zone_high: Decimal
    stop: Decimal
    targets: tuple[Decimal,...]
    event: str = 'NEW'
    fold: int | None = None


@dataclass(frozen=True)
class HurricanePolicy:
    max_signal_age: int = 90
    max_quote_age: int = 5
    future_tolerance: int = 3

    def __post_init__(self):
        if any(type(v) is not int or v<=0 for v in (self.max_signal_age,self.max_quote_age,self.future_tolerance)):
            raise ValueError('INVALID_POLICY')


class HurricaneAdapter:
    strategy_id='hurricane_observation'

    def __init__(self,journal: ObservationJournal,policy=HurricanePolicy()):
        self.journal,self.policy=journal,policy

    def evaluate(self,message: HurricaneMessage,quote: Quote,now: datetime,sizing: ObservationSizing,*,connection_epoch=None):
        utc(now);utc(message.received)
        if connection_epoch is not None:utc(connection_epoch)
        if not isinstance(message.setup_key,str) or not message.setup_key:
            raise ValueError('STABLE_SETUP_ID_REQUIRED')
        if message.event not in ('NEW','EDIT','CANCEL'):
            raise ValueError('UNSUPPORTED_EVENT')
        key=identity(self.strategy_id,message.setup_key)
        if message.event in ('EDIT','CANCEL'):
            reason=message.event+'_NEVER_ENTERS'
            self.journal.record(key,reason,now,edit=True)
            return Decision(reason,invalidated=True)
        proposals=()
        try:
            published=timestamp(message.published,message.convention,fold=message.fold)
            age=(now-published).total_seconds()
            if not -self.policy.future_tolerance<=age<=self.policy.max_signal_age:
                reason='STALE_OR_FUTURE_MESSAGE'
            elif message.received>now or (message.received-published).total_seconds() < -self.policy.future_tolerance:
                reason='INVALID_RECEIPT_TIME'
            elif connection_epoch is not None and published<connection_epoch:
                utc(connection_epoch)
                reason='RECONNECT_BACKLOG'
            else:
                reason=quote_reason(quote,now,self.policy.max_quote_age)
                if reason is None:
                    positive(message.zone_low,message.zone_high,message.stop,*message.targets)
                    lo,hi,side=message.zone_low,message.zone_high,message.side
                    targets=message.targets
                    if side not in ('BUY','SELL') or lo>hi or len(targets)!=3:
                        raise ValueError('INVALID_LEVELS')
                    if (side=='BUY' and not message.stop<lo<=hi<targets[0]<targets[1]<targets[2]) or (side=='SELL' and not targets[2]<targets[1]<targets[0]<lo<=hi<message.stop):
                        raise ValueError('INVALID_LEVELS')
                    price=quote.ask if side=='BUY' else quote.bid
                    if (side=='BUY' and price>=targets[0]) or (side=='SELL' and price<=targets[0]):
                        reason='PAST_TP1'
                    elif (side=='BUY' and price<lo) or (side=='SELL' and price>hi):
                        reason='OUTSIDE_ELIGIBLE_ZONE'
                    else:
                        order_type='MARKET' if lo<=price<=hi else 'LIMIT'
                        entry=price if order_type=='MARKET' else hi if side=='BUY' else lo
                        proposals=tuple(intent(self.strategy_id,key,str(i),side,entry,message.stop,target,published,
                            self.policy.max_signal_age+1,sizing,order_type=order_type,version='hurricane-utc-v1') for i,target in enumerate(targets,1))
                        reason='OBSERVATION_ACCEPTED'
        except (ValueError,TypeError,KeyError):
            reason='INVALID_TIMESTAMP_OR_LEVELS'
        if not self.journal.record(key,reason,now):
            return Decision('DUPLICATE_OR_INVALIDATED')
        return Decision(reason,proposals)
