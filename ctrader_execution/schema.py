"""Narrow official cTrader mutation schemas for OFFLINE preparation only.

Never imported by the read-only transport's allowlist. No credentials or sockets.
https://help.ctrader.com/open-api/messages/
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal as D,ROUND_CEILING
import math
from control_plane.contracts import TradeIntent,Quote,utc
from ctrader_readonly.protocol import integer
from ctrader_readonly.config import SafetyError


@dataclass(frozen=True)
class UISpecEvidence:
    minimum_lots:D
    maximum_lots:D
    step_lots:D
    units_per_lot:D
    at:datetime
    same_account_confirmed:bool=False


@dataclass(frozen=True)
class SymbolSpec:
    symbol_id:int
    lot_hundredths:int
    min_hundredths:int
    max_hundredths:int
    step_hundredths:int
    digits:int
    stop_points:int
    target_points:int
    independently_verified:bool=False
    distance_points:bool=True

    @classmethod
    def from_report(cls,symbol_id,report,ui,now):
        utc(now);utc(ui.at)
        checked=datetime.fromisoformat(report['checked_at_utc']);utc(checked)
        if not 0<=(now-checked).total_seconds()<=300 or not 0<=(now-ui.at).total_seconds()<=86400:raise SafetyError('STALE_SPEC_EVIDENCE')
        identity=report['identity']
        if identity.get('environment')!='DEMO' or identity.get('currency')!='USD' or identity.get('account_mode')!='HEDGED' or identity.get('pinned_account_id_matches') is not True or identity.get('view_authorized') is not True or identity.get('broker_name_matches_configuration') is not True:
            raise SafetyError('NATIVE_ACCOUNT_FENCE')
        if ui.same_account_confirmed is not True:raise SafetyError('MATCHING_ACCOUNT_UI_REQUIRED')
        fields=report['fields']
        def raw(key):
            if fields[key].get('present') is not True:raise SafetyError('MISSING_SPEC_FIELD')
            return integer(fields[key]['raw_integer'])
        lot=raw('lotSize')
        if lot<=0:raise SafetyError('INVALID_NATIVE_SPEC')
        comparisons=((D(raw('minVolume'))/lot,ui.minimum_lots),(D(raw('maxVolume'))/lot,ui.maximum_lots),
            (D(raw('stepVolume'))/lot,ui.step_lots),(D(lot)/100,ui.units_per_lot))
        if any(not isinstance(expected,D) or not expected.is_finite() or expected<=0 or actual!=expected for actual,expected in comparisons):raise SafetyError('API_UI_SPEC_MISMATCH')
        return cls(symbol_id,lot,raw('minVolume'),raw('maxVolume'),raw('stepVolume'),raw('digits'),
            raw('slDistance'),raw('tpDistance'),True,raw('distanceSetIn')==1)

    def __post_init__(self):
        for value in (self.symbol_id,self.lot_hundredths,self.min_hundredths,self.max_hundredths,self.step_hundredths):integer(value,1)
        integer(self.digits)
        if self.min_hundredths>self.max_hundredths or self.digits not in range(0,9):raise SafetyError('INVALID_NATIVE_SPEC')
        integer(self.stop_points);integer(self.target_points)
        if type(self.independently_verified) is not bool or type(self.distance_points) is not bool:raise SafetyError('INVALID_NATIVE_SPEC')

    def volume(self,units,*,close=False):
        if not isinstance(units,D) or not units.is_finite() or units<=0:raise SafetyError('INVALID_NATIVE_VOLUME')
        raw=units*100
        if raw!=raw.to_integral_value():raise SafetyError('VOLUME_PRECISION')
        value=integer(int(raw),1)
        if value%self.step_hundredths or value>self.max_hundredths or (not close and value<self.min_hundredths):raise SafetyError('NATIVE_VOLUME_LIMIT')
        return value

    def units_for_lots(self,lots):
        if not isinstance(lots,D) or not lots.is_finite() or lots<=0:raise SafetyError('INVALID_LOT_SIZE')
        units=lots*D(self.lot_hundredths)/100
        self.volume(units);return units

    def price(self,price):
        if not isinstance(price,D) or not price.is_finite() or price<=0:raise SafetyError('INVALID_NATIVE_PRICE')
        if price.scaleb(self.digits)!=price.scaleb(self.digits).to_integral_value():raise SafetyError('PRICE_PRECISION')
        value=float(price)
        if not math.isfinite(value) or D(str(value))!=price:raise SafetyError('PRICE_CONVERSION_LOSS')
        return value


@dataclass(frozen=True)
class RiskLimits:
    order_risk_usd:D
    total_risk_usd:D
    strategy_risk_usd:D
    total_units:D
    strategy_units:D
    margin_usd:D
    commission_roundtrip_per_lot_usd:D
    slippage_per_unit_usd:D
    max_spread:D
    fee_buffer_usd:D
    max_quote_age:int=5
    strategies:tuple=()  # (strategy, units, risk USD)

    def __post_init__(self):
        for key in ('order_risk_usd','total_risk_usd','strategy_risk_usd','total_units','strategy_units','margin_usd','max_spread'):
            value=getattr(self,key)
            if not isinstance(value,D) or not value.is_finite() or value<=0:raise SafetyError('INVALID_NATIVE_RISK_POLICY')
        for value in (self.commission_roundtrip_per_lot_usd,self.slippage_per_unit_usd,self.fee_buffer_usd):
            if not isinstance(value,D) or not value.is_finite() or value<0:raise SafetyError('INVALID_NATIVE_COST_POLICY')
        names=set()
        for name,units,risk in self.strategies:
            if name in names or not name or any(not isinstance(v,D) or not v.is_finite() or v<=0 for v in (units,risk)):raise SafetyError('INVALID_NATIVE_STRATEGY_POLICY')
            names.add(name)
        if type(self.max_quote_age) is not int or self.max_quote_age<=0:raise SafetyError('INVALID_NATIVE_RISK_POLICY')

    def risk(self,intent,spec):
        # USD XAUUSD quantity is base units; no synthetic margin_per_unit.
        return intent.quantity*(abs(intent.entry_price-intent.stop_loss)+self.slippage_per_unit_usd)+self.fees(intent.quantity,spec)

    def fees(self,units,spec):
        if units<=0:return D(0)
        lots=units*100/D(spec.lot_hundredths)
        # Conservative USD cent rounding per side; explicit extra buffer covers
        # reviewed fill/fee assumptions. Real charge rounding is not proven here.
        side=(lots*self.commission_roundtrip_per_lot_usd/2).quantize(D('.01'),rounding=ROUND_CEILING)
        return side*2+self.fee_buffer_usd


def protected(intent,spec,quote,now):
    utc(now)
    if not spec.independently_verified:raise SafetyError('NATIVE_SPEC_NOT_VERIFIED')
    if not spec.distance_points:raise SafetyError('STOP_DISTANCE_UNIT_UNSUPPORTED')
    spec.volume(intent.quantity);spec.price(intent.entry_price)
    spec.price(intent.stop_loss);spec.price(intent.take_profit)
    if intent.side not in ('BUY','SELL') or intent.order_type not in ('MARKET','LIMIT'):raise SafetyError('UNSUPPORTED_NATIVE_ORDER')
    buy=intent.side=='BUY';entry=intent.entry_price;stop=intent.stop_loss;target=intent.take_profit
    if not (stop<entry<target if buy else target<entry<stop):raise SafetyError('NATIVE_PROTECTION')
    if (entry-stop if buy else stop-entry)<D(spec.stop_points).scaleb(-spec.digits):raise SafetyError('NATIVE_STOP_DISTANCE')
    if abs(target-entry)<D(spec.target_points).scaleb(-spec.digits):raise SafetyError('NATIVE_TARGET_DISTANCE')
    if intent.order_type=='LIMIT' and (entry>quote.ask if buy else entry<quote.bid):raise SafetyError('NATIVE_LIMIT_PRICE')
    # Stop constraints against current liquidation side matter as well.
    if stop>=quote.bid if buy else stop<=quote.ask:raise SafetyError('NATIVE_PROTECTION')


def order_frame(account,spec,cid,intent,quote,now):
    protected(intent,spec,quote,now)
    body={'ctidTraderAccountId':integer(account,1),'symbolId':spec.symbol_id,
        'orderType':1 if intent.order_type=='MARKET' else 2,'tradeSide':1 if intent.side=='BUY' else 2,
        'volume':spec.volume(intent.quantity),'clientOrderId':cid,'label':cid}
    if intent.order_type=='LIMIT':
        body.update(limitPrice=spec.price(intent.entry_price),stopLoss=spec.price(intent.stop_loss),takeProfit=spec.price(intent.take_profit))
    else:
        # Absolute SL/TP on NewOrder MARKET is explicitly unsupported by schema.
        distances=[abs(intent.entry_price-intent.stop_loss)*100000,abs(intent.take_profit-intent.entry_price)*100000]
        if any(v!=v.to_integral_value() for v in distances):raise SafetyError('RELATIVE_PROTECTION_PRECISION')
        body.update(relativeStopLoss=integer(int(distances[0]),1),relativeTakeProfit=integer(int(distances[1]),1))
    return frame(cid,2106,body)


def frame(cid,kind,body):
    result={'clientMsgId':cid,'payloadType':kind,'payload':body}
    validate_mutation(result);return result


KEYS={
    2106:({'ctidTraderAccountId','symbolId','orderType','tradeSide','volume','clientOrderId','label'},
        {'limitPrice','stopLoss','takeProfit','relativeStopLoss','relativeTakeProfit'}),
    2108:({'ctidTraderAccountId','orderId'},set()),
    2109:({'ctidTraderAccountId','orderId','volume','limitPrice','stopLoss','takeProfit'},set()),
    2110:({'ctidTraderAccountId','positionId','stopLoss','takeProfit'},set()),
    2111:({'ctidTraderAccountId','positionId','volume'},set()),
}


def validate_mutation(value):
    if not isinstance(value,dict) or set(value)!={'clientMsgId','payloadType','payload'}:raise SafetyError('NATIVE_SCHEMA')
    cid=value['clientMsgId'];kind=value['payloadType'];p=value['payload']
    if not isinstance(cid,str) or not cid or len(cid)>50 or type(kind) is not int or kind not in KEYS or not isinstance(p,dict):raise SafetyError('NATIVE_SCHEMA')
    required,optional=KEYS[kind]
    if required-p.keys() or p.keys()-required-optional:raise SafetyError('NATIVE_SCHEMA')
    for key,item in p.items():
        if key in ('label','clientOrderId'):
            if item!=cid:raise SafetyError('NATIVE_CORRELATION')
        elif key in ('limitPrice','stopLoss','takeProfit'):
            if type(item) not in (int,float) or not math.isfinite(item) or item<=0:raise SafetyError('NATIVE_SCHEMA')
        else:integer(item,1)
    if kind==2106:
        if p['tradeSide'] not in (1,2) or p['orderType'] not in (1,2):raise SafetyError('NATIVE_SCHEMA')
        extras=set(p)-required
        if extras!=({'relativeStopLoss','relativeTakeProfit'} if p['orderType']==1 else {'limitPrice','stopLoss','takeProfit'}):raise SafetyError('NATIVE_PROTECTION_REQUIRED')
