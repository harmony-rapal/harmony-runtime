import hashlib
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from demo.server import PAYLOAD, digest, make_server


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server(port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.server.session.storage.cleanup()

    def call(self, path, body=None, origin=None, host=None):
        headers = {'Origin': origin or self.base}
        if host:
            headers['Host'] = host
        request = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with urlopen(request) as response:
            return json.load(response)

    def test_reject_then_approve_http_evidence(self):
        state = self.call('/api/session')
        result = self.call('/api/decision', {'decision': 'REJECT', 'token': state['token']})
        self.assertFalse(result['receipt']['executed'])
        self.assertIsNone(result['receipt']['exit_status'])
        self.assertEqual(list(self.server.session.root.iterdir()), [])
        state = self.call('/api/proposal', {'token': state['token']})
        result = self.call('/api/decision', {'decision': 'APPROVE', 'token': state['token']})
        receipt = result['receipt']
        self.assertEqual((self.server.session.root / 'hello.txt').read_bytes(), PAYLOAD)
        self.assertEqual([p.name for p in self.server.session.root.iterdir()], ['hello.txt'])
        self.assertEqual(receipt['evidence_sha256'], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(receipt['exit_status'], 0)
        self.assertEqual(receipt['status'], 'FINAL')
        self.assertEqual(receipt['receipt_sha256'], digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'}))
        self.assertEqual(result['full_release_activation'], 'HOLD')
        with self.assertRaises(HTTPError) as err:
            self.call('/api/decision', {'decision': 'APPROVE', 'token': state['token']})
        self.assertEqual(err.exception.code, 400)

    def test_fail_closed_requests(self):
        state = self.call('/api/session')
        for body, origin, code in [({'decision': 'APPROVE', 'token': 'wrong'}, None, 403),
                                   ({'decision': 'APPROVE', 'token': state['token']}, 'http://attacker.invalid', 403),
                                   ({'decision': 'shell', 'token': state['token']}, None, 400)]:
            with self.assertRaises(HTTPError) as err:
                self.call('/api/decision', body, origin)
            self.assertEqual(err.exception.code, code)
        with self.assertRaises(HTTPError) as err:
            self.call('/api/session', host='attacker.invalid')
        self.assertEqual(err.exception.code, 403)
        self.assertEqual(list(self.server.session.root.iterdir()), [])
        self.assertIsNone(self.server.session.receipt)

    def test_static_ui_and_path_boundary(self):
        with urlopen(self.base + '/demo.html') as response:
            page = response.read().decode()
            self.assertIn('APPROVE', page)
            self.assertIn('No installed PWA', page)
        for path in ('/../README.md', '/telegraph/cli.py'):
            with self.assertRaises(HTTPError) as err:
                self.call(path)
            self.assertEqual(err.exception.code, 404)
