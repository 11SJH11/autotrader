"""Local-only cTrader read observer. All network activity requires an explicit command."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import webbrowser

from .client import Reader
from .config import Config, SafetyError
from .oauth import OAuthFlow, Token


class LocalHTTPServer(HTTPServer):
    def get_request(self):
        connection,address=super().get_request()
        connection.settimeout(2)
        return connection,address


def status_handler(reader):
    class Status(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!='/status':
                self.send_error(404)
                return
            body=json.dumps(reader.status(),allow_nan=False).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):
            pass
    return Status



def open_browser_private(url):
    """Suppress child browser-launcher output; never print an OAuth/launch URL."""
    import os
    saved=[os.dup(1),os.dup(2)]
    sink=os.open(os.devnull,os.O_WRONLY)
    try:
        os.dup2(sink,1);os.dup2(sink,2)
        return webbrowser.open(url)
    finally:
        os.dup2(saved[0],1);os.dup2(saved[1],2)
        for descriptor in (*saved,sink):os.close(descriptor)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('authorize','observe','discover','verify'))
    parser.add_argument('--config',required=True,help='Owner-only JSON outside any Git tree')
    parser.add_argument('--status-port',type=int,default=8768)
    args=parser.parse_args(argv)
    reader=None
    try:
        if args.command=='discover':
            from .discovery import DiscoveryFlow
            flow=DiscoveryFlow(args.config)
            try:
                flow.serve(open_browser_private)
            except SafetyError:
                print('DISCOVERY_INCOMPLETE: '+(flow.last_failure or 'LOCAL_FLOW_NOT_COMPLETED'))
                if flow.failure_stage:print('FAILED_STAGE: '+flow.failure_stage)
                if flow.discovery_failure:print('DISCOVERY_CHECK: '+flow.discovery_failure)
                if flow.last_failure and not flow.failure_stage:print('SELECTION_STAGE: '+flow.selection_stage)
                return 1
            print('DEMO_SELECTED_VIEW_ONLY; account identity saved privately')
            return 0
        config=Config.load(args.config)
        config.require_approved()
        reader=Reader(config)
        if args.command=='verify':
            reader.authorize(Token.load(config.token_path))
            report=reader.verify_identity()
            print(json.dumps(report,allow_nan=False))
            return 0 if report['broker_name_matches_configuration'] else 2
        if args.command=='authorize':
            flow=OAuthFlow(config,reader.authorize)
            flow.serve(open_browser_private)
            print('AUTHORIZED_VIEW_ONLY')
            return 0
        stop=threading.Event()
        # Bind before starting broker activity. No credentials or broker details
        # enter the HTTP handler; only the explicit sanitized status projection.
        with LocalHTTPServer(('127.0.0.1',args.status_port),status_handler(reader)) as server:
            worker=threading.Thread(target=reader.run,args=(lambda:Token.load(config.token_path),stop),daemon=True)
            worker.start()
            try:
                print('Read-only status: http://127.0.0.1:'+str(args.status_port)+'/status')
                server.serve_forever(poll_interval=0.5)
            finally:
                stop.set()
                worker.join(12)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        # Deliberately no traceback, file path, repr, response body or URL.
        print('READ_ONLY_CLIENT_UNAVAILABLE: check local approval, private configuration and authorization')
        return 1
    finally:
        if reader:
            reader.disconnect('STOPPED')


if __name__=='__main__':
    raise SystemExit(main())
