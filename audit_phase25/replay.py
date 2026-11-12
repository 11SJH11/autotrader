"""Execute only explicitly selected frozen pure definitions, not old bot modules.

AST loading removes imports, registry decorators, specification construction and
all startup code. Original function/method bodies remain unchanged. Ledger
contexts and concept observations are small interface shims, not its full engine.
"""
from __future__ import annotations
import __future__
import ast
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
from zoneinfo import ZoneInfo
import pandas as pd

FIXTURES=Path(__file__).resolve().parents[1]/'tests/fixtures/phase25'
UTC=timezone.utc
NY=ZoneInfo('America/New_York')
UK=ZoneInfo('Europe/London')


def load(name,*,base=None,classes=None):
    source=(FIXTURES/(name+'.txt')).read_text()
    tree=ast.parse(source)
    allowed=[]
    for node in tree.body:
        if isinstance(node,ast.Assign):
            allowed.append(node)
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            node.decorator_list=[]
            allowed.append(node)
        if isinstance(node,ast.ClassDef):
            node.decorator_list=[ast.Call(func=ast.Name(id='dataclass',ctx=ast.Load()),args=[],keywords=[ast.keyword(arg='frozen',value=ast.Constant(True))])] if node.name in ('Signal','EntrySignal','ManagePositionSignal','ExitSignal') else []
            node.body=[m for m in node.body if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef)) or
                       (isinstance(m,(ast.Assign,ast.AnnAssign)) and not any(isinstance(t,ast.Name) and t.id=='spec' for t in getattr(m,'targets',[]))) or
                       isinstance(m,ast.Expr) and isinstance(m.value,ast.Constant)]
            if not node.body:node.body=[ast.Pass()]
            allowed.append(node)
    tree.body=allowed
    ast.fix_missing_locations(tree)
    module=ModuleType('audit_phase25._frozen_'+name.replace('.','_'))
    env=module.__dict__
    env.update(dict(dataclass=dataclass,asdict=asdict,field=field,replace=replace,
        datetime=datetime,date=date,time=time,timedelta=timedelta,timezone=timezone,
        ZoneInfo=ZoneInfo,NY=NY,UTC=UTC,pd=pd,math=math,re=re,json=json,
        Observation=lambda **kwargs:SimpleNamespace(**kwargs),
        ASIA_START_ET=time(20),ASIA_END_ET=time(0),TARGET_R=1.0,BREAKEVEN_TRIGGER_R=.68))
    if base:env.update(base)
    sys.modules[module.__name__]=module
    exec(compile(tree,str(FIXTURES/(name+'.txt')),'exec',flags=__future__.annotations.compiler_flag),env)
    return env


class StrategyShim:
    def __init__(self,**params):self.params={'direction':'both',**params}


def sources():
    t02=load('t02_strategy.py')
    models=load('ledger_models.py')
    base=load('ledger_baseline.py',base=dict(models,Strategy=StrategyShim))
    child=load('ledger_child.py',base=base)
    canonical=child['GoldIfvgRatio020V2']
    canonical.spec=SimpleNamespace(key='gold_ifvg_v2_fvg_ratio_020')
    gaps=load('ledger_concepts.py')['gaps']
    return t02,canonical,gaps


def read_frames():
    frames={}
    for tf in ('1m','15m'):
        frame=pd.read_csv(FIXTURES/f'oanda_{tf}.csv.gz')
        frame['timestamp']=pd.to_datetime(frame.timestamp,utc=True)
        frame['available_at']=frame.timestamp+pd.Timedelta(minutes=1 if tf=='1m' else 15)
        frames[tf]=frame
    return frames


def session_minutes(frame,session):
    start=datetime.combine(date.fromisoformat(session),time(19,15),NY).astimezone(UTC)
    end=datetime.combine(date.fromisoformat(session)+timedelta(days=1),time(0),NY).astimezone(UTC)
    return frame[(frame.timestamp>=start)&(frame.timestamp<end)]


def canonical_session(canonical,gaps,one,fifteen,session,direction='both'):
    strategy=canonical(direction=direction)
    for row in fifteen.itertuples(index=False):
        when=row.available_at
        if when.astimezone(NY).date().isoformat()!=session or not 20<=when.astimezone(NY).hour<24:
            continue
        closed=one[one.available_at<=when]
        frame=fifteen[fifteen.available_at<=when]
        if closed.empty:continue
        ctx=SimpleNamespace(primary_timeframe='1m',position=None,current_bar=closed.iloc[-1],decision_time=when)
        ctx.bars=lambda tf,f=frame:f
        ctx.concept=lambda key,f=frame:{'observations':gaps(f,{'minimum_bps':0},{})}
        result=strategy.on_bar(ctx)
        if result is not None:
            return result
    return None


def record_t02(t02,frame,session):
    bars=[dict(time=r.timestamp.to_pydatetime(),open=r.open,high=r.high,low=r.low,close=r.close) for r in frame.itertuples(index=False)]
    signal=t02['first_session_ifvg'](bars,session)
    return signal


def compare():
    t02,canonical,gaps=sources()
    frames=read_frames()
    saved=json.loads((FIXTURES/'ledger_trades.json').read_text())
    run=json.loads((FIXTURES/'ledger_run.json').read_text())
    cfg=run['config']
    saved_keys={(pd.Timestamp(r['metadata.ifvg_confirmation_time']).isoformat(),r['direction']) for r in saved}
    all_t02=[];long_only=[];canonical_records=[];mismatches=[];complete=0;missing=0;raw_t02=[]
    sessions=pd.date_range(cfg['start_date'],cfg['end_date']).date
    for day in sessions:
        session=day.isoformat()
        one=session_minutes(frames['1m'],session)
        fifteen=session_minutes(frames['15m'],session)
        if one.empty:continue
        # Ledger engine._entry_allowed uses exchange-local weekdays (NY default),
        # not UTC weekdays. The old worker has no corresponding weekday filter.
        signal=record_t02(t02,one,session)
        legacy=canonical_session(canonical,gaps,one,fifteen,session)
        expected=285
        if len(one)==expected:complete+=1
        else:missing+=1
        if signal and signal.accepted:
            at=pd.Timestamp(signal.confirmed_at)
            rec={'session':session,'confirmed_at':at.isoformat(),'direction':signal.direction,
                     'stop':signal.stop,'ratio':signal.fvg_stop_ratio}
            raw_t02.append(rec)
            if at.tz_convert('America/New_York').weekday() in cfg['trading_weekdays']:
                all_t02.append(rec)
                if signal.direction=='long':long_only.append(rec)
        if legacy:
            at=pd.Timestamp(legacy.metadata['ifvg_confirmation_time'])
            if at.tz_convert('America/New_York').weekday() in cfg['trading_weekdays']:
                canonical_records.append({'session':session,'confirmed_at':at.isoformat(),'direction':legacy.direction,
                    'stop':legacy.stop_loss,'ratio':legacy.metadata['ifvg_fvg_to_stop_ratio_at_signal']})
        if (legacy is None)!=(signal is None or not signal.accepted) or (legacy and signal and
            (legacy.direction!=signal.direction or abs(legacy.stop_loss-signal.stop)>1e-8)):
            mismatches.append({'session':session,'canonical':None if legacy is None else {'direction':legacy.direction,'stop':legacy.stop_loss},
                               't02':None if signal is None else signal.record(),'m1_rows':len(one),'m15_rows':len(fifteen)})
    def summarize(records):
        keys={(r['confirmed_at'],r['direction']) for r in records}
        return {'matching':len(keys&saved_keys),'missing_saved':len(saved_keys-keys),'extra_detected':len(keys-saved_keys),
            'missing':sorted(saved_keys-keys),'extra':sorted(keys-saved_keys)}
    saved_detail={(pd.Timestamp(r['metadata.ifvg_confirmation_time']).isoformat(),r['direction']):r for r in saved}
    price_mismatches=[]
    for record in all_t02:
        original=saved_detail.get((record['confirmed_at'],record['direction']))
        if original:
            minute=frames['1m'][frames['1m'].timestamp==pd.Timestamp(record['confirmed_at'])]
            if len(minute)!=1 or abs(float(minute.iloc[0]['open'])-float(original['entry_price']))>1e-8 or abs(record['stop']-float(original['metadata.initial_stop']))>1e-8 or abs(record['ratio']-float(original['metadata.ifvg_fvg_to_stop_ratio_at_signal']))>1e-8:
                price_mismatches.append(record['confirmed_at'])
    result={'run_id':214,'saved_trade_count':len(saved),'scope':'signal-only comparison, flat contexts; not full execution/P&L parity',
        'complete_285_minute_windows':complete,'incomplete_nonempty_windows':missing,
        'aligned_t02_detector':summarize(all_t02),'aligned_buy_only_filter':summarize(long_only),
        'unfiltered_t02_detector':summarize(raw_t02),'current_buy_only_no_weekday_filter':summarize([r for r in raw_t02 if r['direction']=='long']),
        'canonical_ledger_signal_replay':summarize(canonical_records),
        'saved_entry_stop_ratio_mismatches':price_mismatches,
        'detector_vs_canonical_mismatches':mismatches,'t02_signals':all_t02,'canonical_signals':canonical_records}
    return result


def main():
    result=compare()
    destination=Path(__file__).resolve().parents[1]/'docs/phase25_comparison.json'
    destination.write_text(json.dumps(result,indent=2,default=str)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('t02_signals','canonical_signals','detector_vs_canonical_mismatches')},indent=2))
    print('canonical/detector discrepancy count',len(result['detector_vs_canonical_mismatches']))

if __name__=='__main__':main()
