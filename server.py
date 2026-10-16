"""Loopback-only research service. API credentials remain in the server environment."""
from __future__ import annotations
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from agent import retrieve
from core import now, org_number
from footprint import TinyFish
from network import Client

gate = threading.Lock()
session_runs = 0
previous_records = {}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(Path(__file__).parent/'dist'), **kwargs)

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def trusted_request(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        origin = self.headers.get('Origin')
        return self.headers.get('Host') in hosts and (origin is None or origin in {f'http://{host}' for host in hosts})

    def do_GET(self):
        if not self.trusted_request():
            return self.reply(403, {'error': 'Loopback host and same-origin access required'})
        if self.path == '/api/status':
            return self.reply(200, {'live_research': bool(os.environ.get('TINYFISH_API_KEY')),
                'provider': 'TinyFish Search + Fetch', 'key_location': 'server environment only',
                'paid_endpoints': False, 'session_runs': session_runs, 'session_run_limit': 20})
        if urlsplit(self.path).path.startswith('/api/'):
            return self.reply(404, {'error': 'Unknown API route'})
        return super().do_GET()

    def do_POST(self):
        global session_runs
        if not self.trusted_request():
            return self.reply(403, {'error': 'Loopback host and same-origin access required'})
        if self.path != '/api/research':
            return self.reply(404, {'error': 'Unknown API route'})
        if not os.environ.get('TINYFISH_API_KEY'):
            return self.reply(503, {'error': 'Set TINYFISH_API_KEY in the server environment. No key belongs in the browser.'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': 'JSON input required'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 2000:
                raise ValueError('Input size limit is 2 KB')
            org = org_number(json.loads(self.rfile.read(length)).get('organisation_number'))
        except (ValueError, TypeError, AttributeError) as error:
            return self.reply(400, {'error': str(error)[:160]})
        if not gate.acquire(blocking=False):
            return self.reply(409, {'error': 'A company research run is already active. Wait for its result.'})
        try:
            if session_runs >= 20:
                return self.reply(429, {'error': 'Local session research limit reached. Restart intentionally to continue.'})
            session_runs += 1
            client = Client(max_requests=12, seconds=110)
            provider = TinyFish(max_requests=6, deadline=client.deadline)
            record = retrieve(client, org, previous_records.get(org), 'fjord-web-'+now(), provider)
            previous_records[org] = record
            record['operations']['web_requests'] = {'searches': provider.searches, 'fetches': provider.fetches}
            return self.reply(200, {'company': record, 'provider_requests': provider.requests,
                                    'source_requests': client.requests, 'paid_endpoints_used': False})
        except Exception as error:
            return self.reply(500, {'error': f'Research failed ({type(error).__name__}); no company fact inferred.'})
        finally:
            gate.release()

    def log_message(self, format, *args):
        # Do not log request bodies, API keys, provider responses or user imports.
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=4180)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    print(f'Fjord local research desk: http://127.0.0.1:{args.port}/', flush=True)
    print('TinyFish credential available: '+str(bool(os.environ.get('TINYFISH_API_KEY'))), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
