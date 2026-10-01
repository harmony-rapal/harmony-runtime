"""
actuator_protocol.py — Narrow Unix-domain IPC protocol for the Actuator Authority Boundary.

Constraints:
  - Framing: Newline-delimited JSON (UTF-8).
  - Narrow request: DISPATCH_BUILDER(packet_sha256) ONLY.
  - Zero caller-controlled execution parameters (no packet, stage, repo_root, argv, env, etc.).
  - Fail-closed parsing and schema validation.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Maximum permitted payload size in bytes (fail closed if exceeded)
MAX_MESSAGE_BYTES = 65536

# Exact 64-character lowercase hex string
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ALLOWED_COMMANDS = frozenset({"DISPATCH_BUILDER", "APPROVE_GATE"})
ALLOWED_DISPATCH_KEYS = frozenset({"command", "packet_sha256"})

# Explicitly forbidden caller-supplied parameter keys
FORBIDDEN_CALLER_KEYS = frozenset(
    {
        "packet",
        "stage",
        "repo_root",
        "cwd",
        "executable",
        "argv",
        "env",
        "environment",
        "retry",
        "retry_policy",
        "result_facts",
        "started_at",
        "finished_at",
        "exit_code",
        "timed_out",
        "launched",
    }
)


class ProtocolError(ValueError):
    """Base error for actuator protocol violations."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"[{error_code}] {message}")
        self.error_code = error_code
        self.message = message


def format_ok_response(data: dict[str, Any]) -> dict[str, Any]:
    """Format a successful protocol response."""
    return {
        "status": "OK",
        **data,
    }


def format_error_response(
    error_code: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format a fail-closed error protocol response."""
    resp: dict[str, Any] = {
        "status": "ERROR",
        "error_code": error_code,
        "message": message,
    }
    if extra:
        resp.update(extra)
    return resp


def parse_and_validate_request(raw_bytes: bytes) -> dict[str, Any]:
    """
    Parse and validate an incoming raw IPC request.

    Raises ProtocolError with specific error_code on any violation (fail closed).
    """
    if len(raw_bytes) > MAX_MESSAGE_BYTES:
        raise ProtocolError("MALFORMED_REQUEST", f"Payload exceeds max limit of {MAX_MESSAGE_BYTES} bytes")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("MALFORMED_REQUEST", f"Payload is not valid UTF-8: {exc}") from exc

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("MALFORMED_REQUEST", f"Malformed JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ProtocolError("MALFORMED_REQUEST", f"Request must be a JSON object, got {type(parsed).__name__}")

    command = parsed.get("command")
    if not isinstance(command, str) or command not in ALLOWED_COMMANDS:
        raise ProtocolError("UNKNOWN_COMMAND", f"Unknown or disallowed command: {command!r}")

    # Check for forbidden or extraneous fields
    keys = set(parsed.keys())
    extraneous = keys - ALLOWED_DISPATCH_KEYS
    if extraneous:
        forbidden_matches = extraneous.intersection(FORBIDDEN_CALLER_KEYS)
        if forbidden_matches:
            raise ProtocolError(
                "FORBIDDEN_FIELD",
                f"Caller-controlled execution field(s) forbidden: {sorted(forbidden_matches)}",
            )
        raise ProtocolError(
            "FORBIDDEN_FIELD",
            f"Extraneous field(s) forbidden in {command}: {sorted(extraneous)}",
        )

    # Validate packet_sha256 format
    packet_sha = parsed.get("packet_sha256")
    if not isinstance(packet_sha, str) or not _SHA256_RE.match(packet_sha):
        raise ProtocolError(
            "INVALID_PACKET_SHA",
            f"packet_sha256 must be a 64-character lowercase hex string, got {packet_sha!r}",
        )

    return parsed
