import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def session(port):
    request = Request(
        f'http://127.0.0.1:{port}/api/session',
        headers={'Host': f'localhost:{port}'},
    )
    with urlopen(request, timeout=1) as response:
        return json.load(response)


class DemoLifecycleTests(unittest.TestCase):
    def run_signal_cleanup(self, sig, inherit_ignored_sigint=False):
        before = set(Path(tempfile.gettempdir()).glob('harmony-demo-*'))
        port = free_port()

        preexec = None
        if inherit_ignored_sigint:
            def ignore_sigint():
                signal.signal(signal.SIGINT, signal.SIG_IGN)
            preexec = ignore_sigint

        process = subprocess.Popen(
            [sys.executable, '-B', '-m', 'demo.server', '--port', str(port)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=preexec,
        )
        try:
            state = None
            for _ in range(100):
                if process.poll() is not None:
                    break
                try:
                    state = session(port)
                    break
                except OSError:
                    time.sleep(0.05)

            output = ''
            self.assertIsNotNone(
                state,
                f'demo did not become ready; rc={process.poll()} output={process.stdout.read() if process.poll() is not None else ""}',
            )

            created = set(Path(tempfile.gettempdir()).glob('harmony-demo-*')) - before
            self.assertEqual(len(created), 1, created)
            sandbox = next(iter(created))
            self.assertTrue(sandbox.is_dir())

            process.send_signal(sig)
            process.wait(timeout=5)
            output = process.stdout.read()

            self.assertEqual(process.returncode, 0, output)
            self.assertFalse(sandbox.exists(), f'sandbox survived signal {sig}: {sandbox}\n{output}')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    @unittest.skipUnless(os.name == 'posix', 'POSIX signals required')
    def test_sigterm_gracefully_cleans_sandbox(self):
        self.run_signal_cleanup(signal.SIGTERM)

    @unittest.skipUnless(os.name == 'posix', 'POSIX signals required')
    def test_explicit_sigint_handler_overrides_inherited_ignore(self):
        self.run_signal_cleanup(signal.SIGINT, inherit_ignored_sigint=True)
