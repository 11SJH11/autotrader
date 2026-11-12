"""Loopback-only diagnostic HTTP server. Opens SQLite read-only; no broker import.

Run: .venv/bin/python -m control_plane.diagnostics --database state.sqlite
"""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote


def snapshot(path):
    uri = 'file:'+quote(str(Path(path).resolve()), safe='/')+'?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=1) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        strategies = [dict(r) for r in db.execute('SELECT strategy, COUNT(*) AS entries FROM commands GROUP BY strategy')]
        unresolved = db.execute("SELECT COUNT(*) FROM commands WHERE state IN ('SUBMITTING','UNKNOWN')").fetchone()[0]
        unresolved += db.execute("SELECT COUNT(*) FROM actions WHERE state IN ('SUBMITTING','UNKNOWN')").fetchone()[0]
        gate = db.execute("SELECT detail FROM audit WHERE kind='LIFECYCLE_RECONCILIATION' ORDER BY seq DESC LIMIT 1").fetchone()
        def records(table, status):
            fields = ('id','strategy','instrument','side','quantity','entry','stop','take_profit','status')
            items = [json.loads(r[0]) for r in db.execute('SELECT data FROM '+table)]
            return [{k:item[k] for k in fields} for item in items if item['status'] == status]
        rejected = []
        for row in db.execute("SELECT at,detail FROM audit WHERE kind='RISK' ORDER BY seq DESC LIMIT 100"):
            decision = json.loads(row['detail'])
            if not decision['accepted']:
                rejected.append({'at':row['at'], 'reason_code':decision['reason_code']})
        return dict(simulation=True, engine={'unresolved_commands':unresolved,
            'last_reconciliation':gate[0] if gate else 'never', 'status':'BLOCKED' if unresolved or not gate or gate[0]!='complete' else 'LAST_RECONCILED'},
            strategies=strategies, pending_orders=records('lifecycle_orders','PENDING'),
            positions=records('positions','OPEN'), recent_rejections=rejected[:20])


def handler(database, worker_database=None, broker_status=None):
    class Diagnostics(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ('/','/dashboard.js'):
                filename='dashboard.html' if self.path=='/' else 'dashboard.js'
                body=Path(__file__).with_name(filename).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8' if filename.endswith('.html') else 'text/javascript; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'")
                self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body);return
            if self.path != '/diagnostics':
                self.send_error(404)
                return
            try:
                try:data=snapshot(database)
                except sqlite3.Error:
                    if worker_database is None:raise
                    data={'simulation':True,'engine':{'status':'NO_CENTRAL_EXECUTION_ENGINE'},'pending_orders':[],'positions':[],'recent_rejections':[]}
                if worker_database is not None:
                    from observation.status import snapshot as worker_snapshot
                    data['worker']=worker_snapshot(worker_database)
                data['broker_observer']=broker_status() if broker_status else {'read_only':True,'health':'NOT_CONFIGURED','real_execution_enabled':False}
                body = json.dumps(data,allow_nan=False).encode()
            except (sqlite3.Error, ValueError, KeyError):
                self.send_error(503, 'Diagnostics unavailable')
                return
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Diagnostics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--port',type=int,default=8767)
    parser.add_argument('--worker-database')
    parser.add_argument('--ctrader-config',help='Explicit owner-only private config; never required for offline replay')
    args = parser.parse_args()
    import threading
    reader=None;stop=threading.Event();thread=None
    broker_status=None
    if args.ctrader_config:
        try:
            from ctrader_readonly.config import Config
            from ctrader_readonly.client import Reader
            from ctrader_readonly.oauth import Token
            config=Config.load(args.ctrader_config);config.require_approved()
            reader=Reader(config);broker_status=reader.status
            thread=threading.Thread(target=reader.run,args=(lambda:Token.load(config.token_path),stop),daemon=True)
            thread.start()
        except Exception:
            broker_status=lambda:{'read_only':True,'health':'BLOCKED_CONFIGURATION','real_execution_enabled':False}
    try:
        with HTTPServer(('127.0.0.1',args.port),handler(args.database,args.worker_database,broker_status)) as server:
            server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:pass
    finally:
        stop.set()
        if thread:thread.join(12)
        if reader:reader.disconnect('STOPPED')


if __name__ == '__main__':
    main()
