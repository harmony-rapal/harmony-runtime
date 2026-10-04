"""Credential-free, single-process demo. No shell or arbitrary action API."""
import argparse
import hashlib
import json
import secrets
import signal
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / 'docs'
PAYLOAD = b'harmony runtime: human-approved demo execution\n'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Session:
    def __init__(self):
        self.storage = tempfile.TemporaryDirectory(prefix='harmony-demo-')
        self.root = Path(self.storage.name)
        self.token = secrets.token_urlsafe(32)
        self.proposal = {
            'baton': secrets.token_hex(16),
            'action': 'Create hello.txt with fixed demo text',
            'mok': 'One new file in this session’s temporary sandbox',
            'player': 'demo-local-worker (process identity; not authenticated)',
            'risk': 'Low: fixed file write; no shell, network, credentials or deploy',
            'authority': 'One local demo decision; no production authority',
        }
        self.packet_sha256 = digest(self.proposal)
        self.receipt = None

    def view(self):
        return dict(proposal=self.proposal, packet_sha256=self.packet_sha256,
                    token=self.token, receipt=self.receipt,
                    status='FINAL' if self.receipt else 'AWAITING_HUMAN_GATE',
                    full_release_activation='HOLD')

    def decide(self, decision, token):
        if not secrets.compare_digest(str(token), self.token):
            raise PermissionError('Invalid session token')
        if self.receipt is not None:
            raise ValueError('Baton already consumed; create a new proposal')
        if decision not in ('APPROVE', 'REJECT'):
            raise ValueError('Decision must be APPROVE or REJECT')
        evidence = None
        if decision == 'APPROVE':
            # Fresh private directory, fixed name/content, exclusive creation.
            with (self.root / 'hello.txt').open('xb') as output:
                output.write(PAYLOAD)
            evidence = hashlib.sha256((self.root / 'hello.txt').read_bytes()).hexdigest()
        receipt = dict(status='FINAL', decision=decision,
                       approver='local-demo-human (self-asserted; not authenticated)',
                       execution_identity=self.proposal['player'] if evidence else None,
                       exit_status=0 if evidence else None, executed=bool(evidence),
                       packet_sha256=self.packet_sha256,
                       evidence_sha256=evidence,
                       output='hello.txt' if evidence else None,
                       output_text=PAYLOAD.decode() if evidence else None,
                       scope='isolated demo receipt; not a signed telegraph receipt')
        receipt['receipt_sha256'] = digest(receipt)
        self.receipt = receipt
        return self.view()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, body, kind='application/json'):
        data = json.dumps(body).encode() if kind == 'application/json' else body
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def valid_host(self):
        return self.headers.get('Host') in self.server.local_hosts

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {'error': 'Local host required'})
        if self.path == '/api/session':
            return self.reply(200, self.server.session.view())
        allowed = {'/': ('index.html', 'text/html; charset=utf-8'),
                   '/index.html': ('index.html', 'text/html; charset=utf-8'),
                   '/demo.html': ('demo.html', 'text/html; charset=utf-8'),
                   '/demo.js': ('demo.js', 'text/javascript'),
                   '/style.css': ('style.css', 'text/css')}
        for asset in ('logo.svg', 'logo-mark.svg', 'logo-mono.svg', 'landscape.svg', 'hero-wordmark.svg'):
            allowed['/assets/' + asset] = ('assets/' + asset, 'image/svg+xml')
        if self.path not in allowed:
            return self.reply(404, {'error': 'Not found'})
        name, kind = allowed[self.path]
        self.reply(200, (DOCS / name).read_bytes(), kind)

    def do_POST(self):
        origin = self.headers.get('Origin')
        if not self.valid_host() or origin != 'http://' + self.headers.get('Host', ''):
            return self.reply(403, {'error': 'Same-origin local request required'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024:
                raise ValueError('Invalid body size')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Object required')
            if self.path == '/api/decision':
                return self.reply(200, self.server.session.decide(body.get('decision'), body.get('token')))
            if self.path == '/api/proposal':
                if not secrets.compare_digest(str(body.get('token')), self.server.session.token):
                    raise PermissionError('Invalid session token')
                # Only finalized Batons can be replaced. Keeps pending scope stable.
                if not self.server.session.receipt:
                    raise ValueError('Decide the current Baton first')
                old = self.server.session
                self.server.session = Session()
                old.storage.cleanup()
                return self.reply(200, self.server.session.view())
            self.reply(404, {'error': 'Not found'})
        except PermissionError as exc:
            self.reply(403, {'error': str(exc)})
        except (ValueError, TypeError) as exc:
            self.reply(400, {'error': str(exc)})
        except OSError:
            self.reply(500, {'error': 'Sandbox write failed; no FINAL receipt issued'})


def make_server(host='127.0.0.1', port=8765, public_port=None):
    if public_port is not None and not 1 <= public_port <= 65535:
        raise ValueError('Public port must be between 1 and 65535')
    server = HTTPServer((host, port), Handler)
    local_port = server.server_port if public_port is None else public_port
    server.local_hosts = (f'localhost:{local_port}', f'127.0.0.1:{local_port}')
    server.session = Session()
    return server


def _graceful_shutdown(_signum, _frame):
    """Route SIGINT/SIGTERM through the same cleanup path."""
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--container', action='store_true', help='Bind all interfaces inside a container only')
    parser.add_argument('--port', type=int, default=8765, help='Local listening port (default: 8765)')
    parser.add_argument('--public-port', type=int, help='Loopback port published by Docker; no remote hosts accepted')
    args = parser.parse_args()
    server = make_server('0.0.0.0' if args.container else '127.0.0.1', port=args.port, public_port=args.public_port)
    try:
        # Background shells may start children with SIGINT ignored. Install both
        # handlers explicitly so Ctrl+C, process-group cleanup and SIGTERM all
        # converge on the same deterministic TemporaryDirectory cleanup path.
        signal.signal(signal.SIGINT, _graceful_shutdown)
        signal.signal(signal.SIGTERM, _graceful_shutdown)
        print(f'harmony runtime demo: http://localhost:{args.public_port or args.port}/demo.html | FULL_RELEASE_ACTIVATION=HOLD', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.session.storage.cleanup()
        server.server_close()


if __name__ == '__main__':
    main()
