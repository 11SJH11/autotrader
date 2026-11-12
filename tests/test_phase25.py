"""Regression evidence, including tests that demonstrate known legacy mismatches.

No old modules are imported; no sockets, secret reads, brokers, orders or daemons.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import time as systime

import pandas as pd
import pytest

from audit_phase25.replay import FIXTURES, UTC, NY, UK, load, sources, compare, read_frames, record_t02
from audit_phase25.timing import london_wall_to_utc


@pytest.fixture(scope='module')
def t02():
    return sources()[0]


@pytest.mark.parametrize('wall,expected',[
    ('2026-03-28T09:00:00','2026-03-28T09:00:00+00:00'),
    ('2026-03-29T09:00:00','2026-03-29T08:00:00+00:00'),
    ('2026-10-24T09:00:00','2026-10-24T08:00:00+00:00'),
    ('2026-10-25T09:00:00','2026-10-25T09:00:00+00:00'),
])
def test_uk_wall_time_conversion(wall,expected):
    assert london_wall_to_utc(wall).isoformat()==expected


def test_uk_gap_and_overlap_require_explicit_resolution():
    with pytest.raises(ValueError,match='Nonexistent'):
        london_wall_to_utc('2026-03-29T01:30:00')
    with pytest.raises(ValueError,match='Ambiguous'):
        london_wall_to_utc('2026-10-25T01:30:00')
    assert london_wall_to_utc('2026-10-25T01:30:00',fold=0).hour==0
    assert london_wall_to_utc('2026-10-25T01:30:00',fold=1).hour==1


@pytest.mark.parametrize('day,utc_hour,uk_hour',[
    ('2026-03-07',1,1),('2026-03-08',0,0),('2026-03-29',0,1),
    ('2026-10-24',0,1),('2026-10-25',0,0),('2026-11-01',1,1),
])
def test_ny_session_boundaries_independent_of_uk_dst(t02,day,utc_hour,uk_hour):
    local=datetime.fromisoformat(day+'T20:00:00').replace(tzinfo=NY)
    begin=local.astimezone(UTC)
    assert begin.hour==utc_hour
    assert begin.astimezone(UK).hour==uk_hour
    assert t02['asia_key'](begin)==day
    assert t02['asia_key'](begin-timedelta(seconds=1)) is None
    assert t02['asia_key'](begin+timedelta(hours=4)-timedelta(seconds=1))==day
    assert t02['asia_key'](begin+timedelta(hours=4)) is None


def test_actual_post_dst_broker_pair_only_proves_its_observation():
    evidence=json.loads((FIXTURES/'broker_clock_evidence.json').read_text())
    assert len(evidence)==1
    row=evidence[0]
    utc=datetime.fromtimestamp(row['reference_utc_epoch'],UTC)
    raw=row['raw_time_msc']/1000
    age=row['reference_utc_epoch']-(raw-row['observed_offset_seconds'])
    assert utc.date().isoformat()=='2026-11-09'
    assert row['observed_offset_seconds']==7200
    assert abs(utc.astimezone(UK).utcoffset().total_seconds())==0
    assert age==pytest.approx(.23375,abs=.0001)
    assert row['advancing_samples']>=6 and row['span_seconds']>=2
    assert row['clock_reference_state']=='VALIDATED'
    assert abs(row['local_clock_delta_seconds'])<2
    # No fabricated observation on either side of a seasonal transition.
    assert not any(datetime.fromtimestamp(x['reference_utc_epoch'],UTC).date().isoformat()<'2026-10-25' for x in evidence)


def test_current_tick_offset_cannot_normalize_mixed_historical_conventions(t02):
    before=datetime(2026,10,24,8,tzinfo=UTC)
    raw=before.timestamp()+10800  # Explicit synthetic historical convention.
    assert t02['dt_from_epoch'](raw,3)==before
    assert t02['dt_from_epoch'](raw,2)==before+timedelta(hours=1)


@pytest.fixture
def hurricane_age():
    return load('hurricane_bot.py')['too_old']


@pytest.mark.parametrize('age,expected',[(0,False),(90,False),(91,True),(-3,False),(-4,True)])
def test_hurricane_publication_age_bounds(hurricane_age,age,expected):
    now=datetime(2026,11,9,9,tzinfo=UTC)
    app=SimpleNamespace(now=lambda:now,max_age=90,started_at=now-timedelta(hours=1))
    assert hurricane_age(app,SimpleNamespace(date=now-timedelta(seconds=age))) is expected


def test_hurricane_reconnect_epoch_blocks_even_fresh_earlier_publication(hurricane_age):
    now=datetime(2026,11,9,9,tzinfo=UTC)
    app=SimpleNamespace(now=lambda:now,max_age=90,started_at=now-timedelta(seconds=2))
    assert hurricane_age(app,SimpleNamespace(date=now-timedelta(seconds=3))) is True
    assert hurricane_age(app,SimpleNamespace(date=now-timedelta(seconds=1))) is False


def test_naive_uk_summer_publication_is_misinterpreted_by_legacy_gate(hurricane_age):
    correct=london_wall_to_utc('2026-07-01T09:00:00')
    now=correct+timedelta(seconds=10)
    app=SimpleNamespace(now=lambda:now,max_age=90,started_at=now-timedelta(minutes=1))
    assert not hurricane_age(app,SimpleNamespace(date=correct))
    # Documents a defect for naive UK input, not a recommended behavior.
    assert hurricane_age(app,SimpleNamespace(date=datetime(2026,7,1,9)))


def test_real_message_delays_sanitized_and_no_publication_in_reconstructions():
    data=json.loads((FIXTURES/'hurricane_delays.json').read_text())
    assert len(data)==169
    assert sum(r['delay_seconds']>90 for r in data)==2
    assert all('text' not in r and 'message_id' not in r and 'peer' not in r for r in data)
    parsed=json.loads((FIXTURES/'hurricane_parsed.json').read_text())
    assert len(parsed)==9
    assert all(row['published'] is None for row in parsed)


def hurricane_parser():
    parser=load('hurricane_parser.py')
    return load('hurricane_rules.py',base=parser)


def test_real_parsed_levels_through_original_parser_without_raw_messages():
    parser=hurricane_parser()
    rows=json.loads((FIXTURES/'hurricane_parsed.json').read_text())
    for index,row in enumerate(rows):
        side=row['side']
        # Generated grammar carrier, explicitly not the original private message.
        text=f"GOLD {side} SETUP\nGold {side.title()} Zone {row['zone'][0]}-{row['zone'][1]}\nSL : {row['stop']}"
        text+=''.join(f'\nTP{i} : {value}' for i,value in enumerate(row['targets'],1))
        msg=SimpleNamespace(id=index,date=None,edit_date=None,reply_to_msg_id=None,raw_text=text)
        signal=parser['from_live_message'](msg)
        assert signal['direction']==side
        assert signal['entry_zone_low']==min(row['zone'])
        assert signal['stop_loss']==row['stop']
        assert signal['take_profits']==row['targets']
        assert signal['date'] is None  # Never substitute audit receipt for publication.


def test_edit_never_becomes_new_entry():
    env=load('hurricane_bot.py',base={'from_live_message':lambda msg:None})
    events=[]
    msg=SimpleNamespace(id=1)
    store=SimpleNamespace(has=lambda mid:True,event=lambda *args:events.append(args),
        lifecycle=SimpleNamespace(outcome=lambda *args:events.append(args)))
    async def notify(text):pass
    app=SimpleNamespace(trusted_source=lambda msg:True,waiting={1:object()},store=store,mode='DRY_RUN',notify=notify)
    asyncio.run(env['edited_message'](app,SimpleNamespace(message=msg)))
    assert 1 not in app.waiting
    assert any('EDIT_NEVER_ENTERS' in args for args in events)


def test_market_eligibility_uses_evaluation_quote_not_publication_price():
    env=load('hurricane_bridge.py',base={'SafetyError':RuntimeError})
    signal={'entry_zone_low':4000,'entry_zone_high':4002,'direction':'BUY','take_profits':[4005,4008,4010]}
    app=SimpleNamespace(quote=lambda:(SimpleNamespace(ask=4001,bid=4000.8),None))
    assert env['entry_plan'](app,signal,.8)==('market',4001)
    app.quote=lambda:(SimpleNamespace(ask=4003,bid=4002.8),None)
    assert env['entry_plan'](app,signal,.8)==('limit',4002)
    app.quote=lambda:(SimpleNamespace(ask=4005,bid=4004.8),None)
    with pytest.raises(RuntimeError,match='TP1'):
        env['entry_plan'](app,signal,.8)


class Clock:
    def __init__(self):self.utc=1794208000.;self.mono=0.
    def now(self):return self.utc
    def advance(self,seconds=.5):self.utc+=seconds;self.mono+=seconds


def tick_types(clock):
    return load('hurricane_ticks.py',base={'time':systime})


def calibrate(estimator,clock,offset):
    statuses=[]
    for _ in range(6):
        tick=SimpleNamespace(time=int(clock.utc+offset),time_msc=(clock.utc+offset)*1000)
        statuses.append(estimator.observe(tick))
        clock.advance()
    assert statuses==[False]*5+[True]


def test_tick_staleness_duplicates_regression_and_reconnect():
    clock=Clock();env=tick_types(clock)
    estimator=env['TickEstimator'](clock=clock,monotonic=lambda:clock.mono)
    calibrate(estimator,clock,7200)
    raw=clock.utc+7200
    estimator.observe(SimpleNamespace(time_msc=raw*1000))
    clock.advance()
    with pytest.raises(env['TimestampError'],match='REGRESSED'):
        estimator.observe(SimpleNamespace(time_msc=(raw-.1)*1000))
    assert estimator.offset is None
    with pytest.raises(env['TimestampError'],match='stale'):
        estimator.observe(SimpleNamespace(time_msc=(clock.utc+7200-20)*1000))
    calibrate(estimator,clock,7200)
    estimator.reset()  # Same reset production uses on reconnect/terminal change.
    assert not estimator.observe(SimpleNamespace(time_msc=(clock.utc+7200)*1000))


def test_duplicate_ticks_cannot_calibrate_and_convention_change_requires_new_samples():
    clock=Clock();env=tick_types(clock)
    estimator=env['TickEstimator'](clock=clock,monotonic=lambda:clock.mono)
    raw=clock.utc+10800
    for _ in range(8):
        assert not estimator.observe(SimpleNamespace(time_msc=raw*1000))
        clock.advance(.25)
    assert estimator.offset is None and len(estimator.samples)==1
    estimator.reset()
    calibrate(estimator,clock,10800)
    calibrate(estimator,clock,7200)
    assert estimator.offset==7200


def test_host_clock_drift_fails_closed_without_network():
    clock=Clock()
    time_proxy=SimpleNamespace(monotonic=lambda:clock.mono,time=lambda:clock.utc+60)
    env=load('hurricane_ticks.py',base={'time':time_proxy})
    utc=env['UTCClock']()
    utc.anchor=(clock.mono,clock.utc)
    with pytest.raises(env['TimestampError'],match='LOCAL_CLOCK_UNSOUND'):
        utc.now()


def synthetic_minutes(day='2026-10-26'):
    start=datetime.fromisoformat(day+'T19:15:00').replace(tzinfo=NY).astimezone(UTC)
    candles=[(111,112,110,111),(109,111,108,109),(108,109,97,108),(108,113,108,112),(113,115,111,114)]
    return [dict(time=start+timedelta(minutes=15*i+j),open=o,high=h,low=l,close=c)
            for i,(o,h,l,c) in enumerate(candles) for j in range(15)]


def test_no_lookahead_first_setup_stop_and_ratio(t02):
    bars=synthetic_minutes()
    assert t02['first_session_ifvg'](bars[:59],'2026-10-26') is None
    signal=t02['first_session_ifvg'](bars[:60],'2026-10-26')
    assert signal.accepted and signal.direction=='long'
    assert signal.confirmed_at=='2026-10-27T00:15:00+00:00'
    assert signal.stop==97
    assert signal.fvg_stop_ratio==pytest.approx(1/15)
    assert t02['first_session_ifvg'](bars,'2026-10-26')==signal
    # Next M1 open differs from signal close: filter may not use the future fill.
    assert bars[60]['open']==113 and signal.signal_close==112


def test_missing_duplicate_and_out_of_order_minutes(t02):
    bars=synthetic_minutes()
    expected=t02['first_session_ifvg'](bars,'2026-10-26')
    assert t02['first_session_ifvg'](list(reversed(bars)),'2026-10-26')==expected
    assert t02['first_session_ifvg'](bars[:35]+bars[36:],'2026-10-26') is None
    assert t02['first_session_ifvg'](bars[:35]+[bars[35]]+bars[35:],'2026-10-26') is None
    with pytest.raises(ValueError,match='UTC aware'):
        t02['complete_15m']([{**bars[0],'time':bars[0]['time'].replace(tzinfo=None)}])


def test_feed_recovery_consumes_prior_first_setup_without_order(t02):
    events=[]
    env=load('t02_worker.py',base={**t02,'event':lambda *args,**kwargs:events.append((args,kwargs)),
                                 'iso':lambda dt:dt.isoformat()})
    bars=synthetic_minutes()[:60]
    closed=bars[-1]['time']+timedelta(minutes=1)
    state={'blocked_sessions':['2026-10-26'],'attempted_sessions':[]}
    env['recover_feed_session'](None,state,bars,'2026-10-26',closed,bars[-2]['time'],0)
    assert state['attempted_sessions']==['2026-10-26']
    assert events[0][0][1]=='recovery_ifvg_consumed_no_order'
    state={'blocked_sessions':['2026-10-26'],'attempted_sessions':[]}
    env['recover_feed_session'](None,state,bars,'2026-10-26',closed,bars[-2]['time'],20)
    assert state['attempted_sessions']==[]


def test_saved_ledger_comparison_reproducible():
    report=compare()
    assert report['saved_trade_count']==64
    assert report['aligned_t02_detector']['matching']==64
    assert report['aligned_t02_detector']['missing_saved']==0
    assert report['aligned_t02_detector']['extra_detected']==0
    assert report['current_buy_only_no_weekday_filter']['matching']==30
    assert report['current_buy_only_no_weekday_filter']['missing_saved']==34
    assert report['current_buy_only_no_weekday_filter']['extra_detected']==7
    assert not report['saved_entry_stop_ratio_mismatches']
    assert not report['detector_vs_canonical_mismatches']


def test_candidate_ratios_are_verified_arithmetic_not_candle_parity():
    candidates=json.loads((FIXTURES/'t02_recorded_candidates.json').read_text())
    for row in candidates:
        ratio=(row['fvg_upper']-row['fvg_lower'])/abs(row['signal_close']-row['stop'])
        assert ratio==pytest.approx(row['fvg_stop_ratio'])
        assert row['accepted']==(ratio<=.2)
    assert candidates[0]['confirmed_at']=='2026-10-27T00:15:00+00:00'
    assert candidates[1]['confirmed_at']=='2026-11-09T04:30:00+00:00'
    assert not candidates[1]['accepted']
    assert candidates[2]['direction']=='short'
    coverage=json.loads((FIXTURES/'market_coverage.json').read_text())
    assert all(pd.Timestamp(c['last'])<pd.Timestamp('2026-10-27',tz='UTC') for c in coverage)


def test_frozen_sources_match_manifest_and_selected_child_hash():
    manifest=json.loads((FIXTURES/'source_manifest.json').read_text())
    for row in manifest:
        assert hashlib.sha256((FIXTURES/(row['name']+'.txt')).read_bytes()).hexdigest()==row['snapshot_sha256']
    run=json.loads((FIXTURES/'ledger_run.json').read_text())
    child=next(r for r in manifest if r['name']=='ledger_child.py')
    assert child['source_sha256']==run['strategy_source_hash']


def test_delayed_source_events_are_not_misreported_as_delayed_trade_signals():
    rows=json.loads((FIXTURES/'hurricane_delays.json').read_text())
    signals=[r for r in rows if r['parsed_signal']]
    assert len(signals)==33
    assert all(r['delay_seconds']<=90 for r in signals)
    assert all(not r['parsed_signal'] for r in rows if r['delay_seconds']>90)


def test_canonical_breakeven_uses_completed_bar_and_next_open_metadata():
    _,canonical,_=sources()
    strategy=canonical()
    pos=SimpleNamespace(entry_price=100,stop_loss=90,initial_risk_per_share=10,direction='long',
        metadata={'strategy_version':strategy.spec.key})
    ctx=SimpleNamespace(position=pos,current_bar={'high':106.79,'low':99},decision_time=datetime(2026,3,10,2,tzinfo=UTC))
    assert strategy._manage_breakeven(ctx) is None
    ctx.current_bar['high']=106.8
    result=strategy._manage_breakeven(ctx)
    assert result.new_stop_loss==100
    assert result.metadata['ifvg_breakeven_application']=='next_primary_bar_open'
    pos.metadata['ifvg_breakeven_armed']=True
    assert strategy._manage_breakeven(ctx) is None


def test_saved_targets_are_one_r_from_actual_saved_open():
    for row in json.loads((FIXTURES/'ledger_trades.json').read_text()):
        entry,stop,target=map(float,(row['entry_price'],row['metadata.initial_stop'],row['take_profit']))
        assert abs(target-entry)==pytest.approx(abs(entry-stop))


def test_current_worker_fixed_sizing_is_not_percentage_risk_sizing():
    env=load('t02_worker.py',base={'SYMBOL':'AUDIT-ONLY'})
    calls=[]
    mt5=SimpleNamespace(ORDER_TYPE_BUY=0,order_calc_profit=lambda *args:calls.append(args) or -50)
    spec=SimpleNamespace(volume_step=.01,volume_min=.01,volume_max=1)
    for risk in (.1,1,10):
        quantity,loss=env['risk_volume'](mt5,object(),spec,'long',100,90,risk_pct=risk)
        assert quantity==.05 and loss==50
    with pytest.raises(RuntimeError,match='long-only'):
        env['risk_volume'](mt5,object(),spec,'short',100,110)
    assert len(calls)==3


def test_missing_candle_invalidation_is_stricter_than_ledger_concept():
    _,_,gaps=sources()
    # Canonical FVG concept uses adjacent dataframe rows with no elapsed-time check.
    # The worker deliberately refuses to build a corresponding discontinuous M15 path.
    frame=pd.DataFrame([
        {'timestamp':pd.Timestamp('2026-10-27T00:00Z'),'available_at':pd.Timestamp('2026-10-27T00:15Z'),'high':112,'low':110,'close':111},
        {'timestamp':pd.Timestamp('2026-10-27T00:15Z'),'available_at':pd.Timestamp('2026-10-27T00:30Z'),'high':111,'low':108,'close':109},
        {'timestamp':pd.Timestamp('2026-10-27T00:45Z'),'available_at':pd.Timestamp('2026-10-27T01:00Z'),'high':109,'low':97,'close':108}])
    assert len(gaps(frame,{'minimum_bps':0},{}))==1


def test_all_frozen_fixture_files_have_integrity_hashes():
    for name,digest in json.loads((FIXTURES/'fixture_integrity.json').read_text()).items():
        assert hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest()==digest


def test_first_rejected_setup_is_not_replaced_by_later_setup(t02):
    bars=synthetic_minutes()
    # Increase the source bearish gap and tighten its path stop, so the first
    # inversion is mechanically valid but fails the .20 ratio rule.
    for bar in bars[30:45]:
        bar.update(open=104,high=105,low=100,close=104)
    signal=t02['first_session_ifvg'](bars[:60],'2026-10-26')
    assert signal is not None and not signal.accepted
    assert signal.fvg_stop_ratio==pytest.approx(5/12)
    assert t02['first_session_ifvg'](bars,'2026-10-26')==signal


def test_stored_m15_matches_complete_m1_aggregation():
    result=json.loads((FIXTURES/'market_consistency.json').read_text())
    assert result['joined_15m']==14869
    assert all(difference==0 for difference in result['maximum_ohlc_difference'].values())
    assert result['duplicate_m1']==result['duplicate_m15']==0


def test_oct27_reason_is_historical_risk_policy_not_today_fixed_lot_policy():
    result=json.loads((FIXTURES/'t02_runtime_diagnostics.json').read_text())
    abort=next(e for e in result['events'] if e['kind']=='entry_aborted_fail_closed')
    assert abort['at'].startswith('2026-10-27T00:15:02')
    assert 'risk budget' in abort['normalized_error']
    assert result['cycle_error_categories']['stale/future']==35694


def worker_harness(bars,now):
    t02=sources()[0]
    events=[];writes=[]
    env=load('t02_worker.py',base={**t02,'_GATEWAY':None,
        'now_utc':lambda:now[0], 'same_account':lambda *args:None,
        'exact_symbol':lambda *args:None,'snapshot_positions':lambda *args:None,
        'quote':lambda *args:(None,0),'read_minutes':lambda *args:bars[:],
        'update_health':lambda *args,**kwargs:None,'iso':lambda dt:dt.isoformat(),
        'runtime_state_path':lambda *args:None,
        'write_json':lambda *args:writes.append(json.loads(json.dumps(args[-1]))),
        'event':lambda root,kind,**kwargs:events.append((kind,kwargs)),
        'open_demo':lambda *args:pytest.fail('Audit attempted execution')})
    env["read_minutes"]=lambda *args:bars[:]
    return env,events,writes


def test_worker_duplicate_boundary_and_restart_do_not_repeat_signal():
    bars=synthetic_minutes()[:60]
    closed=bars[-1]['time']+timedelta(minutes=1)
    env,events,writes=worker_harness(bars,[closed+timedelta(seconds=2)])
    state={'positions':{},'last_bar':(bars[-1]['time']-timedelta(minutes=1)).isoformat(),
           'attempted_sessions':[],'blocked_sessions':[]}
    env['one_iteration'](None,None,{'offset_hours':0},state,SimpleNamespace(mode='signal'))
    assert sum(kind=='signal_only_no_order' for kind,_ in events)==1
    persisted=json.loads(json.dumps(state))
    env['one_iteration'](None,None,{'offset_hours':0},persisted,SimpleNamespace(mode='signal'))
    assert sum(kind=='first_ifvg' for kind,_ in events)==1
    assert writes[0]['attempted_sessions']==['2026-10-26']


def test_worker_stale_boundary_and_bootstrap_never_backfill_orders():
    bars=synthetic_minutes()[:60]
    closed=bars[-1]['time']+timedelta(minutes=1)
    now=[closed+timedelta(seconds=20)]
    env,events,writes=worker_harness(bars,now)
    state={'positions':{},'last_bar':(bars[-1]['time']-timedelta(minutes=1)).isoformat(),
           'attempted_sessions':[],'blocked_sessions':[]}
    env['one_iteration'](None,None,{'offset_hours':0},state,SimpleNamespace(mode='signal'))
    assert '2026-10-26' in state['blocked_sessions']
    assert not any(kind=='signal_only_no_order' for kind,_ in events)
    # A restarted observer bootstraps, then consumes an earlier IFVG without entry.
    now[0]=closed+timedelta(seconds=2)
    state={'positions':{},'attempted_sessions':[],'blocked_sessions':[]}
    env['one_iteration'](None,None,{'offset_hours':0},state,SimpleNamespace(mode='signal'))
    bars.append(synthetic_minutes()[60]);now[0]+=timedelta(minutes=1)
    env['one_iteration'](None,None,{'offset_hours':0},state,SimpleNamespace(mode='signal'))
    assert any(kind=='historical_ifvg_not_tradable' for kind,_ in events)
    assert not any(kind=='signal_only_no_order' for kind,_ in events)
