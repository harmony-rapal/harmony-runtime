"""
cli.py — Command-line interface for Harmony Telegraph Core.

Supported commands:
    ingest  <mission.json> --state-dir <dir>
    approve <packet_sha>   --approver <name>  --state-dir <dir>
    claim   <packet_sha>   --stage <stage>    --state-dir <dir>
    show    <packet_sha>                      --state-dir <dir>
    hold    <packet_sha>   --reason <reason>  --state-dir <dir>
    verify  <packet_sha>                      --state-dir <dir>

Output includes KEY=VALUE lines for machine-readable parsing.
Exit code is non-zero on any error (fail-closed).

No subprocess, no shell, no network — standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from telegraph import ledger as ledger_mod
from telegraph import packet as packet_mod
from telegraph.receipt import format_receipt_chain


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _emit(key: str, value: Any) -> None:
    print(f"{key}={value}")


def _die(message: str, code: int = 1) -> None:
    print(f"ERROR={message}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_ingest(args: argparse.Namespace) -> None:
    try:
        pkt, sha = packet_mod.load_and_validate(args.mission_json)
    except (packet_mod.PacketValidationError, json.JSONDecodeError, OSError) as exc:
        _die(str(exc))

    db = ledger_mod.Ledger(args.state_dir)
    try:
        result = db.ingest(pkt, sha)
    except ledger_mod.LedgerError as exc:
        _die(str(exc))
    finally:
        db.close()

    _emit("PACKET_SHA256", result["packet_sha256"])
    _emit("STATE", result["state"])
    _emit("MISSION_ID", result["mission_id"])
    _emit("REVISION", result["revision"])
    _emit("RECEIPT_ID", result["receipt_id"])


def cmd_approve(args: argparse.Namespace) -> None:
    db = ledger_mod.Ledger(args.state_dir)
    try:
        gate_receipt = None
        approver = args.approver
        allowed_signers = getattr(args, "allowed_signers", None)
        if getattr(args, "receipt", None):
            from telegraph.human_gate import load_and_verify_gate_receipt
            gate_receipt = load_and_verify_gate_receipt(
                args.receipt,
                expected_packet_sha256=args.packet_sha,
                allowed_signers_path=allowed_signers,
            )
            if not approver:
                approver = gate_receipt["signer"]
        result = db.approve(
            args.packet_sha,
            approver,
            gate_receipt=gate_receipt,
            allowed_signers_path=allowed_signers,
        )
    except (ledger_mod.LedgerError, ledger_mod.StateTransitionError, Exception) as exc:
        _die(str(exc))
    finally:
        db.close()

    _emit("PACKET_SHA256", result["packet_sha256"])
    _emit("STATE", result["state"])
    _emit("APPROVAL_ID", result["approval_id"])
    _emit("APPROVER", result["approver"])
    _emit("RECEIPT_ID", result["receipt_id"])
    if result.get("gate_receipt_id"):
        _emit("GATE_RECEIPT_ID", result["gate_receipt_id"])


def cmd_approve_gate(args: argparse.Namespace) -> None:
    db = ledger_mod.Ledger(args.state_dir)
    try:
        from telegraph.human_gate import load_and_verify_gate_receipt
        allowed_signers = getattr(args, "allowed_signers", None)
        gate_receipt = load_and_verify_gate_receipt(
            args.receipt,
            expected_packet_sha256=args.packet_sha,
            allowed_signers_path=allowed_signers,
        )
        result = db.approve(
            args.packet_sha,
            gate_receipt["signer"],
            gate_receipt=gate_receipt,
            allowed_signers_path=allowed_signers,
        )
    except (ledger_mod.LedgerError, ledger_mod.StateTransitionError, Exception) as exc:
        _die(str(exc))
    finally:
        db.close()

    _emit("PACKET_SHA256", result["packet_sha256"])
    _emit("STATE", result["state"])
    _emit("APPROVAL_ID", result["approval_id"])
    _emit("APPROVER", result["approver"])
    _emit("RECEIPT_ID", result["receipt_id"])
    if result.get("gate_receipt_id"):
        _emit("GATE_RECEIPT_ID", result["gate_receipt_id"])


def cmd_finalize(args: argparse.Namespace) -> None:
    from telegraph.builder_evidence import EvidenceStore
    evidence_store = EvidenceStore(args.evidence_dir)
    db = ledger_mod.Ledger(args.state_dir)
    try:
        result = db.finalize(args.packet_sha, args.evidence_sha, evidence_store)
    except (ledger_mod.LedgerError, ledger_mod.StateTransitionError, Exception) as exc:
        _die(str(exc))
    finally:
        db.close()

    _emit("PACKET_SHA256", result["packet_sha256"])
    _emit("STATE", result["state"])
    _emit("EVIDENCE_SHA256", result["evidence_sha256"])
    _emit("RECEIPT_ID", result["receipt_id"])


def cmd_recover(args: argparse.Namespace) -> None:
    db = ledger_mod.Ledger(args.state_dir)
    try:
        result = db.recover(args.packet_sha, args.claim_id, args.reason)
    except (ledger_mod.LedgerError, ledger_mod.StateTransitionError, Exception) as exc:
        _die(str(exc))
    finally:
        db.close()

    _emit("PACKET_SHA256", result["packet_sha256"])
    _emit("STATE", result["state"])
    _emit("CLAIM_ID", result["claim_id"])
    _emit("REASON", result["reason"])
    _emit("RECEIPT_ID", result["receipt_id"])



def cmd_claim(args: argparse.Namespace) -> None:
    db = ledger_mod.Ledger(args.state_dir)
    try:
        result = db.claim(args.packet_sha, args.stage)
    except (ledger_mod.LedgerError, ledger_mod.StateTransitionError) as exc:
        _die(str(exc))
    finally:
        db.close()

    _emit("PACKET_SHA256", result["packet_sha256"])
    _emit("STAGE", result["stage"])
    _emit("CLAIM_STATUS", result["claim_status"])
    _emit("NEW_LAUNCH", "YES" if result["new_launch"] else "NO")
    _emit("CLAIM_ID", result["claim_id"])
    if result["claim_status"] == "NEW":
        _emit("RECEIPT_ID", result.get("receipt_id", ""))


def cmd_show(args: argparse.Namespace) -> None:
    db = ledger_mod.Ledger(args.state_dir)
    try:
        result = db.show(args.packet_sha)
    except ledger_mod.LedgerError as exc:
        _die(str(exc))
    finally:
        db.close()

    _emit("PACKET_SHA256", result["packet_sha256"])
    _emit("STATE", result["state"])
    _emit("MISSION_ID", result["mission_id"])
    _emit("REVISION", result["revision"])
    _emit("ACTION", result["action"])
    _emit("INGESTED_AT", result["ingested_at"])
    _emit("UPDATED_AT", result["updated_at"])
    _emit("APPROVALS", len(result["approvals"]))
    _emit("CLAIMS", len(result["claims"]))
    _emit("RECEIPTS", len(result["receipts"]))

    if result["claims"]:
        print("")
        for claim in result["claims"]:
            _emit("CLAIM_ID", claim["claim_id"])
            _emit("CLAIM_STAGE", claim["stage"])
            _emit("CLAIMED_AT", claim["claimed_at"])

    if result["receipts"]:
        print("")
        print(format_receipt_chain(result["receipts"]))


def cmd_hold(args: argparse.Namespace) -> None:
    db = ledger_mod.Ledger(args.state_dir)
    try:
        result = db.hold(args.packet_sha, args.reason)
    except (ledger_mod.LedgerError, ledger_mod.StateTransitionError) as exc:
        _die(str(exc))
    finally:
        db.close()

    _emit("PACKET_SHA256", result["packet_sha256"])
    _emit("STATE", result["state"])
    _emit("REASON", result["reason"])
    _emit("RECEIPT_ID", result["receipt_id"])


def cmd_verify(args: argparse.Namespace) -> None:
    db = ledger_mod.Ledger(args.state_dir)
    try:
        result = db.verify(args.packet_sha)
    except ledger_mod.ReceiptChainError as exc:
        db.close()
        _emit("PACKET_SHA256", args.packet_sha)
        _emit("VALID", "NO")
        _die(str(exc))
    except ledger_mod.LedgerError as exc:
        _die(str(exc))
    finally:
        db.close()

    _emit("PACKET_SHA256", result["packet_sha256"])
    _emit("VALID", "YES")
    _emit("RECEIPTS_VERIFIED", result["receipts_verified"])


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegraph",
        description="Harmony Telegraph Core — deterministic fail-closed state machine.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest a mission JSON file.")
    p_ingest.add_argument("mission_json", help="Path to mission.json")
    p_ingest.add_argument("--state-dir", required=True, help="State directory")

    # approve
    p_approve = sub.add_parser("approve", help="Approve a packet (READY → APPROVED).")
    p_approve.add_argument("packet_sha", help="Packet SHA256")
    p_approve.add_argument("--approver", default=None, help="Approver identity string")
    p_approve.add_argument("--receipt", default=None, help="Human gate receipt JSON path")
    p_approve.add_argument("--allowed-signers", default=None, help="OpenSSH allowed_signers path")
    p_approve.add_argument("--state-dir", required=True, help="State directory")

    # approve-gate
    p_ag = sub.add_parser("approve-gate", help="Approve a packet using verified human gate receipt.")
    p_ag.add_argument("packet_sha", help="Packet SHA256")
    p_ag.add_argument("--receipt", required=True, help="Human gate receipt JSON path")
    p_ag.add_argument("--allowed-signers", default=None, help="OpenSSH allowed_signers path")
    p_ag.add_argument("--state-dir", required=True, help="State directory")

    # finalize
    p_fin = sub.add_parser("finalize", help="Finalize dispatched packet with frozen evidence.")
    p_fin.add_argument("packet_sha", help="Packet SHA256")
    p_fin.add_argument("--evidence-sha", required=True, help="Evidence content SHA256")
    p_fin.add_argument("--evidence-dir", required=True, help="Evidence store directory")
    p_fin.add_argument("--state-dir", required=True, help="State directory")

    # recover
    p_rec = sub.add_parser("recover", help="Explicitly recover a stranded DISPATCHED packet to HOLD.")
    p_rec.add_argument("packet_sha", help="Packet SHA256")
    p_rec.add_argument("--claim-id", required=True, help="Existing claim ID to verify")
    p_rec.add_argument("--reason", required=True, help="Recovery reason")
    p_rec.add_argument("--state-dir", required=True, help="State directory")

    # claim
    p_claim = sub.add_parser("claim", help="Claim a stage (APPROVED → DISPATCHED).")
    p_claim.add_argument("packet_sha", help="Packet SHA256")
    p_claim.add_argument("--stage", required=True, help="Stage name (e.g. BUILD)")
    p_claim.add_argument("--state-dir", required=True, help="State directory")

    # show
    p_show = sub.add_parser("show", help="Show packet status and receipt chain.")
    p_show.add_argument("packet_sha", help="Packet SHA256")
    p_show.add_argument("--state-dir", required=True, help="State directory")

    # hold
    p_hold = sub.add_parser("hold", help="Transition a packet to HOLD state.")
    p_hold.add_argument("packet_sha", help="Packet SHA256")
    p_hold.add_argument("--reason", required=True, help="Hold reason")
    p_hold.add_argument("--state-dir", required=True, help="State directory")

    # verify
    p_verify = sub.add_parser("verify", help="Verify receipt chain integrity.")
    p_verify.add_argument("packet_sha", help="Packet SHA256")
    p_verify.add_argument("--state-dir", required=True, help="State directory")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "ingest": cmd_ingest,
        "approve": cmd_approve,
        "approve-gate": cmd_approve_gate,
        "claim": cmd_claim,
        "show": cmd_show,
        "hold": cmd_hold,
        "verify": cmd_verify,
        "finalize": cmd_finalize,
        "recover": cmd_recover,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        _die(f"Unknown command: {args.command!r}")

    handler(args)
