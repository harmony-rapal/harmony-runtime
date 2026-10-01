"""
runner_service.py — Serial Builder Runner Service and Unix Domain Socket Host.

Owns:
  - Distinct execution identity from Actuator (Runner UID != Actuator UID)
  - Zero Ledger ownership or access
  - Fail-closed OS peer credential checks:
      1. allowed_actuator_uid != runner effective UID (startup invariant)
      2. peer_uid == allowed_actuator_uid (exact expected Actuator peer authorization)
      3. All other peers (including same-UID) rejected immediately before reading data
  - Sealed executable authority (C6):
      1. agy_path exists and content digest == agy_sha256
      2. helper_target exists and content digest == helper_sha256 (if configured)
      3. No placeholder digests, no test bypass
  - Target git HEAD preflight verification via fixed /usr/bin/git (shell=False)
  - Player subprocess launch (shell=False only)
  - Execution facts collection (C7):
      actual UID/GID, executable path and verified digest, helper digest,
      claim_id, launched status, exit code, timeout, timestamps
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from telegraph.builder_adapter import AdapterError, launch_runner_process, verify_git_head
from telegraph.builder_profiles import get_profile
from telegraph.runner_protocol import (
    MAX_RUNNER_MESSAGE_BYTES,
    RunnerProtocolError,
    format_runner_error_response,
    format_runner_ok_response,
    parse_and_validate_runner_request,
)

# Linux SO_PEERCRED constant
_SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)



class RunnerAuthorityError(RuntimeError):
    """Raised when runner authority prerequisites are violated."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_sha256(path: str) -> str:
    """Compute sha256 of file at path."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_file_digest(path: str, expected_sha: str, description: str = "Executable") -> None:
    """Verify that file exists, is regular, and matches expected SHA256 digest."""
    if not os.path.exists(path):
        raise RunnerAuthorityError(f"{description} at {path!r} does not exist")
    st = os.stat(path)
    if not stat.S_ISREG(st.st_mode):
        raise RunnerAuthorityError(f"{description} at {path!r} must be a regular file")
    actual_sha = _file_sha256(path)
    if actual_sha != expected_sha:
        raise RunnerAuthorityError(
            f"{description} digest mismatch at {path!r}: expected {expected_sha!r}, got {actual_sha!r}"
        )


def verify_runner_authority_environment(
    socket_path: str,
    allowed_actuator_uid: int,
    expected_socket_gid: int,
) -> None:
    """Validate filesystem ownership and permissions for the Runner boundary."""
    current_uid = os.geteuid()

    # 1. Distinct-UID invariant (mandatory)
    if not isinstance(allowed_actuator_uid, int) or isinstance(allowed_actuator_uid, bool) or allowed_actuator_uid < 0:
        raise RunnerAuthorityError(
            f"allowed_actuator_uid must be a non-negative integer, got {allowed_actuator_uid!r}"
        )

    if allowed_actuator_uid == current_uid:
        raise RunnerAuthorityError(
            f"Startup rejected: runner effective UID ({current_uid}) and allowed_actuator_uid ({allowed_actuator_uid}) "
            "cannot be identical. Same-UID execution is not a valid authority boundary."
        )

    # 2. expected_socket_gid validation (mandatory)
    if not isinstance(expected_socket_gid, int) or isinstance(expected_socket_gid, bool) or expected_socket_gid < 0:
        raise RunnerAuthorityError(
            f"expected_socket_gid must be a non-negative integer, got {expected_socket_gid!r}"
        )

    # 3. socket_path parent directory validation
    if not os.path.isabs(socket_path):
        raise RunnerAuthorityError(f"socket_path {socket_path!r} must be an absolute path")

    parent_dir = os.path.dirname(socket_path)
    if not os.path.exists(parent_dir):
        raise RunnerAuthorityError(f"socket parent directory {parent_dir!r} does not exist")

    pst = os.stat(parent_dir)
    if not stat.S_ISDIR(pst.st_mode):
        raise RunnerAuthorityError(f"socket parent {parent_dir!r} must be a directory")

    if pst.st_gid != expected_socket_gid:
        raise RunnerAuthorityError(
            f"socket parent directory {parent_dir!r} has GID {pst.st_gid}, expected GID {expected_socket_gid}"
        )

    pmode = stat.S_IMODE(pst.st_mode)
    if (pmode & 0o007) != 0:
        raise RunnerAuthorityError(
            f"socket parent directory {parent_dir!r} permits world access ({oct(pmode)}); zero world access required"
        )

    if pst.st_uid == current_uid:
        if pmode not in (0o770, 0o750):
            raise RunnerAuthorityError(
                f"runner-owned socket parent {parent_dir!r} has mode {oct(pmode)}, expected 0770 or 0750"
            )
    elif pst.st_uid == 0:
        if pmode != 0o770:
            raise RunnerAuthorityError(
                f"root-owned socket parent {parent_dir!r} has mode {oct(pmode)}, expected exactly 0770"
            )
    else:
        raise RunnerAuthorityError(
            f"socket parent directory {parent_dir!r} is owned by untrusted UID {pst.st_uid}"
        )


@dataclass(frozen=True)
class RunnerConfig:
    """Configuration for Builder Runner authority process."""

    socket_path: str
    allowed_actuator_uid: int
    expected_socket_gid: int
    agy_path: str
    agy_sha256: str
    helper_target: str | None = None
    helper_sha256: str | None = None
    timeout_seconds: int = 3600
    profile_id: str = "agy-builder-v1"


class RunnerServer:
    """
    Serial Unix Domain Socket Server for the Builder Runner Authority Boundary.

    Runs as Runner execution identity (distinct from Actuator).
    Owns zero Ledger access.
    Accepts only the authorized Actuator peer via SO_PEERCRED.
    Enforces sealed executable authority (C6).
    Enforces at-most-once claim execution.
    Executes Player via subprocess.Popen(shell=False).
    Returns authoritative execution facts (C7).
    """

    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self._executed_claims: set[str] = set()
        self._server_sock: socket.socket | None = None
        self._is_closed = False

        # Validate authority environment at startup
        verify_runner_authority_environment(
            socket_path=config.socket_path,
            allowed_actuator_uid=config.allowed_actuator_uid,
            expected_socket_gid=config.expected_socket_gid,
        )

        # Validate B4 permitted_user identity at startup if profile is resolvable
        startup_profile = get_profile(config.profile_id)
        if startup_profile is not None:
            try:
                permitted_uid = startup_profile.resolve_permitted_uid()
                if os.geteuid() != permitted_uid:
                    raise RunnerAuthorityError(
                        f"Startup rejected: runner effective UID ({os.geteuid()}) does not match "
                        f"profile.permitted_user {startup_profile.permitted_user!r} (UID {permitted_uid})"
                    )
            except RuntimeError as exc:
                raise RunnerAuthorityError(f"Startup rejected: {exc}") from exc

        # Validate C6 sealed binary prerequisites at startup
        verify_file_digest(config.agy_path, config.agy_sha256, description="AGY executable")
        if config.helper_target and config.helper_sha256:
            verify_file_digest(config.helper_target, config.helper_sha256, description="Helper target")

    def start(self) -> None:
        """Bind, configure socket permissions, and listen."""
        if os.path.exists(self.config.socket_path):
            try:
                os.unlink(self.config.socket_path)
            except OSError as exc:
                raise RunnerAuthorityError(f"Cannot unlink stale socket at {self.config.socket_path!r}: {exc}") from exc

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.config.socket_path)

        try:
            os.chown(self.config.socket_path, os.geteuid(), self.config.expected_socket_gid)
        except PermissionError:
            pass

        os.chmod(self.config.socket_path, 0o660)

        sst = os.stat(self.config.socket_path)
        if not stat.S_ISSOCK(sst.st_mode):
            sock.close()
            raise RunnerAuthorityError(f"{self.config.socket_path!r} must be a socket")

        if sst.st_uid != os.geteuid():
            sock.close()
            raise RunnerAuthorityError(f"Socket owner UID {sst.st_uid} does not match runner UID {os.geteuid()}")

        if sst.st_gid != self.config.expected_socket_gid:
            sock.close()
            raise RunnerAuthorityError(f"Socket GID {sst.st_gid} does not match expected {self.config.expected_socket_gid}")

        smode = stat.S_IMODE(sst.st_mode)
        if smode != 0o660 or (smode & 0o007) != 0:
            sock.close()
            raise RunnerAuthorityError(f"Socket mode is {oct(smode)}, must be exactly 0660 with zero world access")

        sock.listen(1)
        self._server_sock = sock

    def close(self) -> None:
        """Clean up server socket."""
        self._is_closed = True
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        if os.path.exists(self.config.socket_path):
            try:
                os.unlink(self.config.socket_path)
            except OSError:
                pass

    def handle_one_connection(self) -> bool:
        """
        Accept and process a single connection serially.
        Order:
          accept
          -> SO_PEERCRED
          -> peer_uid == allowed_actuator_uid ? NO -> reject immediately before read
          -> bounded request read
          -> schema validation
          -> claim idempotency check (single launch per claim right)
          -> C6 sealed executable verification
          -> target git HEAD preflight
          -> Player subprocess launch (shell=False)
          -> factual result return
        """
        if self._server_sock is None or self._is_closed:
            return False

        try:
            conn, _ = self._server_sock.accept()
        except OSError:
            return False

        with conn:
            # 1. SO_PEERCRED check: immediate peer authorization
            try:
                creds = conn.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize("3i"))
                pid, peer_uid, peer_gid = struct.unpack("3i", creds)
            except Exception as exc:
                err_resp = format_runner_error_response(
                    "AUTHORITY_CHECK_FAILED", f"Failed to inspect peer credentials: {exc}"
                )
                try:
                    conn.sendall(json.dumps(err_resp).encode("utf-8") + b"\n")
                except OSError:
                    pass
                return True

            current_uid = os.geteuid()
            if peer_uid != self.config.allowed_actuator_uid:
                if peer_uid == current_uid:
                    err_code = "SAME_UID_REJECTED"
                    msg = (
                        f"Authority boundary violation: caller UID {peer_uid} matches runner UID {current_uid}. "
                        "Same-UID execution cannot establish distinct Runner authority."
                    )
                else:
                    err_code = "UNAUTHORIZED_CALLER"
                    msg = (
                        f"Authority boundary violation: caller UID {peer_uid} does not match authorized Actuator UID {self.config.allowed_actuator_uid}."
                    )
                err_resp = format_runner_error_response(err_code, msg)
                try:
                    conn.sendall(json.dumps(err_resp).encode("utf-8") + b"\n")
                except OSError:
                    pass
                return True

            # 2. Bounded request read
            try:
                conn.settimeout(5.0)
                chunks: list[bytes] = []
                total_len = 0
                has_newline = False
                while total_len <= MAX_RUNNER_MESSAGE_BYTES:
                    chunk = conn.recv(min(4096, MAX_RUNNER_MESSAGE_BYTES - total_len + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_len += len(chunk)
                    if b"\n" in chunk:
                        has_newline = True
                        break

                if not has_newline or total_len > MAX_RUNNER_MESSAGE_BYTES:
                    resp = format_runner_error_response(
                        "MALFORMED_REQUEST",
                        f"Request must be newline-delimited and at most {MAX_RUNNER_MESSAGE_BYTES} bytes",
                    )
                else:
                    raw_data = b"".join(chunks).split(b"\n", 1)[0]
                    resp = self._handle_request(raw_data)
            except socket.timeout:
                resp = format_runner_error_response("MALFORMED_REQUEST", "Request timed out")
            except Exception as exc:
                resp = format_runner_error_response("INTERNAL_ERROR", f"Error servicing runner request: {exc}")

            try:
                out = json.dumps(resp).encode("utf-8") + b"\n"
                conn.sendall(out)
            except OSError:
                pass

        return True

    def _handle_request(self, raw_bytes: bytes) -> dict[str, Any]:
        """Validate request, preflight, launch player, and collect facts."""
        try:
            req = parse_and_validate_runner_request(raw_bytes)
        except RunnerProtocolError as exc:
            return format_runner_error_response(exc.error_code, exc.message)
        except Exception as exc:
            return format_runner_error_response("MALFORMED_REQUEST", f"Failed to parse request: {exc}")

        packet_sha = req["packet_sha256"]
        claim_id = req["claim_id"]
        objective = req["objective"]
        repo_root = req["repo_root"]
        base_commit_oid = req["base_commit_oid"]

        # Enforce at-most-once claim execution
        if claim_id in self._executed_claims:
            return format_runner_error_response(
                "DUPLICATE_CLAIM_REJECTED",
                f"Claim {claim_id!r} has already been executed by Runner. No second launch permitted.",
                extra={"packet_sha256": packet_sha, "claim_id": claim_id, "launched": False},
            )

        # Preflight target repo_root
        if not os.path.exists(repo_root) or not os.path.isdir(repo_root):
            return format_runner_error_response(
                "TARGET_NOT_FOUND",
                f"target.repo_root {repo_root!r} does not exist or is not a directory",
                extra={"packet_sha256": packet_sha, "claim_id": claim_id, "launched": False},
            )
        git_marker = os.path.join(repo_root, ".git")
        if not os.path.exists(git_marker):
            return format_runner_error_response(
                "NOT_GIT_REPOSITORY",
                f"target.repo_root {repo_root!r} has no .git entry",
                extra={"packet_sha256": packet_sha, "claim_id": claim_id, "launched": False},
            )

        # Preflight git HEAD
        try:
            verify_git_head(repo_root, base_commit_oid)
        except AdapterError as exc:
            return format_runner_error_response(
                "GIT_HEAD_MISMATCH",
                str(exc),
                extra={"packet_sha256": packet_sha, "claim_id": claim_id, "launched": False},
            )

        # C6 Re-verify sealed executable and helper digests before launch
        try:
            verify_file_digest(self.config.agy_path, self.config.agy_sha256, description="AGY executable")
            if self.config.helper_target and self.config.helper_sha256:
                verify_file_digest(self.config.helper_target, self.config.helper_sha256, description="Helper target")
        except RunnerAuthorityError as exc:
            return format_runner_error_response(
                "SEALED_EXECUTABLE_MISMATCH",
                str(exc),
                extra={"packet_sha256": packet_sha, "claim_id": claim_id, "launched": False},
            )

        # B4: Enforce BuilderProfile.permitted_user authority condition before Player launch
        req_profile_id = req.get("profile_id") or self.config.profile_id
        profile = get_profile(req_profile_id)
        if profile is None:
            return format_runner_error_response(
                "UNKNOWN_PROFILE",
                f"Unknown profile_id {req_profile_id!r}",
                extra={"packet_sha256": packet_sha, "claim_id": claim_id, "launched": False},
            )

        try:
            permitted_uid = profile.resolve_permitted_uid()
        except RuntimeError as exc:
            return format_runner_error_response(
                "PERMITTED_USER_NOT_FOUND",
                str(exc),
                extra={"packet_sha256": packet_sha, "claim_id": claim_id, "launched": False},
            )

        if os.geteuid() != permitted_uid:
            return format_runner_error_response(
                "PERMITTED_USER_MISMATCH",
                f"Runner effective UID {os.geteuid()} does not match profile.permitted_user {profile.permitted_user!r} (UID {permitted_uid})",
                extra={"packet_sha256": packet_sha, "claim_id": claim_id, "launched": False},
            )

        # Mark claim as consumed in Runner before Popen
        self._executed_claims.add(claim_id)

        # Build argv & env
        argv = [self.config.agy_path, "--print", objective, "--add-dir", repo_root]
        env = {k: os.environ[k] for k in ("HOME", "PATH") if k in os.environ}

        started_at = _utcnow_iso()
        timed_out = False
        exit_code: int | None = None

        try:
            exit_code, timed_out = launch_runner_process(
                argv,
                env=env,
                timeout_seconds=self.config.timeout_seconds,
            )
        except OSError as exc:
            finished_at = _utcnow_iso()
            return format_runner_error_response(
                "LAUNCH_FAILED",
                f"subprocess launch failed: {exc}",
                extra={
                    "packet_sha256": packet_sha,
                    "claim_id": claim_id,
                    "launched": False,
                    "started_at": started_at,
                    "finished_at": finished_at,
                },
            )

        finished_at = _utcnow_iso()

        return format_runner_ok_response(
            {
                "command": "RUN_BUILDER",
                "packet_sha256": packet_sha,
                "claim_id": claim_id,
                "actual_uid": os.geteuid(),
                "actual_gid": os.getegid(),
                "actual_executable_path": self.config.agy_path,
                "executable_digest": self.config.agy_sha256,
                "helper_digest": self.config.helper_sha256,
                "launched": True,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "started_at": started_at,
                "finished_at": finished_at,
            }
        )
