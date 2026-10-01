"""
runner_client.py — Unix Domain Socket client for Actuator to communicate with Runner.
"""

from __future__ import annotations

import json
import socket
from typing import Any


class RunnerClientError(RuntimeError):
    """Base error for runner client operations."""


class RunnerConnectionError(RunnerClientError):
    """Raised when unable to connect to the Runner Unix socket."""


class RunnerIPCError(RunnerClientError):
    """Raised when the Runner returns an error response."""

    def __init__(self, error_code: str, message: str, raw_response: dict[str, Any]) -> None:
        super().__init__(f"[{error_code}] {message}")
        self.error_code = error_code
        self.message = message
        self.raw_response = raw_response


class RunnerClient:
    """Client used by Actuator to invoke Runner over Unix domain socket."""

    def __init__(self, socket_path: str, timeout: float = 3700.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def run_builder(
        self,
        packet_sha256: str,
        claim_id: str,
        objective: str,
        repo_root: str,
        base_commit_oid: str,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Send RUN_BUILDER request to Runner."""
        payload: dict[str, Any] = {
            "command": "RUN_BUILDER",
            "packet_sha256": packet_sha256,
            "claim_id": claim_id,
            "objective": objective,
            "repo_root": repo_root,
            "base_commit_oid": base_commit_oid,
        }
        if profile_id is not None:
            payload["profile_id"] = profile_id
        return self._send_request(payload)

    def _send_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        req_bytes = json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n"

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError as exc:
            sock.close()
            raise RunnerConnectionError(
                f"Failed to connect to runner at {self.socket_path!r}: {exc}"
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
                    raise RunnerConnectionError(f"Socket communication error: {send_err}") from send_err
                raise RunnerConnectionError("Runner closed connection without response")

            try:
                resp = json.loads(raw_resp.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RunnerClientError(f"Malformed response from runner: {exc}") from exc

            if not isinstance(resp, dict):
                raise RunnerClientError(f"Response must be a dict, got {type(resp).__name__}")

            if resp.get("status") == "ERROR":
                raise RunnerIPCError(
                    error_code=resp.get("error_code", "UNKNOWN_ERROR"),
                    message=resp.get("message", "Unknown error"),
                    raw_response=resp,
                )

            return resp
