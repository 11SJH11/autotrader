"""Compare independently replayed policies under one explicit OHLC model.

Uses retained Phase 2.5 market fixtures only. No raw source projects or credentials.
Metrics are simulated research-account amounts, not broker P&L or lot economics.
"""
from dataclasses import asdict,dataclass
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pandas as pd
from signal_adapters.t02 import Candle,T02Policy,NY,first_setup

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tests/fixtures/phase25'


@dataclass(frozen=True)
class ResearchConfig:
    starting_balance: float = 10000
    risk_percent: float = 1
    leverage: float = 20
    commission_per_side: float = 0
    spread_bps: float = 0
    slippage_bps: float = 0

    def __post_init__(self):
        import math
        values=asdict(self)
        if any(not math.isfinite(v) or v<0 for v in values.values()) or min(self.starting_balance,self.risk_percent,self.leverage)<=0:
            raise ValueError('INVALID_RESEARCH_CONFIG')


def frames():
    frame=pd.read_csv(FIXTURES/'oanda_1m.csv.gz')
    frame['timestamp']=pd.to_datetime(frame.timestamp,utc=True)
    return frame


def session_candidates(frame):
    selected={}
    for day in pd.date_range('2026-03-01','2026-10-17').date:
        begin=datetime.combine(day,time(19,15),NY).astimezone(timezone.utc)
        end=datetime.combine(day+timedelta(days=1),time(0),NY).astimezone(timezone.utc)
        subset=frame[(frame.timestamp>=begin)&(frame.timestamp<end)]
        if subset.empty:continue
        bars=[Candle(r.timestamp.to_pydatetime(),*(Decimal(str(x)) for x in (r.open,r.high,r.low,r.close))) for r in subset.itertuples(index=False)]
        if len(bars)!=285 or any(b.time!=begin+i*timedelta(minutes=1) for i,b in enumerate(bars)):
            raise ValueError('INCOMPLETE_RESEARCH_SESSION')
        setup=first_setup(bars,day.isoformat())
        if setup:selected[setup.confirmed_at]=setup
    return selected


def protective_exit(position,bar):
    """Open-gap handling and stop-first same-bar rule; no invented tick path."""
    sign=position['sign'];stop=position['stop'];target=position['target']
    if sign==1:
        if bar.open<=stop:return bar.open,'STOP_GAP'
        if bar.open>=target:return bar.open,'TARGET_GAP'
        if bar.low<=stop:return stop,'STOP'
        if bar.high>=target:return target,'TARGET'
    else:
        if bar.open>=stop:return bar.open,'STOP_GAP'
        if bar.open<=target:return bar.open,'TARGET_GAP'
        if bar.high>=stop:return stop,'STOP'
        if bar.low<=target:return target,'TARGET'
    return None


def performance(frame,candidates,policy,config=ResearchConfig()):
    balance=peak=config.starting_balance
    max_dd=max_dd_pct=0.
    position=None;trades=[];skipped={};capped=0
    accepted={at:s for at,s in candidates.items() if policy.reason(s)=='OBSERVATION_ACCEPTED'}
    costs=(config.slippage_bps+config.spread_bps/2)/10000
    def skip(code):skipped[code]=skipped.get(code,0)+1
    def close(position,raw,at,reason,balance):
        fill=raw*(1-position['sign']*costs)
        gross=position['sign']*(fill-position['entry'])*position['quantity']
        pnl=gross-2*config.commission_per_side
        balance+=gross-config.commission_per_side
        trades.append({'entry_time':position['at'].isoformat(),'exit_time':at.isoformat(),
            'side':position['side'],'entry':position['entry'],'stop_initial':position['initial_stop'],
            'exit':fill,'reason':reason,'quantity':position['quantity'],'net_pnl':pnl,
            'r_multiple':pnl/position['initial_risk_amount']})
        return balance
    retained=frame[(frame.timestamp>='2026-03-01')&(frame.timestamp<'2026-10-18')]
    for bar in retained.itertuples(index=False):
        at=bar.timestamp.to_pydatetime()
        setup=accepted.get(at)
        if setup:
            if position:skip('POSITION_ALREADY_OPEN')
            elif balance<=0:skip('NO_CAPITAL')
            else:
                sign=1 if setup.side=='BUY' else -1
                entry=float(bar.open)*(1+sign*costs);stop=float(setup.stop)
                risk=sign*(entry-stop)
                if risk<=0:skip('INVALID_NEXT_OPEN_STOP')
                else:
                    raw_quantity=balance*config.risk_percent/100/risk
                    quantity=min(raw_quantity,balance*config.leverage/entry)
                    if quantity<raw_quantity:capped+=1
                    balance-=config.commission_per_side
                    position={'at':at,'side':setup.side,'sign':sign,'entry':entry,'stop':stop,
                        'initial_stop':stop,'risk':risk,'target':entry+sign*risk,
                        'quantity':quantity,'initial_risk_amount':quantity*risk,'be_next':False,'be_armed':False}
        if position:
            if position['be_next']:
                position['stop']=position['entry'];position['be_armed']=True;position['be_next']=False
            exit=protective_exit(position,bar)
            if exit:
                balance=close(position,exit[0],at,exit[1],balance)
                position=None
            elif not position['be_armed']:
                threshold=position['entry']+position['sign']*.68*position['risk']
                if (bar.high>=threshold if position['sign']==1 else bar.low<=threshold):
                    position['be_next']=True
        equity=balance+(position['sign']*(bar.close-position['entry'])*position['quantity'] if position else 0)
        peak=max(peak,equity)
        max_dd=max(max_dd,peak-equity)
        max_dd_pct=max(max_dd_pct,100*(peak-equity)/peak)
    if position:
        balance=close(position,float(bar.close),at,'END_OF_DATA',balance)
        max_dd=max(max_dd,peak-balance);max_dd_pct=max(max_dd_pct,100*(peak-balance)/peak)
    wins=[t['net_pnl'] for t in trades if t['net_pnl']>1e-8]
    losses=[t['net_pnl'] for t in trades if t['net_pnl'] < -1e-8]
    gross_profit=sum(wins);gross_loss=-sum(losses)
    return {'policy':policy.strategy_id,'policy_parameters':{'sides':policy.sides,'weekdays':policy.weekdays},
        'trade_count':len(trades),'wins':len(wins),'losses':len(losses),'breakeven':len(trades)-len(wins)-len(losses),
        'win_rate_percent':100*len(wins)/len(trades) if trades else 0,
        'profit_factor':gross_profit/gross_loss if gross_loss else None,
        'net_pnl':balance-config.starting_balance,'max_drawdown':max_dd,'max_drawdown_percent':max_dd_pct,
        'ending_balance':balance,'skipped':skipped,'leverage_capped_entries':capped,'trades':trades}


def comparison():
    frame=frames();candidates=session_candidates(frame)
    saved=json.loads((FIXTURES/'ledger_trades.json').read_text())
    keys={(pd.Timestamp(r['metadata.ifvg_confirmation_time']).to_pydatetime(),'BUY' if r['direction']=='long' else 'SELL') for r in saved}
    alignment={}
    for policy in (T02Policy.reference(),T02Policy.long_only()):
        found={(at,s.side) for at,s in candidates.items() if policy.reason(s)=='OBSERVATION_ACCEPTED'}
        alignment[policy.strategy_id]={'matching_saved':len(found&keys),'missing_saved':len(keys-found),
            'extra':len(found-keys),'sunday_candidates_emitted':sum(at.astimezone(NY).weekday()==6 for at,_ in found)}
    reference=performance(frame,candidates,T02Policy.reference())
    # Validate this independent simulator against saved per-trade price/exit facts,
    # not just aggregate entry counts. Does not assert original equity/P&L parity.
    exit_facts=json.loads((ROOT/'tests/fixtures/phase26/ledger_exit_facts.json').read_text())['rows']
    by_entry={pd.Timestamp(r['entry_time']).isoformat():r for r in exit_facts}
    mismatches=[]
    pnl_mismatches=[]
    for row in reference['trades']:
        original=by_entry.get(row['entry_time'])
        if not original or pd.Timestamp(row['exit_time'])!=pd.Timestamp(original['exit_time']) or any(abs(row[field]-float(original[column]))>1e-7 for field,column in [('entry','entry_price'),('exit','exit_price'),('stop_initial','metadata.initial_stop'),('r_multiple','r_multiple')]):
            mismatches.append(row['entry_time'])
        if not original or abs(row['quantity']-float(original['quantity']))>1e-7 or abs(row['net_pnl']-float(original['net_pnl']))>1e-7:
            pnl_mismatches.append(row['entry_time'])
    return {'assumptions':{'dates':['2026-03-01','2026-10-17'],'feed':'retained OANDA mid OHLC',
        **asdict(ResearchConfig()),'sizing':'1% current cash balance / initial stop distance, capped at 20x cash notional',
        'concurrent_positions':1,'entry':'next M1 open','target':'1R actual fill to structural stop',
        'breakeven':'.68R completed M1 touch -> next M1 open; protective exits first',
        'intrabar':'stop first; open gaps fill at open','overnight':True,
        'end':'close remaining at final M1 close','drawdown':'M1-close marked equity, not tick-level',
        'currency':'synthetic USD account, one price-dollar per unit; no broker lot conversion'},
        'input_sha256':hashlib.sha256((FIXTURES/'oanda_1m.csv.gz').read_bytes()).hexdigest(),
        'alignment':alignment,'reference_saved_exit_fact_mismatches':mismatches,'reference_saved_quantity_pnl_mismatches':pnl_mismatches,
        'reference':reference,'long_only':performance(frame,candidates,T02Policy.long_only())}


def main():
    result=comparison()
    (ROOT/'docs/phase26_comparison.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({name:{k:v for k,v in result[name].items() if k!='trades'} for name in ('reference','long_only')},indent=2))
    print('Alignment:',result['alignment'])
    print('Reference exit-fact mismatches:',len(result['reference_saved_exit_fact_mismatches']))

if __name__=='__main__':main()
