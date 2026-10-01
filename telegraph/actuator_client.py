"""
actuator_client.py — Narrow Unix Domain Socket client for ordinary callers.

Enforces:
  - Narrow request: DISPATCH_BUILDER(packet_sha256) ONLY.
  - No caller-provided packet body, stage, repo_root, argv, env, or result facts.
"""

from __future__ import annotations

import json
import re
import socket
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ActuatorClientError(RuntimeError):
    """Base error for actuator client operations."""


class ActuatorConnectionError(ActuatorClientError):
    """Raised when unable to connect to the Actuator Unix socket."""


class ActuatorIPCError(ActuatorClientError):
    """Raised when the Actuator returns an error response."""

    def __init__(self, error_code: str, message: str, raw_response: dict[str, Any]) -> None:
        super().__init__(f"[{error_code}] {message}")
        self.error_code = error_code
        self.message = message
        self.raw_response = raw_response


class ActuatorClient:
    """Client for interacting with the Actuator Authority Boundary over Unix socket."""

    def __init__(self, socket_path: str, timeout: float = 60.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def dispatch_builder(self, packet_sha256: str) -> dict[str, Any]:
        """
        Request the Actuator to dispatch builder execution for packet_sha256.

        Carries ONLY packet_sha256.
        """
        if not isinstance(packet_sha256, str) or not _SHA256_RE.match(packet_sha256):
            raise ValueError(
                f"packet_sha256 must be a 64-character lowercase hex string, got {packet_sha256!r}"
            )

        payload = {
            "command": "DISPATCH_BUILDER",
            "packet_sha256": packet_sha256,
        }
        return self._send_request(payload)

    def approve_gate(self, packet_sha256: str) -> dict[str, Any]:
        """
        Request the Actuator to approve a packet using its verified Human Gate receipt.

        Carries ONLY packet_sha256.
        """
        if not isinstance(packet_sha256, str) or not _SHA256_RE.match(packet_sha256):
            raise ValueError(
                f"packet_sha256 must be a 64-character lowercase hex string, got {packet_sha256!r}"
            )

        payload = {
            "command": "APPROVE_GATE",
            "packet_sha256": packet_sha256,
        }
        return self._send_request(payload)

    def _send_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        req_bytes = json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n"

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError as exc:
            sock.close()
            raise ActuatorConnectionError(
                f"Failed to connect to actuator at {self.socket_path!r}: {exc}"
            ) from exc

        with sock:
            send_err: OSError | None = None
            try:
                sock.sendall(req_bytes)
            except OSError as exc:
                send_err = exc

            chunks: list[bytes] = []
            while True:
                try:
                    chunk = sock.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break

            raw_resp = b"".join(chunks).strip()
            if not raw_resp:
                if send_err is not None:
                    raise ActuatorConnectionError(f"Socket communication error: {send_err}") from send_err
                raise ActuatorConnectionError("Actuator closed connection without response")

            try:
                resp = json.loads(raw_resp.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ActuatorClientError(f"Malformed response from actuator: {exc}") from exc

            if not isinstance(resp, dict):
                raise ActuatorClientError(f"Response must be a dict, got {type(resp).__name__}")

            if resp.get("status") == "ERROR":
                raise ActuatorIPCError(
                    error_code=resp.get("error_code", "UNKNOWN_ERROR"),
                    message=resp.get("message", "Unknown error"),
                    raw_response=resp,
                )

            return resp
