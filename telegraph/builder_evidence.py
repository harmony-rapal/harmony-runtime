import json
import os
import hashlib
from typing import Any, Tuple

from telegraph.packet import compute_sha256
from telegraph.ledger import Ledger, LedgerError

class EvidenceStore:
    def __init__(self, root: str):
        self._root = os.path.abspath(root)
        if not os.path.exists(self._root):
            os.makedirs(self._root)

    def write_evidence(self, packet_sha256: str, evidence_bytes: bytes) -> Tuple[str, str]:
        if len(packet_sha256) != 64 or not all(c in "0123456789abcdef" for c in packet_sha256):
            raise ValueError("Invalid packet_sha256 format, must be 64-char lowercase hex")

        evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()

        path = os.path.join(self._root, f"{packet_sha256}.json")

        if os.path.exists(path) or os.path.islink(path):
            try:
                with open(path, "rb") as f:
                    existing_bytes = f.read()
            except FileNotFoundError:
                raise RuntimeError("FAIL CLOSED: Broken symlink detected")
            if existing_bytes == evidence_bytes:
                return "EXISTING", evidence_sha256
            else:
                raise RuntimeError("FAIL CLOSED: evidence collision with different bytes")

        try:
            # os.O_EXCL ensures write-once atomicity. If a file or symlink exists, it fails.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            with os.fdopen(fd, "wb") as f:
                f.write(evidence_bytes)
        except FileExistsError:
            try:
                with open(path, "rb") as f:
                    existing_bytes = f.read()
            except FileNotFoundError:
                raise RuntimeError("FAIL CLOSED: Broken symlink detected")
            if existing_bytes == evidence_bytes:
                return "EXISTING", evidence_sha256
            else:
                raise RuntimeError("FAIL CLOSED: evidence collision with different bytes")

        return "NEW", evidence_sha256

def freeze_builder_evidence(
    packet: dict[str, Any],
    dispatch_result: dict[str, Any],
    ledger: Ledger,
    store: EvidenceStore
) -> dict[str, Any]:
    """
    Implements the smallest deterministic evidence-freeze layer.
    """
    packet_sha256 = compute_sha256(packet)

    if dispatch_result.get("packet_sha256") != packet_sha256:
        return {"status": "ERROR", "reason": "packet_sha256 mismatch", "evidence_sha256": None}

    try:
        ledger_info = ledger.show(packet_sha256)
    except LedgerError:
        return {"status": "ERROR", "reason": "unknown packet in ledger", "evidence_sha256": None}

    state = ledger_info.get("state")
    if state in ("READY", "HOLD") or state is None:
        return {"status": "ERROR", "reason": f"invalid post-dispatch state: {state}", "evidence_sha256": None}

    builder_result = ledger.get_builder_result(packet_sha256)
    if not builder_result:
        return {"status": "ERROR", "reason": "no builder result found in ledger", "evidence_sha256": None}

    # Extract C7 authoritative facts
    claim_id = builder_result.get("claim_id")
    if not claim_id and ledger_info.get("claims"):
        claim_id = ledger_info["claims"][-1].get("claim_id")

    actual_uid = builder_result.get("actual_uid")
    actual_gid = builder_result.get("actual_gid")

    # Find the builder result receipt
    builder_receipt_id = builder_result.get("receipt_id")
    if not builder_receipt_id:
        for r in ledger_info.get("receipts", []):
            if r.get("stage") == "BUILDER_RESULT":
                builder_receipt_id = r.get("receipt_id")
                break

    # Recompute to canonical JSON dict
    canonical_evidence = {
        "schema_version": 1,
        "packet_sha256": packet_sha256,
        "mission_id": packet.get("mission_id"),
        "revision": packet.get("revision"),
        "project_id": packet.get("project_id"),
        "action": packet.get("action"),
        "profile_id": packet.get("profile_id"),
        "base_commit_oid": packet.get("target", {}).get("base_commit_oid"),
        "ledger_state": state,
        "dispatch_launch_attempted": dispatch_result.get("launch_attempted"),
        "builder_launched": bool(builder_result.get("launched")),
        "builder_exit_code": builder_result.get("exit_code"),
        "builder_timed_out": bool(builder_result.get("timed_out")),
        "builder_started_at": builder_result.get("started_at"),
        "builder_finished_at": builder_result.get("finished_at"),
        "claim_id": claim_id,
        "player_identity": {
            "uid": actual_uid,
            "gid": actual_gid,
        },
        "executable_path": builder_result.get("executable_path"),
        "executable_digest": builder_result.get("executable_digest"),
        "helper_digest": builder_result.get("helper_digest"),
        "human_gate_provenance": ledger_info.get("human_gate_receipt"),
        "builder_receipt_id": builder_receipt_id,
    }

    # Use canonical JSON (deterministic)
    evidence_bytes = json.dumps(
        canonical_evidence,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False
    ).encode('utf-8')

    try:
        status, evidence_sha256 = store.write_evidence(packet_sha256, evidence_bytes)
        return {
            "status": status,
            "evidence_sha256": evidence_sha256,
            "error": None
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "evidence_sha256": None,
            "error": str(e)
        }
