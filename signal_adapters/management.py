"""Pure observation-only management proposals from registered broker evidence.

Hurricane rule source: legacy staged3_be.py _confirmed_tp1/_move_to_be. Never
infer TP1 from price touching, Telegram messages, missing positions or a gap.
No broker imports or mutations. cTrader exit-reason mapping remains unverified.
"""
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from control_plane.contracts import Quote,utc
from ctrader_readonly.config import SafetyError


@dataclass(frozen=True)
class RegisteredLeg:
    campaign:str
    leg:int
    position_ref:str
    side:str
    entry:Decimal
    stop:Decimal
    target:Decimal
    open_units:Decimal
    verified:bool


@dataclass(frozen=True)
class TP1Evidence:
    campaign:str
    position_ref:str
    opened_units:Decimal
    closed_units:Decimal
    positive_profit:bool
    all_exit_reasons_tp:bool
    complete_history:bool
    verified:bool


@dataclass(frozen=True)
class ManagementProposal:
    key:str
    operation:str
    target_ref:str
    stop:Decimal
    take_profit:Decimal
    observation_only:bool=True


def hurricane_break_even(legs,evidence,quote,now,min_gap):
    utc(now);utc(quote.at)
    if not 0<=(now-quote.at).total_seconds()<=5:raise SafetyError('STALE_QUOTE')
    if not isinstance(min_gap,Decimal) or not min_gap.is_finite() or min_gap<=0:raise SafetyError('VERIFIED_MANAGEMENT_GAP_REQUIRED')
    if len({leg.leg for leg in legs})!=len(legs) or len({leg.position_ref for leg in legs})!=len(legs):raise SafetyError('MANAGEMENT_IDENTITIES_NOT_UNIQUE')
    if not all(isinstance(v,Decimal) and v.is_finite() and v>0 for v in (quote.bid,quote.ask)) or quote.ask<quote.bid:raise SafetyError('INVALID_MANAGEMENT_QUOTE')
    first=[leg for leg in legs if leg.leg==1]
    if len(first)!=1 or first[0].verified is not True or not first[0].campaign or evidence.position_ref!=first[0].position_ref or evidence.campaign!=first[0].campaign:
        raise SafetyError('TP1_IDENTITY_NOT_VERIFIED')
    if not (evidence.verified is True and evidence.complete_history is True and evidence.all_exit_reasons_tp is True and evidence.positive_profit is True
        and evidence.opened_units>0 and evidence.opened_units==evidence.closed_units):return ()
    result=[]
    for leg in legs:
        if leg.leg not in (2,3) or leg.open_units<=0:continue
        if leg.verified is not True or leg.campaign!=evidence.campaign or leg.side!=first[0].side or leg.side not in ('BUY','SELL'):raise SafetyError('MANAGEMENT_LEG_NOT_VERIFIED')
        if leg.side=='BUY':
            if leg.stop>=leg.entry:continue
            if leg.entry>=quote.bid-min_gap:continue
        else:
            if leg.stop<=leg.entry:continue
            if leg.entry<=quote.ask+min_gap:continue
        result.append(ManagementProposal('tp1-be:'+leg.campaign+':'+str(leg.leg),'MODIFY_POSITION',leg.position_ref,leg.entry,leg.target))
    return tuple(result)
