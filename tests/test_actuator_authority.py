"""
test_actuator_authority.py — Comprehensive tests for Actuator Authority Boundary.

Covers:
  - IPC schema & fail-closed protocol parsing
  - Unknown command rejection
  - Forbidden caller parameter rejection (packet, stage, repo_root, argv, env, etc.)
  - SHA-only dispatch with Actuator-owned canonical packet lookup
  - Serial dispatch & duplicate zero-second-worker semantics
  - Exact state directory (0700) and DB file (0600) mode checks
  - Initial SQLite creation guarantees 0600 without broader permissions
  - Socket parent owner/mode matrix validation
  - SO_PEERCRED immediate peer authorization and same-UID rejection
"""

import json
import os
import shutil
import stat
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from telegraph.builder_evidence import EvidenceStore

from telegraph.actuator_client import (
    ActuatorClient,
    ActuatorIPCError,
)
from telegraph.actuator_protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    parse_and_validate_request,
)
from telegraph.actuator_service import (
    ActuatorProtocolHandler,
    ActuatorServer,
    AuthorityConfigurationError,
    open_actuator_ledger,
    verify_authority_environment,
)
from telegraph.ledger import Ledger
from telegraph.packet import compute_sha256


class TestActuatorProtocolSchema(unittest.TestCase):
    """Tests fail-closed protocol parsing and IPC schema enforcement."""

    def test_valid_request(self):
        sha = "a" * 64
        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": sha}).encode("utf-8")
        parsed = parse_and_validate_request(req)
        self.assertEqual(parsed["command"], "DISPATCH_BUILDER")
        self.assertEqual(parsed["packet_sha256"], sha)

    def test_malformed_json(self):
        with self.assertRaises(ProtocolError) as cm:
            parse_and_validate_request(b"not a valid json")
        self.assertEqual(cm.exception.error_code, "MALFORMED_REQUEST")

    def test_non_dict_json(self):
        for payload in [b"123", b"\"hello\"", b"[]", b"true"]:
            with self.assertRaises(ProtocolError) as cm:
                parse_and_validate_request(payload)
            self.assertEqual(cm.exception.error_code, "MALFORMED_REQUEST")

    def test_payload_too_large(self):
        oversized = b"{" + b"a" * (MAX_MESSAGE_BYTES + 10) + b"}"
        with self.assertRaises(ProtocolError) as cm:
            parse_and_validate_request(oversized)
        self.assertEqual(cm.exception.error_code, "MALFORMED_REQUEST")

    def test_unknown_command(self):
        for bad_cmd in ["RECORD_BUILDER_RESULT", "CLAIM", "INGEST", "START", "foo"]:
            req = json.dumps({"command": bad_cmd, "packet_sha256": "a" * 64}).encode("utf-8")
            with self.assertRaises(ProtocolError) as cm:
                parse_and_validate_request(req)
            self.assertEqual(cm.exception.error_code, "UNKNOWN_COMMAND")

    def test_forbidden_caller_fields(self):
        sha = "a" * 64
        forbidden_keys = [
            "packet",
            "stage",
            "repo_root",
            "cwd",
            "executable",
            "argv",
            "env",
            "retry",
            "result_facts",
        ]
        for key in forbidden_keys:
            req = json.dumps(
                {
                    "command": "DISPATCH_BUILDER",
                    "packet_sha256": sha,
                    key: "caller_supplied_value",
                }
            ).encode("utf-8")
            with self.assertRaises(ProtocolError) as cm:
                parse_and_validate_request(req)
            self.assertEqual(cm.exception.error_code, "FORBIDDEN_FIELD")

    def test_invalid_sha_format(self):
        invalid_shas = [
            "",
            "1234",
            "a" * 63,
            "a" * 65,
            "A" * 64,  # must be lowercase
            "g" * 64,  # non-hex
            12345,     # non-string
            None,
        ]
        for bad_sha in invalid_shas:
            req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": bad_sha}).encode("utf-8")
            with self.assertRaises(ProtocolError) as cm:
                parse_and_validate_request(req)
            self.assertEqual(cm.exception.error_code, "INVALID_PACKET_SHA")


class TestActuatorDispatchExecution(unittest.TestCase):
    """Tests SHA-only dispatch, Actuator packet lookup, and duplicate semantics."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.evidence_dir = os.path.join(self.tmpdir, "evidence")
        self.evidence_store = EvidenceStore(self.evidence_dir)
        self.ledger = open_actuator_ledger(self.state_dir)
        self.mock_runner = MagicMock()
        self.handler = ActuatorProtocolHandler(
            self.ledger,
            runner_client=self.mock_runner,
            evidence_store=self.evidence_store,
        )

        self.packet = {
            "schema_version": 1,
            "mission_id": "M-ACTUATOR-001",
            "revision": 1,
            "project_id": "P1",
            "profile_id": "agy-builder-v1",
            "action": "START_BUILDER",
            "target": {
                "repo_root": "/tmp/test",
                "base_commit_oid": "1b61d10490442a8842525d2aebab23f569dd91f7",
            },
            "objective": "build authority test",
            "retry_policy": "NEVER",
            "on_failure": "HOLD",
        }
        self.packet_sha = compute_sha256(self.packet)

    def tearDown(self):
        self.ledger.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _ingest_and_approve(self, packet: dict | None = None, sha: str | None = None):
        p = packet or self.packet
        s = sha or self.packet_sha
        self.ledger.ingest(p, s)
        self.ledger.approve(s, approver="test-authority-gate")

    def test_sha_only_dispatch_success(self):
        """Actuator loads canonical packet from Ledger and launches builder via Runner."""
        self._ingest_and_approve()

        def _fake_run_builder(**kwargs):
            return {
                "status": "OK",
                "command": "RUN_BUILDER",
                "packet_sha256": kwargs["packet_sha256"],
                "claim_id": kwargs["claim_id"],
                "actual_uid": 10001,
                "actual_gid": 10001,
                "actual_executable_path": "/fake/agy",
                "executable_digest": "a" * 64,
                "helper_digest": None,
                "launched": True,
                "exit_code": 0,
                "timed_out": False,
                "started_at": "2026-09-25T12:00:00Z",
                "finished_at": "2026-09-25T12:00:01Z",
            }
        self.mock_runner.run_builder.side_effect = _fake_run_builder

        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": self.packet_sha}).encode("utf-8")
        resp = self.handler.handle_raw_request(req)

        self.assertEqual(resp["status"], "OK")
        self.assertEqual(resp["packet_sha256"], self.packet_sha)
        self.assertTrue(resp["launch_attempted"])
        self.assertEqual(resp["builder_result"]["exit_code"], 0)
        self.assertEqual(resp["state"], "FINAL")

        # Verify Actuator retrieved canonical packet from DB and passed to Runner
        self.mock_runner.run_builder.assert_called_once()
        call_kwargs = self.mock_runner.run_builder.call_args[1]
        self.assertEqual(call_kwargs["packet_sha256"], self.packet_sha)
        self.assertEqual(call_kwargs["objective"], self.packet["objective"])
        self.assertEqual(call_kwargs["repo_root"], self.packet["target"]["repo_root"])
        self.assertEqual(call_kwargs["base_commit_oid"], self.packet["target"]["base_commit_oid"])

    def test_dispatch_packet_not_found(self):
        unknown_sha = "0" * 64
        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": unknown_sha}).encode("utf-8")
        resp = self.handler.handle_raw_request(req)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "PACKET_NOT_FOUND")

    def test_dispatch_invalid_state_ready(self):
        self.ledger.ingest(self.packet, self.packet_sha)
        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": self.packet_sha}).encode("utf-8")
        resp = self.handler.handle_raw_request(req)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_STATE")
        self.assertFalse(resp["launch_attempted"])

    def test_dispatch_invalid_state_hold(self):
        self._ingest_and_approve()
        self.ledger.hold(self.packet_sha, reason="security review")
        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": self.packet_sha}).encode("utf-8")
        resp = self.handler.handle_raw_request(req)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_STATE")

    def test_dispatch_invalid_action(self):
        bad_packet = dict(self.packet)
        bad_packet["action"] = "RUN_VALIDATION"
        bad_sha = compute_sha256(bad_packet)
        self._ingest_and_approve(bad_packet, bad_sha)

        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": bad_sha}).encode("utf-8")
        resp = self.handler.handle_raw_request(req)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_ACTION")

    def test_serial_duplicate_semantics(self):
        """First dispatch launches builder; second dispatch returns zero second launch."""
        self._ingest_and_approve()
        self.ledger.claim(self.packet_sha, "BUILD")

        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": self.packet_sha}).encode("utf-8")
        resp = self.handler.handle_raw_request(req)
        self.assertEqual(resp["status"], "OK")
        self.assertFalse(resp["builder_result"]["launched"])
        self.assertEqual(
            resp["builder_result"]["refusal_reason"],
            "DUPLICATE_CLAIM: new_launch=NO",
        )
        self.mock_runner.run_builder.assert_not_called()

    def test_governed_dispatch_no_runner_fails_closed_to_hold(self):
        """When Runner is not configured, dispatch fails closed to HOLD (B1)."""
        self._ingest_and_approve()
        handler_no_runner = ActuatorProtocolHandler(self.ledger, runner_client=None)

        req = json.dumps({"command": "DISPATCH_BUILDER", "packet_sha256": self.packet_sha}).encode("utf-8")
        resp = handler_no_runner.handle_raw_request(req)

        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "RUNNER_NOT_CONFIGURED")
        self.assertEqual(self.ledger.show(self.packet_sha)["state"], "HOLD")


class TestAuthorityBoundaries(unittest.TestCase):
    """Tests fail-closed OS authority checks, owner/mode matrix, and SO_PEERCRED."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        os.chmod(self.state_dir, 0o700)

        self.socket_parent = os.path.join(self.tmpdir, "run")
        os.makedirs(self.socket_parent, mode=0o750, exist_ok=True)
        os.chmod(self.socket_parent, 0o750)
        self.socket_path = os.path.join(self.socket_parent, "actuator.sock")

        self.allowed_client_uid = os.geteuid() + 1000  # Distinct UID
        self.expected_socket_gid = os.getegid()

        # Open ledger using the actuator factory (establishes umask 0077 and creates 0600 DB)
        self.ledger = open_actuator_ledger(self.state_dir)

    def tearDown(self):
        self.ledger.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_initial_creation_guarantees_0600(self):
        """Initial creation via open_actuator_ledger guarantees DB files have mode 0600."""
        new_state = os.path.join(self.tmpdir, "new_state")
        os.makedirs(new_state, mode=0o700)
        ledger = open_actuator_ledger(new_state)
        try:
            for f in ("ledger.db", "ledger.db-wal", "ledger.db-shm"):
                fp = os.path.join(new_state, f)
                if os.path.exists(fp):
                    mode = stat.S_IMODE(os.stat(fp).st_mode)
                    self.assertEqual(mode, 0o600, f"{f} must be created with mode 0600")
        finally:
            ledger.close()

    def test_startup_fails_if_same_uid(self):
        """Startup rejects allowed_client_uid == actuator_uid."""
        with self.assertRaises(AuthorityConfigurationError) as cm:
            verify_authority_environment(
                state_dir=self.state_dir,
                socket_path=self.socket_path,
                allowed_client_uid=os.geteuid(),  # Same UID
                expected_socket_gid=self.expected_socket_gid,
            )
        self.assertIn("cannot be identical", str(cm.exception))

    def test_state_dir_must_be_0700(self):
        """state_dir must be mode exactly 0700."""
        # Valid 0700
        os.chmod(self.state_dir, 0o700)
        verify_authority_environment(
            self.state_dir, self.socket_path, self.allowed_client_uid, self.expected_socket_gid
        )

        # Invalid modes
        for bad_mode in (0o755, 0o750, 0o777, 0o711):
            os.chmod(self.state_dir, bad_mode)
            with self.assertRaises(AuthorityConfigurationError) as cm:
                verify_authority_environment(
                    self.state_dir, self.socket_path, self.allowed_client_uid, self.expected_socket_gid
                )
            self.assertIn("must be mode exactly 0700", str(cm.exception))

        os.chmod(self.state_dir, 0o700)

    def test_database_files_must_be_0600(self):
        """Database files in state_dir must be mode exactly 0600."""
        db_path = os.path.join(self.state_dir, "ledger.db")
        self.assertTrue(os.path.exists(db_path))

        # Initial mode is verified to be 0600
        self.assertEqual(stat.S_IMODE(os.stat(db_path).st_mode), 0o600)
        verify_authority_environment(
            self.state_dir, self.socket_path, self.allowed_client_uid, self.expected_socket_gid
        )

        # Invalid mode fails closed
        for bad_mode in (0o644, 0o666, 0o660, 0o700):
            os.chmod(db_path, bad_mode)
            with self.assertRaises(AuthorityConfigurationError) as cm:
                verify_authority_environment(
                    self.state_dir, self.socket_path, self.allowed_client_uid, self.expected_socket_gid
                )
            self.assertIn("must be mode exactly 0600", str(cm.exception))

        # Reset to 0600 for teardown
        os.chmod(db_path, 0o600)

    def test_socket_parent_owner_mode_matrix(self):
        """Validates socket parent owner and mode combinations."""
        # Actuator-owned: 0750 or 0770 valid
        os.chmod(self.socket_parent, 0o750)
        verify_authority_environment(
            self.state_dir, self.socket_path, self.allowed_client_uid, self.expected_socket_gid
        )
        os.chmod(self.socket_parent, 0o770)
        verify_authority_environment(
            self.state_dir, self.socket_path, self.allowed_client_uid, self.expected_socket_gid
        )

        # Actuator-owned: world access rejected
        for bad_mode in (0o777, 0o755, 0o751):
            os.chmod(self.socket_parent, bad_mode)
            with self.assertRaises(AuthorityConfigurationError) as cm:
                verify_authority_environment(
                    self.state_dir, self.socket_path, self.allowed_client_uid, self.expected_socket_gid
                )
            self.assertIn("zero world access", str(cm.exception))

        # Root-owned parent (uid=0) with mode 0750 must be rejected (not group-writable for actuator)
        os.chmod(self.socket_parent, 0o750)
        real_stat_fn = os.stat

        def _mock_stat_root_750(path, *args, **kwargs):
            if path == self.socket_parent:
                return os.stat_result(
                    (stat.S_IFDIR | 0o750, 0, 0, 1, 0, self.expected_socket_gid, 4096, 0, 0, 0)
                )
            return real_stat_fn(path, *args, **kwargs)

        with patch("os.stat", side_effect=_mock_stat_root_750):
            with self.assertRaises(AuthorityConfigurationError) as cm:
                verify_authority_environment(
                    self.state_dir, self.socket_path, self.allowed_client_uid, self.expected_socket_gid
                )
            self.assertIn("must be mode exactly 0770", str(cm.exception))

        # Root-owned parent (uid=0) with mode 0770 must be accepted
        def _mock_stat_root_770(path, *args, **kwargs):
            if path == self.socket_parent:
                return os.stat_result(
                    (stat.S_IFDIR | 0o770, 0, 0, 1, 0, self.expected_socket_gid, 4096, 0, 0, 0)
                )
            return real_stat_fn(path, *args, **kwargs)

        with patch("os.stat", side_effect=_mock_stat_root_770):
            verify_authority_environment(
                self.state_dir, self.socket_path, self.allowed_client_uid, self.expected_socket_gid
            )

    def test_so_peercred_unauthorized_peer_rejected_before_read(self):
        """
        When unauthorized caller connects:
        SO_PEERCRED check immediately rejects peer without reading request payload.
        """
        server = ActuatorServer(
            ledger=self.ledger,
            socket_path=self.socket_path,
            allowed_client_uid=self.allowed_client_uid,  # Different UID
            expected_socket_gid=self.expected_socket_gid,
        )
        server.start()

        def _serve():
            server.handle_one_connection()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()

        # Connect with client running under test runner UID (which equals server UID)
        client = ActuatorClient(self.socket_path)
        with self.assertRaises(ActuatorIPCError) as cm:
            client.dispatch_builder("a" * 64)

        # Rejection occurs due to same-UID violation
        self.assertEqual(cm.exception.error_code, "SAME_UID_REJECTED")
        self.assertIn("Same-UID execution cannot establish", cm.exception.message)

        server.close()
        t.join(timeout=2.0)

    def test_client_validation_before_send(self):
        """ActuatorClient rejects bad SHA before attempting IPC transmission."""
        client = ActuatorClient(self.socket_path)
        with self.assertRaises(ValueError):
            client.dispatch_builder("invalid_sha")


if __name__ == "__main__":
    unittest.main()
