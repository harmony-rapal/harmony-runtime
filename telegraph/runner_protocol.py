"""
runner_protocol.py — Narrow Unix-domain IPC protocol for the Builder Runner Authority Boundary.

Constraints:
  - Framing: Newline-delimited JSON (UTF-8).
  - Narrow request: RUN_BUILDER only.
  - Required request fields: command, packet_sha256, claim_id, objective, repo_root, base_commit_oid.
  - Zero caller-controlled execution parameters (no executable, argv, env, shell, timeout, uid, gid, etc.).
  - Fail-closed parsing and schema validation.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Maximum permitted payload size in bytes (fail closed if exceeded)
MAX_RUNNER_MESSAGE_BYTES = 65536

# Exact 64-character lowercase hex string
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Exact 40-character lowercase hex git commit OID
_GIT_OID_RE = re.compile(r"^[0-9a-f]{40}$")

ALLOWED_RUNNER_COMMANDS = frozenset({"RUN_BUILDER"})
ALLOWED_RUN_BUILDER_KEYS = frozenset(
    {"command", "packet_sha256", "claim_id", "objective", "repo_root", "base_commit_oid", "profile_id"}
)

# Explicitly forbidden caller-supplied execution parameters
FORBIDDEN_RUNNER_CALLER_KEYS = frozenset(
    {
        "executable",
        "argv",
        "command_line",
        "env",
        "environment",
        "shell",
        "shell_command",
        "timeout",
        "timeout_seconds",
        "uid",
        "gid",
        "user",
        "socket_path",
        "ledger_path",
        "evidence_path",
        "state_dir",
        "retry",
        "retry_policy",
        "sudo",
        "bypass_permissions",
    }
)


class RunnerProtocolError(ValueError):
    """Base error for runner protocol violations."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"[{error_code}] {message}")
        self.error_code = error_code
        self.message = message


def format_runner_ok_response(data: dict[str, Any]) -> dict[str, Any]:
    """Format a successful runner protocol response."""
    return {
        "status": "OK",
        **data,
    }


def format_runner_error_response(
    error_code: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format a fail-closed error runner protocol response."""
    resp: dict[str, Any] = {
        "status": "ERROR",
        "error_code": error_code,
        "message": message,
    }
    if extra:
        resp.update(extra)
    return resp


def parse_and_validate_runner_request(raw_bytes: bytes) -> dict[str, Any]:
    """
    Parse and validate an incoming raw IPC request to Runner.

    Raises RunnerProtocolError with specific error_code on any violation (fail closed).
    """
    if len(raw_bytes) > MAX_RUNNER_MESSAGE_BYTES:
        raise RunnerProtocolError("MALFORMED_REQUEST", f"Payload exceeds max limit of {MAX_RUNNER_MESSAGE_BYTES} bytes")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunnerProtocolError("MALFORMED_REQUEST", f"Payload is not valid UTF-8: {exc}") from exc

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RunnerProtocolError("MALFORMED_REQUEST", f"Malformed JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise RunnerProtocolError("MALFORMED_REQUEST", f"Request must be a JSON object, got {type(parsed).__name__}")

    command = parsed.get("command")
    if not isinstance(command, str) or command not in ALLOWED_RUNNER_COMMANDS:
        raise RunnerProtocolError("UNKNOWN_COMMAND", f"Unknown or disallowed command: {command!r}")

    # Check for forbidden or extraneous fields
    keys = set(parsed.keys())
    extraneous = keys - ALLOWED_RUN_BUILDER_KEYS
    if extraneous:
        forbidden_matches = extraneous.intersection(FORBIDDEN_RUNNER_CALLER_KEYS)
        if forbidden_matches:
            raise RunnerProtocolError(
                "FORBIDDEN_FIELD",
                f"Caller-controlled execution field(s) forbidden in runner request: {sorted(forbidden_matches)}",
            )
        raise RunnerProtocolError(
            "FORBIDDEN_FIELD",
            f"Extraneous field(s) forbidden in {command}: {sorted(extraneous)}",
        )

    # Validate packet_sha256 format
    packet_sha = parsed.get("packet_sha256")
    if not isinstance(packet_sha, str) or not _SHA256_RE.match(packet_sha):
        raise RunnerProtocolError(
            "INVALID_PACKET_SHA",
            f"packet_sha256 must be a 64-character lowercase hex string, got {packet_sha!r}",
        )

    # Validate claim_id format
    claim_id = parsed.get("claim_id")
    if not isinstance(claim_id, str) or not claim_id.strip():
        raise RunnerProtocolError("INVALID_CLAIM_ID", "claim_id must be a non-empty string")

    # Validate objective
    objective = parsed.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise RunnerProtocolError("INVALID_OBJECTIVE", "objective must be a non-empty string")
    if objective.startswith("-"):
        raise RunnerProtocolError("CLI_OPTION_INJECTION", f"objective cannot begin with dash: {objective!r}")

    # Validate repo_root
    repo_root = parsed.get("repo_root")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise RunnerProtocolError("INVALID_REPO_ROOT", "repo_root must be a non-empty string")

    # Validate base_commit_oid
    base_commit_oid = parsed.get("base_commit_oid")
    if not isinstance(base_commit_oid, str) or not _GIT_OID_RE.match(base_commit_oid):
        raise RunnerProtocolError(
            "INVALID_BASE_COMMIT",
            f"base_commit_oid must be a 40-character lowercase hex git OID, got {base_commit_oid!r}",
        )

    # Validate profile_id if present
    profile_id = parsed.get("profile_id")
    if profile_id is not None:
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise RunnerProtocolError("INVALID_PROFILE_ID", "profile_id must be a non-empty string")

    return parsed
