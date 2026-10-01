"""
test_c2_trust_root_propagation.py — Mandatory test suite for C2 trust-root propagation.

Tests:
  1. IPC APPROVE_GATE succeeds with explicit canonical signers and
     HARMONY_ALLOWED_SIGNERS_PATH absent.
  2. Ledger secondary verification receives the same explicit trust root.
  3. Missing explicit trust root fails closed (both Ledger and Actuator).
  4. Forged / tampered gate fails closed.
  5. Ledger.record_human_gate_receipt() follows the same explicit rule.
  6. End-to-end IPC socket communication succeeds with explicit trust root and absent env.
  7. CLI approve and approve-gate propagate explicit allowed_signers and fail closed if missing.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import stat
import struct
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Any
from unittest.mock import patch

from telegraph.actuator_client import ActuatorClient
from telegraph.actuator_service import (
    ActuatorProtocolHandler,
    ActuatorServer,
    _SO_PEERCRED,
    open_actuator_ledger,
)
from telegraph.cli import main as cli_main
from telegraph.human_gate import (
    HumanGateVerificationError,
    create_verified_gate_receipt,
    verify_gate_receipt_dict,
)
from telegraph.ledger import Ledger, LedgerError
from telegraph.packet import compute_sha256
from tests.test_gate_keys import (
    TEST_PUBKEYS_B64,
    ensure_test_keys_registered,
    get_test_allowed_signers_content,
)


class TestC2TrustRootPropagation(unittest.TestCase):
    """Exhaustive tests for C2 trust root propagation without environment workarounds."""

    def setUp(self) -> None:
        ensure_test_keys_registered()

        # Isolate environment: ensure HARMONY_ALLOWED_SIGNERS_PATH is absent
        self._saved_env = os.environ.pop("HARMONY_ALLOWED_SIGNERS_PATH", None)

        self.tmpdir = tempfile.mkdtemp(prefix="c2_trust_root_test_")
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        os.chmod(self.state_dir, 0o700)

        self.gate_dir = os.path.join(self.tmpdir, "gates")
        os.makedirs(self.gate_dir, mode=0o700, exist_ok=True)
        os.chmod(self.gate_dir, 0o700)

        # Explicit canonical signers file
        self.allowed_signers_path = os.path.join(self.tmpdir, "allowed_signers")
        with open(self.allowed_signers_path, "w", encoding="utf-8") as f:
            f.write(get_test_allowed_signers_content())
        os.chmod(self.allowed_signers_path, 0o600)

        # Restricted signers file containing only Bob
        self.bob_only_signers_path = os.path.join(self.tmpdir, "bob_only_signers")
        with open(self.bob_only_signers_path, "w", encoding="utf-8") as f:
            f.write(
                f'bob@corp.com namespaces="harmony-human-gate" ssh-ed25519 {TEST_PUBKEYS_B64["bob@corp.com"]} Bob Key\n'
            )
        os.chmod(self.bob_only_signers_path, 0o600)

        self.ledger = Ledger(self.state_dir)

        # Sample valid MissionPacketV1
        self.packet = {
            "schema_version": 1,
            "action": "START_BUILDER",
            "mission_id": "MISSION-C2-TRUST-001",
            "revision": 1,
            "project_id": "harmony",
            "objective": "Verify C2 trust root propagation",
            "profile_id": "builder-v1",
            "target": {
                "repo_root": "/tmp/repo",
                "base_commit_oid": "0" * 40,
            },
            "on_failure": "HOLD",
            "retry_policy": "NEVER",
        }
        self.packet_sha = compute_sha256(self.packet)
        self.ledger.ingest(self.packet, self.packet_sha)

    def tearDown(self) -> None:
        try:
            self.ledger.close()
        except Exception:
            pass
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir, ignore_errors=True)
        if self._saved_env is not None:
            os.environ["HARMONY_ALLOWED_SIGNERS_PATH"] = self._saved_env
        else:
            os.environ.pop("HARMONY_ALLOWED_SIGNERS_PATH", None)

    def _create_receipt(
        self,
        signer: str = "alice@corp.com",
        packet_sha: str | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        return create_verified_gate_receipt(
            packet_sha256=packet_sha or self.packet_sha,
            mission_id=mission_id or self.packet["mission_id"],
            signer=signer,
        )

    def _write_receipt_file(self, receipt: dict[str, Any]) -> str:
        receipt_path = os.path.join(self.gate_dir, f"{receipt['packet_sha256']}.json")
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)
        os.chmod(receipt_path, 0o600)
        return receipt_path

    # ------------------------------------------------------------------
    # 1. IPC APPROVE_GATE succeeds with explicit signers & env absent
    # ------------------------------------------------------------------

    def test_ipc_approve_gate_succeeds_with_explicit_signers_and_env_absent(self) -> None:
        """APPROVE_GATE succeeds when Actuator has explicit signers and HARMONY_ALLOWED_SIGNERS_PATH is absent."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        receipt = self._create_receipt(signer="alice@corp.com")
        self._write_receipt_file(receipt)

        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            gate_receipt_dir=self.gate_dir,
            allowed_signers_path=self.allowed_signers_path,
        )

        resp = handler.approve_gate(self.packet_sha)
        self.assertEqual(resp["status"], "OK")
        self.assertEqual(resp["state"], "APPROVED")
        self.assertTrue(resp.get("gate_verified"))
        self.assertEqual(resp.get("signer"), "alice@corp.com")

        # Verify ledger state and human gate receipt record
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "APPROVED")
        gate_rec = self.ledger.get_human_gate_receipt(self.packet_sha)
        self.assertIsNotNone(gate_rec)
        self.assertEqual(gate_rec["signer"], "alice@corp.com")
        self.assertEqual(gate_rec["decision"], "APPROVE")
        self.assertEqual(gate_rec["packet_sha256"], self.packet_sha)

    # ------------------------------------------------------------------
    # 2. Ledger secondary verification receives the same explicit trust root
    # ------------------------------------------------------------------

    def test_ledger_secondary_verification_receives_explicit_trust_root(self) -> None:
        """Ledger.approve() uses the provided allowed_signers_path for secondary verification."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        receipt = self._create_receipt(signer="alice@corp.com")

        # Approval succeeds with explicit canonical signers path
        res = self.ledger.approve(
            self.packet_sha,
            approver=receipt["signer"],
            gate_receipt=receipt,
            allowed_signers_path=self.allowed_signers_path,
        )
        self.assertEqual(res["state"], "APPROVED")
        self.assertTrue(res.get("gate_verified"))
        self.assertEqual(res["signer"], "alice@corp.com")

    def test_ledger_secondary_verification_rejects_unauthorized_signer(self) -> None:
        """Ledger.approve() secondary verification enforces the explicit allowed_signers authority."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        # Receipt signed by Alice, but trust root given to Ledger only trusts Bob
        receipt = self._create_receipt(signer="alice@corp.com")

        with self.assertRaises(LedgerError) as cm:
            self.ledger.approve(
                self.packet_sha,
                approver=receipt["signer"],
                gate_receipt=receipt,
                allowed_signers_path=self.bob_only_signers_path,
            )
        self.assertIn("Gate receipt verification failed", str(cm.exception))
        # Packet must remain READY
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

    # ------------------------------------------------------------------
    # 3. Missing explicit trust root fails closed
    # ------------------------------------------------------------------

    def test_missing_explicit_trust_root_fails_closed_in_ledger_approve(self) -> None:
        """Ledger.approve() fails closed if allowed_signers_path is None and env is absent."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        receipt = self._create_receipt(signer="alice@corp.com")

        with self.assertRaises(LedgerError) as cm:
            self.ledger.approve(
                self.packet_sha,
                approver=receipt["signer"],
                gate_receipt=receipt,
                allowed_signers_path=None,
            )
        self.assertIn("Gate receipt verification failed", str(cm.exception))
        self.assertIn("missing external trust root", str(cm.exception))
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

    def test_missing_explicit_trust_root_fails_closed_in_actuator_handler(self) -> None:
        """ActuatorProtocolHandler fails closed if allowed_signers_path is None and env is absent."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        receipt = self._create_receipt(signer="alice@corp.com")
        self._write_receipt_file(receipt)

        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            gate_receipt_dir=self.gate_dir,
            allowed_signers_path=None,
        )

        resp = handler.approve_gate(self.packet_sha)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertIn("missing external trust root", resp["message"])
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

    # ------------------------------------------------------------------
    # 4. Forged / tampered gate fails closed
    # ------------------------------------------------------------------

    def test_forged_tampered_gate_fails_closed_in_ledger_and_actuator(self) -> None:
        """Forged or tampered gate receipts fail closed in both Ledger.approve and Actuator."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        valid_receipt = self._create_receipt(signer="alice@corp.com")

        # 4a. Tampered signer
        tampered_signer = dict(valid_receipt)
        tampered_signer["signer"] = "eve@corp.com"
        with self.assertRaises(LedgerError):
            self.ledger.approve(
                self.packet_sha,
                approver=tampered_signer["signer"],
                gate_receipt=tampered_signer,
                allowed_signers_path=self.allowed_signers_path,
            )
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

        # 4b. Tampered gate_digest
        tampered_digest = dict(valid_receipt)
        tampered_digest["gate_digest"] = "0" * 64
        with self.assertRaises(LedgerError):
            self.ledger.approve(
                self.packet_sha,
                approver=tampered_digest["signer"],
                gate_receipt=tampered_digest,
                allowed_signers_path=self.allowed_signers_path,
            )
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

        # 4c. Tampered packet_sha
        tampered_sha = dict(valid_receipt)
        tampered_sha["packet_sha256"] = "1" * 64
        with self.assertRaises(LedgerError):
            self.ledger.approve(
                self.packet_sha,
                approver=tampered_sha["signer"],
                gate_receipt=tampered_sha,
                allowed_signers_path=self.allowed_signers_path,
            )
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

        # 4d. Actuator rejection of tampered receipt in gate_dir
        self._write_receipt_file(tampered_digest)
        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            gate_receipt_dir=self.gate_dir,
            allowed_signers_path=self.allowed_signers_path,
        )
        resp = handler.approve_gate(self.packet_sha)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

    # ------------------------------------------------------------------
    # 5. Ledger.record_human_gate_receipt follows the same explicit rule
    # ------------------------------------------------------------------

    def test_record_human_gate_receipt_with_explicit_signers(self) -> None:
        """record_human_gate_receipt succeeds with explicit signers path when env is absent."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        receipt = self._create_receipt(signer="alice@corp.com")
        res = self.ledger.record_human_gate_receipt(
            receipt,
            allowed_signers_path=self.allowed_signers_path,
        )
        self.assertEqual(res["signer"], "alice@corp.com")
        self.assertEqual(res["packet_sha256"], self.packet_sha)

        stored = self.ledger.get_human_gate_receipt(self.packet_sha)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["signer"], "alice@corp.com")

    def test_record_human_gate_receipt_missing_explicit_trust_root_fails_closed(self) -> None:
        """record_human_gate_receipt fails closed when allowed_signers_path is None and env is absent."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        receipt = self._create_receipt(signer="alice@corp.com")
        with self.assertRaises(LedgerError) as cm:
            self.ledger.record_human_gate_receipt(
                receipt,
                allowed_signers_path=None,
            )
        self.assertIn("Gate receipt verification failed", str(cm.exception))
        self.assertIn("missing external trust root", str(cm.exception))

    def test_record_human_gate_receipt_tampered_fails_closed(self) -> None:
        """record_human_gate_receipt rejects tampered gate receipts even with explicit trust root."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        receipt = self._create_receipt(signer="alice@corp.com")
        tampered = dict(receipt)
        tampered["signer"] = "eve@corp.com"

        with self.assertRaises(LedgerError):
            self.ledger.record_human_gate_receipt(
                tampered,
                allowed_signers_path=self.allowed_signers_path,
            )

    # ------------------------------------------------------------------
    # 6. Socket IPC APPROVE_GATE End-to-End
    # ------------------------------------------------------------------

    def test_ipc_socket_approve_gate_e2e(self) -> None:
        """Full IPC socket test: ActuatorServer handles APPROVE_GATE with explicit trust root and no env."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        sock_dir = os.path.join(self.tmpdir, "ipc")
        os.makedirs(sock_dir, mode=0o770, exist_ok=True)
        os.chmod(sock_dir, 0o770)
        sock_path = os.path.join(sock_dir, "actuator.sock")

        receipt = self._create_receipt(signer="captain")
        self._write_receipt_file(receipt)

        allowed_client_uid = os.geteuid() + 100

        # Ensure ledger files adhere to 0600 mode for authority verification
        for fname in ("ledger.db", "ledger.db-wal", "ledger.db-shm"):
            p = os.path.join(self.state_dir, fname)
            if os.path.exists(p):
                os.chmod(p, 0o600)

        server = ActuatorServer(
            ledger=self.ledger,
            socket_path=sock_path,
            allowed_client_uid=allowed_client_uid,
            expected_socket_gid=os.getegid(),
            gate_receipt_dir=self.gate_dir,
            allowed_signers_path=self.allowed_signers_path,
        )
        server.start()

        def fake_getsockopt(level: int, optname: int, *args: Any, **kwargs: Any) -> Any:
            if level == socket.SOL_SOCKET and optname == _SO_PEERCRED:
                return struct.pack("3i", os.getpid(), allowed_client_uid, os.getegid())
            return b""

        # Run connection handler in thread
        server_thread = threading.Thread(target=server.handle_one_connection, daemon=True)
        server_thread.start()

        try:
            with patch.object(socket.socket, "getsockopt", side_effect=fake_getsockopt):
                client = ActuatorClient(sock_path)
                resp = client.approve_gate(self.packet_sha)
                self.assertEqual(resp["status"], "OK")
                self.assertEqual(resp["state"], "APPROVED")
                self.assertTrue(resp.get("gate_verified"))
                self.assertEqual(resp.get("signer"), "captain")
        finally:
            server.close()
            server_thread.join(timeout=1.0)

        # Ledger state verified
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "APPROVED")
        gate_rec = self.ledger.get_human_gate_receipt(self.packet_sha)
        self.assertIsNotNone(gate_rec)
        self.assertEqual(gate_rec["signer"], "captain")

    # ------------------------------------------------------------------
    # 7. CLI approve and approve-gate propagation
    # ------------------------------------------------------------------

    def test_cli_approve_gate_with_explicit_allowed_signers(self) -> None:
        """CLI approve-gate succeeds with --allowed-signers when env is absent."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        receipt = self._create_receipt(signer="charlie@corp.com")
        receipt_path = os.path.join(self.tmpdir, "gate.json")
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        stdout_buf = io.StringIO()
        with redirect_stdout(stdout_buf):
            cli_main([
                "approve-gate",
                self.packet_sha,
                "--receipt",
                receipt_path,
                "--allowed-signers",
                self.allowed_signers_path,
                "--state-dir",
                self.state_dir,
            ])

        out = stdout_buf.getvalue()
        self.assertIn("STATE=APPROVED", out)
        self.assertIn("APPROVER=charlie@corp.com", out)
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "APPROVED")

    def test_cli_approve_with_receipt_and_explicit_allowed_signers(self) -> None:
        """CLI approve succeeds with --receipt and --allowed-signers when env is absent."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        receipt = self._create_receipt(signer="bob@corp.com")
        receipt_path = os.path.join(self.tmpdir, "gate.json")
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        stdout_buf = io.StringIO()
        with redirect_stdout(stdout_buf):
            cli_main([
                "approve",
                self.packet_sha,
                "--receipt",
                receipt_path,
                "--allowed-signers",
                self.allowed_signers_path,
                "--state-dir",
                self.state_dir,
            ])

        out = stdout_buf.getvalue()
        self.assertIn("STATE=APPROVED", out)
        self.assertIn("APPROVER=bob@corp.com", out)
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "APPROVED")

    def test_cli_approve_gate_fails_without_allowed_signers_when_env_absent(self) -> None:
        """CLI approve-gate fails closed if --allowed-signers is omitted and env is absent."""
        self.assertNotIn("HARMONY_ALLOWED_SIGNERS_PATH", os.environ)

        receipt = self._create_receipt(signer="alice@corp.com")
        receipt_path = os.path.join(self.tmpdir, "gate.json")
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        stderr_buf = io.StringIO()
        with redirect_stderr(stderr_buf), self.assertRaises(SystemExit) as cm:
            cli_main([
                "approve-gate",
                self.packet_sha,
                "--receipt",
                receipt_path,
                "--state-dir",
                self.state_dir,
            ])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")


if __name__ == "__main__":
    unittest.main()
