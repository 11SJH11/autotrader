"""Bounded read-only symbol/risk probe. No raw response or account export."""
import argparse
from decimal import Decimal
from datetime import datetime,timezone
import json
from pathlib import Path
import time

from .client import Reader,rows,one,money
from .config import Config,SafetyError
from .oauth import Token
from .protocol import integer

VOLUME_FIELDS=('minVolume','maxVolume','stepVolume','lotSize')
INTEGER_FIELDS=VOLUME_FIELDS+('digits','pipPosition','slDistance','tpDistance','distanceSetIn',
    'tradingMode','commission','commissionType','minCommission','minCommissionType',
    'preciseTradingCommissionRate','preciseMinCommission','pnlConversionFeeRate','leverageId')


def numeric_fields(symbol):
    result={}
    for key in INTEGER_FIELDS:
        value=symbol.get(key)
        field={'present':key in symbol,'json_type':type(value).__name__}
        if key in symbol:field['raw_integer']=str(integer(value))
        result[key]=field
    return result


def volumes(symbol):
    raw={key:integer(symbol.get(key),1) for key in VOLUME_FIELDS}
    return {key:{'protocol_hundredths':str(value),'units':str(Decimal(value)/100),
        'lots':str(Decimal(value)/raw['lotSize'])} for key,value in raw.items()}


def hypothetical_margin(reader,sid,lot_size):
    # These are hypothetical .01/.1/1 lot sizes, not metadata-approved volumes.
    quantities=[]
    for lots in (Decimal('.01'),Decimal('.1'),Decimal('1')):
        raw=lots*lot_size
        if raw!=raw.to_integral_value():raise SafetyError('HYPOTHETICAL_VOLUME_NOT_REPRESENTABLE')
        quantities.append(integer(int(raw),1))
    result=reader._read('margin',symbolId=sid,volume=quantities)
    margins=rows(result,'margin')
    if len(margins)!=len(quantities) or {integer(m.get('volume'),1) for m in margins}!=set(quantities):
        raise SafetyError('EXPECTED_MARGIN_VOLUME_MISMATCH')
    output=[]
    for value in quantities:
        m=one([m for m in margins if integer(m['volume'],1)==value])
        buy=money(dict(m,moneyDigits=result.get('moneyDigits')),'buyMargin')
        sell=money(dict(m,moneyDigits=result.get('moneyDigits')),'sellMargin')
        if buy<0 or sell<0:raise SafetyError('INVALID_EXPECTED_MARGIN')
        output.append({'lots':str(Decimal(value)/lot_size),'units':str(Decimal(value)/100),
            'protocol_volume':str(value),'buy_margin':str(buy),'sell_margin':str(sell)})
    return output


def quote(reader,sid):
    with reader._lock:reader._symbol_id=sid
    reader._read('spots',symbolId=[sid],subscribeToSpotTimestamp=True)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        reader.poll()
        status=reader.status()
        if status['quote_fresh']:return status['quote']
    raise SafetyError('QUOTE_NOT_AVAILABLE')


def margin_crosscheck(report):
    fx=Decimal(report['gbp_conversion']['gbp_per_usd_upper'])
    price=Decimal(report['xauusd_quote']['ask'])
    result={'assumed_gold_leverage_for_comparison':'20',
        'source':'Pepperstone UK public retail gold leverage table; not account-specific execution evidence',
        'note':'Quotes and margin estimates sampled at separate instants; raw dynamic tier leverage is preserved without assuming undocumented scaling',
        'hypothetical_rows':[]}
    for row in report['hypothetical_margin']:
        notional=Decimal(row['units'])*price*fx
        margin=Decimal(row['buy_margin'])
        result['hypothetical_rows'].append({'lots':row['lots'],'indicative_notional_gbp':str(notional),
            'indicative_margin_at_20':str(notional/20),'reported_buy_margin_gbp':row['buy_margin'],
            'implied_leverage_from_sampled_quote':str(notional/margin) if margin else None})
    return result


def inspect(reader,token):
    reader.authorize(token)
    identity=reader.verify_identity()
    if not identity['broker_name_matches_configuration']:raise SafetyError('BROKER_IDENTITY_MISMATCH')
    assets=rows(reader._read('assets'),'asset')
    asset_by_id={integer(a['assetId'],1):a['name'] for a in assets}
    lights=rows(reader._read('symbols',includeArchivedSymbols=False),'symbol')
    light=one([s for s in lights if s.get('symbolName','').replace('/','')=='XAUUSD'])
    if asset_by_id.get(integer(light.get('baseAssetId'),1))!='XAU' or asset_by_id.get(integer(light.get('quoteAssetId'),1))!='USD':
        raise SafetyError('SYMBOL_ASSET_MISMATCH')
    sid=integer(light['symbolId'],1)
    response=reader._read('symbol',symbolId=[sid])
    symbol=one(rows(response,'symbol'))
    if integer(symbol.get('symbolId'),1)!=sid:raise SafetyError('RESPONSE_SYMBOL_MISMATCH')
    report={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'identity':identity,
        'execution_enabled':False,'execution_readiness':'NO_GO',
        'response_validation':{'request_type':2116,'response_type':2117,
            'correlation_and_account_checked':True,'symbol_id_matches':True,'full_symbol_count':1},
        'fields':numeric_fields(symbol),'volume':volumes(symbol),
        'volume_independently_verified':False,'blockers':['INDEPENDENT_VOLUME_CONFIRMATION_REQUIRED'],
        'margin_currency':identity['currency'],'margin_note':'Hypothetical estimates; not proof of admissible volume or actual execution margin'}
    report['symbol_boolean_fields']={key:{'present':key in symbol,'json_type':type(symbol.get(key)).__name__,
        'value':symbol[key] if type(symbol.get(key)) is bool else None} for key in ('enableShortSelling','guaranteedStopLoss')}
    unit=symbol.get('measurementUnits')
    report['measurement_unit']={'present':'measurementUnits' in symbol,
        'value':unit if unit in ('oz','oz.','ozs','oz t','oz.t','Ounce','Ounces','ounce','ounces','Troy Ounces','units','Lots') else None}
    trader=reader._read('trader')['trader']
    report['trader_boolean_fields']={key:{'present':key in trader,'json_type':type(trader.get(key)).__name__,
        'value':trader[key] if type(trader.get(key)) is bool else None} for key in ('isLimitedRisk','swapFree','frenchRisk')}
    report['account_risk_fields']={key:{'present':key in trader,
        'raw_integer':str(integer(trader[key])) if key in trader else None}
        for key in ('accountType','leverageInCents','totalMarginCalculationType','limitedRiskMarginCalculationStrategy')}
    report['hypothetical_margin']=hypothetical_margin(reader,sid,integer(symbol['lotSize'],1))
    if 'leverageId' in symbol:
        lid=integer(symbol['leverageId'],1)
        leverage=reader._read('leverage',leverageId=lid).get('leverage')
        if not isinstance(leverage,dict) or integer(leverage.get('leverageId'),1)!=lid:
            raise SafetyError('LEVERAGE_ID_MISMATCH')
        tiers=rows(leverage,'tiers')
        if not tiers:raise SafetyError('MISSING_LEVERAGE_TIERS')
        report['dynamic_leverage_tiers']=[{'max_notional_usd':str(Decimal(integer(t['volume'],1))/100),
            'raw_leverage':integer(t['leverage'],1)} for t in tiers]
    xau=quote(reader,sid);report['xauusd_quote']=xau
    report['spread_usd_per_unit']=str(Decimal(xau['ask'])-Decimal(xau['bid']))
    # Conversion quote is a separate authenticated connection. No symbol ID or
    # account identity is exported, and symbol asset identities choose direction.
    candidates=[s for s in lights if (asset_by_id.get(integer(s.get('baseAssetId'),1)),
        asset_by_id.get(integer(s.get('quoteAssetId'),1))) in (('GBP','USD'),('USD','GBP'))]
    candidates=[s for s in candidates if s.get('symbolName','').replace('/','') in ('GBPUSD','USDGBP')]
    report['gbp_conversion']={'verified':False,'reason':'NO_UNAMBIGUOUS_CONVERSION_SYMBOL'}
    if identity['currency']!='GBP':
        report['gbp_conversion']={'verified':False,'reason':'NOT_REQUIRED_FOR_ACCOUNT_CURRENCY'}
    elif len(candidates)==1:
        conversion=candidates[0]
        base=asset_by_id[integer(conversion['baseAssetId'],1)]
        reader.authorize(token)
        fx=quote(reader,integer(conversion['symbolId'],1))
        bid,ask=Decimal(fx['bid']),Decimal(fx['ask'])
        lower,upper=(Decimal(1)/ask,Decimal(1)/bid) if base=='GBP' else (bid,ask)
        spread=Decimal(report['spread_usd_per_unit'])
        report['gbp_conversion']={'verified':True,'pair':'GBPUSD' if base=='GBP' else 'USDGBP',
            'quote':fx,'gbp_per_usd_lower':str(lower),'gbp_per_usd_upper':str(upper),
            'one_usd_move_gbp_per_unit_range':[str(lower),str(upper)],
            'spread_gbp_per_unit_range':[str(spread*lower),str(spread*upper)],
            'note':'Indicative bid/ask bounds at separate instants, not broker P&L conversion guarantee'}
        if identity['currency']=='GBP':report['indicative_margin_crosscheck']=margin_crosscheck(report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();reader=None
    try:
        config=Config.load(args.config);reader=Reader(config)
        report=inspect(reader,Token.load(config.token_path))
        Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
        print('READ_ONLY_SPECIFICATION_REPORT_WRITTEN; EXECUTION_NO_GO')
    except Exception:
        # Never interpolate provider errors, private paths or broker responses.
        print('READ_ONLY_SPECIFICATION_PROBE_FAILED');return 1
    finally:
        if reader:reader.disconnect('PROBE_FINISHED')
    return 0

if __name__=='__main__':raise SystemExit(main())
