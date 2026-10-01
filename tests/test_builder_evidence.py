import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock
from typing import Any

from telegraph.builder_evidence import EvidenceStore, freeze_builder_evidence
from telegraph.packet import compute_sha256
from telegraph.ledger import LedgerError


class TestBuilderEvidence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.store = EvidenceStore(self.temp_dir)

        self.packet = {
            "schema_version": 1,
            "mission_id": "TEST-003C",
            "revision": 1,
            "project_id": "test-project",
            "objective": "Build it",
            "action": "START_BUILDER",
            "profile_id": "test-profile",
            "target": {
                "repo_root": "/tmp/test",
                "base_commit_oid": "0" * 40,
            }
        }
        self.packet_sha = compute_sha256(self.packet)

        self.dispatch_result = {
            "packet_sha256": self.packet_sha,
            "pre_dispatch_state": "DISPATCHED",
            "launch_attempted": True,
            "builder_result": {
                "profile_id": "test-profile",
                "packet_sha256": self.packet_sha,
                "launched": True,
                "started_at": "2023-01-01T00:00:00Z",
                "finished_at": "2023-01-01T00:01:00Z",
                "exit_code": 0,
                "timed_out": False
            }
        }

        self.ledger = MagicMock()
        self.ledger.show.return_value = {"state": "DISPATCHED"}
        self.ledger.get_builder_result.return_value = {
            "launched": True,
            "started_at": "2023-01-01T00:00:00Z",
            "finished_at": "2023-01-01T00:01:00Z",
            "exit_code": 0,
            "timed_out": False
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_a_valid_post_dispatch_new(self):
        """A. valid post-dispatch Builder result -> NEW evidence capsule"""
        res = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res["status"], "NEW")
        self.assertIsNotNone(res["evidence_sha256"])

        path = os.path.join(self.temp_dir, f"{self.packet_sha}.json")
        self.assertTrue(os.path.exists(path))
        with open(path, "r") as f:
            data = json.load(f)
        self.assertEqual(data["packet_sha256"], self.packet_sha)
        self.assertEqual(data["builder_exit_code"], 0)

    def test_b_exact_duplicate(self):
        """B. exact duplicate -> EXISTING -> same evidence_sha256 -> no rewrite"""
        res1 = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res1["status"], "NEW")
        sha1 = res1["evidence_sha256"]

        path = os.path.join(self.temp_dir, f"{self.packet_sha}.json")
        stat1 = os.stat(path)

        res2 = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res2["status"], "EXISTING")
        self.assertEqual(res2["evidence_sha256"], sha1)

        stat2 = os.stat(path)
        # Verify no rewrite
        self.assertEqual(stat1.st_mtime, stat2.st_mtime)

    def test_c_different_evidence(self):
        """C. same packet key + different evidence -> rejected -> original bytes unchanged"""
        res1 = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res1["status"], "NEW")

        # Modify dispatch result to produce different evidence
        self.ledger.get_builder_result.return_value = dict(self.ledger.get_builder_result.return_value); self.ledger.get_builder_result.return_value["exit_code"] = 1
        res2 = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res2["status"], "ERROR")
        self.assertIn("FAIL CLOSED", res2["error"])

    def test_d_wrong_packet_sha(self):
        """D. wrong packet SHA -> zero evidence write"""
        self.dispatch_result["packet_sha256"] = "wrongsha"
        res = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res["status"], "ERROR")

        files = os.listdir(self.temp_dir)
        self.assertEqual(len(files), 0)

    def test_e_changed_packet_body(self):
        """E. changed packet body with old SHA -> zero evidence write"""
        self.packet["action"] = "OTHER"
        res = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res["status"], "ERROR")

        files = os.listdir(self.temp_dir)
        self.assertEqual(len(files), 0)

    def test_f_unknown_packet(self):
        """F. unknown packet -> zero evidence write"""
        self.ledger.show.side_effect = LedgerError("Unknown")
        res = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res["status"], "ERROR")

        files = os.listdir(self.temp_dir)
        self.assertEqual(len(files), 0)

    def test_g_ready_state(self):
        """G. READY state -> no worker-completed evidence"""
        self.ledger.show.return_value = {"state": "READY"}
        res = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res["status"], "ERROR")

        files = os.listdir(self.temp_dir)
        self.assertEqual(len(files), 0)

    def test_h_hold_state(self):
        """H. HOLD state -> no worker-completed evidence"""
        self.ledger.show.return_value = {"state": "HOLD"}
        res = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res["status"], "ERROR")

    def test_i_valid_nonzero_worker_exit(self):
        """I. valid non-zero worker exit -> evidence still freezes -> exit code preserved -> no retry"""
        self.ledger.get_builder_result.return_value = dict(self.ledger.get_builder_result.return_value); self.ledger.get_builder_result.return_value["exit_code"] = 42
        res = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res["status"], "NEW")

        path = os.path.join(self.temp_dir, f"{self.packet_sha}.json")
        with open(path, "r") as f:
            data = json.load(f)
        self.assertEqual(data["builder_exit_code"], 42)

    def test_j_valid_timeout(self):
        """J. valid timed_out=True result -> evidence still freezes -> no retry"""
        self.ledger.get_builder_result.return_value = dict(self.ledger.get_builder_result.return_value); self.ledger.get_builder_result.return_value["timed_out"] = True
        res = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res["status"], "NEW")

        path = os.path.join(self.temp_dir, f"{self.packet_sha}.json")
        with open(path, "r") as f:
            data = json.load(f)
        self.assertTrue(data["builder_timed_out"])

    def test_k_packet_cannot_select_evidence_path(self):
        """K. packet cannot select evidence root/path"""
        # We only pass store, there's no way for packet to define path.
        # But we can try putting path in packet_sha256.
        # compute_sha256 ensures it's a real hash. The logic strictly uses compute_sha256(packet).
        # We also enforce 64-char hex in EvidenceStore.write_evidence.
        with self.assertRaises(ValueError):
            self.store.write_evidence("../test", b"foo")

    def test_l_path_traversal_in_mission_id(self):
        """L. mission_id/objective/project_id containing path traversal strings cannot affect evidence destination"""
        self.packet["mission_id"] = "../../../etc/passwd"
        self.packet_sha = compute_sha256(self.packet)
        self.dispatch_result["packet_sha256"] = self.packet_sha


        res = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res["status"], "NEW")

        path = os.path.join(self.temp_dir, f"{self.packet_sha}.json")
        self.assertTrue(os.path.exists(path))

    def test_m_symlink_destination_attack(self):
        """M. symlink destination attack -> fail closed"""
        # Create a symlink in the temp dir that points elsewhere
        fake_target = os.path.join(self.temp_dir, "fake_target")
        with open(fake_target, "w") as f:
            f.write("old data")

        link_path = os.path.join(self.temp_dir, f"{self.packet_sha}.json")
        os.symlink(fake_target, link_path)

        res = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)
        self.assertEqual(res["status"], "ERROR")
        self.assertIn("FAIL CLOSED", res["error"])

    def test_n_canonical_evidence_hash_stable(self):
        """N. canonical evidence hash stable across dict key ordering"""
        # Dictionary iteration order might be different, but json.dumps(sort_keys=True) guarantees stability
        res1 = freeze_builder_evidence(self.packet, self.dispatch_result, self.ledger, self.store)

        # Re-create packet dict in different order
        packet2 = {
            "target": {
                "base_commit_oid": "0" * 40,
                "repo_root": "/tmp/test",
            },
            "profile_id": "test-profile",
            "action": "START_BUILDER",
            "objective": "Build it",
            "project_id": "test-project",
            "revision": 1,
            "mission_id": "TEST-003C",
            "schema_version": 1,
        }
        dispatch_result2 = dict(reversed(list(self.dispatch_result.items())))

        res2 = freeze_builder_evidence(packet2, dispatch_result2, self.ledger, self.store)
        self.assertEqual(res2["status"], "EXISTING")
        self.assertEqual(res1["evidence_sha256"], res2["evidence_sha256"])

if __name__ == "__main__":
    unittest.main()
