"""
approved_ingress.py — Single deterministic ingress for already-approved packets.

Provides the submit_approved_packet operation.
Zero execution surface.
"""

from typing import Any
from telegraph.ledger import Ledger, LedgerError

def submit_approved_packet(
    packet: dict[str, Any],
    packet_sha256: str,
    approver: str,
    ledger: Ledger,
) -> dict[str, Any]:
    """
    Ingest and approve a MissionPacketV1 in one operation.

    If the exact packet (by SHA) already exists, it is an idempotent no-op.
    If the packet has invalid schema, wrong SHA, or uses forbidden execution fields,
    it is rejected with zero ledger mutation.
    """
    try:
        ledger.ingest(packet, packet_sha256)
        ingest_status = "NEW"
    except LedgerError as e:
        if "already ingested" in str(e):
            # Check if it's an exact duplicate (SHA exists in DB)
            try:
                ledger.show(packet_sha256)
                ingest_status = "EXISTING"
            except LedgerError:
                # Collision: different SHA, same mission_id/rev
                raise e
        else:
            # Schema validation failed, SHA mismatch, etc.
            raise

    # At this point, the packet is securely in the DB (or already was).
    # Check its state and approve if READY.
    current_info = ledger.show(packet_sha256)

    if current_info["state"] == "READY":
        ledger.approve(packet_sha256, approver)
        approval_status = "NEW"
    else:
        approval_status = "EXISTING"

    final_info = ledger.show(packet_sha256)

    return {
        "packet_sha256": packet_sha256,
        "state": final_info["state"],
        "ingest_status": ingest_status,
        "approval_status": approval_status,
    }
