"""
test_audit_blockers_b1_b4.py — Dedicated Regression Suite for HARMONY-CORE-CLOSURE-001 (B1-B4).

Tests:
  - B1: Remove governed legacy execution fallback
        (no direct builder_adapter launch; runner required; failure fails closed to HOLD).
  - B2: Verify human gate on IPC APPROVE_GATE
        (canonical Human Gate verification; fail closed on unsafe receipt dir authority / symlink / TOCTOU).
  - B3: FINAL requires real EvidenceStore
        (finalize() forbids absent store; exact frozen-byte digest verification; DISPATCHED claim required).
  - B4: Enforce BuilderProfile.permitted_user
        (dynamic OS identity resolution via pwd; Runner effective UID check at startup and before launch).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import pwd
import shutil
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from dataclasses import replace
from unittest.mock import MagicMock, patch

from telegraph.actuator_protocol import format_error_response, format_ok_response
from telegraph.actuator_service import (
    ActuatorProtocolHandler,
    ActuatorServer,
    AuthorityConfigurationError,
    _load_and_verify_gate_receipt_from_dir,
    open_actuator_ledger,
)
from telegraph.builder_evidence import EvidenceStore, freeze_builder_evidence
from telegraph.builder_profiles import BuilderProfile, get_profile, known_profile_ids
from telegraph.human_gate import (
    HumanGateVerificationError,
    create_verified_gate_receipt,
    load_and_verify_gate_receipt,
    verify_gate_receipt_dict,
)
from telegraph.ledger import Ledger, LedgerError, StateTransitionError
from telegraph.packet import compute_sha256
from telegraph.runner_client import RunnerClient, RunnerConnectionError
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
)
from tests.test_gate_keys import ensure_test_keys_registered, TEST_SEEDS, get_test_allowed_signers_content


def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_dummy_git_repo(path: str) -> str:
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


class BaseBlockerTest(unittest.TestCase):
    def setUp(self):
        ensure_test_keys_registered()
        self.tmpdir = tempfile.mkdtemp(prefix="blocker_test_")
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        os.chmod(self.state_dir, 0o700)

        self.evidence_dir = os.path.join(self.tmpdir, "evidence")
        os.makedirs(self.evidence_dir, mode=0o700, exist_ok=True)
        os.chmod(self.evidence_dir, 0o700)
        self.evidence_store = EvidenceStore(self.evidence_dir)

        self.gate_dir = os.path.join(self.tmpdir, "gates")
        os.makedirs(self.gate_dir, mode=0o700, exist_ok=True)
        os.chmod(self.gate_dir, 0o700)

        self.allowed_signers_path = os.path.join(self.tmpdir, "allowed_signers")
        with open(self.allowed_signers_path, "w", encoding="utf-8") as f:
            f.write(get_test_allowed_signers_content())
        os.chmod(self.allowed_signers_path, 0o600)
        os.environ["HARMONY_ALLOWED_SIGNERS_PATH"] = self.allowed_signers_path

        self.run_dir = os.path.join(self.tmpdir, "run")
        os.makedirs(self.run_dir, mode=0o770, exist_ok=True)
        os.chmod(self.run_dir, 0o770)

        self.repo_root = os.path.join(self.tmpdir, "repo")
        self.base_commit = _make_dummy_git_repo(self.repo_root)

        self.bin_path = os.path.join(self.tmpdir, "fake_agy")
        with open(self.bin_path, "wb") as f:
            f.write(b"#!/bin/sh\nexit 0\n")
        os.chmod(self.bin_path, 0o755)
        self.bin_sha256 = _sha256_of_bytes(b"#!/bin/sh\nexit 0\n")

        self.ledger = open_actuator_ledger(self.state_dir)

        self.packet = {
            "schema_version": 1,
            "action": "START_BUILDER",
            "mission_id": "MISSION-BLOCKER-001",
            "revision": 1,
            "project_id": "harmony",
            "objective": "Verify B1-B4 blocker fixes",
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

    def _create_and_write_gate_receipt(self, filename: str | None = None, signer: str = "alice@corp.com") -> dict:
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer=signer,
        )
        fname = filename or f"{self.packet_sha}.json"
        path = os.path.join(self.gate_dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)
        os.chmod(path, 0o600)
        return receipt


class TestB1RemoveLegacyFallback(BaseBlockerTest):
    """B1: The governed Actuator DISPATCH_BUILDER path must not fall back to direct builder_adapter."""

    def test_b1_no_legacy_builder_adapter_import_in_actuator_service(self):
        """Actuator service must not import builder_adapter.launch."""
        actuator_path = os.path.join(os.path.dirname(__file__), "..", "telegraph", "actuator_service.py")
        with open(actuator_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=actuator_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "builder_adapter" in node.module:
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "launch", "builder_adapter.launch must not be imported in actuator_service")

    def test_b1_governed_dispatch_without_runner_fails_closed_to_hold(self):
        """When Runner is not configured, governed dispatch fails closed to HOLD."""
        # Approve packet first
        receipt = self._create_and_write_gate_receipt()
        self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "APPROVED")

        # Create handler WITHOUT runner_client
        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            runner_client=None,
            evidence_store=self.evidence_store,
        )

        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": self.packet_sha}).encode("utf-8")
        resp = handler.handle_raw_request(req)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "RUNNER_NOT_CONFIGURED")
        # Invariant C3: execution failure after claim reaches HOLD
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "HOLD")

    def test_b1_runner_connection_failure_transitions_to_hold(self):
        """Runner connection failure transitions to HOLD (no direct fallback)."""
        receipt = self._create_and_write_gate_receipt()
        self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)

        mock_runner = MagicMock()
        mock_runner.run_builder.side_effect = RunnerConnectionError("Connection refused")

        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            runner_client=mock_runner,
            evidence_store=self.evidence_store,
        )

        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": self.packet_sha}).encode("utf-8")
        resp = handler.handle_raw_request(req)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "RUNNER_FAILURE")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "HOLD")

    def test_b1_runner_malformed_response_transitions_to_hold(self):
        """Malformed or error response from Runner transitions to HOLD."""
        receipt = self._create_and_write_gate_receipt()
        self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)

        mock_runner = MagicMock()
        mock_runner.run_builder.return_value = {"status": "ERROR", "message": "Crash in Runner"}

        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            runner_client=mock_runner,
            evidence_store=self.evidence_store,
        )

        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": self.packet_sha}).encode("utf-8")
        resp = handler.handle_raw_request(req)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "LAUNCH_FAILURE")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "HOLD")

    def test_b1_runner_result_identity_mismatch_transitions_to_hold(self):
        """Mismatched packet or claim from Runner transitions to HOLD."""
        receipt = self._create_and_write_gate_receipt()
        self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)

        mock_runner = MagicMock()
        mock_runner.run_builder.return_value = {
            "status": "OK",
            "packet_sha256": "0" * 64,  # mismatched
            "claim_id": "wrong-claim",
            "launched": True,
            "exit_code": 0,
            "timed_out": False,
        }

        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            runner_client=mock_runner,
            evidence_store=self.evidence_store,
        )

        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": self.packet_sha}).encode("utf-8")
        resp = handler.handle_raw_request(req)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "RESULT_IDENTITY_MISMATCH")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "HOLD")


class TestB2VerifyHumanGate(BaseBlockerTest):
    """B2: The IPC APPROVE_GATE path must cryptographically/deterministically verify the Human Gate receipt."""

    def test_b2_valid_gate_receipt_approved(self):
        """Canonical valid receipt approves packet and records provenance."""
        self._create_and_write_gate_receipt()
        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)

        resp = handler.approve_gate(self.packet_sha)
        self.assertEqual(resp["status"], "OK")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "APPROVED")

        provenance = self.ledger.get_human_gate_receipt(self.packet_sha)
        self.assertIsNotNone(provenance)
        self.assertEqual(provenance["signer"], "alice@corp.com")

    def test_b2_rejects_tampered_gate_digest(self):
        """Receipt with tampered gate_digest must be rejected."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        receipt["gate_digest"] = "f" * 64  # valid hex format but incorrect digest
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertIn("gate_digest mismatch", resp["message"])
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

    def test_b2_rejects_tampered_signer(self):
        """Receipt with altered signer (without matching gate_digest) must be rejected."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        receipt["signer"] = "eve@corp.com"
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

    def test_b2_rejects_tampered_runtime_facts(self):
        """Receipt with mismatched runtime_facts_digest must be rejected."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
            runtime_facts={"key": "val1"},
        )
        receipt["runtime_facts"] = {"key": "val2_tampered"}
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertIn("runtime_facts_digest mismatch", resp["message"])

    def test_b2_rejects_forged_signature(self):
        """Receipt with forged or altered signature must be rejected."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        receipt["signature"] = "0" * 64
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertIn("signature mismatch", resp["message"])

    def test_b2_rejects_symlink_dir(self):
        """Symlink gate_receipt_dir must fail closed."""
        symlink_dir = os.path.join(self.tmpdir, "gate_symlink")
        os.symlink(self.gate_dir, symlink_dir)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=symlink_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertIn("symlink ambiguity", resp["message"])

    def test_b2_rejects_symlink_receipt_file(self):
        """Symlink receipt file inside gate_receipt_dir must fail closed."""
        real_file = os.path.join(self.tmpdir, "real_receipt.json")
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        with open(real_file, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        symlink_file = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        os.symlink(real_file, symlink_file)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertIn("symlink ambiguity", resp["message"])

    def test_b2_rejects_world_writable_dir(self):
        """World-writable gate_receipt_dir must fail closed."""
        self._create_and_write_gate_receipt()
        os.chmod(self.gate_dir, 0o777)
        try:
            handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
            resp = handler.approve_gate(self.packet_sha)
            self.assertEqual(resp["status"], "ERROR")
            self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
            self.assertIn("world write access", resp["message"])
        finally:
            os.chmod(self.gate_dir, 0o700)

    def test_b2_rejects_world_writable_receipt_file(self):
        """World-writable receipt file must fail closed."""
        self._create_and_write_gate_receipt()
        target_file = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        os.chmod(target_file, 0o666)
        try:
            handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
            resp = handler.approve_gate(self.packet_sha)
            self.assertEqual(resp["status"], "ERROR")
            self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
            self.assertIn("world write access", resp["message"])
        finally:
            os.chmod(target_file, 0o600)

    def test_b2_ledger_approve_rejects_forged_receipt(self):
        """Direct call to ledger.approve with forged receipt must raise LedgerError."""
        forged = {
            "schema_version": 1,
            "packet_sha256": self.packet_sha,
            "mission_id": self.packet["mission_id"],
            "signer": "eve@corp.com",
            "decision": "APPROVE",
            "signed_at": datetime.now(timezone.utc).isoformat(),
            "runtime_facts_digest": "0" * 64,
            "gate_digest": "0" * 64,  # forged 64-char hex
            "signature": "0" * 64,
        }
        with self.assertRaises(LedgerError) as cm:
            self.ledger.approve(self.packet_sha, "eve@corp.com", gate_receipt=forged)
        self.assertIn("Gate receipt verification failed", str(cm.exception))

    def test_b2_captain_signed_receipt_approved(self):
        """A valid Captain-signed canonical Human Gate must be accepted."""
        receipt = self._create_and_write_gate_receipt(signer="captain")
        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)

        resp = handler.approve_gate(self.packet_sha)
        self.assertEqual(resp["status"], "OK")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "APPROVED")

        provenance = self.ledger.get_human_gate_receipt(self.packet_sha)
        self.assertIsNotNone(provenance)
        self.assertEqual(provenance["signer"], "captain")

    def test_b2_rejects_self_generated_public_proof(self):
        """Self-generated public proof (GATE_PROOF) is not authentication and must be rejected."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        # Supply old self-generated public input proof
        receipt["signature"] = _sha256_of_bytes(f"GATE_PROOF:alice@corp.com:{receipt['gate_digest']}".encode("utf-8"))
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

    def test_b2_rejects_untrusted_signer_key(self):
        """Signature with key not in allowed_signers must be rejected."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="eve@corp.com",
            private_key_pem=TEST_SEEDS["eve@corp.com"],
        )
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

    def test_b2_rejects_mismatched_namespace(self):
        """Signature with wrong namespace (not harmony-human-gate) must be rejected."""
        from telegraph.human_gate import armor_sshsig, compute_tosign_buffer, _sign_ed25519, _ssh_string, _canonical_json_bytes
        import struct

        core = {
            "schema_version": 1,
            "packet_sha256": self.packet_sha,
            "mission_id": self.packet["mission_id"],
            "signer": "alice@corp.com",
            "decision": "APPROVE",
            "signed_at": datetime.now(timezone.utc).isoformat(),
            "runtime_facts_digest": _sha256_of_bytes(_canonical_json_bytes({})),
        }
        can_bytes = _canonical_json_bytes(core)
        wrong_ns = "git"
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
            "gate_digest": _sha256_of_bytes(can_bytes),
            "signature": armor_sshsig(blob),
        }
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

    def test_b2_rejects_symlink_allowed_signers(self):
        """Symlink allowed_signers file must fail closed."""
        self._create_and_write_gate_receipt()
        real_as = os.path.join(self.tmpdir, "real_allowed_signers")
        with open(real_as, "w", encoding="utf-8") as f:
            f.write('# empty\n')
        symlink_as = os.path.join(self.gate_dir, "allowed_signers")
        os.symlink(real_as, symlink_as)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertIn("symlink ambiguity", resp["message"])

    def test_b2_rejects_world_writable_allowed_signers(self):
        """World-writable allowed_signers file must fail closed."""
        self._create_and_write_gate_receipt()
        as_file = os.path.join(self.gate_dir, "allowed_signers")
        with open(as_file, "w", encoding="utf-8") as f:
            f.write('# empty\n')
        os.chmod(as_file, 0o666)

        try:
            handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
            resp = handler.approve_gate(self.packet_sha)

            self.assertEqual(resp["status"], "ERROR")
            self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
            self.assertIn("world write access", resp["message"])
        finally:
            os.chmod(as_file, 0o600)

    def test_b2_rejects_substituted_receipt_packet_mismatch(self):
        """Receipt for different packet placed under candidate path must fail closed."""
        other_packet_sha = "0" * 64
        receipt = create_verified_gate_receipt(
            packet_sha256=other_packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")

    def test_b2_rejects_tampered_mission_id(self):
        """Tampered mission_id in receipt must fail closed."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        receipt["mission_id"] = "OTHER-MISSION-002"
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "READY")


class TestB3FinalRequiresEvidenceStore(BaseBlockerTest):
    """B3: ledger.finalize() must require a real EvidenceStore and exact byte digest."""

    def setUp(self):
        super().setUp()
        receipt = self._create_and_write_gate_receipt()
        self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)
        claim_res = self.ledger.claim(self.packet_sha, "BUILD")
        self.claim_id = claim_res["claim_id"]
        now = datetime.now(timezone.utc).isoformat()
        self.ledger.record_builder_result(
            packet_sha256=self.packet_sha,
            launched=True,
            exit_code=0,
            timed_out=False,
            started_at=now,
            finished_at=now,
            claim_id=self.claim_id,
            actual_uid=10001,
            actual_gid=10001,
            executable_path=self.bin_path,
            executable_digest=self.bin_sha256,
        )
        self.dispatch_result = {
            "packet_sha256": self.packet_sha,
            "launch_attempted": True,
        }

    def test_b3_finalize_rejects_absent_evidence_store(self):
        """ledger.finalize() must reject None evidence_store."""
        with self.assertRaises(StateTransitionError) as cm:
            self.ledger.finalize(self.packet_sha, "a" * 64, evidence_store=None)
        self.assertIn("evidence_store is mandatory", str(cm.exception))

    def test_b3_finalize_rejects_missing_evidence_file(self):
        """ledger.finalize() must reject when evidence file does not exist in store."""
        fake_sha = "b" * 64
        with self.assertRaises(StateTransitionError) as cm:
            self.ledger.finalize(self.packet_sha, fake_sha, evidence_store=self.evidence_store)
        self.assertIn("does not exist", str(cm.exception))

    def test_b3_finalize_rejects_empty_evidence_file(self):
        """ledger.finalize() must reject empty evidence file."""
        ev_file = os.path.join(self.evidence_dir, f"{self.packet_sha}.json")
        with open(ev_file, "wb") as f:
            pass  # 0 bytes
        empty_sha = _sha256_of_bytes(b"")
        with self.assertRaises(StateTransitionError) as cm:
            self.ledger.finalize(self.packet_sha, empty_sha, evidence_store=self.evidence_store)
        self.assertIn("is empty", str(cm.exception))

    def test_b3_finalize_rejects_mismatched_evidence_digest(self):
        """ledger.finalize() must verify computed SHA256 of exact bytes equals evidence_sha256."""
        ev_file = os.path.join(self.evidence_dir, f"{self.packet_sha}.json")
        data = b'{"test": "data"}'
        with open(ev_file, "wb") as f:
            f.write(data)
        actual_sha = _sha256_of_bytes(data)
        tampered_sha = "c" * 64

        with self.assertRaises(StateTransitionError) as cm:
            self.ledger.finalize(self.packet_sha, tampered_sha, evidence_store=self.evidence_store)
        self.assertIn("digest mismatch", str(cm.exception))

    def test_b3_finalize_succeeds_with_matching_frozen_bytes(self):
        """ledger.finalize() succeeds when exact bytes match and binds receipt."""
        ev_res = freeze_builder_evidence(
            packet=self.packet,
            dispatch_result=self.dispatch_result,
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
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "FINAL")

        verify_res = self.ledger.verify(self.packet_sha)
        self.assertTrue(verify_res["valid"])

    def test_b3_dispatch_without_evidence_store_fails_closed_to_hold(self):
        """Actuator dispatch without EvidenceStore must fail closed to HOLD on completion."""
        # Create second packet
        pkt2 = dict(self.packet)
        pkt2["mission_id"] = "MISSION-BLOCKER-002"
        sha2 = compute_sha256(pkt2)
        self.ledger.ingest(pkt2, sha2)
        rec = create_verified_gate_receipt(packet_sha256=sha2, mission_id=pkt2["mission_id"], signer="alice@corp.com")
        self.ledger.approve(sha2, rec["signer"], gate_receipt=rec)

        def _mock_run(**kwargs):
            return {
                "status": "OK",
                "packet_sha256": kwargs["packet_sha256"],
                "claim_id": kwargs["claim_id"],
                "actual_uid": 10001,
                "actual_gid": 10001,
                "actual_executable_path": self.bin_path,
                "executable_digest": self.bin_sha256,
                "helper_digest": None,
                "launched": True,
                "exit_code": 0,
                "timed_out": False,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }

        mock_runner = MagicMock()
        mock_runner.run_builder.side_effect = _mock_run

        handler = ActuatorProtocolHandler(
            ledger=self.ledger,
            runner_client=mock_runner,
            evidence_store=None,  # No evidence store
        )

        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": sha2}).encode("utf-8")
        resp = handler.handle_raw_request(req)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "EVIDENCE_STORE_NOT_CONFIGURED")
        self.assertEqual(self.ledger.show(sha2)["state"], "HOLD")


class TestB4EnforcePermittedUser(BaseBlockerTest):
    """B4: BuilderProfile.permitted_user must be resolved via OS identity database and enforced."""

    def test_b4_profile_resolve_permitted_uid(self):
        """BuilderProfile.resolve_permitted_uid() dynamically resolves user via pwd."""
        production_profile = get_profile("agy-builder-v1")
        current_identity = pwd.getpwuid(os.geteuid())
        profile = replace(production_profile, permitted_user=current_identity.pw_name)
        # Exercise real OS resolution without requiring a production host account.
        self.assertEqual(profile.resolve_permitted_uid(), current_identity.pw_uid)

    def test_b4_production_profiles_preserve_runner_identity(self):
        """Production identity is a configuration contract, not a unit host prerequisite."""
        for profile_id in ("agy-builder-v1", "agy-closure-v1"):
            with self.subTest(profile_id=profile_id):
                self.assertEqual(get_profile(profile_id).permitted_user, "harmony")

    def test_b4_profile_resolve_nonexistent_user_raises(self):
        """BuilderProfile with nonexistent user raises RuntimeError on resolution."""
        bad_profile = BuilderProfile(
            profile_id="bad-user-profile",
            executable=self.bin_path,
            fixed_args=(),
            timeout_seconds=30,
            allowed_env_names=(),
            execution_policy="HEADLESS",
            permitted_user="nonexistent_user_99999",
        )
        with self.assertRaises(RuntimeError) as cm:
            bad_profile.resolve_permitted_uid()
        self.assertIn("not found in OS identity database", str(cm.exception))

    def test_b4_runner_server_startup_rejects_wrong_effective_uid(self):
        """RunnerServer startup must reject if effective UID does not match profile.permitted_user."""
        # Register a mock profile with permitted_user="root" (UID 0)
        root_profile = BuilderProfile(
            profile_id="root-profile-test",
            executable=self.bin_path,
            fixed_args=(),
            timeout_seconds=30,
            allowed_env_names=(),
            execution_policy="HEADLESS",
            permitted_user="root",
        )

        with patch("telegraph.runner_service.get_profile", return_value=root_profile):
            current_uid = os.geteuid()
            # If test is not running as root (current_uid != 0)
            if current_uid != 0:
                config = RunnerConfig(
                    socket_path=os.path.join(self.run_dir, "runner_test.sock"),
                    allowed_actuator_uid=9999,
                    expected_socket_gid=os.getegid(),
                    agy_path=self.bin_path,
                    agy_sha256=self.bin_sha256,
                    profile_id="root-profile-test",
                )
                with self.assertRaises(RunnerAuthorityError) as cm:
                    RunnerServer(config)
                self.assertIn("does not match profile.permitted_user", str(cm.exception))

    def test_b4_runner_handle_request_rejects_wrong_effective_uid_before_launch(self):
        """RunnerServer._handle_request must reject launch if effective UID != profile.permitted_user."""
        current_uid = os.geteuid()
        current_user = pwd.getpwuid(current_uid).pw_name
        other_user = next(user for user in pwd.getpwall() if user.pw_uid != current_uid)
        # Establish the same startup invariant as production, using this test's
        # real OS identity. The immutable production registry is not changed.
        startup_profile = replace(get_profile("agy-builder-v1"), permitted_user=current_user)
        request_profile = replace(startup_profile, profile_id="wrong-user-profile-test",
                                  permitted_user=other_user.pw_name)
        sock_path = os.path.join(self.run_dir, "runner_test2.sock")
        config = RunnerConfig(
            socket_path=sock_path,
            allowed_actuator_uid=other_user.pw_uid,
            expected_socket_gid=os.getegid(),
            agy_path=self.bin_path,
            agy_sha256=self.bin_sha256,
            profile_id="agy-builder-v1",
        )
        raw_req = json.dumps({
            "command": "RUN_BUILDER",
            "packet_sha256": self.packet_sha,
            "claim_id": "claim-001",
            "objective": "test",
            "repo_root": self.repo_root,
            "base_commit_oid": self.base_commit,
            "profile_id": request_profile.profile_id,
        }).encode("utf-8")

        profiles = {profile.profile_id: profile for profile in (startup_profile, request_profile)}
        with patch("telegraph.runner_service.get_profile", side_effect=profiles.get), \
             patch("telegraph.runner_service.verify_git_head"), \
             patch("telegraph.runner_service.launch_runner_process") as launch:
            server = RunnerServer(config)
            self.addCleanup(server.close)
            resp = server._handle_request(raw_req)
            self.assertEqual(resp["status"], "ERROR")
            self.assertEqual(resp["error_code"], "PERMITTED_USER_MISMATCH")
            self.assertFalse(resp["launched"])
            self.assertIn("does not match profile.permitted_user", resp["message"])
            self.assertNotIn("claim-001", server._executed_claims)
            launch.assert_not_called()


class TestPreservedSemantics(BaseBlockerTest):
    """Verify duplicate/replay, recovery, and packet validation semantics remain intact."""

    def test_duplicate_replay_at_most_once(self):
        """Duplicate claim replay returns new_launch=False and no new process."""
        receipt = self._create_and_write_gate_receipt()
        self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)
        claim1 = self.ledger.claim(self.packet_sha, "BUILD")
        self.assertTrue(claim1["new_launch"])

        claim2 = self.ledger.claim(self.packet_sha, "BUILD")
        self.assertFalse(claim2["new_launch"])
        self.assertEqual(claim2["claim_status"], "EXISTING")

    def test_recovery_stranded_dispatched_to_hold(self):
        """Explicit recovery transitions stranded DISPATCHED packet to HOLD."""
        receipt = self._create_and_write_gate_receipt()
        self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)
        claim_res = self.ledger.claim(self.packet_sha, "BUILD")
        claim_id = claim_res["claim_id"]

        rec_res = self.ledger.recover(self.packet_sha, claim_id=claim_id, reason="Stranded recovery")
        self.assertEqual(rec_res["state"], "HOLD")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "HOLD")

    def test_recovery_rejects_wrong_claim_id(self):
        """Recovery rejects non-existent claim ID."""
        receipt = self._create_and_write_gate_receipt()
        self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)
        self.ledger.claim(self.packet_sha, "BUILD")

        with self.assertRaises(LedgerError) as cm:
            self.ledger.recover(self.packet_sha, claim_id="bad-claim-id", reason="Wrong claim")
        self.assertIn("Claim ID mismatch", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
