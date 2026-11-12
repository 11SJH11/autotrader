"""Restartable read-only broker monitoring. SQLite contains sanitized telemetry only."""
import argparse
from datetime import datetime,timezone
import fcntl
import json
import math
import os
from pathlib import Path
import resource
import signal
import sqlite3
import threading
import time
from uuid import uuid4

from .client import Reader
from .config import Config,SafetyError,REPOSITORY
from .oauth import Token

FLAGS=('connected','view_authorized','heartbeat_fresh','quote_fresh','snapshot_fresh','history_complete')
ERRORS=frozenset(('NETWORK_CONNECT_FAILED','NETWORK_RECEIVE_FAILED','NETWORK_SEND_FAILED',
    'REQUEST_TIMEOUT','HEARTBEAT_TIMEOUT','BROKER_REQUEST_REJECTED','BROKER_SESSION_INVALIDATED',
    'STALE_QUOTE','OUT_OF_ORDER_QUOTE','CROSSED_QUOTE','SNAPSHOT_CHANGED_RETRY','HISTORY_INCOMPLETE',
    'HISTORY_COMPLETENESS_UNKNOWN','TOKEN_EXPIRED_REAUTHORIZE','LOCAL_AUTHORIZATION_UNAVAILABLE',
    'CONNECTING','STOPPED','RESPONSE_ACCOUNT_MISMATCH','BROKER_IDENTITY_MISMATCH',
    'VIEW_SCOPE_REQUIRED','ACCOUNT_CURRENCY_UNKNOWN','UNEXPECTED_RESPONSE_SCHEMA'))


def utc(wall):return datetime.fromtimestamp(wall,timezone.utc).isoformat()


def projection(status):
    """Finite labels/counters only. Never copy arbitrary error or account text."""
    result={key:status.get(key) is True for key in FLAGS}
    result['health']=status.get('health') if status.get('health') in ('HEALTHY','DEGRADED','DISCONNECTED') else 'UNKNOWN'
    error=status.get('last_error')
    result['last_error']=error if error in ERRORS else ('OTHER_ERROR' if error is not None else None)
    for key in ('metrics','rate_limiter'):
        result[key]={k:v for k,v in status.get(key,{}).items()
            if k in (('connect_attempts','connections','authorizations','disconnects','recoveries','requests',
                'history_requests','heartbeats_sent','heartbeats_received','quote_events','api_errors',
                'rate_limit_errors','request_timeouts','history_errors','stale_quote_errors','snapshots')
                if key=='metrics' else ('wait_count','wait_seconds','peak_requests_per_second','peak_history_per_second'))
            and type(v) in (int,float) and math.isfinite(v) and v>=0}
    q=status.get('quote')
    age=q.get('server_age_seconds') if isinstance(q,dict) else None
    result['quote_age_seconds']=age if type(age) in (int,float) and math.isfinite(age) else None
    remaining=status.get('token_seconds_remaining')
    result['token_seconds_remaining']=remaining if type(remaining) is int and remaining>=0 else None
    result['execution_enabled']=False
    return result


class MonitorStore:
    """One writer, atomic samples, accumulated active time; wall gaps uncredited."""
    def __init__(self,path,target_seconds=86400,wall=time.time,mono=time.monotonic,max_interval=10):
        self.wall,self.mono=wall,mono
        self.max_interval=max_interval
        self.path=Path(path).resolve()
        if REPOSITORY not in self.path.parents:raise SafetyError('MONITOR_DATABASE_INSIDE_REPOSITORY_REQUIRED')
        if not math.isfinite(target_seconds) or target_seconds<=0:raise SafetyError('INVALID_MONITOR_TARGET')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.owner=open(str(self.path)+'.owner','a')
        try:fcntl.flock(self.owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:self.owner.close();raise SafetyError('MONITOR_ALREADY_RUNNING') from None
        try:
            self.db=sqlite3.connect(self.path,timeout=2,isolation_level=None)
            self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''CREATE TABLE IF NOT EXISTS monitor_meta (id INTEGER PRIMARY KEY CHECK(id=1), target REAL, active REAL);
                CREATE TABLE IF NOT EXISTS monitor_runs (id TEXT PRIMARY KEY, start_wall REAL, last_wall REAL, stop_wall REAL, final_state TEXT);
                CREATE TABLE IF NOT EXISTS monitor_samples (seq INTEGER PRIMARY KEY, run TEXT, wall REAL, interval REAL, unobserved REAL, data TEXT);''')
            self.db.execute('INSERT OR IGNORE INTO monitor_meta VALUES(1,?,0)',(target_seconds,))
            if self.db.execute('SELECT target FROM monitor_meta').fetchone()[0]!=target_seconds:
                raise SafetyError('MONITOR_TARGET_CHANGED')
            self.run=uuid4().hex;now=wall()
            self.db.execute('INSERT INTO monitor_runs VALUES(?,?,?,NULL,NULL)',(self.run,now,now))
            self.last=mono();self.last_wall=now;self.closed=False
        except Exception:
            if hasattr(self,'db'):self.db.close()
            self.owner.close();raise

    def sample(self,status,resources=None):
        now=self.mono();elapsed=now-self.last
        if elapsed<0:raise SafetyError('MONITOR_CLOCK_REGRESSION')
        # A suspended/hung collector cannot credit an arbitrarily large gap.
        credited=min(elapsed,self.max_interval)
        data=projection(status)
        if resources:
            data['resources']={k:v for k,v in resources.items() if k in ('rss_mib','max_rss_mib','cpu_seconds','cpu_percent_one_core')
                and type(v) in (int,float) and math.isfinite(v) and v>=0}
        wall=self.wall()
        unobserved=elapsed-credited+max(0,wall-self.last_wall-elapsed)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT INTO monitor_samples(run,wall,interval,unobserved,data) VALUES(?,?,?,?,?)',
                (self.run,wall,credited,unobserved,json.dumps(data,allow_nan=False)))
            self.db.execute('UPDATE monitor_meta SET active=active+? WHERE id=1',(credited,))
            self.db.execute('UPDATE monitor_runs SET last_wall=? WHERE id=?',(wall,self.run))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise
        self.last=now;self.last_wall=wall

    def summary(self):return summarize(self.db)

    def close(self,state='STOPPED'):
        if self.closed:return
        self.db.execute('UPDATE monitor_runs SET stop_wall=?,final_state=? WHERE id=?',(self.wall(),state,self.run))
        self.db.close();self.owner.close();self.closed=True


def summarize(db):
    owns=not db.in_transaction
    if owns:db.execute('BEGIN')
    try:
        result=_summarize(db)
        if owns:db.execute('COMMIT')
        return result
    except Exception:
        if owns:db.execute('ROLLBACK')
        raise


def _summarize(db):
    target,active=db.execute('SELECT target,active FROM monitor_meta WHERE id=1').fetchone()
    result={'mode':'DEMO_READ_ONLY','execution_enabled':False,'target_seconds':target,
        'recorded_active_seconds':active,'target_duration_observed':active>=target,
        'samples':0,'sampled_seconds':{k:0.0 for k in FLAGS},'sampled_false_periods':{k:0 for k in FLAGS},
        'unobserved_gap_seconds':0.0,'runs':0,'planned_stop_runs':0,'unclean_or_active_runs':0,'counter_totals':{},'errors':{},
        'resource_maxima':{},'rate_limiter_maxima':{},'last_sample_utc':None}
    previous={};last_run=None;prior_wall=None;last_counters={}
    runs=db.execute('SELECT id,start_wall,last_wall,stop_wall,final_state FROM monitor_runs ORDER BY start_wall').fetchall()
    for _,start,last,stop,state in runs:
        result['runs']+=1
        if state in ('STOPPED','COMPLETED'):result['planned_stop_runs']+=1
        if stop is None:result['unclean_or_active_runs']+=1
        if prior_wall is not None:result['unobserved_gap_seconds']+=max(0,start-prior_wall)
        prior_wall=stop if stop is not None else last
    for run,wall,interval,unobserved,payload in db.execute('SELECT run,wall,interval,unobserved,data FROM monitor_samples ORDER BY seq'):
        result['unobserved_gap_seconds']+=unobserved
        data=json.loads(payload);result['samples']+=1;result['last_sample_utc']=utc(wall)
        if run!=last_run:previous={};last_counters={};last_run=run
        for key in FLAGS:
            # Right-endpoint sampled estimate, not continuous interval proof.
            if data[key]:result['sampled_seconds'][key]+=interval
            if not data[key] and previous.get(key,True):result['sampled_false_periods'][key]+=1
            previous[key]=data[key]
        for key,value in data['metrics'].items():
            result['counter_totals'][key]=result['counter_totals'].get(key,0)+max(0,value-last_counters.get(key,0))
            last_counters[key]=value
        error=data['last_error']
        if error:result['errors'][error]=result['errors'].get(error,0)+1
        for key,value in data.get('resources',{}).items():result['resource_maxima'][key]=max(value,result['resource_maxima'].get(key,0))
        for key,value in data.get('rate_limiter',{}).items():result['rate_limiter_maxima'][key]=max(value,result['rate_limiter_maxima'].get(key,0))
    result['sample_interval_semantics']='right-endpoint estimates; short outages between samples may be missed; counters track reader events'
    return result


def resource_sample(previous_cpu,interval):
    usage=resource.getrusage(resource.RUSAGE_SELF);cpu=usage.ru_utime+usage.ru_stime
    rss=int(Path('/proc/self/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')/1048576
    return {'rss_mib':rss,'max_rss_mib':usage.ru_maxrss/1024,'cpu_seconds':cpu,
        'cpu_percent_one_core':100*max(0,cpu-previous_cpu)/interval if interval>=.1 else 0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--config')
    parser.add_argument('--target-hours',type=float,default=24);parser.add_argument('--interval',type=float,default=5)
    parser.add_argument('--summary',action='store_true')
    args=parser.parse_args()
    if args.summary:
        try:
            with sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as db:
                print(json.dumps(summarize(db),indent=2,allow_nan=False))
            return 0
        except Exception:print('MONITOR_SUMMARY_UNAVAILABLE');return 1
    if not args.config or not 1<=args.interval<=60:parser.error('config and interval 1..60 required')
    stop=threading.Event();reader=None;store=None;thread=None
    def shutdown(*_):stop.set()
    signal.signal(signal.SIGINT,shutdown);signal.signal(signal.SIGTERM,shutdown)
    final='STOPPED'
    try:
        config=Config.load(args.config);config.require_approved()
        store=MonitorStore(args.database,args.target_hours*3600,max_interval=2*args.interval)
        # No new process is needed if this persisted campaign already completed.
        if store.summary()['target_duration_observed']:final='COMPLETED';return 0
        reader=Reader(config)
        thread=threading.Thread(target=reader.run,args=(lambda:Token.load(config.token_path),stop),daemon=True)
        thread.start();previous_cpu=0;previous=time.monotonic()
        print('DEMO_READ_ONLY_MONITOR_RUNNING; EXECUTION_NO_GO',flush=True)
        while not stop.is_set():
            now=time.monotonic();resources=resource_sample(previous_cpu,now-previous)
            previous_cpu=resources['cpu_seconds'];previous=now
            store.sample(reader.status(),resources)
            # Query O(1) rather than replaying the accumulated samples each tick.
            target,active=store.db.execute('SELECT target,active FROM monitor_meta').fetchone()
            if active>=target:final='COMPLETED';break
            stop.wait(args.interval)
    except Exception:
        final='MONITOR_FAILED';print('MONITOR_FAILED; inspect sanitized persisted summary',flush=True);return 1
    finally:
        stop.set()
        if thread:thread.join(12)
        if reader:reader.disconnect('STOPPED')
        if store:store.close(final)
    return 0

if __name__=='__main__':raise SystemExit(main())
