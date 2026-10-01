"""
test_builder_adapter.py — Tests for the Harmony Telegraph 002B Builder Adapter.
"""

import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from telegraph import builder_adapter
from telegraph.builder_adapter import AdapterError
from telegraph.packet import compute_sha256, validate


def _make_packet(**overrides):
    pkt = {
        "schema_version": 1,
        "mission_id": "TEST-002B",
        "revision": 1,
        "project_id": "test-project",
        "objective": "Run tests",
        "action": "START_BUILDER",
        "profile_id": "agy-builder-v1",
        "target": {
            "repo_root": "/tmp/testrepo",
            "base_commit_oid": "a" * 40,
        },
        "retry_policy": "NEVER",
        "on_failure": "HOLD",
    }
    pkt.update(overrides)
    validate(pkt)
    return pkt


class TestBuilderAdapter(unittest.TestCase):
    def setUp(self):
        self.ledger = MagicMock()

        # Valid packet baseline
        self._pkt = _make_packet()
        self._sha = compute_sha256(self._pkt)

        # Mock preflight checks that use OS
        self.patcher_exists = patch("os.path.exists", return_value=True)
        self.patcher_isdir = patch("os.path.isdir", return_value=True)
        self.patcher_verify = patch("telegraph.builder_adapter._verify_git_head")

        self.mock_exists = self.patcher_exists.start()
        self.mock_isdir = self.patcher_isdir.start()
        self.mock_verify = self.patcher_verify.start()

    def tearDown(self):
        self.patcher_exists.stop()
        self.patcher_isdir.stop()
        self.patcher_verify.stop()

    def test_a_valid_start_builder_single_launch(self):
        """A. Valid START_BUILDER + NEW launch right -> exactly one subprocess launch."""
        with patch.object(self.ledger, "claim", return_value={"new_launch": True}):
            with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_popen.return_value = mock_proc

                result = builder_adapter.launch(self._pkt, self._sha, self.ledger)

        mock_popen.assert_called_once()
        self.assertTrue(result["launched"])
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["timed_out"])

    def test_b_duplicate_claim_no_launch(self):
        """B. Duplicate claim / NEW_LAUNCH=NO -> zero subprocess launches."""
        with patch.object(self.ledger, "claim", return_value={"new_launch": False}):
            with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                result = builder_adapter.launch(self._pkt, self._sha, self.ledger)

        mock_popen.assert_not_called()
        self.assertFalse(result["launched"])
        self.assertEqual(result["refusal_reason"], "DUPLICATE_CLAIM: new_launch=NO")

    def test_c_hold_state_zero_launches(self):
        """C. HOLD state -> zero launches (Ledger claim raises exception)."""
        from telegraph.ledger import StateTransitionError
        with patch.object(self.ledger, "claim", side_effect=StateTransitionError("Packet is on HOLD")):
            with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                with self.assertRaises(AdapterError):
                    builder_adapter.launch(self._pkt, self._sha, self.ledger)

        mock_popen.assert_not_called()

    def test_d_wrong_action_zero_launches(self):
        """D. Wrong action -> zero launches."""
        # Mutate action and recompute sha
        self._pkt["action"] = "SOME_OTHER_ACTION"
        self._sha = compute_sha256(self._pkt)

        with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
            with self.assertRaises(AdapterError):
                builder_adapter.launch(self._pkt, self._sha, self.ledger)

        mock_popen.assert_not_called()

    def test_e_unknown_profile_zero_launches(self):
        """E. Unknown profile -> zero launches."""
        self._pkt["profile_id"] = "unknown-profile"
        self._sha = compute_sha256(self._pkt)

        with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
            with self.assertRaises(AdapterError):
                builder_adapter.launch(self._pkt, self._sha, self.ledger)

        mock_popen.assert_not_called()

    def test_h_packet_shell_metacharacters(self):
        """H. packet objective containing shell metacharacters -> remains data, never shell syntax."""
        self._pkt["objective"] = "echo 'hello'; rm -rf /"
        self._sha = compute_sha256(self._pkt)

        with patch.object(self.ledger, "claim", return_value={"new_launch": True}):
            with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_popen.return_value = mock_proc
                builder_adapter.launch(self._pkt, self._sha, self.ledger)

        kwargs = mock_popen.call_args[1]
        self.assertFalse(kwargs.get("shell", True), "shell=False MUST be explicit")

        argv = mock_popen.call_args[0][0]
        self.assertIn("echo 'hello'; rm -rf /", argv)

    def test_l_worker_exit_nonzero_no_retry(self):
        """L. worker exit non-zero -> no retry."""
        with patch.object(self.ledger, "claim", return_value={"new_launch": True}):
            with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.returncode = 42
                mock_popen.return_value = mock_proc

                result = builder_adapter.launch(self._pkt, self._sha, self.ledger)

        mock_popen.assert_called_once()
        self.assertTrue(result["launched"])
        self.assertEqual(result["exit_code"], 42)

    def test_m_timeout_no_retry(self):
        """M. timeout -> no retry."""
        with patch.object(self.ledger, "claim", return_value={"new_launch": True}):
            with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="fake", timeout=3600), None]
                mock_proc.returncode = -15
                mock_popen.return_value = mock_proc

                result = builder_adapter.launch(self._pkt, self._sha, self.ledger)

        mock_popen.assert_called_once()
        self.assertTrue(result["launched"])
        self.assertTrue(result["timed_out"])

    def test_fixed_build_stage(self):
        """launch() does not accept a stage parameter and uses _BUILDER_STAGE fixed to 'BUILD'."""
        import telegraph.builder_adapter as adapter
        self.assertEqual(adapter._BUILDER_STAGE, "BUILD")

        with patch.object(self.ledger, "claim", return_value={"new_launch": True}) as mock_claim:
            with patch("telegraph.builder_adapter.subprocess.Popen"):
                adapter.launch(self._pkt, self._sha, self.ledger)

        mock_claim.assert_called_once_with(self._sha, "BUILD")

    def test_missing_repo_no_claim(self):
        """Missing repo raises AdapterError BEFORE ledger.claim is called."""
        import telegraph.builder_adapter as adapter

        self._pkt["target"]["repo_root"] = "/does/not/exist"
        validate(self._pkt)
        new_sha = compute_sha256(self._pkt)

        self.mock_exists.return_value = False

        with patch.object(self.ledger, "claim") as mock_claim:
            with self.assertRaisesRegex(AdapterError, "does not exist"):
                adapter.launch(self._pkt, new_sha, self.ledger)

        mock_claim.assert_not_called()

    def test_pre_claim_head_mismatch_no_claim(self):
        """HEAD mismatch before claim raises AdapterError and NO claim is made."""
        import telegraph.builder_adapter as adapter
        self.patcher_verify.stop()

        with patch("telegraph.builder_adapter._verify_git_head", side_effect=AdapterError("mismatch")):
            with patch.object(self.ledger, "claim") as mock_claim:
                with self.assertRaises(AdapterError):
                    adapter.launch(self._pkt, self._sha, self.ledger)

        mock_claim.assert_not_called()
        self.patcher_verify.start()

    def test_post_claim_head_mismatch_holds_and_zero_popen(self):
        """HEAD mismatch AFTER claim records HOLD, calls zero Popen, and raises AdapterError."""
        import telegraph.builder_adapter as adapter
        self.patcher_verify.stop()

        call_count = []
        def fake_verify(repo, oid):
            call_count.append(1)
            if len(call_count) == 2:  # Fail on the second (post-claim) call
                raise AdapterError("HEAD moved")

        with patch.object(self.ledger, "claim", return_value={"new_launch": True}):
            with patch("telegraph.builder_adapter._verify_git_head", side_effect=fake_verify):
                with patch.object(self.ledger, "hold") as mock_hold:
                    with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                        with self.assertRaisesRegex(AdapterError, "POST_CLAIM_HEAD_MISMATCH"):
                            adapter.launch(self._pkt, self._sha, self.ledger)

        mock_hold.assert_called_once()
        self.assertEqual(mock_hold.call_args[0][0], self._sha)
        self.assertIn("POST_CLAIM_HEAD_MISMATCH", mock_hold.call_args[0][1])
        mock_popen.assert_not_called()
        self.patcher_verify.start()

    def test_fixed_git_executable(self):
        """_verify_git_head uses fixed absolute /usr/bin/git, avoiding PATH resolution."""
        import telegraph.builder_adapter as adapter
        self.patcher_verify.stop()
        self.assertEqual(adapter._GIT_EXECUTABLE, "/usr/bin/git")

        with patch("telegraph.builder_adapter.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=self._pkt["target"]["base_commit_oid"] + "\n")
            adapter._verify_git_head("/tmp", self._pkt["target"]["base_commit_oid"])

        argv = mock_run.call_args[0][0]
        self.assertEqual(argv[0], "/usr/bin/git")
        self.assertNotEqual(argv[0], "git")
        self.patcher_verify.start()

    def test_profile_executable_sole_popen_argv0(self):
        """Popen is invoked with argv[0] strictly drawn from profile.executable."""
        import telegraph.builder_adapter as adapter

        with patch.object(self.ledger, "claim", return_value={"new_launch": True}):
            with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_popen.return_value = mock_proc

                adapter.launch(self._pkt, self._sha, self.ledger)

        popen_args = mock_popen.call_args[0][0]
        # For agy-builder-v1, profile.executable is /home/r200dev/.local/bin/agy
        self.assertEqual(popen_args[0], "/home/r200dev/.local/bin/agy")
        self.assertEqual(popen_args[1], "--print")

    def test_pre_claim_cli_option_injection_rejected(self):
        """Adversarial test: objective = '--dangerously-skip-permissions' is blocked."""
        import telegraph.builder_adapter as adapter
        from telegraph.packet import compute_sha256, validate

        self._pkt["objective"] = "--dangerously-skip-permissions"
        validate(self._pkt)
        new_sha = compute_sha256(self._pkt)

        with patch.object(self.ledger, "claim") as mock_claim:
            with patch(
                "telegraph.builder_adapter.subprocess.Popen"
            ) as mock_popen:
                with self.assertRaisesRegex(
                    adapter.AdapterError,
                    "prevent CLI option injection",
                ):
                    adapter.launch(self._pkt, new_sha, self.ledger)

        mock_claim.assert_not_called()
        mock_popen.assert_not_called()

    def test_popen_oserror_holds_and_does_not_retry(self):
        """Popen raising OSError records HOLD, does not retry, and raises AdapterError."""
        import telegraph.builder_adapter as adapter

        with patch.object(self.ledger, "claim", return_value={"new_launch": True}) as mock_claim:
            with patch("telegraph.builder_adapter.subprocess.Popen", side_effect=OSError("Exec format error")) as mock_popen:
                with patch.object(self.ledger, "hold") as mock_hold:
                    with self.assertRaisesRegex(AdapterError, "BUILDER_LAUNCH_FAILED"):
                        adapter.launch(self._pkt, self._sha, self.ledger)

        mock_claim.assert_called_once_with(self._sha, "BUILD")
        mock_popen.assert_called_once()
        mock_hold.assert_called_once()
        self.assertEqual(mock_hold.call_args[0][0], self._sha)
        self.assertIn("BUILDER_LAUNCH_FAILED", mock_hold.call_args[0][1])

    def test_a_provenance_real_adapter_completion(self):
        """A. real adapter completion records one Builder result"""
        with patch.object(self.ledger, "claim", return_value={"new_launch": True}):
            with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_popen.return_value = mock_proc

                builder_adapter.launch(self._pkt, self._sha, self.ledger)

        self.ledger.record_builder_result.assert_called_once()
        kwargs = self.ledger.record_builder_result.call_args[1]
        self.assertEqual(kwargs["exit_code"], 0)
        self.assertEqual(kwargs["timed_out"], False)
        self.assertTrue(kwargs["launched"])

    def test_b_provenance_nonzero_exit(self):
        """B. non-zero exit records factual result"""
        with patch.object(self.ledger, "claim", return_value={"new_launch": True}):
            with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.returncode = 123
                mock_popen.return_value = mock_proc

                builder_adapter.launch(self._pkt, self._sha, self.ledger)

        self.ledger.record_builder_result.assert_called_once()
        kwargs = self.ledger.record_builder_result.call_args[1]
        self.assertEqual(kwargs["exit_code"], 123)

    def test_c_provenance_timeout(self):
        """C. timeout records factual result"""
        with patch.object(self.ledger, "claim", return_value={"new_launch": True}):
            with patch("telegraph.builder_adapter.subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="fake", timeout=3600), None]
                mock_proc.returncode = -15
                mock_popen.return_value = mock_proc

                builder_adapter.launch(self._pkt, self._sha, self.ledger)

        self.ledger.record_builder_result.assert_called_once()
        kwargs = self.ledger.record_builder_result.call_args[1]
        self.assertTrue(kwargs["timed_out"])
        self.assertEqual(kwargs["exit_code"], -15)

    def test_d_provenance_popen_oserror(self):
        """D. Popen OSError creates no completed Builder result"""
        with patch.object(self.ledger, "claim", return_value={"new_launch": True}):
            with patch("telegraph.builder_adapter.subprocess.Popen", side_effect=OSError("fail")):
                with self.assertRaises(AdapterError):
                    builder_adapter.launch(self._pkt, self._sha, self.ledger)

        self.ledger.record_builder_result.assert_not_called()

    def test_e_provenance_duplicate_dispatch(self):
        """E. duplicate dispatch creates no second result"""
        with patch.object(self.ledger, "claim", return_value={"new_launch": False}):
            builder_adapter.launch(self._pkt, self._sha, self.ledger)

        self.ledger.record_builder_result.assert_not_called()
if __name__ == "__main__":
    unittest.main()
