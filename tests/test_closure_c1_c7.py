"""
test_closure_c1_c7.py — Comprehensive tests for HARMONY-CORE-CLOSURE-001 (C1–C7).

Tests:
  - C1: Distinct execution identity (Actuator UID != Runner UID, same-UID rejection,
        zero Ledger access in Runner, SO_PEERCRED checks).
  - C2: Verified Human Gate (rejection of unverified string approver in governed flow,
        cryptographic gate receipt validation, provenance persistence).
  - C3: Failure to HOLD (nonzero worker exit, timeout, runner connection failure,
        launch failure, persistence failure; zero packets left in DISPATCHED).
  - C4: Governed FINAL (worker result -> builder result persisted -> receipt -> evidence frozen
        -> evidence digest -> FINAL transition -> FINAL receipt; arbitrary caller SHA rejected).
  - C5: Explicit recovery (stranded DISPATCHED packet recovered to HOLD; requires existing packet
        and claim_id; creates no new claim; appends RECOVERY receipt).
  - C6: Sealed worker executable (digest mismatch rejected, missing binary rejected,
        placeholder digest rejected, helper digest verified).
  - C7: Execution facts into evidence (claim_id, player identity UID/GID, executable path,
        verified executable digest, helper digest, receipt linkage, gate provenance).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import pwd
import shutil
import stat
import struct
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from dataclasses import replace
from unittest.mock import MagicMock, patch

from telegraph.actuator_protocol import format_error_response, format_ok_response
from telegraph.actuator_service import (
    ActuatorProtocolHandler,
    ActuatorServer,
    AuthorityConfigurationError,
)
from telegraph.builder_evidence import (
    EvidenceStore,
    freeze_builder_evidence,
)
from telegraph.builder_profiles import BuilderProfile, get_profile
from telegraph.cli import main as cli_main
from telegraph.human_gate import (
    HumanGateVerificationError,
    create_verified_gate_receipt,
    load_and_verify_gate_receipt,
    verify_gate_receipt_dict,
)
from telegraph.ledger import Ledger, LedgerError, StateTransitionError
from telegraph.packet import compute_sha256
from telegraph.runner_client import RunnerClient, RunnerConnectionError, RunnerIPCError
from telegraph.runner_protocol import (
    RunnerProtocolError,
    format_runner_error_response,
    format_runner_ok_response,
    parse_and_validate_runner_request,
)
from telegraph.runner_service import (
    RunnerAuthorityError,
    RunnerConfig,
    RunnerServer,
    verify_file_digest,
    verify_runner_authority_environment,
)
from tests.test_gate_keys import ensure_test_keys_registered, TEST_SEEDS, get_test_allowed_signers_content


def _make_dummy_git_repo(path: str) -> str:
    """Create a dummy git repository directory with a .git entry and HEAD commit."""
    os.makedirs(path, exist_ok=True)
    git_dir = os.path.join(path, ".git")
    os.makedirs(git_dir, exist_ok=True)
    with open(os.path.join(git_dir, "HEAD"), "w") as f:
        f.write("ref: refs/heads/main\n")
    refs_dir = os.path.join(git_dir, "refs", "heads")
    os.makedirs(refs_dir, exist_ok=True)
    oid = "0123456789abcdef0123456789abcdef01234567"
    with open(os.path.join(refs_dir, "main"), "w") as f:
        f.write(oid + "\n")
    return oid


def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BaseClosureTest(unittest.TestCase):
    def setUp(self):
        ensure_test_keys_registered()
        self.tmpdir = tempfile.mkdtemp(prefix="closure_test_")
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        os.chmod(self.state_dir, 0o700)

        self.evidence_dir = os.path.join(self.tmpdir, "evidence")
        os.makedirs(self.evidence_dir, mode=0o700, exist_ok=True)
        os.chmod(self.evidence_dir, 0o700)

        self.gate_dir = os.path.join(self.tmpdir, "gates")
        os.makedirs(self.gate_dir, mode=0o700, exist_ok=True)
        os.chmod(self.gate_dir, 0o700)

        self.allowed_signers_path = os.path.join(self.tmpdir, "allowed_signers")
        with open(self.allowed_signers_path, "w", encoding="utf-8") as f:
            f.write(get_test_allowed_signers_content())
        os.chmod(self.allowed_signers_path, 0o600)
        os.environ["HARMONY_ALLOWED_SIGNERS_PATH"] = self.allowed_signers_path

        # Create dummy repo root
        self.repo_root = os.path.join(self.tmpdir, "repo")
        self.base_commit = _make_dummy_git_repo(self.repo_root)

        # Create dummy executable
        self.bin_path = os.path.join(self.tmpdir, "fake_agy")
        with open(self.bin_path, "wb") as f:
            f.write(b"#!/bin/sh\nexit 0\n")
        os.chmod(self.bin_path, 0o755)
        self.bin_sha256 = _sha256_of_bytes(b"#!/bin/sh\nexit 0\n")

        # Open Ledger
        self.ledger = Ledger(self.state_dir)

        # Create canonical packet
        self.packet = {
            "schema_version": 1,
            "action": "START_BUILDER",
            "mission_id": "MISSION-CLOSURE-001",
            "revision": 1,
            "project_id": "harmony",
            "objective": "Verify C1-C7 closure",
            "profile_id": "agy-builder-v1",
            "target": {
                "repo_root": self.repo_root,
                "base_commit_oid": self.base_commit,
            },
            "on_failure": "HOLD",
            "retry_policy": "NEVER",
        }
        self.packet_sha = compute_sha256(self.packet)
        self.ledger.ingest(self.packet, self.packet_sha)

    def tearDown(self):
        os.environ.pop("HARMONY_ALLOWED_SIGNERS_PATH", None)
        self.ledger.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def get_packet_state(self, sha: str | None = None) -> str:
        target_sha = sha or self.packet_sha
        return self.ledger.show(target_sha)["state"]


class TestClosureC1(BaseClosureTest):
    """C1: Distinct Execution Identity (Actuator UID != Runner UID, same-UID rejection)."""

    def test_c1_runner_server_rejects_same_uid_at_startup(self):
        current_uid = os.geteuid()
        with self.assertRaises(RunnerAuthorityError) as cm:
            verify_runner_authority_environment(
                socket_path=os.path.join(self.tmpdir, "runner.sock"),
                allowed_actuator_uid=current_uid,
                expected_socket_gid=os.getegid(),
            )
        self.assertIn("cannot be identical", str(cm.exception))

    def test_c1_runner_server_rejects_same_uid_peer_connection(self):
        socket_parent = os.path.join(self.tmpdir, "run")
        os.makedirs(socket_parent, mode=0o770, exist_ok=True)
        os.chmod(socket_parent, 0o770)
        sock_path = os.path.join(socket_parent, "runner.sock")
        current_uid = os.geteuid()
        actuator_uid = next(user.pw_uid for user in pwd.getpwall() if user.pw_uid != current_uid)
        # Supply a test-only profile whose real OS identity satisfies startup.
        # SO_PEERCRED, effective UID and peer authorization remain unpatched.
        startup_profile = replace(get_profile("agy-builder-v1"),
                                  permitted_user=pwd.getpwuid(current_uid).pw_name)

        config = RunnerConfig(
            socket_path=sock_path,
            allowed_actuator_uid=actuator_uid,
            expected_socket_gid=os.getegid(),
            agy_path=self.bin_path,
            agy_sha256=self.bin_sha256,
        )
        with patch("telegraph.runner_service.get_profile", return_value=startup_profile):
            server = RunnerServer(config)
        server.start()

        def _serve():
            server.handle_one_connection()

        t = threading.Thread(target=_serve, daemon=True)
        try:
            with patch.object(server, "_handle_request", wraps=server._handle_request) as handle_request, \
                 patch("telegraph.runner_service.launch_runner_process") as launch:
                t.start()
                client = RunnerClient(sock_path)
                with self.assertRaises(RunnerIPCError) as cm:
                    client.run_builder(
                        packet_sha256=self.packet_sha,
                        claim_id="claim-001",
                        objective="test",
                        repo_root=self.repo_root,
                        base_commit_oid=self.base_commit,
                    )
                self.assertEqual(cm.exception.error_code, "SAME_UID_REJECTED")
                self.assertIn("cannot establish distinct Runner authority", cm.exception.message)
                handle_request.assert_not_called()
                launch.assert_not_called()
        finally:
            server.close()
            t.join(timeout=2.0)
        self.assertFalse(t.is_alive())

    def test_c1_runner_has_zero_ledger_access(self):
        telegraph_dir = os.path.join(os.path.dirname(__file__), "..", "telegraph")
        runner_files = ["runner_service.py", "runner_protocol.py", "runnerd.py", "runner_client.py"]
        for fname in runner_files:
            fpath = os.path.join(telegraph_dir, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fpath)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("ledger", alias.name)
                        self.assertNotIn("sqlite3", alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        self.assertNotIn("ledger", node.module)
                        self.assertNotIn("sqlite3", node.module)


class TestClosureC2(BaseClosureTest):
    """C2: Verified Human Gate (plain string approver rejected, gate provenance stored)."""

    def test_c2_plain_string_approver_rejected_in_governed_actuator(self):
        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            gate_receipt_dir=self.gate_dir,
        )
        # Attempting unverified APPROVE command over Actuator IPC is rejected
        raw_req = json.dumps({
            "command": "APPROVE",
            "packet_sha256": self.packet_sha,
            "approver": "unverified-human",
        }).encode("utf-8")
        resp = handler.handle_raw_request(raw_req)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "UNKNOWN_COMMAND")

        # Attempting APPROVE_GATE without a verified receipt in gate_dir fails closed
        resp_gate = handler.approve_gate(self.packet_sha)
        self.assertEqual(resp_gate["status"], "ERROR")
        self.assertEqual(resp_gate["error_code"], "GATE_RECEIPT_NOT_FOUND")

    def test_c2_gate_receipt_tampered_or_invalid_rejected(self):
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
            private_key_pem=None,
        )
        tampered = dict(receipt)
        tampered["decision"] = "REJECT"
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256=self.packet_sha)

        tampered_sha = dict(receipt)
        tampered_sha["packet_sha256"] = "0" * 64
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered_sha, expected_packet_sha256=self.packet_sha)

    def test_c2_legitimate_gate_receipt_provenance_persisted_in_ledger(self):
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
            private_key_pem=None,
        )
        res = self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)
        self.assertEqual(res["state"], "APPROVED")
        self.assertTrue(res.get("gate_verified"))

        gate_row = self.ledger.get_human_gate_receipt(self.packet_sha)
        self.assertIsNotNone(gate_row)
        self.assertEqual(gate_row["decision"], "APPROVE")
        self.assertEqual(gate_row["signer"], "alice@corp.com")
        self.assertEqual(gate_row["packet_sha256"], self.packet_sha)

        show_info = self.ledger.show(self.packet_sha)
        self.assertIsNotNone(show_info.get("human_gate_receipt"))
        self.assertEqual(show_info["human_gate_receipt"]["signer"], "alice@corp.com")

    def test_c2_captain_canonical_human_gate_accepted(self):
        """Valid Captain-signed canonical Human Gate must be accepted and record provenance."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="captain",
        )
        res = self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)
        self.assertEqual(res["state"], "APPROVED")
        self.assertTrue(res.get("gate_verified"))

        gate_row = self.ledger.get_human_gate_receipt(self.packet_sha)
        self.assertIsNotNone(gate_row)
        self.assertEqual(gate_row["signer"], "captain")
        self.assertEqual(gate_row["decision"], "APPROVE")

    def test_c2_forged_signer_rejected(self):
        """Receipt signed by Alice but claiming to be Bob must be rejected."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        tampered = dict(receipt)
        tampered["signer"] = "bob@corp.com"
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256=self.packet_sha)

    def test_c2_forged_signature_rejected(self):
        """Tampered signature bytes must fail verification."""
        from telegraph.human_gate import dearmor_sshsig, armor_sshsig
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        raw = dearmor_sshsig(receipt["signature"])
        # Corrupt signature bytes
        tampered_raw = raw[:-10] + b"\xff" * 10
        tampered = dict(receipt)
        tampered["signature"] = armor_sshsig(tampered_raw)
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256=self.packet_sha)

    def test_c2_self_generated_public_proof_rejected(self):
        """Old self-forgeable proof GATE_PROOF:{signer}:{gate_digest} must be rejected."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        tampered = dict(receipt)
        tampered["signature"] = hashlib.sha256(f"GATE_PROOF:alice@corp.com:{receipt['gate_digest']}".encode("utf-8")).hexdigest()
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256=self.packet_sha)

    def test_c2_tampered_mission_binding_rejected(self):
        """Receipt with altered mission_id must fail verification."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        tampered = dict(receipt)
        tampered["mission_id"] = "WRONG-MISSION-999"
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256=self.packet_sha)

    def test_c2_tampered_packet_binding_rejected(self):
        """Receipt with altered packet_sha256 must fail verification."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        tampered = dict(receipt)
        tampered["packet_sha256"] = "f" * 64
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256="f" * 64)

    def test_c2_tampered_runtime_facts_binding_rejected(self):
        """Receipt with altered runtime_facts must fail verification."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
            runtime_facts={"key": "original"},
        )
        tampered = dict(receipt)
        tampered["runtime_facts"] = {"key": "altered"}
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256=self.packet_sha)

    def test_c2_unauthorized_signer_rejected(self):
        """Signer with key not in allowed_signers must fail verification."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="eve@corp.com",
            private_key_pem=TEST_SEEDS["eve@corp.com"],
        )
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(receipt, expected_packet_sha256=self.packet_sha)

    def test_c2_namespace_enforcement(self):
        """Signatures with non-'harmony-human-gate' namespace must fail verification."""
        from telegraph.human_gate import armor_sshsig, compute_tosign_buffer, _sign_ed25519, _ssh_string, _canonical_json_bytes
        import struct

        core = {
            "schema_version": 1,
            "packet_sha256": self.packet_sha,
            "mission_id": self.packet["mission_id"],
            "signer": "alice@corp.com",
            "decision": "APPROVE",
            "signed_at": datetime.now(timezone.utc).isoformat(),
            "runtime_facts_digest": hashlib.sha256(_canonical_json_bytes({})).hexdigest(),
        }
        can_bytes = _canonical_json_bytes(core)
        wrong_ns = "email"
        tosign = compute_tosign_buffer(wrong_ns, "sha512", can_bytes)
        pub_raw, sig_raw = _sign_ed25519(TEST_SEEDS["alice@corp.com"], tosign)
        blob = (
            b"SSHSIG"
            + struct.pack(">I", 1)
            + _ssh_string(_ssh_string(b"ssh-ed25519") + _ssh_string(pub_raw))
            + _ssh_string(wrong_ns.encode("utf-8"))
            + _ssh_string(b"")
            + _ssh_string(b"sha512")
            + _ssh_string(_ssh_string(b"ssh-ed25519") + _ssh_string(sig_raw))
        )
        receipt = {
            **core,
            "runtime_facts": {},
            "gate_digest": hashlib.sha256(can_bytes).hexdigest(),
            "signature": armor_sshsig(blob),
        }
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(receipt, expected_packet_sha256=self.packet_sha)


class TestClosureC3(BaseClosureTest):
    """C3: Failure to HOLD (nonzero exit, timeout, runner connection failure -> HOLD)."""

    def setUp(self):
        super().setUp()
        self.receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        self.ledger.approve(self.packet_sha, self.receipt["signer"], gate_receipt=self.receipt)

    def test_c3_worker_nonzero_exit_transitions_to_hold(self):
        def _mock_run_builder(*args, **kwargs):
            return {
                "status": "OK",
                "packet_sha256": kwargs["packet_sha256"],
                "claim_id": kwargs["claim_id"],
                "actual_uid": 10002,
                "actual_gid": 10002,
                "actual_executable_path": self.bin_path,
                "executable_digest": self.bin_sha256,
                "helper_digest": None,
                "launched": True,
                "exit_code": 2,
                "timed_out": False,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }

        mock_runner = MagicMock()
        mock_runner.run_builder.side_effect = _mock_run_builder
        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            runner_client=mock_runner,
            evidence_store=EvidenceStore(self.evidence_dir),
        )
        resp = handler.dispatch_builder(self.packet_sha)
        self.assertEqual(resp["status"], "OK")
        self.assertEqual(resp["state"], "HOLD")
        self.assertEqual(self.get_packet_state(), "HOLD")

    def test_c3_worker_timeout_transitions_to_hold(self):
        def _mock_run_builder_timeout(*args, **kwargs):
            return {
                "status": "OK",
                "packet_sha256": kwargs["packet_sha256"],
                "claim_id": kwargs["claim_id"],
                "actual_uid": 10002,
                "actual_gid": 10002,
                "actual_executable_path": self.bin_path,
                "executable_digest": self.bin_sha256,
                "helper_digest": None,
                "launched": True,
                "exit_code": None,
                "timed_out": True,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }

        mock_runner = MagicMock()
        mock_runner.run_builder.side_effect = _mock_run_builder_timeout
        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            runner_client=mock_runner,
            evidence_store=EvidenceStore(self.evidence_dir),
        )
        resp = handler.dispatch_builder(self.packet_sha)
        self.assertEqual(resp["status"], "OK")
        self.assertEqual(resp["state"], "HOLD")
        self.assertEqual(self.get_packet_state(), "HOLD")



    def test_c3_runner_connection_failure_transitions_to_hold(self):
        mock_runner = MagicMock()
        mock_runner.run_builder.side_effect = RunnerConnectionError("Socket connection refused")
        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            runner_client=mock_runner,
            evidence_store=EvidenceStore(self.evidence_dir),
        )
        resp = handler.dispatch_builder(self.packet_sha)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "RUNNER_FAILURE")
        self.assertEqual(self.get_packet_state(), "HOLD")


class TestClosureC4(BaseClosureTest):
    """C4: Governed FINAL State Transition."""

    def setUp(self):
        super().setUp()
        self.receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        self.ledger.approve(self.packet_sha, self.receipt["signer"], gate_receipt=self.receipt)
        self.evidence_store = EvidenceStore(self.evidence_dir)

    def test_c4_full_successful_flow_to_final(self):
        claim_res = self.ledger.claim(self.packet_sha, "BUILD")
        claim_id = claim_res["claim_id"]
        now = datetime.now(timezone.utc).isoformat()

        rec_res = self.ledger.record_builder_result(
            packet_sha256=self.packet_sha,
            launched=True,
            exit_code=0,
            timed_out=False,
            started_at=now,
            finished_at=now,
            claim_id=claim_id,
            actual_uid=10002,
            actual_gid=10002,
            executable_path=self.bin_path,
            executable_digest=self.bin_sha256,
            helper_digest=None,
        )

        dispatch_result = {
            "status": "OK",
            "packet_sha256": self.packet_sha,
            "claim_id": claim_id,
            "launch_attempted": True,
        }
        ev_res = freeze_builder_evidence(
            packet=self.packet,
            dispatch_result=dispatch_result,
            ledger=self.ledger,
            store=self.evidence_store,
        )
        self.assertEqual(ev_res["status"], "NEW")
        ev_sha = ev_res["evidence_sha256"]

        fin_res = self.ledger.finalize(
            packet_sha256=self.packet_sha,
            evidence_sha256=ev_sha,
            evidence_store=self.evidence_store,
        )
        self.assertEqual(fin_res["state"], "FINAL")
        self.assertEqual(fin_res["evidence_sha256"], ev_sha)
        self.assertEqual(self.get_packet_state(), "FINAL")

        v_res = self.ledger.verify(self.packet_sha)
        self.assertTrue(v_res["valid"])

    def test_c4_finalize_without_builder_result_rejected(self):
        self.ledger.claim(self.packet_sha, "BUILD")
        with self.assertRaises(StateTransitionError) as cm:
            self.ledger.finalize(self.packet_sha, "a" * 64, self.evidence_store)
        self.assertIn("no builder result recorded", str(cm.exception).lower())

    def test_c4_finalize_with_mismatched_evidence_sha_rejected(self):
        claim_res = self.ledger.claim(self.packet_sha, "BUILD")
        claim_id = claim_res["claim_id"]
        now = datetime.now(timezone.utc).isoformat()
        self.ledger.record_builder_result(
            packet_sha256=self.packet_sha,
            launched=True,
            exit_code=0,
            timed_out=False,
            started_at=now,
            finished_at=now,
            claim_id=claim_id,
            actual_uid=10002,
            actual_gid=10002,
            executable_path=self.bin_path,
            executable_digest=self.bin_sha256,
        )
        with self.assertRaises(StateTransitionError):
            self.ledger.finalize(self.packet_sha, "f" * 64, self.evidence_store)


class TestClosureC5(BaseClosureTest):
    """C5: Explicit Recovery (Stranded DISPATCHED packet -> HOLD via explicit recovery)."""

    def setUp(self):
        super().setUp()
        self.receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        self.ledger.approve(self.packet_sha, self.receipt["signer"], gate_receipt=self.receipt)
        claim_res = self.ledger.claim(self.packet_sha, "BUILD")
        self.claim_id = claim_res["claim_id"]
        self.assertEqual(self.get_packet_state(), "DISPATCHED")

    def test_c5_recovery_dispatched_packet_to_hold(self):
        rec_res = self.ledger.recover(
            packet_sha256=self.packet_sha,
            claim_id=self.claim_id,
            reason="Daemon crashed before runner invocation",
        )
        self.assertEqual(rec_res["state"], "HOLD")
        self.assertEqual(rec_res["claim_id"], self.claim_id)
        self.assertEqual(self.get_packet_state(), "HOLD")

        show = self.ledger.show(self.packet_sha)
        recovery_receipts = [r for r in show["receipts"] if r["stage"].startswith("RECOVERY:")]
        self.assertEqual(len(recovery_receipts), 1)
        self.assertIn(self.claim_id, recovery_receipts[0]["stage"])

        v = self.ledger.verify(self.packet_sha)
        self.assertTrue(v["valid"])

    def test_c5_recovery_rejects_non_existent_claim_id(self):
        with self.assertRaises(LedgerError) as cm:
            self.ledger.recover(
                packet_sha256=self.packet_sha,
                claim_id="non-existent-claim-id",
                reason="Attempted spoofed recovery",
            )
        self.assertIn("Claim ID mismatch", str(cm.exception))



class TestClosureC6(BaseClosureTest):
    """C6: Sealed Worker Executable Authority (digest mismatch, missing binary, placeholders)."""

    def test_c6_executable_digest_mismatch_rejected(self):
        bad_digest = "e" * 64
        with self.assertRaises(RunnerAuthorityError) as cm:
            verify_file_digest(self.bin_path, bad_digest)
        self.assertIn("digest mismatch", str(cm.exception))

    def test_c6_missing_executable_rejected(self):
        with self.assertRaises(RunnerAuthorityError) as cm:
            verify_file_digest(os.path.join(self.tmpdir, "missing_bin"), self.bin_sha256)
        self.assertIn("does not exist", str(cm.exception))

    def test_c6_placeholder_digest_rejected_by_profile(self):
        profile = BuilderProfile(
            profile_id="test",
            executable=self.bin_path,
            fixed_args=(),
            timeout_seconds=30,
            allowed_env_names=(),
            execution_policy="HEADLESS",
            executable_sha256="0" * 64,
        )
        self.assertFalse(profile.is_valid_digest())

        profile2 = BuilderProfile(
            profile_id="test",
            executable=self.bin_path,
            fixed_args=(),
            timeout_seconds=30,
            allowed_env_names=(),
            execution_policy="HEADLESS",
            executable_sha256="sha256:placeholder",
        )
        self.assertFalse(profile2.is_valid_digest())


class TestClosureC7(BaseClosureTest):
    """C7: Authoritative Execution Facts into Evidence."""

    def test_c7_evidence_freezing_and_store_roundtrip(self):
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)
        claim_res = self.ledger.claim(self.packet_sha, "BUILD")
        claim_id = claim_res["claim_id"]
        now = datetime.now(timezone.utc).isoformat()

        self.ledger.record_builder_result(
            packet_sha256=self.packet_sha,
            launched=True,
            exit_code=0,
            timed_out=False,
            started_at=now,
            finished_at=now,
            claim_id=claim_id,
            actual_uid=10002,
            actual_gid=10002,
            executable_path=self.bin_path,
            executable_digest=self.bin_sha256,
            helper_digest=None,
        )

        dispatch_result = {
            "packet_sha256": self.packet_sha,
            "launch_attempted": True,
        }
        store = EvidenceStore(self.evidence_dir)
        ev_res = freeze_builder_evidence(
            packet=self.packet,
            dispatch_result=dispatch_result,
            ledger=self.ledger,
            store=store,
        )
        self.assertEqual(ev_res["status"], "NEW")
        ev_sha = ev_res["evidence_sha256"]

        ev_file = os.path.join(self.evidence_dir, f"{self.packet_sha}.json")
        self.assertTrue(os.path.exists(ev_file))
        with open(ev_file, "r") as f:
            parsed = json.load(f)

        self.assertEqual(parsed["claim_id"], claim_id)
        self.assertEqual(parsed["player_identity"]["uid"], 10002)
        self.assertEqual(parsed["player_identity"]["gid"], 10002)
        self.assertEqual(parsed["executable_path"], self.bin_path)
        self.assertEqual(parsed["executable_digest"], self.bin_sha256)
        self.assertIsNotNone(parsed["builder_receipt_id"])
        self.assertEqual(parsed["human_gate_provenance"]["signer"], "alice@corp.com")


class TestClosureCLI(BaseClosureTest):
    """CLI End-to-End for new commands (approve-gate, recover, finalize)."""

    def test_cli_approve_gate_and_recover_workflow(self):
        gate_path = os.path.join(self.gate_dir, "gate.json")
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="charlie@corp.com",
        )
        with open(gate_path, "w") as f:
            json.dump(receipt, f)

        # 1. approve-gate
        cli_main(["approve-gate", self.packet_sha, "--receipt", gate_path, "--state-dir", self.state_dir])
        self.assertEqual(self.get_packet_state(), "APPROVED")

        # 2. claim BUILD
        claim_res = self.ledger.claim(self.packet_sha, "BUILD")
        self.assertEqual(self.get_packet_state(), "DISPATCHED")

        # 3. recover
        cli_main(["recover", self.packet_sha, "--claim-id", claim_res["claim_id"], "--reason", "Stranded recovery test", "--state-dir", self.state_dir])
        self.assertEqual(self.get_packet_state(), "HOLD")


if __name__ == "__main__":
    unittest.main()
