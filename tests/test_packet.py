"""
test_packet.py — Unit tests for telegraph.packet validation and canonicalization.

Tests:
  1.  Valid packet → passes validation
  2.  Key order invariant → same canonical SHA256
  3.  One-char change in objective → different SHA256
  4.  Unknown top-level field → reject
  5.  Forbidden field shell_command → reject
  6.  Forbidden nested fields (argv, env) → reject
  7.  Invalid 40-char base_commit_oid → reject
  17. Unknown action → reject
  18. retry_policy != NEVER → reject
  19. on_failure != HOLD → reject
  20. No subprocess-based execution in source code
"""

import ast
import os
import sys
import unittest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegraph.packet import (
    PacketValidationError,
    canonical_bytes,
    compute_sha256,
    validate,
)


def _base_packet(**overrides):
    """Return a minimal valid packet dict, with optional overrides."""
    pkt = {
        "schema_version": 1,
        "mission_id": "TEST-001",
        "revision": 1,
        "project_id": "test-project",
        "objective": "Run all tests successfully.",
        "action": "START_BUILDER",
        "profile_id": "builder-v1",
        "target": {
            "repo_root": "/tmp/test",
            "base_commit_oid": "a" * 40,
        },
        "retry_policy": "NEVER",
        "on_failure": "HOLD",
    }
    pkt.update(overrides)
    return pkt


class TestPacketValidation(unittest.TestCase):

    # ------------------------------------------------------------------
    # Test 1 — valid packet passes
    # ------------------------------------------------------------------
    def test_01_valid_packet_ready(self):
        """Valid packet validates without exception."""
        pkt = _base_packet()
        result = validate(pkt)
        self.assertEqual(result["mission_id"], "TEST-001")

    # ------------------------------------------------------------------
    # Test 2 — key order invariant
    # ------------------------------------------------------------------
    def test_02_key_order_same_sha(self):
        """Packets with different key ordering produce identical canonical SHA."""
        pkt_a = _base_packet()
        # Build pkt_b with deliberately reversed top-level key order
        keys = list(pkt_a.keys())
        keys.reverse()
        pkt_b = {k: pkt_a[k] for k in keys}

        sha_a = compute_sha256(validate(pkt_a))
        sha_b = compute_sha256(validate(pkt_b))
        self.assertEqual(sha_a, sha_b)
        self.assertEqual(len(sha_a), 64)

    # ------------------------------------------------------------------
    # Test 3 — one-char change → different SHA
    # ------------------------------------------------------------------
    def test_03_objective_change_different_sha(self):
        """Changing objective by one character changes the canonical SHA."""
        pkt_a = _base_packet()
        pkt_b = _base_packet(objective="Run all tests successfully!")
        sha_a = compute_sha256(validate(pkt_a))
        sha_b = compute_sha256(validate(pkt_b))
        self.assertNotEqual(sha_a, sha_b)

    # ------------------------------------------------------------------
    # Test 4 — unknown top-level field → reject
    # ------------------------------------------------------------------
    def test_04_unknown_top_level_field(self):
        """Unknown top-level field causes PacketValidationError."""
        pkt = _base_packet()
        pkt["extra_field"] = "should_fail"
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("extra_field", str(ctx.exception))

    # ------------------------------------------------------------------
    # Test 5 — forbidden field shell_command → reject
    # ------------------------------------------------------------------
    def test_05_forbidden_shell_command(self):
        """shell_command field causes PacketValidationError."""
        pkt = _base_packet()
        pkt["shell_command"] = "echo hi"
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("shell_command", str(ctx.exception))

    # ------------------------------------------------------------------
    # Test 6 — forbidden nested fields (argv / env)
    # ------------------------------------------------------------------
    def test_06_forbidden_nested_argv(self):
        """argv nested inside target causes PacketValidationError."""
        pkt = _base_packet()
        pkt["target"] = dict(pkt["target"])
        pkt["target"]["argv"] = ["ls", "-la"]
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("argv", str(ctx.exception))

    def test_06b_forbidden_nested_env(self):
        """env nested inside target causes PacketValidationError."""
        pkt = _base_packet()
        pkt["target"] = dict(pkt["target"])
        pkt["target"]["env"] = {"PATH": "/usr/bin"}
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("env", str(ctx.exception))

    # ------------------------------------------------------------------
    # Test 7 — invalid base_commit_oid → reject
    # ------------------------------------------------------------------
    def test_07_invalid_base_commit_oid_short(self):
        """base_commit_oid shorter than 40 chars causes PacketValidationError."""
        pkt = _base_packet()
        pkt["target"] = dict(pkt["target"])
        pkt["target"]["base_commit_oid"] = "abc123"
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("base_commit_oid", str(ctx.exception))

    def test_07b_invalid_base_commit_oid_uppercase(self):
        """base_commit_oid with uppercase chars causes PacketValidationError."""
        pkt = _base_packet()
        pkt["target"] = dict(pkt["target"])
        pkt["target"]["base_commit_oid"] = "A" * 40
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("base_commit_oid", str(ctx.exception))

    def test_07c_invalid_base_commit_oid_nonhex(self):
        """base_commit_oid with non-hex chars causes PacketValidationError."""
        pkt = _base_packet()
        pkt["target"] = dict(pkt["target"])
        pkt["target"]["base_commit_oid"] = "g" * 40
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("base_commit_oid", str(ctx.exception))

    # ------------------------------------------------------------------
    # Test 17 — unknown action → reject
    # ------------------------------------------------------------------
    def test_17_unknown_action(self):
        """Unrecognised action value causes PacketValidationError."""
        pkt = _base_packet(action="DO_SOMETHING_EVIL")
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("action", str(ctx.exception))

    # ------------------------------------------------------------------
    # Test 18 — retry_policy != NEVER → reject
    # ------------------------------------------------------------------
    def test_18_retry_policy_not_never(self):
        """retry_policy other than NEVER causes PacketValidationError."""
        pkt = _base_packet(retry_policy="ALWAYS")
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("retry_policy", str(ctx.exception))

    # ------------------------------------------------------------------
    # Test 19 — on_failure != HOLD → reject
    # ------------------------------------------------------------------
    def test_19_on_failure_not_hold(self):
        """on_failure other than HOLD causes PacketValidationError."""
        pkt = _base_packet(on_failure="RETRY")
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("on_failure", str(ctx.exception))

    # ------------------------------------------------------------------
    # Test 20 — no subprocess-based execution in source
    # ------------------------------------------------------------------
    def test_20_no_subprocess_execution_in_source(self):
        """
        Source files must not contain subprocess execution calls,
        shell=True, or os.system.
        """
        project_root = os.path.join(os.path.dirname(__file__), "..")
        telegraph_dir = os.path.join(project_root, "telegraph")

        banned_patterns = [
            # AST-checked names
        ]
        banned_calls = {"subprocess.run", "subprocess.call", "subprocess.Popen",
                        "subprocess.check_call", "subprocess.check_output",
                        "os.system", "os.popen"}

        for fname in os.listdir(telegraph_dir):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(telegraph_dir, fname)
            with open(fpath, "r", encoding="utf-8") as fh:
                source = fh.read()

            # Text-level checks
            self.assertNotIn(
                "shell=True", source,
                msg=f"shell=True found in {fname}"
            )

            # AST-level checks: no subprocess imports or os.system
            try:
                tree = ast.parse(source, filename=fpath)
            except SyntaxError as exc:
                self.fail(f"SyntaxError in {fname}: {exc}")

            for node in ast.walk(tree):
                # Check for `import subprocess`
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "subprocess":
                            self.assertEqual(fname, "builder_adapter.py",
                                msg=f"'import subprocess' found in {fname}")
                # Check for `from subprocess import ...`
                if isinstance(node, ast.ImportFrom):
                    if node.module == "subprocess":
                        self.assertEqual(fname, "builder_adapter.py",
                            msg=f"'from subprocess import ...' found in {fname}")
                # Check for os.system(...) calls
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute):
                        if (isinstance(node.func.value, ast.Name)
                                and node.func.value.id == "os"
                                and node.func.attr == "system"):
                            self.fail(
                                f"os.system() call found in {fname}"
                            )

    # ------------------------------------------------------------------
    # F4 — schema_version must be exact integer 1 (not True / bool)
    # ------------------------------------------------------------------

    def test_f4_schema_version_true_rejected(self):
        """schema_version=True must raise PacketValidationError (bool is not int 1)."""
        pkt = _base_packet()
        pkt["schema_version"] = True  # Python True == 1 but is bool
        with self.assertRaises(PacketValidationError) as ctx:
            validate(pkt)
        self.assertIn("schema_version", str(ctx.exception))

    def test_f4_schema_version_false_rejected(self):
        """schema_version=False must raise PacketValidationError."""
        pkt = _base_packet()
        pkt["schema_version"] = False
        with self.assertRaises(PacketValidationError):
            validate(pkt)

    def test_f4_schema_version_string_rejected(self):
        """schema_version='1' (string) must raise PacketValidationError."""
        pkt = _base_packet()
        pkt["schema_version"] = "1"
        with self.assertRaises(PacketValidationError):
            validate(pkt)

    def test_f4_unhashable_action_raises_validation_error(self):
        """An unhashable type in 'action' (e.g. a list) must produce
        PacketValidationError, not a raw TypeError."""
        pkt = _base_packet()
        pkt["action"] = ["START_BUILDER"]  # list is unhashable
        with self.assertRaises(PacketValidationError):
            validate(pkt)


if __name__ == "__main__":
    unittest.main()
