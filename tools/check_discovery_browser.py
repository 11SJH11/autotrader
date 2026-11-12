"""Offline Firefox reproduction of form Origin policy; synthetic data only.

Fresh disposable browser profiles, no broker/auth/private-config access. Prints
only fixed booleans; never request headers, URLs or nonce values.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import threading


def check(policy):
    seen={}
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            body=b'<form method="post" action="/confirm"><input name="csrf" value="synthetic"></form><script>document.forms[0].submit()</script>'
            self.send_response(200)
            self.send_header('Referrer-Policy',policy)
            self.send_header('Set-Cookie','fixture_session=synthetic; HttpOnly; SameSite=Strict; Path=/')
            self.send_header('Content-Type','text/html')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def do_POST(self):
            origin=f'http://127.0.0.1:{self.server.server_port}'
            seen.update(origin_null=self.headers.get('Origin')=='null',
                origin_matches=self.headers.get('Origin')==origin,
                referrer_origin_only=self.headers.get('Referer')==origin+'/',
                query_excluded='SYNTHETIC_CODE' not in self.headers.get('Referer',''),
                cookie_present='fixture_session=synthetic' in self.headers.get('Cookie',''),
                form_token_present=self.rfile.read(int(self.headers['Content-Length']))==b'csrf=synthetic')
            self.send_response(200);self.end_headers()
            self.wfile.write(b'<p>Offline fixture complete</p>')
        def log_message(self,*args):pass
    with HTTPServer(('127.0.0.1',0),Page) as server, tempfile.TemporaryDirectory(prefix='autotrader-browser-') as directory:
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        profile=Path(directory)/'profile';profile.mkdir()
        try:
            subprocess.run([shutil.which('firefox'),'--headless','--no-remote','--profile',str(profile),
                '--screenshot',str(Path(directory)/'fixture.png'),
                f'http://127.0.0.1:{server.server_port}/callback?code=SYNTHETIC_CODE'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,check=True)
        finally:server.shutdown();thread.join()
    return seen


def main():
    old=check('no-referrer');fixed=check('origin')
    assert old.get('origin_null') and old.get('cookie_present') and old.get('form_token_present')
    assert fixed.get('origin_matches') and fixed.get('referrer_origin_only') and fixed.get('query_excluded')
    assert fixed.get('cookie_present') and fixed.get('form_token_present')
    print(json.dumps({'old_policy_reproduces_null_origin':True,'fixed_policy_exact_origin':True,
        'callback_query_excluded':True,'cookie_and_form_token_transmitted':True}))

if __name__=='__main__':main()
