"""
ledger.py — SQLite-backed state ledger for Harmony Telegraph Core.

Manages packets, approvals, claims, and receipts in a single SQLite file.
All state transitions are fail-closed; integrity violations raise exceptions.

No subprocess, no shell, no network — standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from telegraph.human_gate import HumanGateVerificationError, verify_gate_receipt_dict
from telegraph.packet import (
    PacketValidationError,
    canonical_bytes as packet_canonical_bytes,
    validate as packet_validate,
)

# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

VALID_STATES: frozenset[str] = frozenset(
    {"READY", "APPROVED", "DISPATCHED", "HOLD", "HUMAN_GATE", "FINAL", "RETIRE"}
)

# Only these automatic transitions are allowed.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "READY": frozenset({"APPROVED", "HOLD"}),
    "APPROVED": frozenset({"DISPATCHED", "HOLD"}),
    "DISPATCHED": frozenset({"HOLD", "FINAL"}),
}

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LedgerError(RuntimeError):
    """Raised on any ledger-level error."""


class StateTransitionError(LedgerError):
    """Raised when a state transition is not permitted."""


class ReceiptChainError(LedgerError):
    """Raised when a receipt chain integrity check fails."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _receipt_canonical_bytes(receipt_body: dict[str, Any]) -> bytes:
    """Canonical JSON bytes for a receipt (same rules as packet)."""
    return json.dumps(
        receipt_body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS packets (
    packet_sha256   TEXT PRIMARY KEY NOT NULL,
    mission_id      TEXT NOT NULL,
    revision        INTEGER NOT NULL,
    packet_json     TEXT NOT NULL,
    state           TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (mission_id, revision)
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id     TEXT PRIMARY KEY NOT NULL,
    packet_sha256   TEXT NOT NULL REFERENCES packets(packet_sha256),
    approver        TEXT NOT NULL,
    approved_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_gate_receipts (
    packet_sha256         TEXT PRIMARY KEY NOT NULL REFERENCES packets(packet_sha256),
    mission_id            TEXT NOT NULL,
    decision              TEXT NOT NULL,
    signer                TEXT NOT NULL,
    gate_digest           TEXT NOT NULL,
    runtime_facts_digest  TEXT NOT NULL,
    verified_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id        TEXT PRIMARY KEY NOT NULL,
    packet_sha256   TEXT NOT NULL REFERENCES packets(packet_sha256),
    stage           TEXT NOT NULL,
    claimed_at      TEXT NOT NULL,
    UNIQUE (packet_sha256, stage)
);

CREATE TABLE IF NOT EXISTS receipts (
    receipt_id              TEXT PRIMARY KEY NOT NULL,
    mission_id              TEXT NOT NULL,
    revision                INTEGER NOT NULL,
    packet_sha256           TEXT NOT NULL REFERENCES packets(packet_sha256),
    sequence                INTEGER NOT NULL,
    previous_receipt_sha256 TEXT,
    from_state              TEXT NOT NULL,
    to_state                TEXT NOT NULL,
    stage                   TEXT NOT NULL,
    timestamp               TEXT NOT NULL,
    receipt_sha256          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS builder_results (
    packet_sha256       TEXT PRIMARY KEY NOT NULL REFERENCES packets(packet_sha256),
    launched            BOOLEAN NOT NULL,
    exit_code           INTEGER,
    timed_out           BOOLEAN NOT NULL,
    started_at          TEXT NOT NULL,
    finished_at         TEXT NOT NULL,
    claim_id            TEXT,
    actual_uid          INTEGER,
    actual_gid          INTEGER,
    executable_path     TEXT,
    executable_digest   TEXT,
    helper_digest       TEXT
);
"""

# ---------------------------------------------------------------------------
# Ledger class
# ---------------------------------------------------------------------------


class Ledger:
    """
    Thin wrapper around a SQLite database.

    ``state_dir`` is the directory that will contain ``ledger.db``.
    """

    def __init__(self, state_dir: str) -> None:
        os.makedirs(state_dir, exist_ok=True)
        self._state_dir = state_dir
        self._db_path = os.path.join(state_dir, "ledger.db")
        # isolation_level=None means autocommit off — we manage transactions
        # explicitly with BEGIN IMMEDIATE / COMMIT / ROLLBACK.
        self._conn: sqlite3.Connection = sqlite3.connect(
            self._db_path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    @property
    def state_dir(self) -> str:
        return self._state_dir

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self._conn.executescript(_DDL)
        existing_cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(builder_results)").fetchall()
        }
        for col, ctype in [
            ("claim_id", "TEXT"),
            ("actual_uid", "INTEGER"),
            ("actual_gid", "INTEGER"),
            ("executable_path", "TEXT"),
            ("executable_digest", "TEXT"),
            ("helper_digest", "TEXT"),
        ]:
            if col not in existing_cols:
                self._conn.execute(f"ALTER TABLE builder_results ADD COLUMN {col} {ctype}")

    def _get_packet_row(self, cur: sqlite3.Cursor, packet_sha256: str) -> sqlite3.Row:
        """Fetch packet row using *cur* (must be called inside a transaction)."""
        row = cur.execute(
            "SELECT * FROM packets WHERE packet_sha256 = ?", (packet_sha256,)
        ).fetchone()
        if row is None:
            raise LedgerError(f"Unknown packet SHA256: {packet_sha256!r}")
        return row

    def _get_packet_row_autocommit(self, packet_sha256: str) -> sqlite3.Row:
        """Fetch packet row outside a transaction (read-only helper)."""
        row = self._conn.execute(
            "SELECT * FROM packets WHERE packet_sha256 = ?", (packet_sha256,)
        ).fetchone()
        if row is None:
            raise LedgerError(f"Unknown packet SHA256: {packet_sha256!r}")
        return row

    def _last_receipt(
        self, cur: sqlite3.Cursor, packet_sha256: str
    ) -> sqlite3.Row | None:
        return cur.execute(
            """
            SELECT * FROM receipts
            WHERE packet_sha256 = ?
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (packet_sha256,),
        ).fetchone()

    def _append_receipt(
        self,
        cur: sqlite3.Cursor,
        packet_sha256: str,
        mission_id: str,
        revision: int,
        from_state: str,
        to_state: str,
        stage: str,
    ) -> dict[str, Any]:
        """Insert an append-only receipt and return its body + sha."""
        last = self._last_receipt(cur, packet_sha256)
        sequence = (last["sequence"] + 1) if last else 1
        prev_sha = last["receipt_sha256"] if last else None

        receipt_id = str(uuid.uuid4())
        ts = _utcnow()

        body: dict[str, Any] = {
            "receipt_id": receipt_id,
            "mission_id": mission_id,
            "revision": revision,
            "packet_sha256": packet_sha256,
            "sequence": sequence,
            "previous_receipt_sha256": prev_sha,
            "from_state": from_state,
            "to_state": to_state,
            "stage": stage,
            "timestamp": ts,
        }
        receipt_sha = _sha256_hex(_receipt_canonical_bytes(body))

        cur.execute(
            """
            INSERT INTO receipts (
                receipt_id, mission_id, revision, packet_sha256,
                sequence, previous_receipt_sha256,
                from_state, to_state, stage, timestamp, receipt_sha256
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                receipt_id,
                mission_id,
                revision,
                packet_sha256,
                sequence,
                prev_sha,
                from_state,
                to_state,
                stage,
                ts,
                receipt_sha,
            ),
        )
        return {**body, "receipt_sha256": receipt_sha}

    # ------------------------------------------------------------------
    # Transaction context
    # ------------------------------------------------------------------

    def _begin(self, cur: sqlite3.Cursor) -> None:
        """Start an explicit IMMEDIATE transaction."""
        cur.execute("BEGIN IMMEDIATE")

    # ------------------------------------------------------------------
    # INGEST  (NEW → READY)
    # ------------------------------------------------------------------

    def ingest(self, packet: dict[str, Any], packet_sha256: str) -> dict[str, Any]:
        """
        Persist a validated packet in READY state.

        Before any database mutation this method:
        1. Independently validates the packet against Mission Packet V1 schema.
        2. Independently computes the canonical SHA256 of the packet body.
        3. Compares the computed SHA256 against the caller-supplied
           ``packet_sha256``; raises LedgerError on mismatch.

        Returns a result dict with ``state``, ``packet_sha256``, etc.
        Raises LedgerError if the (mission_id, revision) pair already exists,
        if the packet fails schema validation, or if the supplied SHA256 does
        not match the canonical SHA256 computed from the packet body.
        """
        # ------------------------------------------------------------------
        # PRE-MUTATION INVARIANT ENFORCEMENT
        # Step 1: independently validate the packet schema.
        # ------------------------------------------------------------------
        try:
            packet_validate(packet)
        except PacketValidationError as exc:
            raise LedgerError(
                f"Packet failed schema validation: {exc}"
            ) from exc

        # Step 2: independently compute the canonical SHA256.
        computed_sha = _sha256_hex(packet_canonical_bytes(packet))

        # Step 3 & 4: compare; fail closed on mismatch.
        if computed_sha != packet_sha256:
            raise LedgerError(
                "packet_sha256 mismatch: supplied value does not match the "
                f"canonical SHA256 computed from the packet body. "
                f"computed={computed_sha!r}, supplied={packet_sha256!r}"
            )

        # ------------------------------------------------------------------
        # All pre-mutation checks passed — proceed with DB writes.
        # ------------------------------------------------------------------
        mid = packet["mission_id"]
        rev = packet["revision"]
        now = _utcnow()
        packet_json = json.dumps(
            packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

        cur = self._conn.cursor()
        self._begin(cur)
        try:
            cur.execute(
                """
                INSERT INTO packets
                    (packet_sha256, mission_id, revision, packet_json,
                     state, ingested_at, updated_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (packet_sha256, mid, rev, packet_json, "READY", now, now),
            )
            receipt = self._append_receipt(
                cur, packet_sha256, mid, rev, "NEW", "READY", "INGEST"
            )
            cur.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            cur.execute("ROLLBACK")
            raise LedgerError(
                f"Packet (mission_id={mid!r}, revision={rev}) already ingested."
            ) from exc
        except Exception:
            cur.execute("ROLLBACK")
            raise

        return {
            "packet_sha256": packet_sha256,
            "state": "READY",
            "mission_id": mid,
            "revision": rev,
            "receipt_id": receipt["receipt_id"],
        }

    # ------------------------------------------------------------------
    # APPROVE  (READY → APPROVED)
    # ------------------------------------------------------------------

    def approve(
        self,
        packet_sha256: str,
        approver: str,
        gate_receipt: dict[str, Any] | None = None,
        allowed_signers_path: str | None = None,
    ) -> dict[str, Any]:
        """
        Approve a packet that is in READY state.

        If gate_receipt is provided, validates that:
        - packet_sha256 matches
        - mission_id matches
        - decision is APPROVE
        - signer is non-empty string
        - canonical gate_digest is 64 hex
        - runtime_facts_digest is 64 hex
        and records the verified human gate receipt.
        Verification uses explicit allowed_signers_path if provided.

        Raises StateTransitionError if the packet is not READY.
        State check, approval insert, state update, and receipt append are
        all performed inside one IMMEDIATE transaction.
        """
        approval_id = str(uuid.uuid4())
        now = _utcnow()

        if gate_receipt is not None:
            try:
                verify_gate_receipt_dict(
                    gate_receipt,
                    expected_packet_sha256=packet_sha256,
                    state_dir=self.state_dir,
                    allowed_signers_path=allowed_signers_path,
                )
            except HumanGateVerificationError as exc:
                raise LedgerError(f"Gate receipt verification failed: {exc}") from exc

        cur = self._conn.cursor()
        self._begin(cur)
        try:
            row = self._get_packet_row(cur, packet_sha256)
            current_state = row["state"]
            if current_state != "READY":
                raise StateTransitionError(
                    f"Cannot approve packet in state {current_state!r}. "
                    "Packet must be READY."
                )

            effective_approver = approver
            if gate_receipt is not None:
                if gate_receipt.get("mission_id") != row["mission_id"]:
                    raise LedgerError(
                        f"Gate receipt mission_id mismatch: {gate_receipt.get('mission_id')!r} != {row['mission_id']!r}"
                    )
                effective_approver = gate_receipt["signer"]
                cur.execute(
                    """
                    INSERT OR REPLACE INTO human_gate_receipts (
                        packet_sha256, mission_id, decision, signer,
                        gate_digest, runtime_facts_digest, verified_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        packet_sha256,
                        row["mission_id"],
                        "APPROVE",
                        effective_approver,
                        gate_receipt["gate_digest"],
                        gate_receipt["runtime_facts_digest"],
                        gate_receipt.get("verified_at", now),
                    ),
                )

            cur.execute(
                """
                INSERT INTO approvals (approval_id, packet_sha256, approver, approved_at)
                VALUES (?,?,?,?)
                """,
                (approval_id, packet_sha256, effective_approver, now),
            )
            cur.execute(
                "UPDATE packets SET state=?, updated_at=? WHERE packet_sha256=?",
                ("APPROVED", now, packet_sha256),
            )
            stage_name = "APPROVE:HUMAN_GATE" if gate_receipt is not None else "APPROVE"
            receipt = self._append_receipt(
                cur,
                packet_sha256,
                row["mission_id"],
                row["revision"],
                "READY",
                "APPROVED",
                stage_name,
            )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

        res = {
            "packet_sha256": packet_sha256,
            "state": "APPROVED",
            "approval_id": approval_id,
            "approver": effective_approver,
            "receipt_id": receipt["receipt_id"],
        }
        if gate_receipt is not None:
            res["gate_verified"] = True
            res["signer"] = effective_approver
            res["gate_digest"] = gate_receipt["gate_digest"]
            res["runtime_facts_digest"] = gate_receipt["runtime_facts_digest"]
        return res

    def record_human_gate_receipt(
        self,
        receipt: dict[str, Any],
        allowed_signers_path: str | None = None,
    ) -> dict[str, Any]:
        """Record verified human gate receipt independently."""
        try:
            verify_gate_receipt_dict(
                receipt,
                state_dir=self.state_dir,
                allowed_signers_path=allowed_signers_path,
            )
        except HumanGateVerificationError as exc:
            raise LedgerError(f"Gate receipt verification failed: {exc}") from exc

        now = _utcnow()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO human_gate_receipts (
                packet_sha256, mission_id, decision, signer,
                gate_digest, runtime_facts_digest, verified_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                receipt["packet_sha256"],
                receipt.get("mission_id", ""),
                "APPROVE",
                receipt["signer"],
                receipt["gate_digest"],
                receipt["runtime_facts_digest"],
                receipt.get("verified_at", now),
            ),
        )
        return receipt

    def get_human_gate_receipt(self, packet_sha256: str) -> dict[str, Any] | None:
        """Fetch human gate receipt for packet if any."""
        row = self._conn.execute(
            "SELECT * FROM human_gate_receipts WHERE packet_sha256 = ?",
            (packet_sha256,),
        ).fetchone()
        if not row:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # CLAIM  (APPROVED → DISPATCHED, idempotent per stage)
    # ------------------------------------------------------------------

    def claim(self, packet_sha256: str, stage: str) -> dict[str, Any]:
        """
        Claim a stage for an APPROVED packet.

        Semantics:
        - HOLD state → always StateTransitionError (checked before idempotency).
        - APPROVED + no existing claim → NEW, state → DISPATCHED.
        - DISPATCHED + same stage existing claim → EXISTING, NEW_LAUNCH=NO.
        - DISPATCHED + different stage, no existing claim → StateTransitionError.
        - Any other state → StateTransitionError.

        The entire check-then-insert sequence runs inside one IMMEDIATE
        transaction to eliminate check-then-act races.
        """
        claim_id = str(uuid.uuid4())
        now = _utcnow()

        cur = self._conn.cursor()
        self._begin(cur)
        try:
            # --- Read current state inside the transaction ---
            row = self._get_packet_row(cur, packet_sha256)
            current_state = row["state"]

            # F1: HOLD blocks ALL claims regardless of existing claim.
            if current_state == "HOLD":
                raise StateTransitionError(
                    f"Cannot claim stage {stage!r}: packet is in HOLD state."
                )

            # --- Check for existing claim (same stage) ---
            existing_claim = cur.execute(
                "SELECT * FROM claims WHERE packet_sha256=? AND stage=?",
                (packet_sha256, stage),
            ).fetchone()

            if existing_claim is not None:
                # Idempotent duplicate — return without any mutation.
                cur.execute("ROLLBACK")
                return {
                    "packet_sha256": packet_sha256,
                    "stage": stage,
                    "claim_status": "EXISTING",
                    "new_launch": False,
                    "claim_id": existing_claim["claim_id"],
                    "claimed_at": existing_claim["claimed_at"],
                }

            # --- Validate state for a NEW claim ---
            if current_state == "APPROVED":
                # Normal first-time claim path.
                pass
            elif current_state == "DISPATCHED":
                # F2: DISPATCHED + different stage (no existing claim) → REJECT.
                raise StateTransitionError(
                    f"Cannot claim new stage {stage!r}: packet is already "
                    "DISPATCHED. Only a duplicate claim on the same stage is "
                    "allowed when DISPATCHED."
                )
            else:
                raise StateTransitionError(
                    f"Cannot claim stage {stage!r}: packet state is "
                    f"{current_state!r}. Packet must be APPROVED."
                )

            # --- Insert claim, update state, append receipt ---
            cur.execute(
                """
                INSERT INTO claims (claim_id, packet_sha256, stage, claimed_at)
                VALUES (?,?,?,?)
                """,
                (claim_id, packet_sha256, stage, now),
            )
            cur.execute(
                "UPDATE packets SET state=?, updated_at=? WHERE packet_sha256=?",
                ("DISPATCHED", now, packet_sha256),
            )
            receipt = self._append_receipt(
                cur,
                packet_sha256,
                row["mission_id"],
                row["revision"],
                current_state,
                "DISPATCHED",
                f"CLAIM:{stage}",
            )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

        return {
            "packet_sha256": packet_sha256,
            "stage": stage,
            "claim_status": "NEW",
            "new_launch": True,
            "claim_id": claim_id,
            "receipt_id": receipt["receipt_id"],
        }

    # ------------------------------------------------------------------
    # HOLD  (READY|APPROVED|DISPATCHED → HOLD)
    # ------------------------------------------------------------------

    def hold(self, packet_sha256: str, reason: str) -> dict[str, Any]:
        """Transition a packet to HOLD state."""
        now = _utcnow()

        cur = self._conn.cursor()
        self._begin(cur)
        try:
            row = self._get_packet_row(cur, packet_sha256)
            current_state = row["state"]
            if current_state not in ("READY", "APPROVED", "DISPATCHED"):
                raise StateTransitionError(
                    f"Cannot hold packet in state {current_state!r}."
                )

            cur.execute(
                "UPDATE packets SET state=?, updated_at=? WHERE packet_sha256=?",
                ("HOLD", now, packet_sha256),
            )
            receipt = self._append_receipt(
                cur,
                packet_sha256,
                row["mission_id"],
                row["revision"],
                current_state,
                "HOLD",
                f"HOLD:{reason}",
            )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

        return {
            "packet_sha256": packet_sha256,
            "state": "HOLD",
            "reason": reason,
            "receipt_id": receipt["receipt_id"],
        }

    # ------------------------------------------------------------------
    # BUILDER RESULT
    # ------------------------------------------------------------------

    def record_builder_result(
        self,
        packet_sha256: str,
        launched: bool,
        exit_code: int | None,
        timed_out: bool,
        started_at: str,
        finished_at: str,
        claim_id: str | None = None,
        actual_uid: int | None = None,
        actual_gid: int | None = None,
        executable_path: str | None = None,
        executable_digest: str | None = None,
        helper_digest: str | None = None,
    ) -> dict[str, Any]:
        """
        Record the factual completion of a Builder run.
        Only valid for packets in DISPATCHED state.
        Raises StateTransitionError if the state is invalid or if a result already exists (FAIL CLOSED).
        Appends a receipt representing the result recording.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self._conn.cursor()
            row = self._get_packet_row(cur, packet_sha256)
            current_state = row["state"]

            if current_state != "DISPATCHED":
                raise StateTransitionError(
                    f"Builder result requires DISPATCHED state, got {current_state}"
                )

            existing = cur.execute(
                "SELECT * FROM builder_results WHERE packet_sha256=?",
                (packet_sha256,)
            ).fetchone()
            if existing:
                raise StateTransitionError("Builder result already recorded for this packet")

            cur.execute(
                """
                INSERT INTO builder_results (
                    packet_sha256, launched, exit_code, timed_out, started_at, finished_at,
                    claim_id, actual_uid, actual_gid, executable_path, executable_digest, helper_digest
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    packet_sha256, launched, exit_code, timed_out, started_at, finished_at,
                    claim_id, actual_uid, actual_gid, executable_path, executable_digest, helper_digest,
                ),
            )

            receipt = self._append_receipt(
                cur,
                packet_sha256=packet_sha256,
                mission_id=row["mission_id"],
                revision=row["revision"],
                from_state=current_state,
                to_state=current_state,
                stage="BUILDER_RESULT",
            )
            self._conn.execute("COMMIT")

            return {
                "packet_sha256": packet_sha256,
                "launched": launched,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "started_at": started_at,
                "finished_at": finished_at,
                "claim_id": claim_id,
                "actual_uid": actual_uid,
                "actual_gid": actual_gid,
                "executable_path": executable_path,
                "executable_digest": executable_digest,
                "helper_digest": helper_digest,
                "receipt_id": receipt["receipt_id"],
            }
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def get_builder_result(self, packet_sha256: str) -> dict[str, Any] | None:
        """Return the builder result for this packet, if any."""
        row = self._conn.execute(
            "SELECT * FROM builder_results WHERE packet_sha256=?",
            (packet_sha256,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # RECOVER (DISPATCHED → HOLD, governed recovery operation)
    # ------------------------------------------------------------------

    def recover(
        self,
        packet_sha256: str,
        claim_id: str,
        reason: str = "STRANDED_EXECUTION_RECOVERY",
    ) -> dict[str, Any]:
        """
        Explicit governed recovery operation for stranded DISPATCHED state.

        Requirements:
        - Packet must be in DISPATCHED state.
        - Identifies and verifies the existing claim_id.
        - Never creates a second launch right (no new claim row).
        - Contacts no Runner, launches no Player.
        - Appends a dedicated Recovery Receipt.
        - Deterministically transitions DISPATCHED -> HOLD.
        """
        now = _utcnow()
        cur = self._conn.cursor()
        self._begin(cur)
        try:
            row = self._get_packet_row(cur, packet_sha256)
            current_state = row["state"]
            if current_state != "DISPATCHED":
                raise StateTransitionError(
                    f"Cannot recover packet in state {current_state!r}: packet must be DISPATCHED."
                )

            # Identify the existing claim/execution authority
            claim_row = cur.execute(
                "SELECT * FROM claims WHERE packet_sha256 = ? AND claim_id = ?",
                (packet_sha256, claim_id),
            ).fetchone()
            if claim_row is None:
                raise LedgerError(
                    f"Claim ID mismatch or not found for packet {packet_sha256!r}: claim {claim_id!r}"
                )

            cur.execute(
                "UPDATE packets SET state='HOLD', updated_at=? WHERE packet_sha256=?",
                (now, packet_sha256),
            )
            receipt = self._append_receipt(
                cur,
                packet_sha256=packet_sha256,
                mission_id=row["mission_id"],
                revision=row["revision"],
                from_state="DISPATCHED",
                to_state="HOLD",
                stage=f"RECOVERY:{claim_id}",
            )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

        return {
            "packet_sha256": packet_sha256,
            "state": "HOLD",
            "claim_id": claim_id,
            "reason": reason,
            "receipt_id": receipt["receipt_id"],
        }

    # ------------------------------------------------------------------
    # FINALIZE (DISPATCHED → FINAL, governed final transition)
    # ------------------------------------------------------------------

    def finalize(
        self,
        packet_sha256: str,
        evidence_sha256: str,
        evidence_store: Any,
    ) -> dict[str, Any]:
        """
        Governed transition from DISPATCHED to FINAL.

        Requirements:
        - EvidenceStore is mandatory for governed FINAL.
        - Packet must be in DISPATCHED state.
        - Existing DISPATCHED claim must exist in claims table.
        - Successful builder result must exist (launched=True, exit_code=0, timed_out=False).
        - Frozen evidence file must exist, non-empty, and exact byte SHA256 matches evidence_sha256.
        - Appends FINAL receipt binding that verified digest.
        """
        if evidence_store is None:
            raise StateTransitionError("Cannot finalize: evidence_store is mandatory for governed FINAL")

        root_path = getattr(evidence_store, "_root", None)
        if root_path is None and isinstance(evidence_store, str):
            root_path = evidence_store
        if not root_path or not isinstance(root_path, str):
            raise StateTransitionError(
                "Cannot finalize: evidence_store must be a valid EvidenceStore instance or directory path"
            )

        if not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64 or not all(c in "0123456789abcdef" for c in evidence_sha256):
            raise LedgerError(
                f"Invalid evidence_sha256 format: must be 64-character lowercase hex, got {evidence_sha256!r}"
            )

        now = _utcnow()
        cur = self._conn.cursor()
        self._begin(cur)
        try:
            row = self._get_packet_row(cur, packet_sha256)
            current_state = row["state"]
            if current_state != "DISPATCHED":
                raise StateTransitionError(
                    f"Cannot finalize packet in state {current_state!r}: packet must be DISPATCHED."
                )

            # Verify existing DISPATCHED claim
            claim_row = cur.execute(
                "SELECT * FROM claims WHERE packet_sha256 = ?",
                (packet_sha256,),
            ).fetchone()
            if claim_row is None:
                raise StateTransitionError(
                    f"Cannot finalize: no claim recorded for packet {packet_sha256!r}"
                )

            # Verify persisted canonical Builder result exists and succeeded
            b_result = cur.execute(
                "SELECT * FROM builder_results WHERE packet_sha256 = ?",
                (packet_sha256,),
            ).fetchone()
            if b_result is None:
                raise StateTransitionError(
                    f"Cannot finalize: no builder result recorded for {packet_sha256!r}"
                )
            if not b_result["launched"] or b_result["exit_code"] != 0 or b_result["timed_out"]:
                raise StateTransitionError(
                    f"Cannot finalize: builder result was not successful "
                    f"(launched={b_result['launched']}, exit_code={b_result['exit_code']}, timed_out={b_result['timed_out']})"
                )

            # Verify frozen evidence bytes exist and match digest
            ev_file = os.path.join(root_path, f"{packet_sha256}.json")
            if not os.path.exists(ev_file) or not os.path.isfile(ev_file):
                raise StateTransitionError(
                    f"Cannot finalize: frozen evidence file {ev_file!r} does not exist"
                )
            try:
                with open(ev_file, "rb") as f:
                    ev_bytes = f.read()
            except OSError as exc:
                raise StateTransitionError(
                    f"Cannot finalize: failed to read evidence file {ev_file!r}: {exc}"
                ) from exc

            if not ev_bytes:
                raise StateTransitionError(
                    f"Cannot finalize: evidence file {ev_file!r} is empty"
                )

            computed = _sha256_hex(ev_bytes)
            if computed != evidence_sha256:
                raise StateTransitionError(
                    f"Cannot finalize: evidence file digest mismatch (file={computed!r} != supplied={evidence_sha256!r})"
                )

            cur.execute(
                "UPDATE packets SET state='FINAL', updated_at=? WHERE packet_sha256=?",
                (now, packet_sha256),
            )
            receipt = self._append_receipt(
                cur,
                packet_sha256=packet_sha256,
                mission_id=row["mission_id"],
                revision=row["revision"],
                from_state="DISPATCHED",
                to_state="FINAL",
                stage=f"FINAL:{evidence_sha256}",
            )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

        return {
            "packet_sha256": packet_sha256,
            "state": "FINAL",
            "evidence_sha256": evidence_sha256,
            "receipt_id": receipt["receipt_id"],
        }

    # ------------------------------------------------------------------
    # SHOW
    # ------------------------------------------------------------------

    def show(self, packet_sha256: str) -> dict[str, Any]:
        """Return a summary of packet state, approvals, claims, and receipts."""
        row = self._get_packet_row_autocommit(packet_sha256)
        packet = json.loads(row["packet_json"])

        approvals = self._conn.execute(
            "SELECT * FROM approvals WHERE packet_sha256=? ORDER BY approved_at",
            (packet_sha256,),
        ).fetchall()

        claims = self._conn.execute(
            "SELECT * FROM claims WHERE packet_sha256=? ORDER BY claimed_at",
            (packet_sha256,),
        ).fetchall()

        receipts = self._conn.execute(
            "SELECT * FROM receipts WHERE packet_sha256=? ORDER BY sequence",
            (packet_sha256,),
        ).fetchall()

        gate_receipt = self._conn.execute(
            "SELECT * FROM human_gate_receipts WHERE packet_sha256=?",
            (packet_sha256,),
        ).fetchone()

        builder_result = self.get_builder_result(packet_sha256)

        return {
            "packet_sha256": packet_sha256,
            "state": row["state"],
            "mission_id": row["mission_id"],
            "revision": row["revision"],
            "action": packet.get("action"),
            "ingested_at": row["ingested_at"],
            "updated_at": row["updated_at"],
            "approvals": [dict(a) for a in approvals],
            "claims": [dict(c) for c in claims],
            "receipts": [dict(r) for r in receipts],
            "human_gate_receipt": dict(gate_receipt) if gate_receipt else None,
            "builder_result": builder_result,
        }

    # ------------------------------------------------------------------
    # VERIFY — receipt chain integrity
    # ------------------------------------------------------------------

    def verify(self, packet_sha256: str) -> dict[str, Any]:
        """
        Verify the receipt chain for a packet.

        Checks:
        1. Each receipt's SHA256 matches recomputed hash of its body.
        2. ``previous_receipt_sha256`` links are correct.
        3. ``sequence`` is strictly monotonically increasing from 1.

        Returns a dict with ``valid=True`` or raises ReceiptChainError.
        """
        # Ensure packet exists
        self._get_packet_row_autocommit(packet_sha256)

        receipts = self._conn.execute(
            "SELECT * FROM receipts WHERE packet_sha256=? ORDER BY sequence",
            (packet_sha256,),
        ).fetchall()

        if not receipts:
            raise ReceiptChainError(
                f"No receipts found for packet {packet_sha256!r}."
            )

        prev_sha: str | None = None
        for idx, r in enumerate(receipts):
            expected_seq = idx + 1
            if r["sequence"] != expected_seq:
                raise ReceiptChainError(
                    f"Receipt sequence mismatch at index {idx}: "
                    f"expected {expected_seq}, got {r['sequence']}."
                )

            body: dict[str, Any] = {
                "receipt_id": r["receipt_id"],
                "mission_id": r["mission_id"],
                "revision": r["revision"],
                "packet_sha256": r["packet_sha256"],
                "sequence": r["sequence"],
                "previous_receipt_sha256": r["previous_receipt_sha256"],
                "from_state": r["from_state"],
                "to_state": r["to_state"],
                "stage": r["stage"],
                "timestamp": r["timestamp"],
            }
            recomputed = _sha256_hex(_receipt_canonical_bytes(body))
            if recomputed != r["receipt_sha256"]:
                raise ReceiptChainError(
                    f"Receipt SHA256 mismatch at sequence {r['sequence']}: "
                    f"stored={r['receipt_sha256']!r}, computed={recomputed!r}."
                )

            if r["previous_receipt_sha256"] != prev_sha:
                raise ReceiptChainError(
                    f"Receipt chain broken at sequence {r['sequence']}: "
                    f"expected previous={prev_sha!r}, "
                    f"got {r['previous_receipt_sha256']!r}."
                )

            prev_sha = r["receipt_sha256"]

        return {
            "packet_sha256": packet_sha256,
            "valid": True,
            "receipts_verified": len(receipts),
        }
