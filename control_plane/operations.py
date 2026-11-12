"""Local observation controls. No process/service control or broker mutations."""
import argparse
from datetime import datetime,timezone
from decimal import Decimal
from html import escape
from http.server import BaseHTTPRequestHandler,HTTPServer
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import parse_qs
from urllib.request import build_opener,ProxyHandler,HTTPRedirectHandler

from .storage import open_database
from ctrader_readonly.config import SafetyError

STRATEGIES=('hurricane_observation','t02_reference','t02_long_only')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class OpsStore:
    def __init__(self,path):
        self.db=open_database(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS native_controls(strategy TEXT PRIMARY KEY,state TEXT);
            CREATE TABLE IF NOT EXISTS native_switch(id INTEGER PRIMARY KEY,suspended INTEGER);
            CREATE TABLE IF NOT EXISTS native_sizing(strategy TEXT PRIMARY KEY,lots TEXT);
            CREATE TABLE IF NOT EXISTS ops_commands(key TEXT PRIMARY KEY,operation TEXT,strategy TEXT,value TEXT);
            CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,at TEXT,command_id TEXT,kind TEXT,detail TEXT);
            INSERT OR IGNORE INTO native_switch VALUES(1,1);
        ''')
        for strategy in STRATEGIES:
            self.db.execute("INSERT OR IGNORE INTO native_controls VALUES(?,'STOPPED')",(strategy,))
            self.db.execute("INSERT OR IGNORE INTO native_sizing VALUES(?,'0.01')",(strategy,))

    def close(self):self.db.close()

    def command(self,key,operation,strategy='',value=''):
        if not isinstance(key,str) or not 1<=len(key)<=100:raise SafetyError('INVALID_CONTROL_KEY')
        if operation not in ('START','STOP','PAUSE','RESUME','SUSPEND','CLEAR_SUSPENSION','SIZE'):raise SafetyError('INVALID_CONTROL')
        if operation not in ('SUSPEND','CLEAR_SUSPENSION') and strategy not in STRATEGIES:raise SafetyError('INVALID_STRATEGY')
        if operation=='SIZE':
            try:lots=Decimal(value)
            except Exception:raise SafetyError('INVALID_OBSERVATION_LOTS') from None
            if not lots.is_finite() or not Decimal('.01')<=lots<=50:raise SafetyError('INVALID_OBSERVATION_LOTS')
            value=str(lots)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior=self.db.execute('SELECT operation,strategy,value FROM ops_commands WHERE key=?',(key,)).fetchone()
            if prior:
                if tuple(prior)!=(operation,strategy,value):raise SafetyError('CONTROL_KEY_CONFLICT')
                self.db.rollback();return 'DUPLICATE'
            self.db.execute('INSERT INTO ops_commands VALUES(?,?,?,?)',(key,operation,strategy,value))
            if operation in ('SUSPEND','CLEAR_SUSPENSION'):
                self.db.execute('UPDATE native_switch SET suspended=?',(int(operation=='SUSPEND'),))
            elif operation=='SIZE':self.db.execute('UPDATE native_sizing SET lots=? WHERE strategy=?',(value,strategy))
            else:self.db.execute('UPDATE native_controls SET state=? WHERE strategy=?',({'START':'RUNNING','RESUME':'RUNNING','STOP':'STOPPED','PAUSE':'PAUSED'}[operation],strategy))
            self.db.execute('INSERT INTO audit(at,command_id,kind,detail) VALUES(?,?,?,?)',
                (datetime.now(timezone.utc).isoformat(),key,'OBSERVATION_CONTROL',operation+':'+strategy))
            self.db.commit();return 'APPLIED'
        except BaseException:self.db.rollback();raise

    def snapshot(self):return controls_snapshot(self.db)


def controls_snapshot(db):
    return {'execution_enabled':False,'mode':'OBSERVATION_CONTROLS_ONLY',
        'entry_suspended':bool(db.execute('SELECT suspended FROM native_switch').fetchone()[0]),
        'strategies':[dict(r) for r in db.execute('SELECT c.strategy,c.state,s.lots FROM native_controls c LEFT JOIN native_sizing s ON c.strategy=s.strategy')],
        'recent_controls':[dict(r) for r in db.execute("SELECT at,kind,detail FROM audit WHERE kind='OBSERVATION_CONTROL' ORDER BY seq DESC LIMIT 30")]}


def read_controls(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN');return controls_snapshot(db)


def entry_reason(snapshot,strategy):
    if snapshot['entry_suspended']:return 'ENTRY_SUSPENDED'
    rows=[s for s in snapshot['strategies'] if s['strategy']==strategy]
    return None if len(rows)==1 and rows[0]['state']=='RUNNING' else 'STRATEGY_NOT_RUNNING'


def handler(store,port,worker_database=None,broker_port=None):
    csrf=secrets.token_urlsafe(32);cookie=secrets.token_urlsafe(32)
    host='127.0.0.1:'+str(port);origin='http://'+host
    def status():
        result=store.snapshot()
        if worker_database:
            try:
                if not Path(worker_database).exists():raise FileNotFoundError()
                from observation.status import snapshot
                result['worker']=snapshot(worker_database)
            except FileNotFoundError:result['worker']={'status':'NOT_STARTED','heartbeat_fresh':False,'mode':'OFFLINE_REPLAY'}
            except Exception:result['worker']={'status':'UNAVAILABLE','heartbeat_fresh':False}
        if broker_port:
            try:
                with build_opener(ProxyHandler({}),NoRedirect()).open('http://127.0.0.1:'+str(broker_port)+'/status',timeout=1) as response:
                    raw=json.load(response)
                    result['broker']={key:raw[key] for key in ('read_only','environment','health','heartbeat_fresh','snapshot_fresh','quote_fresh','history_complete','quote','account','symbol','last_error') if key in raw}
            except Exception:result['broker']={'health':'UNAVAILABLE','read_only':True}
        try:
            result['native_preparation']={'commands':[dict(r) for r in store.db.execute('SELECT id,strategy,state,quantity,risk,margin FROM native_commands ORDER BY rowid DESC LIMIT 50')],
                'actions':[dict(r) for r in store.db.execute('SELECT operation,state FROM native_actions ORDER BY rowid DESC LIMIT 50')],
                'positions':[json.loads(r[0]) for r in store.db.execute('SELECT data FROM native_positions')],
                'orders':[json.loads(r[0]) for r in store.db.execute('SELECT data FROM native_orders')],
                'audit':[dict(r) for r in store.db.execute('SELECT at,kind,detail FROM audit ORDER BY seq DESC LIMIT 50')]}
        except sqlite3.Error:result['native_preparation']={'status':'NOT_STARTED','execution_enabled':False}
        return result
    class LocalOps(BaseHTTPRequestHandler):
        def reply(self,code,body,content='text/html; charset=utf-8'):
            self.send_response(code);self.send_header('Content-Type',content)
            self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','origin')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            self.send_header('Set-Cookie','autotrader_ops='+cookie+'; HttpOnly; SameSite=Strict; Path=/')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.headers.get('Host')!=host:return self.reply(400,b'LOCAL_HOST_REQUIRED')
            if self.path=='/ops.js':
                body=Path(__file__).with_name('operations.js').read_bytes()
                return self.reply(200,body,'text/javascript; charset=utf-8')
            if self.path=='/status':return self.reply(200,json.dumps(status(),allow_nan=False).encode(),'application/json')
            if self.path!='/':return self.reply(404,b'NOT_FOUND')
            data=status()
            summary='\n'.join(['Mode: observation / offline preparation — real execution disabled',
                'Razor read-only observer: '+data.get('broker',{}).get('health','NOT CONFIGURED'),
                'Replay worker: '+data.get('worker',{}).get('status','NOT CONFIGURED'),
                'New entry decisions: '+('SUSPENDED' if data['entry_suspended'] else 'per-bot observation controls')]+
                [s['strategy']+': '+s['state']+' · preview '+str(s['lots'])+' lots per leg' for s in data['strategies']])
            forms=[]
            for strategy in STRATEGIES:
                forms.append('<form method="post" action="/control"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="key" value="'+secrets.token_hex(16)+'"><input type="hidden" name="strategy" value="'+strategy+'"><strong>'+strategy+'</strong> '+''.join('<button name="operation" value="'+op+'">'+label+'</button>' for op,label in [('START','Start observation'),('PAUSE','Pause'),('STOP','Stop')])+'</form>')
                forms.append('<form method="post" action="/control"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="key" value="'+secrets.token_hex(16)+'"><input type="hidden" name="strategy" value="'+strategy+'"><input type="hidden" name="operation" value="SIZE"><label>'+strategy+' native sizing preview (lots) <input required name="value" inputmode="decimal" placeholder="0.01"></label><button>Save sizing preview</button></form>')
            forms.append('<form method="post" action="/control"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="key" value="'+secrets.token_hex(16)+'"><button name="operation" value="SUSPEND">Suspend all new entries</button><button name="operation" value="CLEAR_SUSPENSION">Clear observation suspension</button></form>')
            body=('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AutoTrader observation controls</title><style>body{font:16px system-ui;max-width:1100px;margin:36px auto;padding:18px;background:#10151c;color:#eee}button{margin:8px;padding:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere}form{padding:12px;border:1px solid #789;margin:12px 0}#health-summary{padding:18px;background:#192f38}</style><h1>Observation operations</h1><p><strong>REAL EXECUTION DISABLED — no trading authorization</strong></p><pre id="health-summary">'+escape(summary)+'</pre><p>Controls govern a separately started replay worker; they do not launch services. Stop/pause suspends new decisions, not existing pending fills or position management. Preview lots are per entry leg; fictional paper sizing is separate. No strategy starts automatically.</p>'+''.join(forms)+'<p id="freshness">Initial server snapshot; automatic updates every two seconds</p><pre id="live-status">'+escape(json.dumps(data,indent=2))+'</pre><script src="/ops.js"></script>').encode()
            self.reply(200,body)
        def do_POST(self):
            try:
                from http.cookies import SimpleCookie
                if self.path!='/control' or self.headers.get('Host')!=host or self.headers.get('Origin')!=origin:raise SafetyError('CONTROL_ORIGIN_REQUIRED')
                cookies=SimpleCookie();cookies.load(self.headers.get('Cookie',''))
                supplied=cookies.get('autotrader_ops')
                if supplied is None or not hmac.compare_digest(supplied.value,cookie):raise SafetyError('CONTROL_SESSION_REQUIRED')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=2048:raise SafetyError('CONTROL_BODY_REJECTED')
                values=parse_qs(self.rfile.read(length).decode('ascii'),keep_blank_values=True)
                if set(values)-{'csrf','key','operation','strategy','value'} or any(len(v)!=1 for v in values.values()):raise SafetyError('CONTROL_BODY_REJECTED')
                if not hmac.compare_digest(values.get('csrf',[''])[0],csrf):raise SafetyError('CONTROL_CSRF_REQUIRED')
                result=store.command(values.get('key',[''])[0],values.get('operation',[''])[0],values.get('strategy',[''])[0],values.get('value',[''])[0])
                self.reply(200,('Observation control '+result+'. <a href="/">Return to status</a>').encode())
            except Exception:
                try:store.db.execute('INSERT INTO audit(at,command_id,kind,detail) VALUES(?,?,?,?)',
                    (datetime.now(timezone.utc).isoformat(),None,'OBSERVATION_CONTROL_REJECTED','LOCAL_CONTROL_REJECTED'))
                except sqlite3.Error:pass
                self.reply(400,b'CONTROL_REJECTED; no broker action authorized')
        def log_message(self,*args):pass
    return LocalOps


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--database',required=True)
    parser.add_argument('command',choices=('status','start','stop','pause','resume','suspend','clear-suspension','size','serve'))
    parser.add_argument('--strategy',choices=STRATEGIES);parser.add_argument('--lots');parser.add_argument('--key')
    parser.add_argument('--port',type=int,default=18772);parser.add_argument('--worker-database');parser.add_argument('--broker-port',type=int)
    args=parser.parse_args();store=None
    try:
        if args.broker_port is not None and not 1024<=args.broker_port<=65535:raise SafetyError('INVALID_BROKER_STATUS_PORT')
        store=OpsStore(args.database)
        if args.command=='status':print(json.dumps(store.snapshot(),indent=2));return 0
        if args.command=='serve':
            from ctrader_readonly.__main__ import LocalHTTPServer
            with LocalHTTPServer(('127.0.0.1',args.port),handler(store,args.port,args.worker_database,args.broker_port)) as server:
                print('OBSERVATION_CONTROLS_ONLY: http://127.0.0.1:'+str(args.port),flush=True)
                server.serve_forever(poll_interval=.5)
            return 0
        result=store.command(args.key or secrets.token_hex(16),args.command.upper().replace('-','_'),args.strategy or '',args.lots or '')
        print('OBSERVATION_CONTROL_'+result+'; EXECUTION_DISABLED');return 0
    except KeyboardInterrupt:return 0
    except Exception:print('OBSERVATION_CONTROL_UNAVAILABLE');return 1
    finally:
        if store:store.close()

if __name__=='__main__':raise SystemExit(main())
