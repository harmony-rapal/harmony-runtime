import unittest
from unittest.mock import patch, MagicMock
from typing import Any

from telegraph.ledger import Ledger, LedgerError
from telegraph.packet import compute_sha256
from telegraph.builder_adapter import AdapterError
from telegraph.approved_builder_dispatch import dispatch_approved_packet

class TestApprovedBuilderDispatch(unittest.TestCase):
    def setUp(self):
        self.ledger_mock = MagicMock(spec=Ledger)
        self.packet = {
            "schema_version": 1,
            "mission_id": "M1",
            "revision": 1,
            "project_id": "P1",
            "profile_id": "agy-builder-v1",
            "action": "START_BUILDER",
            "target": {
                "repo_root": "/tmp/test",
                "base_commit_oid": "1b61d10490442a8842525d2aebab23f569dd91f7",
            },
            "objective": "test objective",
            "retry_policy": "NEVER",
            "on_failure": "HOLD"
        }
        self.packet_sha256 = compute_sha256(self.packet)
        self.ledger_mock.show.return_value = {"state": "APPROVED"}

    def test_exact_packet_binding(self):
        # wrong sha256
        result = dispatch_approved_packet(self.packet, "wrong_sha", self.ledger_mock)
        self.assertEqual(result["launch_attempted"], False)
        self.assertEqual(result["error"], "SHA256 mismatch")
        self.ledger_mock.show.assert_not_called()

    def test_ledger_lookup_failure(self):
        self.ledger_mock.show.side_effect = LedgerError("Not found")
        result = dispatch_approved_packet(self.packet, self.packet_sha256, self.ledger_mock)
        self.assertEqual(result["launch_attempted"], False)
        self.assertEqual(result["error"], "Packet not found in ledger")

    def test_approved_only(self):
        # state is NEW
        self.ledger_mock.show.return_value = {"state": "NEW"}
        result = dispatch_approved_packet(self.packet, self.packet_sha256, self.ledger_mock)
        self.assertEqual(result["launch_attempted"], False)
        self.assertIn("requires APPROVED or DISPATCHED", result["error"])

        # state is HOLD
        self.ledger_mock.show.return_value = {"state": "HOLD"}
        result = dispatch_approved_packet(self.packet, self.packet_sha256, self.ledger_mock)
        self.assertEqual(result["launch_attempted"], False)
        self.assertIn("requires APPROVED or DISPATCHED", result["error"])

    def test_start_builder_only(self):
        # action is something else
        bad_packet = dict(self.packet)
        bad_packet["action"] = "SOMETHING_ELSE"
        bad_sha = compute_sha256(bad_packet)
        self.ledger_mock.show.return_value = {"state": "APPROVED"}

        result = dispatch_approved_packet(bad_packet, bad_sha, self.ledger_mock)
        self.assertEqual(result["launch_attempted"], False)
        self.assertEqual(result["error"], "Action is not START_BUILDER")

    @patch("telegraph.approved_builder_dispatch.builder_adapter_launch")
    def test_builder_adapter_single_path_success(self, mock_launch):
        mock_launch.return_value = {"launched": True, "exit_code": 0}

        result = dispatch_approved_packet(self.packet, self.packet_sha256, self.ledger_mock)

        self.assertEqual(result["launch_attempted"], True)
        self.assertEqual(result["builder_result"], {"launched": True, "exit_code": 0})
        mock_launch.assert_called_once_with(self.packet, self.packet_sha256, self.ledger_mock)

    @patch("telegraph.approved_builder_dispatch.builder_adapter_launch")
    def test_builder_adapter_single_path_error(self, mock_launch):
        mock_launch.side_effect = AdapterError("Launch refused")

        result = dispatch_approved_packet(self.packet, self.packet_sha256, self.ledger_mock)

        self.assertEqual(result["launch_attempted"], True)
        self.assertEqual(result["builder_result"], None)
        self.assertEqual(result["error"], "Launch refused")
        mock_launch.assert_called_once_with(self.packet, self.packet_sha256, self.ledger_mock)

    @patch("telegraph.approved_builder_dispatch.builder_adapter_launch")
    def test_duplicate_zero_second_launch(self, mock_launch):
        # state is DISPATCHED
        self.ledger_mock.show.return_value = {"state": "DISPATCHED"}
        mock_launch.return_value = {"launched": False, "refusal_reason": "DUPLICATE_CLAIM: new_launch=NO"}

        result = dispatch_approved_packet(self.packet, self.packet_sha256, self.ledger_mock)

        self.assertEqual(result["launch_attempted"], True)
        self.assertEqual(result["builder_result"], {"launched": False, "refusal_reason": "DUPLICATE_CLAIM: new_launch=NO"})
        mock_launch.assert_called_once_with(self.packet, self.packet_sha256, self.ledger_mock)

if __name__ == "__main__":
    unittest.main()
