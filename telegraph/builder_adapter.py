"""
builder_adapter.py — Builder Adapter for Harmony Telegraph 002B.

Launches exactly one registered Builder process for an already-authorized
START_BUILDER claim.  This is NOT a general executor.

Security constraints (all mechanically enforced, not just documented):
  - NO shell execution
  - NO bash -lc
  - NO os.system / os.popen / eval / exec
  - NO arbitrary executable from packet
  - NO argv from packet
  - NO environment from packet
  - NO sudo
  - NO retry after non-zero exit
  - NO retry after timeout
  - NO alternate executable
  - NO fallback execution path

The worker executable, fixed flags, timeout, and env allowlist come exclusively
from the closed BuilderProfile registry (builder_profiles.py).

Packet fields provide DATA only:
  - objective → argv element (never shell syntax)
  - target.repo_root → argv element after --add-dir (never shell syntax)

Execution order (fail-closed at every step):
  1.  Re-verify packet SHA256 binding.
  2.  Validate action == "START_BUILDER".
  3.  Resolve profile from closed registry.
  4.  Guard against CLI option injection (objective starting with "-").
  5.  Verify target.repo_root exists and is a directory.
  6.  Verify target.repo_root is a git worktree/repository.
  7.  Verify git HEAD == target.base_commit_oid (fixed argv, no packet args).
  8.  Build argv and env from profile only.
  9.  Claim launch right: ledger.claim(packet_sha256, _BUILDER_STAGE).
  10. If new_launch=False → return no-launch result (no process).
  11. Re-verify git HEAD immediately before Popen; on failure, HOLD + abort.
  12. If new_launch=True → exactly one subprocess.Popen(argv, shell=False).
  13. Wait ≤ profile.timeout_seconds; terminate on expiry.
  14. Return deterministic result dict.

Steps 4–8 (target preflight) execute BEFORE step 9 (claim), so a known-invalid
target never consumes a launch right or transitions the packet to DISPATCHED.

_BUILDER_STAGE is an internal constant; callers cannot override it.
_GIT_EXECUTABLE is an absolute path; it is not resolved via PATH.

Target verification may invoke only the fixed /usr/bin/git command.
Worker Popen occurs only after a NEW BUILD claim.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from telegraph.builder_profiles import BuilderProfile, get_profile
from telegraph.ledger import Ledger, StateTransitionError
from telegraph.packet import compute_sha256 as packet_compute_sha256

# ---------------------------------------------------------------------------
# Internal constants — NOT overridable by callers or packet
# ---------------------------------------------------------------------------

_BUILDER_STAGE = "BUILD"

# Absolute path to git — confirmed locally at /usr/bin/git.
# Not resolved through PATH so the verifier's authority is fixed.
_GIT_EXECUTABLE = "/usr/bin/git"


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class AdapterError(RuntimeError):
    """Raised when the adapter refuses to launch (authorization/validation)."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verify_git_head(repo_root: str, expected_oid: str) -> None:
    """
    Verify that git HEAD in *repo_root* equals *expected_oid*.

    Uses a fixed subprocess argv list (shell=False) with an absolute path
    to the git executable. No packet-provided git executable, option, or subcommand.
    Raises AdapterError on mismatch or failure.
    """
    try:
        result = subprocess.run(
            [_GIT_EXECUTABLE, "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            shell=False,       # explicit — no shell interpolation
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise AdapterError(f"git not found at {_GIT_EXECUTABLE}; cannot verify HEAD") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(
            "git rev-parse timed out during HEAD verification"
        ) from exc

    if result.returncode != 0:
        raise AdapterError(
            f"git rev-parse HEAD failed (exit {result.returncode}): "
            f"{result.stderr.strip()!r}"
        )

    actual_oid = result.stdout.strip()
    if actual_oid != expected_oid:
        raise AdapterError(
            f"Git HEAD mismatch: packet requires {expected_oid!r}, "
            f"repo_root {repo_root!r} is at {actual_oid!r}. "
            "Refusing to launch — base-commit binding violated."
        )


def _build_env(profile: BuilderProfile) -> dict[str, str]:
    """
    Build subprocess env from profile.allowed_env_names allowlist only.
    Values come from the current process environment; never from a packet.
    """
    env: dict[str, str] = {}
    for name in profile.allowed_env_names:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


def _build_argv(
    profile: BuilderProfile, objective: str, repo_root: str
) -> list[str]:
    """
    Build the full argv list.

    profile.executable is the single authoritative source for argv[0].
    profile.fixed_args provides the fixed flags (no executable there).
    objective and repo_root are packet DATA — passed as argv elements,
    never interpreted as shell syntax (shell=False at the Popen call site).
    """
    return [
        profile.executable,
        *profile.fixed_args,
        objective,
        "--add-dir",
        repo_root,
    ]


def _make_result(
    *,
    profile_id: str,
    packet_sha256: str,
    launched: bool,
    started_at: str,
    finished_at: str,
    exit_code: int | None,
    timed_out: bool,
    refusal_reason: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "profile_id": profile_id,
        "packet_sha256": packet_sha256,
        "launched": launched,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    if refusal_reason is not None:
        result["refusal_reason"] = refusal_reason
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def launch(
    packet: dict[str, Any],
    packet_sha256: str,
    ledger: Ledger,
) -> dict[str, Any]:
    """
    Attempt to launch the builder worker for *packet*.

    The claim stage is always _BUILDER_STAGE ("BUILD") — callers cannot
    override it.  See module docstring for the full execution order.

    Returns a result dict.  On worker failure (non-zero exit, timeout) the
    result reflects the outcome — no retry, no re-raise.
    Raises AdapterError for any authorization or validation failure (no launch).
    """
    invoked_at = _utcnow_iso()

    # ------------------------------------------------------------------
    # Step 1: Re-verify SHA256 binding.
    # ------------------------------------------------------------------
    computed_sha = packet_compute_sha256(packet)
    if computed_sha != packet_sha256:
        raise AdapterError(
            f"packet_sha256 mismatch: supplied {packet_sha256!r} != "
            f"computed {computed_sha!r}. Refusing launch."
        )

    # ------------------------------------------------------------------
    # Step 2: Action must be START_BUILDER.
    # ------------------------------------------------------------------
    action = packet.get("action")
    if action != "START_BUILDER":
        raise AdapterError(
            f"Adapter only handles action='START_BUILDER'; got {action!r}."
        )

    # ------------------------------------------------------------------
    # Step 3: Resolve profile from closed registry.
    # ------------------------------------------------------------------
    profile_id: str = packet.get("profile_id", "")
    profile = get_profile(profile_id)
    if profile is None:
        raise AdapterError(
            f"Unknown profile_id {profile_id!r}. "
            "Only profiles in the closed registry are permitted."
        )

    # ------------------------------------------------------------------
    # Step 4: Extract objective and guard against CLI option injection.
    # Runs BEFORE ledger.claim so bad payloads never consume a launch right.
    # ------------------------------------------------------------------
    objective: str = packet.get("objective", "")
    if objective.startswith("-"):
        raise AdapterError(
            f"Objective begins with a dash ({objective!r}). "
            "Refusing launch to prevent CLI option injection."
        )

    # ------------------------------------------------------------------
    # Step 5: Verify target.repo_root exists, is a directory, has .git.
    # These run BEFORE the Ledger claim so a bad target never consumes a
    # launch right or transitions the packet to DISPATCHED.
    # ------------------------------------------------------------------
    target = packet.get("target", {})
    repo_root: str = target.get("repo_root", "")
    base_commit_oid: str = target.get("base_commit_oid", "")

    if not repo_root or not os.path.exists(repo_root):
        raise AdapterError(
            f"target.repo_root {repo_root!r} does not exist. Refusing launch."
        )
    if not os.path.isdir(repo_root):
        raise AdapterError(
            f"target.repo_root {repo_root!r} is not a directory. Refusing launch."
        )
    # .git may be a file (worktree) or a directory (normal clone) — both valid.
    git_marker = os.path.join(repo_root, ".git")
    if not os.path.exists(git_marker):
        raise AdapterError(
            f"target.repo_root {repo_root!r} is not a git repository "
            "(no .git entry found). Refusing launch."
        )

    # ------------------------------------------------------------------
    # Step 6: Verify git HEAD == base_commit_oid.
    # Fixed subprocess argv — no packet-provided git executable, option, or subcommand.
    # ------------------------------------------------------------------
    _verify_git_head(repo_root, base_commit_oid)

    # ------------------------------------------------------------------
    # Step 7: Build argv and env entirely from profile.
    # ------------------------------------------------------------------
    argv = _build_argv(profile, objective, repo_root)
    env = _build_env(profile)

    # ------------------------------------------------------------------
    # Step 8: Claim launch right using the fixed internal stage constant.
    # Callers cannot choose the stage.
    # ------------------------------------------------------------------
    try:
        claim_result = ledger.claim(packet_sha256, _BUILDER_STAGE)
    except StateTransitionError as exc:
        raise AdapterError(
            f"Ledger claim refused for stage {_BUILDER_STAGE!r}: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # Step 9: Duplicate claim — existing launch right, no new process.
    # ------------------------------------------------------------------
    if not claim_result.get("new_launch", False):
        finished_at = _utcnow_iso()
        return _make_result(
            profile_id=profile_id,
            packet_sha256=packet_sha256,
            launched=False,
            started_at=invoked_at,
            finished_at=finished_at,
            exit_code=None,
            timed_out=False,
            refusal_reason="DUPLICATE_CLAIM: new_launch=NO",
        )

    # ------------------------------------------------------------------
    # Step 10 (POST-CLAIM): Re-verify HEAD before Popen.
    # The mission binds base_commit_oid at launch time; if HEAD moved
    # between the pre-claim check and now, fail closed — no worker launch,
    # no retry, transition to HOLD.
    # ------------------------------------------------------------------
    try:
        _verify_git_head(repo_root, base_commit_oid)
    except AdapterError as head_exc:
        reason = f"POST_CLAIM_HEAD_MISMATCH: {head_exc}"
        try:
            ledger.hold(packet_sha256, reason)
        except Exception as hold_exc:
            raise AdapterError(
                f"Launch aborted: {reason} (AND ledger.hold failed: {hold_exc})"
            ) from hold_exc

        # Raise AdapterError after HOLD is successfully recorded (zero Popen)
        raise AdapterError(f"Launch aborted: {reason}") from head_exc

    # ------------------------------------------------------------------
    # Step 11: Exactly one subprocess launch.
    # shell=False is explicit — argv is a list, never a shell string.
    # argv[0] comes from profile.executable (single authority).
    # ------------------------------------------------------------------
    started_at = _utcnow_iso()
    exit_code: int | None = None
    timed_out = False

    try:
        proc = subprocess.Popen(
            argv,
            shell=False,           # explicit — no shell interpretation
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        reason = f"BUILDER_LAUNCH_FAILED: {exc}"
        try:
            ledger.hold(packet_sha256, reason)
        except Exception as hold_exc:
            raise AdapterError(
                f"Launch aborted: {reason} (AND ledger.hold failed: {hold_exc})"
            ) from hold_exc

        raise AdapterError(f"Launch aborted: {reason}") from exc

    # ------------------------------------------------------------------
    # Step 12: Wait with hard timeout; terminate on expiry.  No retry.
    # ------------------------------------------------------------------
    try:
        proc.wait(timeout=profile.timeout_seconds)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        exit_code = proc.returncode

    finished_at = _utcnow_iso()

    # ------------------------------------------------------------------
    # Step 13: Record deterministic result in Ledger. No retry on non-zero or timeout.
    # ------------------------------------------------------------------
    try:
        ledger.record_builder_result(
            packet_sha256=packet_sha256,
            launched=True,
            exit_code=exit_code,
            timed_out=timed_out,
            started_at=started_at,
            finished_at=finished_at,
        )
    except Exception as e:
        raise AdapterError(f"Failed to record builder result in ledger: {e}") from e

    return _make_result(
        profile_id=profile_id,
        packet_sha256=packet_sha256,
        launched=True,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=exit_code,
        timed_out=timed_out,
    )


verify_git_head = _verify_git_head


def launch_runner_process(
    argv: list[str],
    env: dict[str, str],
    timeout_seconds: int,
) -> tuple[int | None, bool]:
    """
    Launch worker process without shell, wait up to timeout_seconds, and return
    (exit_code, timed_out).
    """
    proc = subprocess.Popen(
        argv,
        shell=False,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    timed_out = False
    try:
        proc.wait(timeout=timeout_seconds)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        exit_code = proc.returncode
    return exit_code, timed_out
