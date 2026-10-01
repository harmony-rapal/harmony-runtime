"""
actuator_service.py — Serial Actuator Service and Unix Domain Socket Host.

Owns:
  - Canonical Ledger access (inaccessible to ordinary callers)
  - BUILD execution authority via builder_adapter.launch
  - Canonical Builder result persistence
  - Fail-closed OS peer credential checks:
      1. actuator_uid != allowed_client_uid (startup invariant)
      2. peer_uid == allowed_client_uid (exact expected caller authorization)
      3. All other UIDs rejected immediately before reading data
  - Exact production authority environment validation:
      1. state_dir: directory, owner == actuator UID, mode exactly 0700
      2. DB files (ledger.db, -wal, -shm): regular file, owner == actuator UID, mode exactly 0600
         (guaranteed on first creation via umask 0077 and verified fail-closed)
      3. socket parent: absolute path, trusted owner (actuator UID or root),
         expected harmony-telegraph group, zero world access,
         owner/mode matrix: (owner == actuator -> 0770/0750; owner == root -> 0770)
      4. socket file: expected actuator owner, expected harmony-telegraph group,
         fixed mode 0660 (owner + group only), zero world access
  - Bounded request read (MAX_MESSAGE_BYTES, newline-delimited, 5s timeout)
  - Serial dispatch loop (deterministic, no worker pools)
"""

from __future__ import annotations

import json
import os
import socket
import stat
import struct
from typing import Any

from telegraph.actuator_protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    format_error_response,
    format_ok_response,
    parse_and_validate_request,
)
from telegraph.builder_evidence import EvidenceStore, freeze_builder_evidence
from telegraph.human_gate import HumanGateVerificationError, verify_gate_receipt_dict
from telegraph.ledger import Ledger, LedgerError, StateTransitionError
from telegraph.runner_client import RunnerClient

# Linux SO_PEERCRED constant
_SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)


class AuthorityConfigurationError(RuntimeError):
    """Raised when host authority prerequisites are violated."""


def ensure_ledger_authority(state_dir: str) -> None:
    """
    Verify every existing canonical SQLite file:
      - regular file
      - owner UID == actuator effective UID
      - mode exactly 0600
    Fail closed if the resulting filesystem state violates the invariant.
    """
    current_uid = os.geteuid()
    for file_name in ("ledger.db", "ledger.db-wal", "ledger.db-shm"):
        target_file = os.path.join(state_dir, file_name)
        if os.path.exists(target_file):
            fst = os.stat(target_file)
            if not stat.S_ISREG(fst.st_mode):
                raise AuthorityConfigurationError(
                    f"{file_name} in {state_dir!r} must be a regular file"
                )
            if fst.st_uid != current_uid:
                raise AuthorityConfigurationError(
                    f"{file_name} is owned by UID {fst.st_uid}, expected Actuator UID {current_uid}"
                )
            fmode = stat.S_IMODE(fst.st_mode)
            if fmode != 0o600:
                raise AuthorityConfigurationError(
                    f"{file_name} has mode {oct(fmode)}, must be mode exactly 0600"
                )


def open_actuator_ledger(state_dir: str) -> Ledger:
    """
    Establish restrictive umask 0077 and open/create the Ledger.
    Guarantees that initial creation of canonical SQLite files produces 0600
    files without broader permissions, then verifies the resulting files.
    """
    os.umask(0o077)
    ledger = Ledger(state_dir)
    ensure_ledger_authority(state_dir)
    return ledger


def verify_authority_environment(
    state_dir: str,
    socket_path: str,
    allowed_client_uid: int,
    expected_socket_gid: int,
) -> None:
    """
    Validate filesystem ownership and permissions for the Actuator authority model.

    Fail-closed requirements:
      1. allowed_client_uid is mandatory and must not equal actuator effective UID.
      2. expected_socket_gid is mandatory.
      3. Canonical state directory:
         - must be a directory
         - owner UID == actuator effective UID
         - mode must be exactly 0700
      4. Database files (ledger.db, ledger.db-wal, ledger.db-shm):
         - must be regular files
         - owner UID == actuator effective UID
         - mode must be exactly 0600
      5. socket_path parent directory:
         - must be an absolute path
         - must exist and be a directory
         - GID must match expected_socket_gid (harmony-telegraph group)
         - zero world access (no world r/w/x)
         - valid owner/mode matrix:
             * owner == actuator effective UID: mode must be 0770 or 0750
             * owner == 0 (root): mode must be exactly 0770 (group-writable for actuator)
             * any other owner: rejected
    """
    current_uid = os.geteuid()

    # 1. Distinct-UID invariant (mandatory, no bypass)
    if not isinstance(allowed_client_uid, int) or isinstance(allowed_client_uid, bool) or allowed_client_uid < 0:
        raise AuthorityConfigurationError(
            f"allowed_client_uid must be a non-negative integer, got {allowed_client_uid!r}"
        )

    if allowed_client_uid == current_uid:
        raise AuthorityConfigurationError(
            f"Startup rejected: actuator effective UID ({current_uid}) and allowed_client_uid ({allowed_client_uid}) "
            "cannot be identical. Same-UID execution is not a valid authority boundary."
        )

    # 2. expected_socket_gid validation (mandatory)
    if not isinstance(expected_socket_gid, int) or isinstance(expected_socket_gid, bool) or expected_socket_gid < 0:
        raise AuthorityConfigurationError(
            f"expected_socket_gid must be a non-negative integer, got {expected_socket_gid!r}"
        )

    # 3. Canonical state directory validation
    if not os.path.exists(state_dir):
        raise AuthorityConfigurationError(f"state_dir {state_dir!r} does not exist")

    st = os.stat(state_dir)
    if not stat.S_ISDIR(st.st_mode):
        raise AuthorityConfigurationError(f"state_dir {state_dir!r} must be a directory")

    if st.st_uid != current_uid:
        raise AuthorityConfigurationError(
            f"state_dir {state_dir!r} is owned by UID {st.st_uid}, "
            f"expected Actuator service UID {current_uid}"
        )

    mode_bits = stat.S_IMODE(st.st_mode)
    if mode_bits != 0o700:
        raise AuthorityConfigurationError(
            f"state_dir {state_dir!r} has mode {oct(mode_bits)}, "
            "must be mode exactly 0700"
        )

    # 4. Canonical database files validation
    ensure_ledger_authority(state_dir)

    # 5. socket_path parent validation
    if not os.path.isabs(socket_path):
        raise AuthorityConfigurationError(
            f"socket_path {socket_path!r} must be an absolute path"
        )

    parent_dir = os.path.dirname(socket_path)
    if not os.path.exists(parent_dir):
        raise AuthorityConfigurationError(
            f"socket parent directory {parent_dir!r} does not exist"
        )

    pst = os.stat(parent_dir)
    if not stat.S_ISDIR(pst.st_mode):
        raise AuthorityConfigurationError(
            f"socket parent {parent_dir!r} must be a directory"
        )

    # Must match expected harmony-telegraph group
    if pst.st_gid != expected_socket_gid:
        raise AuthorityConfigurationError(
            f"socket parent directory {parent_dir!r} has GID {pst.st_gid}, "
            f"expected harmony-telegraph group GID {expected_socket_gid}"
        )

    pmode = stat.S_IMODE(pst.st_mode)
    # Zero world access
    if (pmode & 0o007) != 0:
        raise AuthorityConfigurationError(
            f"socket parent directory {parent_dir!r} permits world access ({oct(pmode)}); "
            "production socket parent must have zero world access"
        )

    # Owner / Mode matrix validation:
    # Actuator must have write authority in the directory to create/unlink the socket.
    if pst.st_uid == current_uid:
        if pmode not in (0o770, 0o750):
            raise AuthorityConfigurationError(
                f"actuator-owned socket parent {parent_dir!r} has mode {oct(pmode)}, "
                "expected mode 0770 or 0750"
            )
    elif pst.st_uid == 0:
        if pmode != 0o770:
            raise AuthorityConfigurationError(
                f"root-owned socket parent {parent_dir!r} has mode {oct(pmode)}, "
                "must be mode exactly 0770 to permit harmony-actuator socket creation"
            )
    else:
        raise AuthorityConfigurationError(
            f"socket parent directory {parent_dir!r} is owned by untrusted UID {pst.st_uid}"
        )


def _load_and_verify_gate_receipt_from_dir(
    gate_receipt_dir: str,
    packet_sha256: str,
    mission_id: str,
    allowed_signers_path: str | None = None,
) -> dict[str, Any]:
    """
    Fail-closed loader and verifier for Human Gate receipts from trusted gate_receipt_dir.
    Checks:
      - absolute path
      - no symlinks anywhere in gate_receipt_dir or candidate file
      - directory/file must not be world-writable
      - directory/file must be owned by actuator UID or root
      - O_NOFOLLOW safe open to prevent TOCTOU substitution
      - canonical Human Gate verification (verify_gate_receipt_dict)
    """
    resolved_as_path = allowed_signers_path or os.environ.get("HARMONY_ALLOWED_SIGNERS_PATH")
    if not resolved_as_path:
        raise HumanGateVerificationError(
            "missing external trust root: allowed_signers_path is required; no fallback allowed"
        )

    if not gate_receipt_dir or not isinstance(gate_receipt_dir, str):
        raise HumanGateVerificationError("gate_receipt_dir must be a non-empty string path")

    if not os.path.isabs(gate_receipt_dir):
        raise HumanGateVerificationError(f"gate_receipt_dir {gate_receipt_dir!r} must be an absolute path")

    if not os.path.exists(gate_receipt_dir):
        raise HumanGateVerificationError(f"gate_receipt_dir {gate_receipt_dir!r} does not exist")

    dst = os.lstat(gate_receipt_dir)
    if stat.S_ISLNK(dst.st_mode):
        raise HumanGateVerificationError(f"gate_receipt_dir {gate_receipt_dir!r} is a symlink (symlink ambiguity)")

    if (dst.st_mode & 0o002) != 0:
        raise HumanGateVerificationError(f"gate_receipt_dir {gate_receipt_dir!r} permits world write access ({oct(dst.st_mode)})")

    current_uid = os.geteuid()
    if dst.st_uid not in (current_uid, 0):
        raise HumanGateVerificationError(f"gate_receipt_dir {gate_receipt_dir!r} is owned by untrusted UID {dst.st_uid}")

    gate_as_path = os.path.join(gate_receipt_dir, "allowed_signers")
    if os.path.lexists(gate_as_path):
        as_st = os.lstat(gate_as_path)
        if stat.S_ISLNK(as_st.st_mode):
            raise HumanGateVerificationError(f"allowed_signers path {gate_as_path!r} is a symlink (symlink ambiguity)")
        if not stat.S_ISREG(as_st.st_mode):
            raise HumanGateVerificationError(f"allowed_signers path {gate_as_path!r} must be a regular file")
        if (as_st.st_mode & 0o002) != 0:
            raise HumanGateVerificationError(f"allowed_signers file {gate_as_path!r} permits world write access ({oct(as_st.st_mode)})")
        if as_st.st_uid not in (current_uid, 0):
            raise HumanGateVerificationError(f"allowed_signers file {gate_as_path!r} is owned by untrusted UID {as_st.st_uid}")
        resolved_as_path = gate_as_path

    candidates: list[str] = []
    if stat.S_ISREG(dst.st_mode):
        candidates.append(gate_receipt_dir)
    elif stat.S_ISDIR(dst.st_mode):
        candidates.append(os.path.join(gate_receipt_dir, f"{packet_sha256}.json"))
        candidates.append(os.path.join(gate_receipt_dir, f"{mission_id}.json"))
    else:
        raise HumanGateVerificationError(f"gate_receipt_dir {gate_receipt_dir!r} must be a directory or regular file")

    receipt_file: str | None = None
    for c in candidates:
        if os.path.lexists(c):
            c_st = os.lstat(c)
            if stat.S_ISLNK(c_st.st_mode):
                raise HumanGateVerificationError(f"Candidate gate receipt file {c!r} is a symlink (symlink ambiguity)")
            if not stat.S_ISREG(c_st.st_mode):
                raise HumanGateVerificationError(f"Candidate gate receipt file {c!r} must be a regular file")
            if (c_st.st_mode & 0o002) != 0:
                raise HumanGateVerificationError(f"Candidate gate receipt file {c!r} permits world write access ({oct(c_st.st_mode)})")
            if c_st.st_uid not in (current_uid, 0):
                raise HumanGateVerificationError(f"Candidate gate receipt file {c!r} is owned by untrusted UID {c_st.st_uid}")
            receipt_file = c
            break

    if not receipt_file:
        raise FileNotFoundError(f"No gate receipt found for packet {packet_sha256} in {gate_receipt_dir}")

    # TOCTOU-safe open with O_NOFOLLOW
    try:
        fd = os.open(receipt_file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise HumanGateVerificationError(f"Cannot open gate receipt safely at {receipt_file!r}: {exc}") from exc

    with os.fdopen(fd, "r", encoding="utf-8") as f:
        fst = os.fstat(f.fileno())
        if not stat.S_ISREG(fst.st_mode):
            raise HumanGateVerificationError(f"Gate receipt file {receipt_file!r} is not a regular file")
        if (fst.st_mode & 0o002) != 0:
            raise HumanGateVerificationError(f"Gate receipt file {receipt_file!r} permits world write access")
        if fst.st_uid not in (current_uid, 0):
            raise HumanGateVerificationError(f"Gate receipt file {receipt_file!r} is owned by untrusted UID {fst.st_uid}")
        try:
            raw_receipt = json.load(f)
        except Exception as exc:
            raise HumanGateVerificationError(f"Failed to parse gate receipt JSON: {exc}") from exc

    return verify_gate_receipt_dict(
        raw_receipt,
        expected_packet_sha256=packet_sha256,
        expected_mission_id=mission_id,
        allowed_signers_path=resolved_as_path,
        gate_receipt_dir=gate_receipt_dir,
    )


class ActuatorProtocolHandler:
    """
    Application-layer request handler for the Actuator.
    Performs canonical packet retrieval, state verification, and executes
    the builder adapter lifecycle serially.
    """

    def __init__(
        self,
        ledger: Ledger,
        runner_client: RunnerClient | None = None,
        gate_receipt_dir: str | None = None,
        evidence_store: EvidenceStore | None = None,
        allowed_signers_path: str | None = None,
    ) -> None:
        self.ledger = ledger
        self.runner_client = runner_client
        self.gate_receipt_dir = gate_receipt_dir
        self.evidence_store = evidence_store
        self.allowed_signers_path = allowed_signers_path or os.environ.get("HARMONY_ALLOWED_SIGNERS_PATH")

    def handle_raw_request(self, raw_bytes: bytes) -> dict[str, Any]:
        """Parse raw request bytes and dispatch to corresponding handler."""
        try:
            req = parse_and_validate_request(raw_bytes)
        except ProtocolError as exc:
            return format_error_response(exc.error_code, exc.message)
        except Exception as exc:
            return format_error_response("INTERNAL_ERROR", f"Unexpected error parsing request: {exc}")

        command = req["command"]
        if command == "DISPATCH_BUILDER":
            return self.dispatch_builder(req["packet_sha256"])
        elif command == "APPROVE_GATE":
            return self.approve_gate(req["packet_sha256"])

        return format_error_response("UNKNOWN_COMMAND", f"Unrecognized command {command!r}")

    def approve_gate(self, packet_sha256: str) -> dict[str, Any]:
        """
        Approve a packet in READY state using a verifier-owned Human Gate receipt.
        The receipt location is read strictly from trusted Actuator configuration (gate_receipt_dir).
        """
        try:
            info = self.ledger.show(packet_sha256)
            current_state = info["state"]
        except LedgerError:
            return format_error_response("PACKET_NOT_FOUND", f"Packet {packet_sha256} not found in ledger")

        if current_state != "READY":
            if current_state == "APPROVED":
                return format_ok_response({
                    "command": "APPROVE_GATE",
                    "packet_sha256": packet_sha256,
                    "state": "APPROVED",
                    "message": "Packet is already in APPROVED state",
                })
            return format_error_response(
                "INVALID_STATE",
                f"Packet state is {current_state}, requires READY to approve with gate receipt",
            )

        if not self.gate_receipt_dir:
            return format_error_response("GATE_RECEIPT_CONFIG_MISSING", "No gate_receipt_dir configured on Actuator")

        if not self.allowed_signers_path:
            return format_error_response(
                "INVALID_GATE_RECEIPT",
                "missing external trust root: allowed_signers_path is required; no fallback allowed",
            )

        try:
            verified_receipt = _load_and_verify_gate_receipt_from_dir(
                self.gate_receipt_dir,
                packet_sha256=packet_sha256,
                mission_id=info["mission_id"],
                allowed_signers_path=self.allowed_signers_path,
            )
        except FileNotFoundError as exc:
            return format_error_response("GATE_RECEIPT_NOT_FOUND", str(exc))
        except (HumanGateVerificationError, Exception) as exc:
            return format_error_response("INVALID_GATE_RECEIPT", str(exc))

        try:
            approve_res = self.ledger.approve(
                packet_sha256,
                approver="",
                gate_receipt=verified_receipt,
                allowed_signers_path=self.allowed_signers_path,
            )
            return format_ok_response({
                "command": "APPROVE_GATE",
                **approve_res,
            })
        except Exception as exc:
            return format_error_response("APPROVAL_FAILED", str(exc))

    def dispatch_builder(self, packet_sha256: str) -> dict[str, Any]:
        """
        Dispatches a builder execution for packet_sha256.

        Canonical packet is loaded directly from Actuator's Ledger.
        Caller provides zero execution parameters.
        """
        # 1. Canonical State & Packet Lookup
        try:
            info = self.ledger.show(packet_sha256)
            pre_state = info["state"]
        except LedgerError:
            return format_error_response(
                "PACKET_NOT_FOUND",
                f"Packet {packet_sha256} not found in ledger",
            )

        # If in READY state and gate_receipt_dir is configured, attempt human gate approval
        if pre_state == "READY" and self.gate_receipt_dir:
            app_res = self.approve_gate(packet_sha256)
            if app_res.get("status") == "OK":
                info = self.ledger.show(packet_sha256)
                pre_state = info["state"]

        # 2. State constraint check (requires APPROVED or DISPATCHED for duplicate check)
        if pre_state not in ("APPROVED", "DISPATCHED"):
            return format_error_response(
                "INVALID_STATE",
                f"Packet state is {pre_state}, requires APPROVED or DISPATCHED",
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": False,
                },
            )

        # Retrieve canonical packet body directly from ledger row
        try:
            row = self.ledger._conn.execute(
                "SELECT packet_json FROM packets WHERE packet_sha256 = ?",
                (packet_sha256,),
            ).fetchone()
            if row is None:
                return format_error_response(
                    "PACKET_NOT_FOUND",
                    f"Packet body for {packet_sha256} not found in ledger",
                )
            packet = json.loads(row["packet_json"])
        except Exception as exc:
            return format_error_response(
                "LEDGER_READ_ERROR",
                f"Failed to read canonical packet JSON: {exc}",
            )

        # 3. Action check
        action = packet.get("action")
        if action != "START_BUILDER":
            return format_error_response(
                "INVALID_ACTION",
                f"Packet action is {action!r}, requires START_BUILDER",
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": False,
                },
            )

        # 4. Execute Builder lifecycle
        # Claim launch right in Ledger
        try:
            claim_res = self.ledger.claim(packet_sha256, "BUILD")
        except StateTransitionError as exc:
            return format_error_response(
                "CLAIM_FAILED",
                str(exc),
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": False,
                },
            )

        # Duplicate claim -> existing launch right, no process
        if not claim_res.get("new_launch", False):
            return format_ok_response(
                {
                    "command": "DISPATCH_BUILDER",
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": False,
                    "builder_result": {
                        "packet_sha256": packet_sha256,
                        "launched": False,
                        "exit_code": None,
                        "timed_out": False,
                        "refusal_reason": "DUPLICATE_CLAIM: new_launch=NO",
                    },
                }
            )

        claim_id = claim_res["claim_id"]

        # B1: Governed execution Actuator -> Runner -> Player is mandatory.
        # If Runner is not configured, fail closed to HOLD immediately.
        if self.runner_client is None:
            try:
                self.ledger.hold(packet_sha256, "RUNNER_NOT_CONFIGURED")
            except Exception:
                pass
            return format_error_response(
                "RUNNER_NOT_CONFIGURED",
                "Runner client is not configured; governed execution requires Runner authority",
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": False,
                    "builder_result": None,
                },
            )

        # Contact Runner over Unix domain socket
        try:
            runner_resp = self.runner_client.run_builder(
                packet_sha256=packet_sha256,
                claim_id=claim_id,
                objective=packet.get("objective", ""),
                repo_root=packet.get("target", {}).get("repo_root", ""),
                base_commit_oid=packet.get("target", {}).get("base_commit_oid", ""),
                profile_id=packet.get("profile_id", "agy-builder-v1"),
            )
        except Exception as exc:
            # Runner connection failure or peer auth failure (C3)
            try:
                self.ledger.hold(packet_sha256, f"RUNNER_FAILURE: {exc}")
            except Exception:
                pass
            return format_error_response(
                "RUNNER_FAILURE",
                f"Runner invocation failed: {exc}",
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": True,
                    "builder_result": None,
                },
            )

        if not isinstance(runner_resp, dict) or runner_resp.get("status") != "OK":
            err_msg = runner_resp.get("message", "Runner returned failure") if isinstance(runner_resp, dict) else "Malformed runner response"
            try:
                self.ledger.hold(packet_sha256, f"LAUNCH_FAILURE: {err_msg}")
            except Exception:
                pass
            return format_error_response(
                "LAUNCH_FAILURE",
                err_msg,
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": True,
                    "builder_result": None,
                },
            )

        # Validate result binding
        if runner_resp.get("packet_sha256") != packet_sha256 or runner_resp.get("claim_id") != claim_id:
            try:
                self.ledger.hold(packet_sha256, "RESULT_IDENTITY_MISMATCH")
            except Exception:
                pass
            return format_error_response(
                "RESULT_IDENTITY_MISMATCH",
                "Runner returned result for mismatched packet or claim",
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": True,
                    "builder_result": None,
                },
            )

        exit_code = runner_resp.get("exit_code")
        timed_out = bool(runner_resp.get("timed_out"))
        launched = bool(runner_resp.get("launched"))

        # Persist canonical Builder result in Ledger
        try:
            b_res = self.ledger.record_builder_result(
                packet_sha256=packet_sha256,
                launched=launched,
                exit_code=exit_code,
                timed_out=timed_out,
                started_at=runner_resp.get("started_at", ""),
                finished_at=runner_resp.get("finished_at", ""),
                claim_id=claim_id,
                actual_uid=runner_resp.get("actual_uid"),
                actual_gid=runner_resp.get("actual_gid"),
                executable_path=runner_resp.get("actual_executable_path"),
                executable_digest=runner_resp.get("executable_digest"),
                helper_digest=runner_resp.get("helper_digest"),
            )
        except Exception as exc:
            # Result persistence failure (C3)
            try:
                self.ledger.hold(packet_sha256, f"RESULT_PERSISTENCE_FAILURE: {exc}")
            except Exception:
                pass
            return format_error_response(
                "RESULT_PERSISTENCE_FAILURE",
                f"Failed to record builder result: {exc}",
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": True,
                    "builder_result": None,
                },
            )

        # Worker nonzero exit or timeout -> fail closed to HOLD (C3)
        if timed_out:
            self.ledger.hold(packet_sha256, "TIMEOUT")
            return format_ok_response(
                {
                    "command": "DISPATCH_BUILDER",
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "state": "HOLD",
                    "launch_attempted": True,
                    "builder_result": b_res,
                }
            )

        if exit_code != 0:
            self.ledger.hold(packet_sha256, f"NONZERO_EXIT: {exit_code}")
            return format_ok_response(
                {
                    "command": "DISPATCH_BUILDER",
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "state": "HOLD",
                    "launch_attempted": True,
                    "builder_result": b_res,
                }
            )

        # Success path (C4): worker exit == 0 and not timed_out
        # B3: EvidenceStore is mandatory for governed FINAL. If missing, fail closed to HOLD.
        if self.evidence_store is None:
            self.ledger.hold(packet_sha256, "EVIDENCE_STORE_NOT_CONFIGURED")
            return format_error_response(
                "EVIDENCE_STORE_NOT_CONFIGURED",
                "EvidenceStore is mandatory for governed execution finalization",
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": True,
                    "builder_result": b_res,
                },
            )

        ev_res = freeze_builder_evidence(
            packet=packet,
            dispatch_result={"packet_sha256": packet_sha256, "launch_attempted": True},
            ledger=self.ledger,
            store=self.evidence_store,
        )
        if ev_res.get("status") not in ("NEW", "EXISTING") or not ev_res.get("evidence_sha256"):
            err_msg = ev_res.get("error") or ev_res.get("reason") or "Evidence freeze failed"
            self.ledger.hold(packet_sha256, f"EVIDENCE_FREEZE_FAILURE: {err_msg}")
            return format_error_response(
                "EVIDENCE_FREEZE_FAILURE",
                err_msg,
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": True,
                    "builder_result": b_res,
                },
            )

        evidence_sha = ev_res["evidence_sha256"]
        try:
            self.ledger.finalize(
                packet_sha256=packet_sha256,
                evidence_sha256=evidence_sha,
                evidence_store=self.evidence_store,
            )
        except Exception as exc:
            self.ledger.hold(packet_sha256, f"FINALIZATION_FAILURE: {exc}")
            return format_error_response(
                "FINALIZATION_FAILURE",
                str(exc),
                extra={
                    "packet_sha256": packet_sha256,
                    "pre_dispatch_state": pre_state,
                    "launch_attempted": True,
                    "builder_result": b_res,
                },
            )

        return format_ok_response(
            {
                "command": "DISPATCH_BUILDER",
                "packet_sha256": packet_sha256,
                "pre_dispatch_state": pre_state,
                "state": "FINAL",
                "evidence_sha256": evidence_sha,
                "launch_attempted": True,
                "builder_result": b_res,
            }
        )


class ActuatorServer:
    """
    Serial Unix Domain Socket Server for the Actuator Authority Boundary.

    Enforces:
      - Startup invariant:
          actuator_uid != allowed_client_uid (mandatory configuration)
          state_dir owned by actuator_uid, mode exactly 0700
          DB files owned by actuator_uid, mode exactly 0600 (both initial & existing)
          socket parent: absolute path, trusted owner, harmony-telegraph group,
                         fixed secure mode (0770/0750), zero world access
          socket file: actuator owner, harmony-telegraph group, mode 0660, zero world access
      - SO_PEERCRED checks: Only exact allowed_client_uid is authorized.
        All other UIDs (including server_uid) rejected immediately before reading data.
      - Bounded request read: at most MAX_MESSAGE_BYTES, newline-delimited, 5s timeout.
      - Serial execution: Accepts and handles one connection at a time.
    """

    def __init__(
        self,
        ledger: Ledger,
        socket_path: str,
        allowed_client_uid: int,
        expected_socket_gid: int,
        runner_client: RunnerClient | None = None,
        runner_socket_path: str | None = None,
        gate_receipt_dir: str | None = None,
        evidence_store: EvidenceStore | None = None,
        evidence_store_dir: str | None = None,
        allowed_signers_path: str | None = None,
    ) -> None:
        # Establish umask 0077 for lifetime of the Actuator authority process
        os.umask(0o077)

        self.ledger = ledger
        self.socket_path = socket_path
        self.allowed_client_uid = allowed_client_uid
        self.expected_socket_gid = expected_socket_gid
        self.allowed_signers_path = allowed_signers_path or os.environ.get("HARMONY_ALLOWED_SIGNERS_PATH")
        if gate_receipt_dir is not None and not self.allowed_signers_path:
            raise AuthorityConfigurationError(
                "Production Actuator requires explicitly provisioned external allowed_signers path when gate_receipt_dir is configured"
            )

        state_dir = ledger._db_path.rsplit(os.sep, 1)[0]
        # Verify all authority environment prerequisites at startup
        verify_authority_environment(
            state_dir=state_dir,
            socket_path=socket_path,
            allowed_client_uid=allowed_client_uid,
            expected_socket_gid=expected_socket_gid,
        )

        if runner_client is None and runner_socket_path is not None:
            runner_client = RunnerClient(runner_socket_path)

        if evidence_store is None and evidence_store_dir is not None:
            evidence_store = EvidenceStore(evidence_store_dir)

        self.protocol_handler = ActuatorProtocolHandler(
            ledger=ledger,
            runner_client=runner_client,
            gate_receipt_dir=gate_receipt_dir,
            evidence_store=evidence_store,
            allowed_signers_path=self.allowed_signers_path,
        )
        self._server_sock: socket.socket | None = None
        self._is_closed = False

    def start(self) -> None:
        """Bind, enforce socket ownership/mode contract, and listen."""
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError as exc:
                raise AuthorityConfigurationError(
                    f"Cannot unlink stale socket at {self.socket_path!r}: {exc}"
                ) from exc

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.socket_path)

        # Apply exact ownership and fixed mode (owner + group only, zero world access)
        try:
            os.chown(self.socket_path, os.geteuid(), self.expected_socket_gid)
        except PermissionError:
            pass

        os.chmod(self.socket_path, 0o660)

        # Verify socket file authority contract
        sst = os.stat(self.socket_path)
        if not stat.S_ISSOCK(sst.st_mode):
            sock.close()
            raise AuthorityConfigurationError(f"{self.socket_path!r} must be a socket")

        if sst.st_uid != os.geteuid():
            sock.close()
            raise AuthorityConfigurationError(
                f"Socket owner UID {sst.st_uid} does not match actuator UID {os.geteuid()}"
            )

        if sst.st_gid != self.expected_socket_gid:
            sock.close()
            raise AuthorityConfigurationError(
                f"Socket GID {sst.st_gid} does not match expected harmony-telegraph group {self.expected_socket_gid}"
            )

        smode = stat.S_IMODE(sst.st_mode)
        if smode != 0o660 or (smode & 0o007) != 0:
            sock.close()
            raise AuthorityConfigurationError(
                f"Socket file has mode {oct(smode)}, must be exactly 0660 with zero world access"
            )

        sock.listen(1)  # Serial backlog
        self._server_sock = sock

    def close(self) -> None:
        """Clean up socket and unlink path."""
        self._is_closed = True
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

    def handle_one_connection(self) -> bool:
        """
        Accept and process a single connection serially.
        Order:
          accept
          → SO_PEERCRED
          → peer_uid == allowed_client_uid ? NO -> reject immediately
          → bounded request read
          → exact schema validation
          → dispatch
        """
        if self._server_sock is None or self._is_closed:
            return False

        try:
            conn, _ = self._server_sock.accept()
        except OSError:
            return False

        with conn:
            # 1. Inspect SO_PEERCRED — immediate authority check before any request reading
            try:
                creds = conn.getsockopt(
                    socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize("3i")
                )
                pid, peer_uid, peer_gid = struct.unpack("3i", creds)
            except Exception as exc:
                err_resp = format_error_response(
                    "AUTHORITY_CHECK_FAILED",
                    f"Failed to inspect peer credentials: {exc}",
                )
                try:
                    conn.sendall(json.dumps(err_resp).encode("utf-8") + b"\n")
                except OSError:
                    pass
                return True

            server_uid = os.geteuid()
            # 2. peer_uid == allowed_client_uid ? NO -> reject immediately
            if peer_uid != self.allowed_client_uid:
                if peer_uid == server_uid:
                    err_code = "SAME_UID_REJECTED"
                    msg = (
                        f"Authority boundary violation: caller UID {peer_uid} matches "
                        f"actuator UID {server_uid}. Same-UID execution cannot establish "
                        "canonical Builder results."
                    )
                else:
                    err_code = "UNAUTHORIZED_CALLER"
                    msg = (
                        f"Authority boundary violation: caller UID {peer_uid} does not match "
                        f"authorized caller UID {self.allowed_client_uid}."
                    )
                err_resp = format_error_response(err_code, msg)
                try:
                    conn.sendall(json.dumps(err_resp).encode("utf-8") + b"\n")
                except OSError:
                    pass
                return True

            # 3. Only authorized peer may send/read bounded request
            try:
                conn.settimeout(5.0)  # Avoid indefinite blocking on incomplete request
                chunks: list[bytes] = []
                total_len = 0
                has_newline = False
                while total_len <= MAX_MESSAGE_BYTES:
                    chunk = conn.recv(min(4096, MAX_MESSAGE_BYTES - total_len + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_len += len(chunk)
                    if b"\n" in chunk:
                        has_newline = True
                        break

                if not has_newline or total_len > MAX_MESSAGE_BYTES:
                    resp = format_error_response(
                        "MALFORMED_REQUEST",
                        f"Request must be a newline-delimited single request and at most {MAX_MESSAGE_BYTES} bytes",
                    )
                else:
                    raw_data = b"".join(chunks).split(b"\n", 1)[0]
                    resp = self.protocol_handler.handle_raw_request(raw_data)
            except socket.timeout:
                resp = format_error_response("MALFORMED_REQUEST", "Request timed out")
            except Exception as exc:
                resp = format_error_response("INTERNAL_ERROR", f"Error servicing request: {exc}")

            # 4. Send response line
            try:
                out = json.dumps(resp).encode("utf-8") + b"\n"
                conn.sendall(out)
            except OSError:
                pass

        return True
