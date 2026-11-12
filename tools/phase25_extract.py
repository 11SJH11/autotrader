"""Read-only, column-allowlisted local evidence extraction. No bot imports or broker I/O.

Writes sanitized evidence only inside AutoTrader. Does not open ledger.db, config,
health, environment, session or credential files. Raw Telegram text is never read.
"""
from pathlib import Path
import csv
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from collections import Counter
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'tests/fixtures/phase25'
PROJECTS=Path('/home/SJH/Projects')
H=PROJECTS/'telegram_mt5_demo_bot'
T=PROJECTS/'t02_mt5_demo_bot'
L=PROJECTS/'Investment-Tool'


def write(name,data):
    (OUT/name).write_text(json.dumps(data,indent=2,default=str)+'\n')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    # Strategy-only source snapshots have no top-level imports or startup code
    # executed by extraction. Their original hashes/locations are retained.
    files={'t02_strategy.py':T/'t02_strategy.py',
        'ledger_baseline.py':L/'backend/app/backtesting/strategies/gold_asia_15m_ifvg_research_v1.py',
        'ledger_child.py':L/'backend/app/backtesting/strategies/gold_asia_15m_ifvg_loss_tests_v2.py',
        'ledger_concepts.py':L/'backend/app/market_concepts/builtins.py',
        'ledger_models.py':L/'backend/app/backtesting/models.py',
        'hurricane_bot.py':H/'bot.py','hurricane_bridge.py':H/'mt5_bridge.py',
        'hurricane_ticks.py':H/'tick_normalization.py','hurricane_rules.py':H/'rules.py',
        'hurricane_parser.py':H/'export_parser.py','t02_worker.py':T/'t02_demo_bot.py'}
    import ast
    # Retain only audit-relevant definitions; never copy configuration/startup code.
    selections={
        't02_strategy.py':None,
        'ledger_baseline.py':['GoldAsia15mIfvgResearchV1','_no_prior_inversion','_path_from_gap_to_inversion','_asia_session_key','_asia_session_end','_available_at','_ts'],
        'ledger_child.py':['_ControlledGoldChild','GoldIfvgRatio020V2'],
        'ledger_concepts.py':['gaps'], 'ledger_models.py':['EntrySignal','ManagePositionSignal','ExitSignal'],
        'hurricane_bot.py':['App.too_old','App.edited_message'],
        'hurricane_bridge.py':['DemoMT5.entry_plan'],
        'hurricane_ticks.py':['TimestampError','UTCClock','TickEstimator'],
        'hurricane_rules.py':['from_live_message','validate'],
        'hurricane_parser.py':['message_text','parse_signal'],
        't02_worker.py':['read_minutes','session_history_complete','recover_feed_session','risk_volume','_open_demo','one_iteration','manage_demo']}
    manifest=[]
    for name,path in files.items():
        raw=path.read_bytes();source=raw.decode();tree=ast.parse(source)
        selected=[]
        names=selections[name]
        for node in tree.body:
            constants={'t02_strategy.py':{'NY','UTC'},'ledger_baseline.py':{'NY','ASIA_START_ET','ASIA_END_ET','TARGET_R','BREAKEVEN_TRIGGER_R'}}
            if isinstance(node,ast.Assign) and (name=='hurricane_parser.py' or any(isinstance(target,ast.Name) and target.id in constants.get(name,set()) for target in node.targets)):
                selected.append(node)
            if names is None:
                if isinstance(node,(ast.FunctionDef,ast.ClassDef)):
                    selected.append(node)
            elif isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in names:
                selected.append(node)
            elif isinstance(node,ast.ClassDef):
                for method in node.body:
                    if isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name+'.'+method.name in names:
                        selected.append(method)
        # Snapshot as text; AST loader never executes imports, registry, spec or constructors.
        text='\n\n'.join(ast.get_source_segment(source,n) for n in selected)+'\n'
        (OUT/(name+'.txt')).write_text(text)
        manifest.append({'name':name,'source':str(path.relative_to(PROJECTS)),
            'source_sha256':hashlib.sha256(raw).hexdigest(),
            'snapshot_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'locations':[{'definition':getattr(n,'name',ast.unparse(n.targets[0]) if isinstance(n,ast.Assign) else 'constant'),'line':n.lineno,'end_line':n.end_lineno} for n in selected]})
    write('source_manifest.json',manifest)
    summary=json.loads((L/'backend/research_output/run-214-summary.json').read_text())
    config=summary['config']
    allowed=('start_date','end_date','primary_timeframe','session','strategy_params','sizing_mode','risk_value','commission_per_order','slippage_bps','spread_bps','max_leverage','max_open_positions','same_bar_policy','trading_weekdays','allow_overnight','force_close_time','market_data_fingerprint')
    write('ledger_run.json',{'run_id':214,'strategy_key':summary['strategy']['key'],
        'strategy_source_hash':summary['strategy']['implementation']['source_sha256'],
        'config':{k:config[k] for k in allowed},'trade_count':summary['trade_count_saved']})
    trades=[]
    with (L/'backend/research_output/run-214-trades.csv').open() as stream:
        for row in csv.DictReader(stream):
            fields=('direction','entry_time','exit_time','entry_price','stop_loss','take_profit','exit_reason',
                    'metadata.asia_session_key','metadata.ifvg_confirmation_time','metadata.fvg_confirmed_at',
                    'metadata.initial_stop','metadata.ifvg_fvg_to_stop_ratio_at_signal','r_multiple')
            trades.append({k:row[k] for k in fields})
    write('ledger_trades.json',trades)
    # Original cache is a public-market OHLC feed, not broker/account records.
    frames={}
    coverage=[]
    for tf in ('1m','15m'):
        path=L/f'backend/data/market/bars/oanda-mid-v1/{tf}/XAUUSD.parquet'
        table=pq.read_table(path)
        frame=table.to_pandas()
        coverage.append({'timeframe':tf,'rows':len(frame),'first':str(frame.timestamp.min()),'last':str(frame.timestamp.max()),
            'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        # Full selected run interval; include enough warmup for first eligible FVG.
        subset=frame[(frame.timestamp>='2026-02-28') & (frame.timestamp<'2026-10-18')]
        subset.to_csv(OUT/f'oanda_{tf}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    write('market_coverage.json',coverage)
    import pandas as pd
    one=pd.read_csv(OUT/'oanda_1m.csv.gz');fifteen=pd.read_csv(OUT/'oanda_15m.csv.gz')
    for frame in (one,fifteen):frame['timestamp']=pd.to_datetime(frame.timestamp,utc=True)
    grouped=one.set_index('timestamp').resample('15min')
    derived=grouped.agg({'open':'first','high':'max','low':'min','close':'last'})
    derived=derived[grouped.close.count()==15]
    joined=derived.merge(fifteen,on='timestamp',suffixes=('_derived','_cached'))
    write('market_consistency.json',{'m1_rows':len(one),'m15_rows':len(fifteen),
        'derived_complete_15m':len(derived),'joined_15m':len(joined),
        'maximum_ohlc_difference':{k:float((joined[k+'_derived']-joined[k+'_cached']).abs().max()) for k in ('open','high','low','close')},
        'duplicate_m1':int(one.timestamp.duplicated().sum()),'duplicate_m15':int(fifteen.timestamp.duplicated().sum())})
    connection=sqlite3.connect((H/'bot_state.sqlite3').as_uri()+'?mode=ro',uri=True)
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    # Only publication/receipt metadata; peer/sender/message IDs and text excluded.
    timing=[]
    for observed,published,edited,parsed in connection.execute("SELECT e.time,json_extract(e.details,'$.message_date'),json_extract(e.details,'$.edited'),EXISTS(SELECT 1 FROM events x WHERE x.message_id=e.message_id AND x.kind='SIGNAL') FROM events e WHERE e.kind='SOURCE_AUTHENTICATED' ORDER BY e.id"):
        if published:
            a=datetime.fromisoformat(published);b=datetime.fromisoformat(observed)
            timing.append({'fixture_id':f'H{len(timing)+1:03}', 'published':published,'observed':observed,
                'edited':bool(edited),'parsed_signal':bool(parsed),'delay_seconds':(b-a).total_seconds()})
    write('hurricane_delays.json',timing)
    ticks=[]
    for values in connection.execute("SELECT json_extract(plan,'$.tick_normalization.utc'),json_extract(plan,'$.tick_normalization.raw_time_msc'),json_extract(plan,'$.tick_normalization.observed_offset_seconds'),json_extract(plan,'$.tick_normalization.advancing_samples'),json_extract(plan,'$.tick_normalization.observation_span_seconds'),json_extract(plan,'$.tick_normalization.utc_reference.state'),json_extract(plan,'$.tick_normalization.utc_reference.local_clock_delta_seconds') FROM accepted WHERE json_extract(plan,'$.tick_normalization.utc') IS NOT NULL"):
        ticks.append(dict(zip(('reference_utc_epoch','raw_time_msc','observed_offset_seconds','advancing_samples','span_seconds','clock_reference_state','local_clock_delta_seconds'),values)))
    write('broker_clock_evidence.json',ticks)
    parsed=[]
    for payload in (H/'fixtures/hurricane_audit_20261027.jsonl').open():
        row=json.loads(payload)
        fields=row['parsed_fields']
        parsed.append({'fixture_id':f'P{len(parsed)+1:02}','published':row.get('date'),
            'observed_at':row['audit_observed_at'],'side':fields['side'],
            'zone':fields['zone'],'stop':fields['sl'],'targets':fields['tps'],
            'provenance':'Real parsed audit; original message text and publication time unavailable'})
    write('hurricane_parsed.json',parsed)
    connection.close()
    # Events are structured worker diagnostics; whitelist only strategy facts.
    candidates=[]
    event_counts=Counter()
    diagnostics=[]
    failures=Counter()
    for line in (T/'t02_state/events.jsonl').open():
        try:row=json.loads(line)
        except ValueError:continue
        event_counts[row.get('kind')]+=1
        kind=row.get('kind')
        if kind=='cycle_error_no_trade':
            error=str(row.get('error',''))
            category=next((key for key in ('UTC_REFERENCE_UNAVAILABLE','UTC_SOURCES_DISAGREE','LOCAL_CLOCK_UNSOUND','TICK_TIMESTAMP_REGRESSED','MT5_INIT_EXHAUSTED','OFFSET_CONVENTION_UNSTABLE','TICK_CALIBRATION_TIMEOUT','stale/future','disconnected') if key in error),'other_unclassified')
            failures[category]+=1
        if kind in ('first_ifvg','entry_aborted_fail_closed','preflight_rejected_no_submission','strategy_rejected','data_gap_skip','stale_completed_candle_skip'):
            safe={k:row[k] for k in ('kind','at','session','confirmed_at','direction','accepted','reason','from_bar','to_bar','delay_seconds','session_blocked') if k in row}
            if kind=='entry_aborted_fail_closed' and 'exceeds risk budget' in str(row.get('error','')):
                safe['normalized_error']='Minimum authorized/broker lot exceeds risk budget: SKIP'
            diagnostics.append(safe)
        if kind=='first_ifvg':
            fields=('session','direction','confirmed_at','source_fvg_at','fvg_lower','fvg_upper','stop','signal_close','fvg_stop_ratio','accepted','reason')
            candidates.append({k:row[k] for k in fields if k in row})
        if kind=='strategy_evaluated' and row.get('candle_close')=='2026-11-09T04:30:00+00:00':
            candidates.append({'event':'strategy_evaluated','execution_gate':row['execution_gate'],**row['signal']})
    write('t02_recorded_candidates.json',candidates)
    write('t02_event_counts.json',dict(event_counts))
    write('t02_runtime_diagnostics.json',{'events':diagnostics,'cycle_error_categories':dict(failures)})
    write('fixture_integrity.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='fixture_integrity.json'})
    print(json.dumps({'source_files':len(files),'market_coverage':coverage,'saved_trades':len(trades),
        'hurricane_publication_receipt_pairs':len(timing),'paired_broker_clock_records':len(ticks),'recorded_candidates':len(candidates)},indent=2))

if __name__=='__main__':main()
