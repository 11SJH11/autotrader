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


def handler(database):
    class Diagnostics(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/diagnostics':
                self.send_error(404)
                return
            try:
                body = json.dumps(snapshot(database)).encode()
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
    args = parser.parse_args()
    HTTPServer(('127.0.0.1',args.port),handler(args.database)).serve_forever()


if __name__ == '__main__':
    main()
