"""Phase 2.8 tests use fabricated account data and offline clocks only."""
import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import pytest

from test_ctrader import rig,private_dir,ACCESS,SECRET,ACCOUNT,SYMBOL
from ctrader_readonly.config import SafetyError
from ctrader_readonly.specifications import numeric_fields,volumes,hypothetical_margin,inspect
from ctrader_readonly.reliability import MonitorStore,projection
from ctrader_readonly.protocol import validate_frame,RateLimiter
from ctrader_readonly.oauth import Token


class Clock:
    def __init__(self):self.now=1000.
    def wall(self):return self.now+100000.
    def mono(self):return self.now
    def advance(self,seconds):self.now+=seconds


def status(**changes):
    data=dict(health='HEALTHY',connected=True,view_authorized=True,heartbeat_fresh=True,
        quote_fresh=True,snapshot_fresh=True,history_complete=True,last_error=None,
        metrics={'connect_attempts':1,'connections':1,'requests':10,'disconnects':0},
        quote={'server_age_seconds':.2},rate_limiter={'wait_count':0},token_seconds_remaining=1000)
    data.update(changes);return data


def test_exact_volume_presence_scaling_and_lots():
    symbol={'minVolume':99999999999900,'maxVolume':'99999999999900','stepVolume':1,'lotSize':10000}
    fields=numeric_fields(symbol);converted=volumes(symbol)
    assert fields['minVolume']=={'present':True,'json_type':'int','raw_integer':'99999999999900'}
    assert fields['maxVolume']['json_type']=='str'
    assert fields['slDistance']['present'] is False
    assert converted['minVolume']['units']=='999999999999'
    assert converted['minVolume']['lots']=='9999999999.99'
    assert converted['stepVolume']['units']=='0.01'
    assert converted['stepVolume']['lots']=='0.0001'
    assert converted['lotSize']['units']=='100'


@pytest.mark.parametrize('value',[None,True,1.0,'1.0',-1,0])
def test_invalid_volume_has_no_fallback(value):
    with pytest.raises(SafetyError):volumes(dict(minVolume=value,maxVolume=10000,stepVolume=1,lotSize=10000))


def test_hypothetical_margin_requires_every_volume_and_precision(rig):
    _,_,t,reader,token=rig;reader.authorize(token)
    with pytest.raises(SafetyError,match='VOLUME_MISMATCH'):hypothetical_margin(reader,SYMBOL,10000)
    # Missing moneyDigits is never silently assumed to mean cents.
    prior=t.change
    def change(name,response):
        if name=='margin':response.pop('moneyDigits',None)
        return prior(name,response)
    t.change=change
    with pytest.raises(SafetyError):hypothetical_margin(reader,SYMBOL,10000)


def test_probe_only_exports_selected_numeric_symbol_fields(rig):
    _,_,t,reader,token=rig
    prior=t.response
    def response(name,payload):
        result=prior(name,payload)
        if name=='margin':result['margin']=[{'volume':v,'buyMargin':v*2,'sellMargin':v*3} for v in payload['volume']]
        if name=='symbol':result['symbol'][0]['private_note']=SECRET+ACCESS
        return result
    t.response=response
    report=inspect(reader,token)
    encoded=json.dumps(report)
    assert SECRET not in encoded and ACCESS not in encoded and str(ACCOUNT) not in encoded
    assert not report['execution_enabled'] and report['execution_readiness']=='NO_GO'
    assert not report['volume_independently_verified']
    assert report['hypothetical_margin'][0]['lots']=='0.01'
    assert report['hypothetical_margin'][0]['buy_margin']=='2'
    assert report['spread_usd_per_unit']=='0.2'
    assert not report['gbp_conversion']['verified']
    assert not {2106,2108,2109,2110,2111}&{f['payloadType'] for _,f in t.sent}


def test_monitor_restart_duplicate_owner_gaps_and_completion(tmp_path):
    clock=Clock();path=tmp_path/'monitor.sqlite'
    store=MonitorStore(path,20,clock.wall,clock.mono)
    with pytest.raises(SafetyError,match='ALREADY_RUNNING'):MonitorStore(path,20,clock.wall,clock.mono)
    clock.advance(5);store.sample(status())
    clock.advance(5);store.sample(status(quote_fresh=False,metrics={'requests':20,'disconnects':1},last_error='STALE_QUOTE'))
    assert store.summary()['recorded_active_seconds']==10
    assert not store.summary()['target_duration_observed']
    store.close();clock.advance(100)
    store=MonitorStore(path,20,clock.wall,clock.mono)
    clock.advance(5);store.sample(status(metrics={'requests':2,'disconnects':0}))
    summary=store.summary()
    assert summary['unobserved_gap_seconds']==100
    assert summary['recorded_active_seconds']==15
    assert summary['counter_totals']['requests']==22
    assert summary['sampled_false_periods']['quote_fresh']==1
    assert summary['errors']=={'STALE_QUOTE':1}
    clock.advance(5);store.sample(status());assert store.summary()['target_duration_observed']
    store.close('COMPLETED')
    with pytest.raises(SafetyError,match='TARGET_CHANGED'):MonitorStore(path,21,clock.wall,clock.mono)


def test_monitor_suspend_and_clock_regression_do_not_claim_coverage(tmp_path):
    c=Clock();store=MonitorStore(tmp_path/'suspend.sqlite',86400,c.wall,c.mono)
    c.advance(10000);store.sample(status())
    assert store.summary()['recorded_active_seconds']==10
    assert store.summary()['unobserved_gap_seconds']==9990
    assert not store.summary()['target_duration_observed']
    c.advance(-1)
    with pytest.raises(SafetyError,match='CLOCK_REGRESSION'):store.sample(status())
    store.close()


def test_monitor_locked_database_does_not_advance_duration(tmp_path):
    c=Clock();store=MonitorStore(tmp_path/'lock.sqlite',100,c.wall,c.mono)
    store.db.execute('PRAGMA busy_timeout=1')
    other=sqlite3.connect(store.path);other.execute('BEGIN IMMEDIATE')
    c.advance(5)
    with pytest.raises(sqlite3.OperationalError):store.sample(status())
    other.rollback();other.close()
    assert store.summary()['recorded_active_seconds']==0
    store.sample(status());assert store.summary()['recorded_active_seconds']==5
    store.close()


def test_monitor_projection_never_copies_raw_error_or_extra_fields(tmp_path):
    c=Clock();store=MonitorStore(tmp_path/'safe.sqlite',100,c.wall,c.mono)
    unsafe=status(last_error=SECRET+ACCESS,account={'ctidTraderAccountId':ACCOUNT,'token':ACCESS},
        metrics={'requests':1,'private_note':ACCESS},quote={'server_age_seconds':float('nan')})
    store.sample(unsafe,{'rss_mib':25,'secret':ACCESS})
    data=store.db.execute('SELECT data FROM monitor_samples').fetchone()[0]
    assert SECRET not in data and ACCESS not in data and str(ACCOUNT) not in data
    assert json.loads(data)['last_error']=='OTHER_ERROR'
    assert json.loads(data)['quote_age_seconds'] is None
    store.close()


def test_reader_telemetry_records_rate_rejection_and_recovery(rig):
    _,_,t,reader,token=rig
    reader.authorize(token);reader.refresh()
    before=reader.status()['metrics']
    assert before['snapshots']==1 and before['connect_attempts']==1
    t.inject=lambda *_:{'payloadType':2142,'payload':{'errorCode':'REQUEST_FREQUENCY_EXCEEDED','description':SECRET}}
    with pytest.raises(SafetyError):reader._read('orders',fromTimestamp=0,toTimestamp=100)
    counters=reader.status()['metrics']
    assert counters['rate_limit_errors']==1 and counters['api_errors']==1 and counters['history_errors']==1
    assert counters['disconnects']==1
    t.inject=None;reader.authorize(token);reader.refresh()
    assert reader.status()['metrics']['recoveries']==1
    assert not reader.status()['execution_enabled']
    assert not reader.status()['volume_independently_verified']
    assert SECRET not in json.dumps(reader.status())


def test_limiter_metrics_keep_requests_within_windows(rig):
    c=rig[1];limiter=RateLimiter(c.monotonic,c.sleep)
    for _ in range(25):limiter.acquire(True)
    assert limiter.peak_requests_per_second<=10
    assert limiter.peak_history_per_second<=2
    assert limiter.wait_count>0 and limiter.wait_seconds>0


def test_dynamic_leverage_read_schema_has_no_trade_escape():
    validate_frame({'clientMsgId':'synthetic','payloadType':2177,'payload':{'ctidTraderAccountId':123,'leverageId':456}})
    with pytest.raises(SafetyError):validate_frame({'clientMsgId':'synthetic','payloadType':2177,'payload':{'ctidTraderAccountId':123,'leverageId':456,'volume':100}})


def test_refresh_token_is_discarded_and_expiry_needs_reauthorization():
    token=Token.from_response({'accessToken':ACCESS,'tokenType':'bearer','expiresIn':100,'refreshToken':SECRET,'scope':'accounts'},now=1000)
    assert not hasattr(token,'refresh_token') and SECRET not in repr(token)
    with pytest.raises(SafetyError,match='EXPIRED_REAUTHORIZE'):token.require_current(now=1071)


def test_expired_token_run_reports_fixed_reauthorization_reason_without_network(rig):
    _,_,t,reader,token=rig
    class Stop:
        done=False
        def is_set(self):return self.done
        def wait(self,_):self.done=True
    def expired():raise SafetyError('TOKEN_EXPIRED_REAUTHORIZE')
    reader.run(expired,Stop(),jitter=lambda *_:0)
    assert not t.sent
    # Final STOPPED is deliberate; record the transient expiry reason in wait.
    seen=[]
    class ObserveStop(Stop):
        done=False
        def wait(self,_):seen.append(reader.status()['last_error']);self.done=True
    reader.run(expired,ObserveStop(),jitter=lambda *_:0)
    assert seen==['TOKEN_EXPIRED_REAUTHORIZE']


def test_monitor_unclean_restart_retains_committed_rows(tmp_path):
    c=Clock();path=tmp_path/'crash.sqlite'
    store=MonitorStore(path,100,c.wall,c.mono)
    c.advance(5);store.sample(status())
    # Model process death: no clean stop metadata, OS releases descriptor/lock.
    store.db.close();store.owner.close()
    c.advance(10)
    resumed=MonitorStore(path,100,c.wall,c.mono)
    assert resumed.summary()['recorded_active_seconds']==5
    assert resumed.summary()['unclean_or_active_runs']==2
    assert resumed.summary()['unobserved_gap_seconds']==10
    resumed.close()


def test_margin_crosscheck_is_indicative_and_does_not_approve_volume():
    from ctrader_readonly.specifications import margin_crosscheck
    report={'gbp_conversion':{'gbp_per_usd_upper':'0.8'},'xauusd_quote':{'ask':'4000'},
        'hypothetical_margin':[{'lots':'0.01','units':'1','buy_margin':'160'}]}
    row=margin_crosscheck(report)['hypothetical_rows'][0]
    assert row['indicative_notional_gbp']=='3200.0'
    assert Decimal(row['indicative_margin_at_20'])==160
    assert Decimal(row['implied_leverage_from_sampled_quote'])==20
    assert 'volume_independently_verified' not in report
