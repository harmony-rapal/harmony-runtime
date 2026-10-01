"""
test_b2_c2_ssh_keygen_human_gate.py — Focused tests for B2/C2 Human Gate verification.

Validates:
  1. Canonical signed bytes binding
  2. Actual detached Ed25519 signature verification (OpenSSH SSHSIG format)
  3. ssh-keygen -Y verify semantics with allowed_signers authority
  4. Namespace enforcement: exactly 'harmony-human-gate'
  5. Canonical trusted allowed-signers authority (and rejection of untrusted signers)
  6. Signer identity bound by cryptographic verification, not caller assertion
  7. Mission binding
  8. Packet binding
  9. Runtime-facts binding
  10. Receipt substitution resistance
  11. Fail closed on verification error
  12. AST inspection: No shell=True, no shell escape, no private keys in telegraph/
  13. Negative cases:
      - forged signer
      - forged signature
      - self-generated public-input proof (GATE_PROOF)
      - changed mission binding
      - changed packet binding
      - changed runtime binding
      - substituted receipt
      - symlink authority artifact (receipt, gate dir, allowed_signers)
      - unsafe authority permissions (world-writable, untrusted UID)
  14. Positive case: A valid Captain-signed canonical Human Gate accepted
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import struct
import tempfile
import unittest
from datetime import datetime, timezone

from telegraph.actuator_service import (
    ActuatorProtocolHandler,
    _load_and_verify_gate_receipt_from_dir,
)
from telegraph.human_gate import (
    HUMAN_GATE_NAMESPACE,
    HumanGateVerificationError,
    armor_sshsig,
    compute_tosign_buffer,
    create_verified_gate_receipt,
    dearmor_sshsig,
    find_and_load_allowed_signers,
    load_and_verify_gate_receipt,
    load_trusted_allowed_signers,
    parse_allowed_signers,
    parse_sshsig,
    verify_gate_receipt_dict,
    _canonical_json_bytes,
    _sha256_hex,
    _sign_ed25519,
    _ssh_string,
)
from telegraph.ledger import Ledger
from telegraph.packet import compute_sha256
from tests.test_gate_keys import (
    TEST_PUBKEYS_B64,
    TEST_SEEDS,
    ensure_test_keys_registered,
    get_test_allowed_signers_content,
)


class TestB2C2HumanGateAuthority(unittest.TestCase):
    def setUp(self):
        ensure_test_keys_registered()
        self.tmpdir = tempfile.mkdtemp(prefix="hg_test_")
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        self.gate_dir = os.path.join(self.tmpdir, "gates")
        os.makedirs(self.gate_dir, mode=0o700, exist_ok=True)

        self.allowed_signers_path = os.path.join(self.tmpdir, "allowed_signers")
        with open(self.allowed_signers_path, "w", encoding="utf-8") as f:
            f.write(get_test_allowed_signers_content())
        os.chmod(self.allowed_signers_path, 0o600)
        os.environ["HARMONY_ALLOWED_SIGNERS_PATH"] = self.allowed_signers_path

        self.ledger = Ledger(self.state_dir)
        self.packet = {
            "schema_version": 1,
            "action": "START_BUILDER",
            "mission_id": "MISSION-CLOSURE-008",
            "revision": 1,
            "project_id": "harmony",
            "objective": "Verify B2/C2 closure",
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

    def tearDown(self):
        os.environ.pop("HARMONY_ALLOWED_SIGNERS_PATH", None)
        self.ledger.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_valid_captain_signed_canonical_human_gate_accepted(self):
        """A valid Captain-signed canonical Human Gate must be accepted."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="captain",
        )
        receipt_path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)

        self.assertEqual(resp["status"], "OK")
        self.assertEqual(resp["state"], "APPROVED")

        # Provenance in ledger bound to captain
        prov = self.ledger.get_human_gate_receipt(self.packet_sha)
        self.assertIsNotNone(prov)
        self.assertEqual(prov["signer"], "captain")
        self.assertEqual(prov["decision"], "APPROVE")
        self.assertEqual(prov["packet_sha256"], self.packet_sha)
        self.assertEqual(prov["mission_id"], self.packet["mission_id"])

    def test_02_valid_alice_signed_receipt_accepted(self):
        """A valid authorized human approver (alice@corp.com) must be accepted."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        res = self.ledger.approve(self.packet_sha, receipt["signer"], gate_receipt=receipt)
        self.assertEqual(res["state"], "APPROVED")
        self.assertTrue(res.get("gate_verified"))

    def test_03_rejects_forged_signer(self):
        """Receipt signed by Alice's key but asserting Bob's identity must be rejected."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        forged = dict(receipt)
        forged["signer"] = "bob@corp.com"
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(forged, expected_packet_sha256=self.packet_sha)

    def test_04_rejects_forged_signature(self):
        """Receipt with corrupted signature bytes must fail closed."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        raw = dearmor_sshsig(receipt["signature"])
        corrupted = raw[:-10] + b"\x00" * 10
        tampered = dict(receipt)
        tampered["signature"] = armor_sshsig(corrupted)
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256=self.packet_sha)

    def test_05_rejects_self_generated_public_proof(self):
        """The old self-forgeable GATE_PROOF construction must be rejected."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        fake_proof = _sha256_hex(f"GATE_PROOF:alice@corp.com:{receipt['gate_digest']}".encode("utf-8"))
        tampered = dict(receipt)
        tampered["signature"] = fake_proof
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256=self.packet_sha)

    def test_06_rejects_changed_mission_binding(self):
        """Receipt with altered mission_id must fail verification."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        tampered = dict(receipt)
        tampered["mission_id"] = "OTHER-MISSION-999"
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256=self.packet_sha)

    def test_07_rejects_changed_packet_binding(self):
        """Receipt with altered packet_sha256 must fail verification."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        tampered = dict(receipt)
        tampered["packet_sha256"] = "1" * 64
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256="1" * 64)

    def test_08_rejects_changed_runtime_binding(self):
        """Receipt with altered runtime_facts or digest must fail verification."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
            runtime_facts={"key": "val1"},
        )
        tampered = dict(receipt)
        tampered["runtime_facts"] = {"key": "val2_tampered"}
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(tampered, expected_packet_sha256=self.packet_sha)

    def test_09_rejects_substituted_receipt(self):
        """Receipt for packet A presented for packet B must be rejected."""
        receipt_a = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(receipt_a, expected_packet_sha256="2" * 64)

    def test_10_rejects_symlink_receipt_file(self):
        """Symlink candidate receipt file must fail closed."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        real_file = os.path.join(self.tmpdir, "real_rec.json")
        with open(real_file, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        symlink_file = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        os.symlink(real_file, symlink_file)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertIn("symlink ambiguity", resp["message"])

    def test_11_rejects_symlink_allowed_signers(self):
        """Symlink allowed_signers authority file must fail closed."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        real_as = os.path.join(self.tmpdir, "real_as")
        with open(real_as, "w", encoding="utf-8") as f:
            f.write("# empty\n")
        symlink_as = os.path.join(self.gate_dir, "allowed_signers")
        os.symlink(real_as, symlink_as)

        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        resp = handler.approve_gate(self.packet_sha)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
        self.assertIn("symlink ambiguity", resp["message"])

    def test_12_rejects_world_writable_allowed_signers(self):
        """World-writable allowed_signers file must fail closed."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)

        as_file = os.path.join(self.gate_dir, "allowed_signers")
        with open(as_file, "w", encoding="utf-8") as f:
            f.write("# empty\n")
        os.chmod(as_file, 0o666)

        try:
            handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
            resp = handler.approve_gate(self.packet_sha)
            self.assertEqual(resp["status"], "ERROR")
            self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
            self.assertIn("world write access", resp["message"])
        finally:
            os.chmod(as_file, 0o600)

    def test_13_rejects_world_writable_receipt_file(self):
        """World-writable receipt file must fail closed."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        path = os.path.join(self.gate_dir, f"{self.packet_sha}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(receipt, f)
        os.chmod(path, 0o666)

        try:
            handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
            resp = handler.approve_gate(self.packet_sha)
            self.assertEqual(resp["status"], "ERROR")
            self.assertEqual(resp["error_code"], "INVALID_GATE_RECEIPT")
            self.assertIn("world write access", resp["message"])
        finally:
            os.chmod(path, 0o600)

    def test_14_rejects_wrong_namespace(self):
        """Signature with namespace other than harmony-human-gate must fail closed."""
        core = {
            "schema_version": 1,
            "packet_sha256": self.packet_sha,
            "mission_id": self.packet["mission_id"],
            "signer": "alice@corp.com",
            "decision": "APPROVE",
            "signed_at": datetime.now(timezone.utc).isoformat(),
            "runtime_facts_digest": _sha256_hex(_canonical_json_bytes({})),
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
            "gate_digest": _sha256_hex(can_bytes),
            "signature": armor_sshsig(blob),
        }
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(receipt, expected_packet_sha256=self.packet_sha)

    def test_15_rejects_untrusted_signer_key(self):
        """Signer whose key is not present in allowed_signers must fail closed."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="eve@corp.com",
            private_key_pem=TEST_SEEDS["eve@corp.com"],
        )
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(receipt, expected_packet_sha256=self.packet_sha)

    def test_16_allowed_signers_namespace_restriction_enforced(self):
        """allowed_signers entry with restricted namespace must enforce harmony-human-gate."""
        pub_b64 = TEST_PUBKEYS_B64["alice@corp.com"]
        # File only permits git namespace
        content_git_only = f'alice@corp.com namespaces="git" ssh-ed25519 {pub_b64} Alice Git Key\n'
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(
                receipt,
                expected_packet_sha256=self.packet_sha,
                allowed_signers_content=content_git_only,
            )

        # File permits harmony-human-gate namespace
        content_hg = f'alice@corp.com namespaces="harmony-human-gate" ssh-ed25519 {pub_b64} Alice Gate Key\n'
        verified = verify_gate_receipt_dict(
            receipt,
            expected_packet_sha256=self.packet_sha,
            allowed_signers_content=content_hg,
        )
        self.assertEqual(verified["signer"], "alice@corp.com")

    def test_17_no_shell_or_subprocess_or_private_keys_in_telegraph_source(self):
        """
        Verify architectural invariants via AST:
        - No shell=True
        - Subprocess imports in telegraph/ permitted only in builder_adapter and human_gate
        - No private keys in telegraph/ source files
        """
        project_root = os.path.join(os.path.dirname(__file__), "..")
        telegraph_dir = os.path.join(project_root, "telegraph")

        for fname in os.listdir(telegraph_dir):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(telegraph_dir, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                source = f.read()

            self.assertNotIn("shell=True", source, msg=f"shell=True found in {fname}")
            self.assertNotIn("BEGIN PRIVATE KEY", source, msg=f"Private key found in {fname}")
            self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", source, msg=f"OpenSSH private key found in {fname}")

            tree = ast.parse(source, filename=fpath)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "subprocess":
                            self.assertIn(
                                fname,
                                ("builder_adapter.py", "human_gate.py"),
                                msg=f"'import subprocess' found in {fname}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and "subprocess" in node.module:
                        self.assertIn(
                            fname,
                            ("builder_adapter.py", "human_gate.py"),
                            msg=f"'from subprocess' found in {fname}",
                        )

    def test_18_rejects_missing_external_trust_root(self):
        """When no external allowed_signers path is provisioned, verification fails closed."""
        old_env = os.environ.pop("HARMONY_ALLOWED_SIGNERS_PATH", None)
        try:
            receipt = create_verified_gate_receipt(
                packet_sha256=self.packet_sha,
                mission_id=self.packet["mission_id"],
                signer="alice@corp.com",
            )
            with self.assertRaises(HumanGateVerificationError) as cm:
                verify_gate_receipt_dict(
                    receipt,
                    expected_packet_sha256=self.packet_sha,
                    allowed_signers_path=None,
                )
            self.assertIn("missing external trust root", str(cm.exception))
        finally:
            if old_env is not None:
                os.environ["HARMONY_ALLOWED_SIGNERS_PATH"] = old_env

    def test_19_rejects_package_source_trust_root_only(self):
        """Pointing allowed_signers to telegraph/ package or workspace source is forbidden."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        forbidden_path = os.path.join(os.path.dirname(__file__), "..", "telegraph", "allowed_signers")
        with self.assertRaises(HumanGateVerificationError) as cm:
            verify_gate_receipt_dict(
                receipt,
                expected_packet_sha256=self.packet_sha,
                allowed_signers_path=forbidden_path,
            )
        self.assertIn("package/source trust root", str(cm.exception))

    def test_20_rejects_tests_directory_trust_root(self):
        """Pointing allowed_signers to tests/ directory is forbidden."""
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="alice@corp.com",
        )
        tests_as_path = os.path.join(os.path.dirname(__file__), "allowed_signers")
        with self.assertRaises(HumanGateVerificationError) as cm:
            verify_gate_receipt_dict(
                receipt,
                expected_packet_sha256=self.packet_sha,
                allowed_signers_path=tests_as_path,
            )
        self.assertIn("tests trust root", str(cm.exception))

    def test_21_rejects_test_seed_signer_not_in_canonical_allowed_signers(self):
        """Signer signed with unauthorized test seed key (eve@corp.com) must be rejected."""
        # Create external allowed_signers with Captain ONLY
        captain_as = os.path.join(self.tmpdir, "captain_only_allowed_signers")
        with open(captain_as, "w", encoding="utf-8") as f:
            f.write(
                f'captain,captain@harmony.local namespaces="harmony-human-gate" '
                f'ssh-ed25519 {TEST_PUBKEYS_B64["captain"]} Captain Key\n'
            )
        os.chmod(captain_as, 0o600)

        # Receipt signed by Eve (test seed)
        receipt = create_verified_gate_receipt(
            packet_sha256=self.packet_sha,
            mission_id=self.packet["mission_id"],
            signer="eve@corp.com",
            private_key_pem=TEST_SEEDS["eve@corp.com"],
        )
        with self.assertRaises(HumanGateVerificationError):
            verify_gate_receipt_dict(
                receipt,
                expected_packet_sha256=self.packet_sha,
                allowed_signers_path=captain_as,
            )

    def test_22_rejects_caller_selected_server_trust_root(self):
        """Caller cannot supply allowed_signers in IPC request payload."""
        handler = ActuatorProtocolHandler(ledger=self.ledger, gate_receipt_dir=self.gate_dir)
        raw_req = json.dumps({
            "command": "APPROVE_GATE",
            "packet_sha256": self.packet_sha,
            "allowed_signers": "/tmp/attacker/allowed_signers",
        }).encode("utf-8")
        resp = handler.handle_raw_request(raw_req)
        self.assertEqual(resp["status"], "ERROR")
        self.assertEqual(resp["error_code"], "FORBIDDEN_FIELD")

    def test_23_custom_crypto_fallback_absent(self):
        """telegraph.human_gate must not contain _verify_ed25519 or in-process verification fallback."""
        import telegraph.human_gate as hg
        self.assertFalse(
            hasattr(hg, "_verify_ed25519"),
            "Custom crypto verifier _verify_ed25519 must be absent from telegraph.human_gate",
        )
        self.assertFalse(
            hasattr(hg, "verify_ed25519"),
            "Custom crypto verifier verify_ed25519 must be absent from telegraph.human_gate",
        )


if __name__ == "__main__":
    unittest.main()
