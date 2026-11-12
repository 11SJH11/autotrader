from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
import json
from pathlib import Path

import pandas as pd
import pytest

from control_plane.contracts import Quote,TradeIntent
from control_plane.engine import Engine,FakeBroker
from signal_adapters.common import ObservationJournal,ObservationSizing
from signal_adapters.hurricane import HurricaneAdapter,HurricaneMessage
from signal_adapters.timestamps import timestamp,RawBrokerEpoch,OffsetEvidence,broker_epoch_to_utc
from signal_adapters.t02 import Candle,T02Policy,T02Adapter,first_setup,session_key,NY
from audit_phase26.compare import comparison,performance,ResearchConfig,protective_exit
from test_phase25 import synthetic_minutes

UTC=timezone.utc
FIX=Path(__file__).parent/'fixtures/phase25'
SIZE=ObservationSizing(D('.01'))
NOW=datetime(2026,7,1,8,0,10,tzinfo=UTC)
Q=Quote(D('4000.8'),D('4001'),NOW)


@pytest.fixture
def journal(tmp_path):
    value=ObservationJournal(tmp_path/'observations.sqlite')
    yield value
    value.close()


def message(**changes):
    return replace(HurricaneMessage('opaque-setup','2026-07-01T09:00:00','uk_local',NOW,'BUY',
        D('4000'),D('4002'),D('3990'),(D('4005'),D('4008'),D('4010'))),**changes)


@pytest.mark.parametrize('value,utc',[
    ('2026-03-28T09:00:00','2026-03-28T09:00:00+00:00'),
    ('2026-03-29T09:00:00','2026-03-29T08:00:00+00:00'),
    ('2026-10-24T09:00:00','2026-10-24T08:00:00+00:00'),
    ('2026-10-25T09:00:00','2026-10-25T09:00:00+00:00')])
def test_corrected_london_dates(value,utc):
    assert timestamp(value,'uk_local').isoformat()==utc


def test_gap_overlap_and_resolved_offset():
    with pytest.raises(ValueError,match='NONEXISTENT'):
        timestamp('2026-03-29T01:30:00','uk_local')
    with pytest.raises(ValueError,match='AMBIGUOUS'):
        timestamp('2026-10-25T01:30:00','uk_local')
    assert timestamp('2026-10-25T01:30:00','uk_local',fold=0).hour==0
    assert timestamp('2026-10-25T01:30:00','uk_local',fold=1).hour==1
    assert timestamp('2026-10-25T01:30:00+01:00','uk_local').hour==0
    with pytest.raises(ValueError,match='UK_OFFSET_MISMATCH'):
        timestamp('2026-07-01T09:00:00+00:00','uk_local')
    with pytest.raises(ValueError,match='INVALID_FOLD'):
        timestamp('2026-10-25T01:30:00','uk_local',fold=True)


def test_api_utc_is_preserved_not_reinterpreted_or_offset_shifted():
    value=datetime(2026,7,1,9,tzinfo=UTC)
    assert timestamp(value,'telegram')==value
    assert timestamp('2026-07-01T09:00:00+00:00','telegram')==value
    with pytest.raises(ValueError,match='AWARE'):
        timestamp('2026-07-01T09:00:00','telegram')
    with pytest.raises(ValueError,match='CONVENTION'):
        timestamp(value,'broker')


def test_historical_offset_sample_is_bounded_and_cannot_be_applied_twice():
    sample=json.loads((FIX/'broker_clock_evidence.json').read_text())[0]
    raw=RawBrokerEpoch(D(str(sample['raw_time_msc']))/1000)
    expected=datetime(2026,11,9,7,8,13,840000,tzinfo=UTC)
    evidence=OffsetEvidence(7200,expected-timedelta(seconds=1),expected+timedelta(seconds=1))
    assert broker_epoch_to_utc(raw,evidence)==expected
    assert (datetime.fromtimestamp(sample['reference_utc_epoch'],UTC)-expected).total_seconds()==pytest.approx(.23375)
    with pytest.raises(ValueError,match='RAW_EPOCH'):
        broker_epoch_to_utc(expected,evidence)
    with pytest.raises(ValueError,match='UNVERIFIED'):
        broker_epoch_to_utc(RawBrokerEpoch(raw.seconds+D(86400)),evidence)


def test_hurricane_summer_fix_produces_utc_observation_intents(journal):
    result=HurricaneAdapter(journal).evaluate(message(),Q,NOW,SIZE)
    assert result.accepted and len(result.intents)==3
    for value in result.intents:
        assert value.created_at==datetime(2026,7,1,8,tzinfo=UTC)
        assert value.observation_only
        assert value.account_scope=='SIM-DEMO'
        assert value.quantity==D('.01')
    assert [v.take_profit for v in result.intents]==[D('4005'),D('4008'),D('4010')]


@pytest.mark.parametrize('change,quote,reason',[
    ({'published':'2026-07-01T08:58:00'},Q,'STALE_OR_FUTURE_MESSAGE'),
    ({'published':'2026-07-01T09:01:00'},Q,'STALE_OR_FUTURE_MESSAGE'),
    ({},replace(Q,at=NOW-timedelta(seconds=6)),'STALE_QUOTE'),
    ({},replace(Q,at=NOW+timedelta(seconds=1)),'STALE_QUOTE'),
    ({'published':'2026-10-25T01:30:00'},Q,'INVALID_TIMESTAMP_OR_LEVELS'),
    ({},replace(Q,bid=D('4005'),ask=D('4005.2')),'PAST_TP1'),
])
def test_hurricane_rejects_invalid_times_and_stale_prices(journal,change,quote,reason):
    result=HurricaneAdapter(journal).evaluate(message(**change),quote,NOW,SIZE)
    assert not result.intents and result.reason==reason


def test_duplicate_edit_and_restart_tombstones(tmp_path):
    path=tmp_path/'journal.sqlite'
    journal=ObservationJournal(path)
    adapter=HurricaneAdapter(journal)
    original=adapter.evaluate(message(),Q,NOW,SIZE)
    assert original.accepted
    assert not adapter.evaluate(message(),Q,NOW,SIZE).intents
    assert adapter.evaluate(message(event='EDIT'),Q,NOW,SIZE).invalidated
    journal.close()
    reopened=ObservationJournal(path)
    try:
        assert not HurricaneAdapter(reopened).evaluate(message(),Q,NOW,SIZE).intents
        # An edit arriving before its original also prevents later new-entry replay.
        adapter=HurricaneAdapter(reopened)
        adapter.evaluate(message(setup_key='other',event='EDIT'),Q,NOW,SIZE)
        assert not adapter.evaluate(message(setup_key='other'),Q,NOW,SIZE).intents
    finally:reopened.close()


def test_hurricane_connection_epoch_and_stale_delivery_cannot_freshen(journal):
    adapter=HurricaneAdapter(journal)
    assert adapter.evaluate(message(),Q,NOW,SIZE,connection_epoch=NOW-timedelta(seconds=2)).reason=='RECONNECT_BACKLOG'
    delayed=message(setup_key='late',published='2026-07-01T08:57:00',received=NOW)
    assert not adapter.evaluate(delayed,Q,NOW,SIZE).intents
    assert not adapter.evaluate(message(setup_key='late'),Q,NOW,SIZE).intents


def bars():
    return [Candle(r['time'],*(D(str(r[k])) for k in ('open','high','low','close'))) for r in synthetic_minutes()]


def test_t02_policy_has_no_sizing_and_first_setup_never_uses_next_open():
    candles=bars()
    assert first_setup(candles[:59],'2026-10-26') is None
    setup=first_setup(candles[:60],'2026-10-26')
    assert setup.side=='BUY' and setup.stop==D(97) and setup.ratio==D(1)/15
    assert not hasattr(setup,'quantity')
    assert first_setup(candles,'2026-10-26')==setup
    assert not hasattr(T02Policy.reference(),'risk_percent')


@pytest.mark.parametrize('day,hour', [('2026-03-07',1),('2026-03-08',0),('2026-10-25',0),('2026-11-01',1)])
def test_t02_ny_session_independent_of_london(day,hour):
    start=datetime.fromisoformat(day+'T20:00:00').replace(tzinfo=NY).astimezone(UTC)
    assert start.hour==hour and session_key(start)==day
    assert session_key(start+timedelta(hours=4)) is None


def test_sunday_is_rejected_in_both_named_policies():
    setup=first_setup(bars(),'2026-10-26')
    # Monday UTC but still Sunday New York.
    sunday=replace(setup,confirmed_at=datetime(2026,3,2,3,45,tzinfo=UTC))
    assert all(p.reason(sunday)=='WEEKDAY_FILTER' for p in (T02Policy.reference(),T02Policy.long_only()))
    explicitly_changed=replace(T02Policy.long_only(),strategy_id='sunday_experiment',weekdays=(6,))
    assert explicitly_changed.reason(sunday)=='OBSERVATION_ACCEPTED'


def test_t02_duplicate_boundary_missing_candles_and_restart(tmp_path):
    candles=bars()[:60];now=candles[-1].time+timedelta(minutes=1)
    quote=Quote(D(113),D(113),now)
    path=tmp_path/'t02.sqlite'
    journal=ObservationJournal(path)
    adapter=T02Adapter(journal,T02Policy.reference())
    result=adapter.evaluate(candles,quote,now,SIZE)
    assert result.accepted and result.intents[0].stop_loss==D(97)
    assert result.intents[0].take_profit==D(129)
    journal.close()
    journal=ObservationJournal(path)
    try:
        adapter=T02Adapter(journal,T02Policy.reference())
        assert adapter.evaluate(candles,quote,now,SIZE).reason=='DUPLICATE_OR_CONSUMED_SESSION'
        assert adapter.evaluate(candles[:10]+candles[11:],quote,now,SIZE).reason=='INCOMPLETE_SESSION_HISTORY'
        assert adapter.evaluate(candles+[candles[0]],quote,now,SIZE).reason=='INVALID_CANDLES'
    finally:journal.close()


def test_t02_stale_quote_bootstrap_and_direction_rejection_consume(journal):
    candles=bars()[:60];now=candles[-1].time+timedelta(minutes=1)
    quote=Quote(D(113),D(113),now-timedelta(seconds=6))
    adapter=T02Adapter(journal,T02Policy.reference())
    assert adapter.evaluate(candles,quote,now,SIZE).reason=='STALE_QUOTE'
    assert not adapter.evaluate(candles,replace(quote,at=now),now,SIZE).accepted
    # Independently named long-only policy consumes a rejected first short.
    setup=replace(first_setup(candles,'2026-10-26'),side='SELL')
    assert T02Policy.long_only().reason(setup)=='DIRECTION_FILTER'


def test_t02_historical_setup_is_not_a_new_current_entry(journal):
    candles=bars()[:61];now=candles[-1].time+timedelta(minutes=1)
    quote=Quote(D(113),D(113),now)
    assert T02Adapter(journal,T02Policy.reference()).evaluate(candles,quote,now,SIZE).reason=='HISTORICAL_SETUP_NO_CATCHUP'


def test_intents_cannot_be_dispatched_by_central_engine(journal,tmp_path):
    result=HurricaneAdapter(journal).evaluate(message(),Q,NOW,SIZE)
    broker=FakeBroker();engine=Engine(tmp_path/'engine.sqlite',broker)
    try:
        engine.reconcile(NOW)
        for proposal in result.intents:
            decision,command=engine.enqueue(proposal,Q,NOW)
            assert decision.reason_code=='OBSERVATION_ONLY' and command is None
        assert broker.calls==0
        assert engine.db.execute('SELECT COUNT(*) FROM commands').fetchone()[0]==0
    finally:engine.close();broker.close()


@pytest.fixture(scope='module')
def research():return comparison()


def test_new_reference_matches_64_saved_and_long_only_is_30(research):
    assert research['alignment']['t02_reference']=={'matching_saved':64,'missing_saved':0,'extra':0,'sunday_candidates_emitted':0}
    assert research['alignment']['t02_long_only']=={'matching_saved':30,'missing_saved':34,'extra':0,'sunday_candidates_emitted':0}
    assert not research['reference_saved_exit_fact_mismatches']
    assert not research['reference_saved_quantity_pnl_mismatches']


def test_independently_replayed_metrics_are_not_filtered_saved_pnl(research):
    ref,long=research['reference'],research['long_only']
    assert (ref['trade_count'],ref['wins'],ref['losses'],ref['breakeven'])==(64,37,17,10)
    assert (long['trade_count'],long['wins'],long['losses'],long['breakeven'])==(30,15,10,5)
    assert ref['net_pnl']==pytest.approx(2181.173755)
    assert long['net_pnl']==pytest.approx(499.595129)
    assert ref['profit_factor']==pytest.approx(2.13816121)
    assert long['profit_factor']==pytest.approx(1.48361796)
    assert ref['max_drawdown_percent']==pytest.approx(3.80270809)
    assert long['max_drawdown_percent']==pytest.approx(3.56347403)


def test_stop_first_and_entry_bar_protection():
    pos={'sign':1,'stop':90,'target':110}
    bar=type('Bar',(),dict(open=100,high=111,low=89,close=105))()
    assert protective_exit(pos,bar)==(90,'STOP')
    bar.open=85
    assert protective_exit(pos,bar)==(85,'STOP_GAP')


def test_research_breakeven_only_applies_on_next_m1_open():
    from signal_adapters.t02 import Setup
    at=datetime(2026,3,10,1,tzinfo=UTC)
    setup=Setup('2026-03-09','BUY',at,at-timedelta(minutes=15),D(99),D(100),D(90),D(100),D('.1'),True)
    frame=pd.DataFrame([{'timestamp':at,'open':100,'high':107,'low':95,'close':105},
        {'timestamp':at+timedelta(minutes=1),'open':102,'high':104,'low':99,'close':101}])
    result=performance(frame,{at:setup},T02Policy.reference())
    assert len(result['trades'])==1 and result['trades'][0]['exit']==100
    assert result['trades'][0]['exit_time']==(at+timedelta(minutes=1)).isoformat()
    assert result['trades'][0]['net_pnl']==0


def test_common_costs_change_both_models_consistently():
    at=datetime(2026,3,10,1,tzinfo=UTC)
    from signal_adapters.t02 import Setup
    setup=Setup('2026-03-09','BUY',at,at-timedelta(minutes=15),D(99),D(100),D(90),D(100),D('.1'),True)
    frame=pd.DataFrame([{'timestamp':at,'open':100,'high':111,'low':99,'close':110}])
    free=performance(frame,{at:setup},T02Policy.reference())
    cost=performance(frame,{at:setup},T02Policy.reference(),ResearchConfig(commission_per_side=1))
    assert cost['net_pnl']==pytest.approx(free['net_pnl']-2)


def test_full_adapter_paths_match_saved_reference_and_sunday_filters(tmp_path):
    from audit_phase26.compare import frames,session_candidates
    frame=frames();candidates=session_candidates(frame)
    saved=json.loads((FIX/'ledger_trades.json').read_text())
    expected={(pd.Timestamp(r['metadata.ifvg_confirmation_time']).to_pydatetime(),'BUY' if r['direction']=='long' else 'SELL') for r in saved}
    emitted={p.strategy_id:set() for p in (T02Policy.reference(),T02Policy.long_only())}
    blocked_sunday_long=0
    journal=ObservationJournal(tmp_path/'full-adapter.sqlite')
    try:
        for at,setup in candidates.items():
            start=datetime.combine(datetime.fromisoformat(setup.session).date(),datetime.min.time().replace(hour=19,minute=15),NY).astimezone(UTC)
            subset=frame[(frame.timestamp>=start)&(frame.timestamp<at)]
            candles=[Candle(r.timestamp.to_pydatetime(),*(D(str(x)) for x in (r.open,r.high,r.low,r.close))) for r in subset.itertuples(index=False)]
            next_open=D(str(frame.loc[frame.timestamp==at,'open'].iloc[0]))
            quote=Quote(next_open,next_open,at)
            for policy in (T02Policy.reference(),T02Policy.long_only()):
                result=T02Adapter(journal,policy).evaluate(candles,quote,at,SIZE)
                if result.accepted:
                    proposal=result.intents[0]
                    assert proposal.created_at==at and proposal.entry_price==next_open
                    assert proposal.observation_only and proposal.quantity==SIZE.quantity_per_leg
                    emitted[policy.strategy_id].add((at,proposal.side))
                if setup.accepted and setup.side=='BUY' and at.astimezone(NY).weekday()==6 and policy.strategy_id=='t02_reference':
                    assert result.reason=='WEEKDAY_FILTER'
                    blocked_sunday_long+=1
        assert emitted['t02_reference']==expected
        assert emitted['t02_long_only']=={key for key in expected if key[1]=='BUY'}
        assert blocked_sunday_long==7
    finally:journal.close()


def test_t02_stale_candle_never_backfills_on_retry(journal):
    candles=bars()[:60];at=candles[-1].time+timedelta(minutes=1)
    adapter=T02Adapter(journal,T02Policy.reference())
    quote=Quote(D(113),D(113),at+timedelta(seconds=20))
    assert adapter.evaluate(candles,quote,at+timedelta(seconds=20),SIZE).reason=='STALE_CANDLE'
    assert not adapter.evaluate(candles,replace(quote,at=at),at,SIZE).accepted


def test_hurricane_concurrent_duplicate_deliveries_emit_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/'shared.sqlite'
    journal=ObservationJournal(path);journal.close()
    def observe(index):
        local=ObservationJournal(path)
        try:return HurricaneAdapter(local).evaluate(message(),Q,NOW,SIZE).accepted
        finally:local.close()
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(observe,range(2)))==1


def test_journal_lock_failure_never_emits_proposal(tmp_path):
    import sqlite3
    path=tmp_path/'blocked.sqlite'
    journal=ObservationJournal(path)
    lock=sqlite3.connect(path)
    lock.execute('BEGIN IMMEDIATE')
    journal.db.execute('PRAGMA busy_timeout=1')
    try:
        with pytest.raises(sqlite3.OperationalError):
            HurricaneAdapter(journal).evaluate(message(),Q,NOW,SIZE)
        assert journal.db.execute('SELECT COUNT(*) FROM adapter_decisions').fetchone()[0]==0
    finally:lock.rollback();lock.close();journal.close()


def test_rewrapping_normalized_epoch_still_does_not_establish_interval():
    expected=datetime(2026,11,9,7,8,13,840000,tzinfo=UTC)
    evidence=OffsetEvidence(7200,expected-timedelta(seconds=1),expected+timedelta(seconds=1))
    with pytest.raises(ValueError,match='UNVERIFIED'):
        broker_epoch_to_utc(RawBrokerEpoch(D(str(expected.timestamp()))),evidence)


def test_observation_flag_survives_durable_payload_and_dispatch_revalidation(tmp_path):
    from dataclasses import asdict
    broker=FakeBroker();engine=Engine(tmp_path/'engine.sqlite',broker)
    at=datetime(2026,11,9,9,tzinfo=UTC)
    quote=Quote(D(100),D('100.2'),at)
    normal=TradeIntent('test','source','test','setup','1','SIM-DEMO','SIM-XAU','BUY',
        D('.01'),D('100.2'),D(99),at,at+timedelta(seconds=30))
    try:
        engine.reconcile(at)
        _,command=engine.enqueue(normal,quote,at)
        payload=asdict(replace(normal,observation_only=True))
        engine.db.execute('UPDATE commands SET payload=? WHERE id=?',(json.dumps(payload,default=str),command.command_id))
        engine.dispatch(command.command_id,quote,at)
        assert broker.calls==0
        assert engine.db.execute('SELECT state FROM commands WHERE id=?',(command.command_id,)).fetchone()[0]=='REJECTED'
    finally:engine.close();broker.close()
