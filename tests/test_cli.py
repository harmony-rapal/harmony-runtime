"""
test_cli.py — Integration tests for the telegraph CLI.

Tests the CLI end-to-end by invoking cli.main() directly with captured stdout/stderr.
Covers the full demo workflow and key failure paths.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegraph.cli import main


def _run(*argv: str) -> tuple[str, str, int]:
    """
    Run the CLI with given arguments.
    Returns (stdout, stderr, exit_code).
    Exit code 0 means success; non-zero means failure.
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = 0

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            main(list(argv))
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1

    return stdout_buf.getvalue(), stderr_buf.getvalue(), exit_code


class TestCLIWorkflow(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._mission = os.path.join(
            os.path.dirname(__file__), "..", "examples", "mission.json"
        )
        self._state_dir = os.path.join(self._tmpdir, "state")
        self._sha = None  # will be populated after ingest

    def _ingest(self):
        out, err, code = _run(
            "ingest", self._mission, "--state-dir", self._state_dir
        )
        self.assertEqual(code, 0, msg=f"ingest failed:\n{err}")
        # Extract SHA from output
        for line in out.splitlines():
            if line.startswith("PACKET_SHA256="):
                self._sha = line.split("=", 1)[1].strip()
        self.assertIsNotNone(self._sha, "SHA256 not found in ingest output")
        return out

    def _approve(self):
        out, err, code = _run(
            "approve", self._sha,
            "--approver", "test-human",
            "--state-dir", self._state_dir,
        )
        self.assertEqual(code, 0, msg=f"approve failed:\n{err}")
        return out

    def _claim(self, stage="BUILD"):
        out, err, code = _run(
            "claim", self._sha,
            "--stage", stage,
            "--state-dir", self._state_dir,
        )
        return out, err, code

    # ------------------------------------------------------------------
    # Full demo workflow
    # ------------------------------------------------------------------

    def test_full_workflow(self):
        """ingest → approve → claim → duplicate claim → show → verify."""
        # 1. Ingest
        ingest_out = self._ingest()
        self.assertIn("STATE=READY", ingest_out)

        # 2. Approve
        approve_out = self._approve()
        self.assertIn("STATE=APPROVED", approve_out)

        # 3. First claim
        claim_out, claim_err, claim_code = self._claim("BUILD")
        self.assertEqual(claim_code, 0, msg=claim_err)
        self.assertIn("CLAIM_STATUS=NEW", claim_out)
        self.assertIn("NEW_LAUNCH=YES", claim_out)

        # 4. Duplicate claim → EXISTING
        claim2_out, claim2_err, claim2_code = self._claim("BUILD")
        self.assertEqual(claim2_code, 0, msg=claim2_err)
        self.assertIn("CLAIM_STATUS=EXISTING", claim2_out)
        self.assertIn("NEW_LAUNCH=NO", claim2_out)

        # 5. Show
        show_out, show_err, show_code = _run(
            "show", self._sha, "--state-dir", self._state_dir
        )
        self.assertEqual(show_code, 0, msg=show_err)
        self.assertIn("STATE=DISPATCHED", show_out)
        self.assertIn("PACKET_SHA256=" + self._sha, show_out)

        # 6. Verify
        verify_out, verify_err, verify_code = _run(
            "verify", self._sha, "--state-dir", self._state_dir
        )
        self.assertEqual(verify_code, 0, msg=verify_err)
        self.assertIn("VALID=YES", verify_out)

    # ------------------------------------------------------------------
    # Failure paths
    # ------------------------------------------------------------------

    def test_ingest_invalid_json(self):
        """Ingesting a non-existent file exits non-zero."""
        out, err, code = _run(
            "ingest", "/nonexistent/path.json",
            "--state-dir", self._state_dir,
        )
        self.assertNotEqual(code, 0)

    def test_approve_unknown_packet(self):
        """Approving an unknown packet SHA exits non-zero."""
        self._ingest()
        out, err, code = _run(
            "approve", "0" * 64,
            "--approver", "x",
            "--state-dir", self._state_dir,
        )
        self.assertNotEqual(code, 0)

    def test_claim_before_approval_exits_nonzero(self):
        """Claiming a READY packet exits non-zero."""
        self._ingest()
        out, err, code = self._claim("BUILD")
        self.assertNotEqual(code, 0)

    def test_hold_command(self):
        """hold command transitions to HOLD and exits zero."""
        self._ingest()
        out, err, code = _run(
            "hold", self._sha,
            "--reason", "manual-review",
            "--state-dir", self._state_dir,
        )
        self.assertEqual(code, 0, msg=err)
        self.assertIn("STATE=HOLD", out)

    def test_claim_after_hold_exits_nonzero(self):
        """Claiming a held packet exits non-zero."""
        self._ingest()
        _run("hold", self._sha, "--reason", "test", "--state-dir", self._state_dir)
        out, err, code = self._claim("BUILD")
        self.assertNotEqual(code, 0)

    def test_verify_unknown_packet_exits_nonzero(self):
        """Verifying an unknown packet exits non-zero."""
        out, err, code = _run(
            "verify", "a" * 64,
            "--state-dir", self._state_dir,
        )
        self.assertNotEqual(code, 0)

    def test_duplicate_claim_new_launch_no(self):
        """
        Explicit test: second claim with identical (packet, stage) must output
        CLAIM_STATUS=EXISTING and NEW_LAUNCH=NO.
        """
        self._ingest()
        self._approve()
        self._claim("BUILD")  # first
        out, _, code = self._claim("BUILD")  # second
        self.assertEqual(code, 0)
        self.assertIn("CLAIM_STATUS=EXISTING", out)
        self.assertIn("NEW_LAUNCH=NO", out)


if __name__ == "__main__":
    unittest.main()
