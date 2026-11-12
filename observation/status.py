"""Consistent read-only projection of replay state. No credentials or raw source."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote


def snapshot(path,now=None):
    now=now or datetime.now(timezone.utc)
    with sqlite3.connect('file:'+quote(str(Path(path).resolve()),safe='/')+'?mode=ro',uri=True,timeout=1) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        row=db.execute('SELECT data FROM worker_meta').fetchone()
        if not row:return {'mode':'OFFLINE_REPLAY','status':'NOT_STARTED'}
        meta=json.loads(row[0])
        age=(now-datetime.fromisoformat(meta['heartbeat'])).total_seconds()
        received=meta.get('last_received_at')
        input_age=(now-datetime.fromisoformat(received)).total_seconds() if received else None
        policies=meta['policies']
        decisions=[]
        for row in db.execute('SELECT strategy,at,reason,setup,intents FROM worker_decisions ORDER BY rowid DESC LIMIT 60'):
            d=dict(row);d['detected_setup']=json.loads(d.pop('setup'));d['intent_count']=len(json.loads(d.pop('intents')))
            decisions.append(d)
        def book(table):return [json.loads(r[0]) for r in db.execute('SELECT data FROM '+table+' ORDER BY rowid DESC LIMIT 100')]
        reconciliation=not db.execute("SELECT 1 FROM paper_orders o LEFT JOIN paper_positions p ON o.id=p.id WHERE json_extract(o.data,'$.status')='FILLED' AND p.id IS NULL LIMIT 1").fetchone()
        return {'mode':'OFFLINE_REPLAY','real_execution_enabled':False,'status':meta['status'],
            'heartbeat':meta['heartbeat'],'heartbeat_age_seconds':age,'heartbeat_fresh':0<=age<=5,
            'fresh_input':bool(meta['fresh_input'] and input_age is not None and 0<=input_age<=5),
            'last_signal':meta['last_signal'],'last_quote':meta['last_quote'],'replay_market_time':meta['last_event'],
            'cursor':meta['cursor'],'configurations':policies,'hurricane':'WAITING_FOR_STRUCTURED_REPLAY_MESSAGES',
            'recent_decisions':decisions,'recent_rejections':[d for d in decisions if d['reason'] not in ('OBSERVATION_ACCEPTED','NO_SETUP','NO_CONFIRMATION_BOUNDARY','OUTSIDE_SESSION')],
            'paper_orders':book('paper_orders'),'paper_positions':book('paper_positions'),
            'reconciliation':'CONSISTENT_LOCAL_PAPER_BOOK' if reconciliation else 'ATTENTION_REQUIRED',
            'errors':[dict(r) for r in db.execute('SELECT at,code FROM worker_errors ORDER BY seq DESC LIMIT 20')],
            'last_error':meta['last_error']}
