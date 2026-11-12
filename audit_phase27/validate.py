from dataclasses import asdict
from datetime import datetime,timedelta,time,timezone
from decimal import Decimal
import json
from pathlib import Path
import pandas as pd

from audit_phase26.compare import frames,session_candidates,performance,ResearchConfig
from signal_adapters.t02 import T02Policy,Candle,NY,first_setup

ROOT=Path(__file__).resolve().parents[1]
SCENARIOS={
    'zero_cost':ResearchConfig(),
    'mild':ResearchConfig(spread_bps=1,slippage_bps=.25,commission_per_side=1),
    'moderate':ResearchConfig(spread_bps=3,slippage_bps=.5,commission_per_side=2.5),
    'severe':ResearchConfig(spread_bps=6,slippage_bps=1,commission_per_side=5),
}


def trade_metrics(trades,capital=10000):
    winners=[t['net_pnl'] for t in trades if t['net_pnl']>1e-8]
    losers=[t['net_pnl'] for t in trades if t['net_pnl'] < -1e-8]
    equity=peak=capital;dd=pct=0
    for trade in sorted(trades,key=lambda t:t['exit_time']):
        equity+=trade['net_pnl'];peak=max(peak,equity)
        dd=max(dd,peak-equity);pct=max(pct,100*(peak-equity)/peak)
    return {'trade_count':len(trades),'win_rate_percent':100*len(winners)/len(trades) if trades else 0,
        'average_winner':sum(winners)/len(winners) if winners else None,
        'average_loser':sum(losers)/len(losers) if losers else None,
        'profit_factor':sum(winners)/-sum(losers) if losers else None,
        'net_pnl':sum(t['net_pnl'] for t in trades),'closed_contribution_drawdown':dd,
        'closed_contribution_drawdown_percent':pct}


def breakdown(trades,frame):
    groups={'side':{},'entry_month_utc':{},'ny_confirmation_hour':{},'ny_session_date':{},'prior_hour_condition':{}}
    for trade in trades:
        at=pd.Timestamp(trade['entry_time']);ny=at.tz_convert('America/New_York')
        prior=frame[(frame.timestamp>=at-pd.Timedelta(hours=1))&(frame.timestamp<at)]
        condition='unavailable'
        if len(prior)==60 and (prior.timestamp.diff().dropna()==pd.Timedelta(minutes=1)).all():
            range_bps=(prior.high.max()-prior.low.min())/prior.close.iloc[-1]*10000
            trend='up' if prior.close.iloc[-1]>prior.open.iloc[0] else 'down' if prior.close.iloc[-1]<prior.open.iloc[0] else 'flat'
            condition=('range_below_20bps' if range_bps<20 else 'range_at_least_20bps')+'_'+trend
        keys={'side':trade['side'],'entry_month_utc':at.strftime('%Y-%m'),
            'ny_confirmation_hour':str(ny.hour),'ny_session_date':ny.date().isoformat(),'prior_hour_condition':condition}
        for dimension,key in keys.items():groups[dimension].setdefault(key,[]).append(trade)
    return {dimension:{key:trade_metrics(items) for key,items in sorted(values.items())} for dimension,values in groups.items()}


def causal_checks(frame,candidates):
    failures=[];checked=0
    for at,setup in candidates.items():
        begin=datetime.combine(datetime.fromisoformat(setup.session).date(),time(19,15),NY).astimezone(timezone.utc)
        subset=frame[(frame.timestamp>=begin)&(frame.timestamp<at)]
        bars=[Candle(r.timestamp.to_pydatetime(),*(Decimal(str(x)) for x in (r.open,r.high,r.low,r.close))) for r in subset.itertuples(index=False)]
        detected=first_setup(bars,setup.session)
        checked+=1
        if detected!=setup:failures.append(at.isoformat())
        # A future extreme may not change the first setup, including rejected firsts.
        if bars:
            last=bars[-1]
            future=Candle(at,Decimal(100000),Decimal(200000),Decimal(1),Decimal(100000))
            if first_setup(bars+[future],setup.session)!=detected:failures.append('future:'+at.isoformat())
    return {'prefix_cases':checked,'failures':failures,
        'scope':'prefix and future-perturbation checks, not a proof of all absence of bias'}


def validate():
    frame=frames();candidates=session_candidates(frame)
    report={'dates':['2026-03-01','2026-10-17'],'classification':'development',
        'classification_evidence':'Saved run-214-summary.json test_role=development; 44 inspected summaries were development',
        'genuinely_unseen_validation':False,
        'missing_unseen_evidence':['pre-registered untouched time interval','independent dataset provenance and immutable split manifest','complete broker-independent M1 history after retained coverage; calibration without test-period tuning'],
        'scenarios':{},'causality':causal_checks(frame,candidates)}
    for name,config in SCENARIOS.items():
        values={'cost_parameters':asdict(config),'policies':{}}
        for policy in (T02Policy.reference(),T02Policy.long_only()):
            result=performance(frame,candidates,policy,config)
            metrics=trade_metrics(result['trades'])
            metrics.update({k:result[k] for k in ('net_pnl','max_drawdown','max_drawdown_percent','wins','losses','breakeven')})
            values['policies'][policy.strategy_id]={'metrics':metrics,'breakdown':breakdown(result['trades'],frame),
                'skipped':result['skipped'],'leverage_capped_entries':result['leverage_capped_entries']}
        report['scenarios'][name]=values
    return report


def main():
    report=validate()
    (ROOT/'docs/phase27_validation.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    for scenario,data in report['scenarios'].items():
        print(scenario,json.dumps({name:value['metrics'] for name,value in data['policies'].items()}))
    print('Causal checks:',report['causality'])

if __name__=='__main__':main()
