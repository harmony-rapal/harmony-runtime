"""
receipt.py — Receipt helpers for Harmony Telegraph Core.

Provides utilities for reading and formatting receipt chains.
Actual persistence is handled by ledger.py.

No subprocess, no shell, no network — standard library only.
"""

from __future__ import annotations

from typing import Any


def format_receipt(receipt: dict[str, Any]) -> str:
    """Return a human-readable representation of a single receipt."""
    lines = [
        f"RECEIPT_ID={receipt['receipt_id']}",
        f"SEQUENCE={receipt['sequence']}",
        f"FROM_STATE={receipt['from_state']}",
        f"TO_STATE={receipt['to_state']}",
        f"STAGE={receipt['stage']}",
        f"TIMESTAMP={receipt['timestamp']}",
        f"RECEIPT_SHA256={receipt['receipt_sha256']}",
        f"PREVIOUS_RECEIPT_SHA256={receipt.get('previous_receipt_sha256') or 'NONE'}",
    ]
    return "\n".join(lines)


def format_receipt_chain(receipts: list[dict[str, Any]]) -> str:
    """Return a formatted multi-receipt chain string."""
    blocks = []
    for r in receipts:
        blocks.append(format_receipt(r))
    return "\n\n".join(blocks)
