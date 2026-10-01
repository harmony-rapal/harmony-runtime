"""
packet.py — Mission Packet V1 validation and canonicalization.

Validates incoming JSON against the strict Mission Packet V1 schema.
Computes the canonical SHA256 used as the packet's permanent identity.

No subprocess, no shell, no network — standard library only.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

ALLOWED_TOP_LEVEL_FIELDS: frozenset[str] = frozenset(
    {
        "schema_version",
        "mission_id",
        "revision",
        "project_id",
        "objective",
        "action",
        "profile_id",
        "target",
        "retry_policy",
        "on_failure",
    }
)

ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {
        "CREATE_WORKTREE",
        "START_BUILDER",
        "RUN_VALIDATION",
        "FREEZE_RESULT",
        "CREATE_AUDIT_CAPSULE",
        "START_AUDITOR",
        "CAPTURE",
        "FINALIZE",
        "RETIRE",
    }
)

ALLOWED_TARGET_FIELDS: frozenset[str] = frozenset({"repo_root", "base_commit_oid"})

# Fields explicitly forbidden anywhere in the packet (nested included).
FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "shell_command",
        "command",
        "argv",
        "environment",
        "env",
        "sudo",
        "retry",
        "bypass_permissions",
        "callback_url",
    }
)

_GIT_OID_RE = re.compile(r"^[0-9a-f]{40}$")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PacketValidationError(ValueError):
    """Raised when a Mission Packet fails schema validation."""


# ---------------------------------------------------------------------------
# Recursive forbidden-field scan
# ---------------------------------------------------------------------------


def _scan_forbidden(obj: Any, path: str = "") -> None:
    """Walk any JSON value and raise if a forbidden field key is found."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            full = f"{path}.{key}" if path else key
            if key in FORBIDDEN_FIELDS:
                raise PacketValidationError(
                    f"Forbidden field detected: {full!r}"
                )
            _scan_forbidden(value, full)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_forbidden(item, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Validate *raw* against Mission Packet V1 schema.

    Returns the validated packet dict unchanged if all checks pass.
    Raises PacketValidationError on any violation.
    """
    if not isinstance(raw, dict):
        raise PacketValidationError("Packet must be a JSON object.")

    # --- Forbidden-field scan (entire tree) ---------------------------------
    _scan_forbidden(raw)

    # --- Unknown top-level fields -------------------------------------------
    unknown = set(raw.keys()) - ALLOWED_TOP_LEVEL_FIELDS
    if unknown:
        raise PacketValidationError(
            f"Unknown top-level field(s): {sorted(unknown)}"
        )

    # --- schema_version -----------------------------------------------------
    sv = raw.get("schema_version")
    # Must be exactly the integer 1 — bool True has sv==1 but isinstance(True,bool).
    if not isinstance(sv, int) or isinstance(sv, bool) or sv != SCHEMA_VERSION:
        raise PacketValidationError(
            f"schema_version must be integer {SCHEMA_VERSION}, got {sv!r}"
        )

    # --- mission_id ---------------------------------------------------------
    mid = raw.get("mission_id")
    if not isinstance(mid, str) or not mid.strip():
        raise PacketValidationError("mission_id must be a non-empty string.")

    # --- revision -----------------------------------------------------------
    rev = raw.get("revision")
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
        raise PacketValidationError(
            "revision must be a positive integer."
        )

    # --- project_id ---------------------------------------------------------
    pid = raw.get("project_id")
    if not isinstance(pid, str) or not pid.strip():
        raise PacketValidationError("project_id must be a non-empty string.")

    # --- objective ----------------------------------------------------------
    obj = raw.get("objective")
    if not isinstance(obj, str) or not obj.strip():
        raise PacketValidationError("objective must be a non-empty string.")

    # --- action -------------------------------------------------------------
    action = raw.get("action")
    # Guard against unhashable types (e.g. list/dict) before set membership test.
    try:
        action_in_allowed = action in ALLOWED_ACTIONS
    except TypeError:
        action_in_allowed = False
    if not action_in_allowed:
        raise PacketValidationError(
            f"action {action!r} is not a recognised action. "
            f"Allowed: {sorted(ALLOWED_ACTIONS)}"
        )

    # --- profile_id ---------------------------------------------------------
    prof = raw.get("profile_id")
    if not isinstance(prof, str) or not prof.strip():
        raise PacketValidationError("profile_id must be a non-empty string.")

    # --- target -------------------------------------------------------------
    target = raw.get("target")
    if not isinstance(target, dict):
        raise PacketValidationError("target must be a JSON object.")

    # No unknown fields inside target
    unknown_target = set(target.keys()) - ALLOWED_TARGET_FIELDS
    if unknown_target:
        raise PacketValidationError(
            f"Unknown field(s) in target: {sorted(unknown_target)}"
        )

    repo_root = target.get("repo_root")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise PacketValidationError(
            "target.repo_root must be a non-empty string."
        )

    base_commit = target.get("base_commit_oid")
    if not isinstance(base_commit, str) or not _GIT_OID_RE.match(base_commit):
        raise PacketValidationError(
            "target.base_commit_oid must be a 40-character lowercase hex git OID."
        )

    # --- retry_policy -------------------------------------------------------
    rp = raw.get("retry_policy")
    if rp != "NEVER":
        raise PacketValidationError(
            f"retry_policy must be 'NEVER', got {rp!r}"
        )

    # --- on_failure ---------------------------------------------------------
    of = raw.get("on_failure")
    if of != "HOLD":
        raise PacketValidationError(
            f"on_failure must be 'HOLD', got {of!r}"
        )

    return raw


def canonical_bytes(packet: dict[str, Any]) -> bytes:
    """Return the deterministic UTF-8 encoding used for SHA256 computation."""
    return json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def compute_sha256(packet: dict[str, Any]) -> str:
    """Return the hex SHA256 of the canonical packet bytes."""
    return hashlib.sha256(canonical_bytes(packet)).hexdigest()


def load_and_validate(path: str) -> tuple[dict[str, Any], str]:
    """
    Load a JSON file from *path*, validate it, and return
    ``(validated_packet, packet_sha256)``.

    Raises PacketValidationError or json.JSONDecodeError on failure.
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    validated = validate(raw)
    sha = compute_sha256(validated)
    return validated, sha
