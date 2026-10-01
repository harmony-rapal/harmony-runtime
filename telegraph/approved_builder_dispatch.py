"""
approved_builder_dispatch.py — 003B Approved to Builder Dispatch.

Connects the 003A Approved Packet Ingress to the 002B Builder Adapter.
"""

from typing import Any
from telegraph.ledger import Ledger, LedgerError
from telegraph.builder_adapter import launch as builder_adapter_launch, AdapterError
from telegraph.packet import compute_sha256


def dispatch_approved_packet(
    packet: dict[str, Any],
    packet_sha256: str,
    ledger: Ledger,
) -> dict[str, Any]:
    """
    Safely dispatches an approved packet to the builder adapter.
    """
    # 1. EXACT PACKET IDENTITY
    if compute_sha256(packet) != packet_sha256:
        return {
            "packet_sha256": packet_sha256,
            "pre_dispatch_state": "UNKNOWN",
            "launch_attempted": False,
            "builder_result": None,
            "error": "SHA256 mismatch",
        }

    # 2. STATE LOOKUP
    try:
        info = ledger.show(packet_sha256)
        pre_state = info["state"]
    except LedgerError:
        return {
            "packet_sha256": packet_sha256,
            "pre_dispatch_state": "UNKNOWN",
            "launch_attempted": False,
            "builder_result": None,
            "error": "Packet not found in ledger",
        }

    # 3. APPROVAL MANDATORY
    if pre_state not in ("APPROVED", "DISPATCHED"):
        return {
            "packet_sha256": packet_sha256,
            "pre_dispatch_state": pre_state,
            "launch_attempted": False,
            "builder_result": None,
            "error": f"Packet state is {pre_state}, requires APPROVED or DISPATCHED",
        }

    # 4. ACTION
    if packet.get("action") != "START_BUILDER":
        return {
            "packet_sha256": packet_sha256,
            "pre_dispatch_state": pre_state,
            "launch_attempted": False,
            "builder_result": None,
            "error": "Action is not START_BUILDER",
        }

    # 5. DISPATCH
    try:
        builder_result = builder_adapter_launch(packet, packet_sha256, ledger)
        return {
            "packet_sha256": packet_sha256,
            "pre_dispatch_state": pre_state,
            "launch_attempted": True,
            "builder_result": builder_result,
        }
    except AdapterError as e:
        return {
            "packet_sha256": packet_sha256,
            "pre_dispatch_state": pre_state,
            "launch_attempted": True,
            "builder_result": None,
            "error": str(e),
        }
