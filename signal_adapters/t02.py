"""Broker-free T02 detection. No sizing, risk allocation, fees or execution here.

Mechanical rules follow the Phase 2.5 audited reconstruction of Ledger run 214.
The first inversion consumes the session even when its direction/ratio rejects.
"""
from dataclasses import dataclass
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from control_plane.contracts import Quote,utc
from .common import Decision,ObservationJournal,ObservationSizing,identity,intent,positive,quote_reason

NY=ZoneInfo('America/New_York')
UTC=timezone.utc
MINUTE=timedelta(minutes=1)


@dataclass(frozen=True)
class Candle:
    time: datetime  # UTC OPEN timestamp.
    open: D
    high: D
    low: D
    close: D

    def __post_init__(self):
        utc(self.time)
        object.__setattr__(self,"time",self.time.astimezone(UTC))
        positive(self.open,self.high,self.low,self.close)
        if self.time.second or self.time.microsecond or not self.low<=min(self.open,self.close)<=max(self.open,self.close)<=self.high:
            raise ValueError('INVALID_CANDLE')


@dataclass(frozen=True)
class T02Policy:
    strategy_id: str
    sides: tuple[str,...]
    weekdays: tuple[int,...] = (0,1,2,3,4)

    def __post_init__(self):
        if not isinstance(self.strategy_id,str) or not self.strategy_id or type(self.sides) is not tuple or not self.sides or len(set(self.sides))!=len(self.sides) or any(s not in ('BUY','SELL') for s in self.sides):
            raise ValueError('INVALID_SIGNAL_POLICY')
        if type(self.weekdays) is not tuple or not self.weekdays or len(set(self.weekdays))!=len(self.weekdays) or any(type(d) is not int or not 0<=d<=6 for d in self.weekdays):
            raise ValueError('INVALID_WEEKDAY_POLICY')

    @classmethod
    def reference(cls):return cls('t02_reference',('BUY','SELL'))

    @classmethod
    def long_only(cls):return cls('t02_long_only',('BUY',))

    @property
    def version(self):
        return 't02-v1-'+identity(self.sides,self.weekdays)[:16]

    def reason(self,setup):
        if setup.confirmed_at.astimezone(NY).weekday() not in self.weekdays:
            return 'WEEKDAY_FILTER'
        if not setup.accepted:
            return 'FVG_RATIO_OR_STOP_REJECTED'
        if setup.side not in self.sides:
            return 'DIRECTION_FILTER'
        return 'OBSERVATION_ACCEPTED'


@dataclass(frozen=True)
class Setup:
    session: str
    side: str
    confirmed_at: datetime
    fvg_at: datetime
    lower: D
    upper: D
    stop: D
    signal_close: D
    ratio: D | None
    accepted: bool


def session_key(instant):
    utc(instant)
    local=instant.astimezone(NY)
    return local.date().isoformat() if 20<=local.hour<24 else None


def ordered(candles):
    result=sorted(candles,key=lambda c:c.time)
    if any(type(c) is not Candle for c in result) or len({c.time for c in result})!=len(result):
        raise ValueError('DUPLICATE_OR_INVALID_CANDLES')
    return result


def first_setup(candles,session):
    """Pure detection, not a policy/sizing decision. Missing buckets cannot invert."""
    candles=ordered(candles)
    slots={}
    for candle in candles:
        key=candle.time.replace(minute=candle.time.minute//15*15)
        slots.setdefault(key,[]).append(candle)
    bars=[]
    for key,chunk in sorted(slots.items()):
        if len(chunk)==15 and all(c.time==key+i*MINUTE for i,c in enumerate(chunk)):
            bars.append(Candle(key,chunk[0].open,max(c.high for c in chunk),min(c.low for c in chunk),chunk[-1].close))
    gaps=[]
    for index,current in enumerate(bars):
        if index>=2 and current.time-bars[index-2].time==30*MINUTE:
            prior=bars[index-2]
            if current.low>prior.high:
                gaps.append((index,'SELL',prior.high,current.low))
            elif current.high<prior.low:
                gaps.append((index,'BUY',current.high,prior.low))
        done=current.time+15*MINUTE
        if session_key(done)!=session:
            continue
        candidates=[]
        for start,side,lower,upper in gaps:
            if start>=index or session_key(bars[start].time+15*MINUTE)!=session:
                continue
            if current.time-bars[start].time!=15*(index-start)*MINUTE:
                continue
            previous=bars[start+1:index]
            qualifies=(current.close>upper and all(b.close<=upper for b in previous)) if side=='BUY' else (current.close<lower and all(b.close>=lower for b in previous))
            if qualifies:candidates.append((start,side,lower,upper))
        if not candidates:continue
        start,side,lower,upper=max(candidates,key=lambda c:c[0])
        path=bars[start:index+1]
        stop=min(b.low for b in path) if side=='BUY' else max(b.high for b in path)
        distance=abs(current.close-stop)
        ratio=(upper-lower)/distance if distance else None
        accepted=ratio is not None and ratio<=D('.20') and (stop<current.close if side=='BUY' else stop>current.close)
        return Setup(session,side,done,bars[start].time+15*MINUTE,lower,upper,stop,current.close,ratio,accepted)
    return None


class T02Adapter:
    def __init__(self,journal: ObservationJournal,policy: T02Policy):
        self.journal,self.policy=journal,policy

    def evaluate(self,candles,quote: Quote,now: datetime,sizing: ObservationSizing,*,connection_epoch=None):
        utc(now)
        if connection_epoch is not None:utc(connection_epoch)
        try:
            closed=[c for c in ordered(candles) if c.time+MINUTE<=now]
        except (ValueError,TypeError,AttributeError):
            return Decision('INVALID_CANDLES')
        if not closed:return Decision('NO_COMPLETED_CANDLE')
        end=closed[-1].time+MINUTE
        session=session_key(end)
        if not session:return Decision('OUTSIDE_SESSION')
        start=datetime.combine(date.fromisoformat(session),time(19,15),NY).astimezone(UTC)
        window=[c for c in closed if start<=c.time<end]
        expected=int((end-start).total_seconds()//60)
        if len(window)!=expected or any(c.time!=start+i*MINUTE for i,c in enumerate(window)):
            return Decision('INCOMPLETE_SESSION_HISTORY')
        setup=first_setup(window,session)
        if setup is None:return Decision('NO_SETUP')
        key=identity(self.policy.strategy_id,session)
        reason=self.policy.reason(setup)
        proposals=()
        if reason=='OBSERVATION_ACCEPTED':
            if not 0<=(now-end).total_seconds()<=15:
                reason='STALE_CANDLE'
            elif setup.confirmed_at!=end:
                reason='HISTORICAL_SETUP_NO_CATCHUP'
            elif connection_epoch is not None and setup.confirmed_at<connection_epoch:
                reason='RECONNECT_BACKLOG'
            else:
                reason=quote_reason(quote,now) or 'OBSERVATION_ACCEPTED'
                if reason=='OBSERVATION_ACCEPTED':
                    price=quote.ask if setup.side=='BUY' else quote.bid
                    target=price+abs(price-setup.stop) if setup.side=='BUY' else price-abs(price-setup.stop)
                    try:
                        proposals=(intent(self.policy.strategy_id,key,'1',setup.side,price,setup.stop,target,
                            setup.confirmed_at,16,sizing,version=self.policy.version),)
                    except ValueError:
                        reason='INVALID_PROTECTION'
        if not self.journal.record(key,reason,now):return Decision('DUPLICATE_OR_CONSUMED_SESSION')
        return Decision(reason,proposals)
