"""
builder_profiles.py — Closed Builder Profile Registry for Harmony Telegraph 002B.

Contains one production profile for the locally-installed Antigravity/agy worker.

Headless invocation contract — confirmed from LOCAL evidence only (no network):

  Evidence 1 (agy --help):
    --print   Run a single prompt non-interactively and print the response
    --add-dir Add a directory to the workspace (repeatable)

  Evidence 2 (strings(1) on /home/r200dev/.local/bin/agy — changelog entry):
    "Fixed headless (-p) runs hanging or silently auto-approving tools that
     require a permission confirmation, so the CLI now soft-denies such tools
     and prints a stderr notice naming the allow-rule needed to permit them."
    → print mode is deterministically non-blocking without any bypass flag.
    → tools without a pre-granted allow-rule are soft-denied to stderr; the
      process completes without hanging.

  Evidence 3 (strings(1) — internal log format strings):
    "updatePendingApprovalsQueue: print mode, soft-denying: %s step %d"
    "addFromDiff: print-mode soft-deny failed for %s step %d: %v"
    → soft-deny is a distinct code path, not a prompt or block.

  Evidence 4 (strings(1) — --mode warning):
    "warning: --mode %s is not supported in print mode; continuing in the
     default mode."
    → --mode accept-edits is silently ignored in print mode; excluded.

  --dangerously-skip-permissions is NOT included. It crosses the approved
  authority boundary and is not required for deterministic headless completion.

Minimal environment allowlist — necessity evidence only (no inference from
string presence):

  HOME: binary string "workspace directory %q does not exist, falling back to
        home dir" → HOME is consulted for workspace resolution fallback.
  PATH: binary string "git not found; install via https://git-scm.com/install/
        and restart the app" → git must be discoverable via PATH for builder
        operations; PATH is required.

  All other env vars omitted: no local evidence of necessity for headless
  non-TTY execution (SHELL, TERM, USER, NO_COLOR are UI/cosmetic only).

Design rules (mechanically enforced via frozen dataclass):
  - No field is mutable after construction (frozen=True raises FrozenInstanceError).
  - fixed_args and allowed_env_names are immutable tuples.
  - executable is the single authoritative source for the worker binary path;
    it does NOT appear inside fixed_args.
  - Packet content MUST NOT add, remove, or modify any registry entry.

No subprocess, no shell, no network — standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Mechanically immutable profile type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuilderProfile:
    """
    Immutable description of one registered builder worker.

    frozen=True: Python raises FrozenInstanceError on any post-construction
    field assignment — mechanical enforcement, not documentation only.
    """

    profile_id: str
    executable: str
    # Immutable tuple of fixed flag arguments inserted between executable and
    # the DATA arguments.  The adapter builds the full argv as:
    #   [profile.executable, *profile.fixed_args, objective, "--add-dir", repo_root]
    # profile.executable is the sole source of the binary; it does NOT appear here.
    fixed_args: tuple[str, ...]
    timeout_seconds: int
    # Allowlist of env-var NAMES whose values are inherited from os.environ.
    # Inclusion requires local evidence of necessity, not mere string presence.
    allowed_env_names: tuple[str, ...]
    execution_policy: str
    executable_sha256: str = "ec7cf797ecb0e1d91ddf3b6d9d6c1d616bb89f78a5b0e43536b72a7fce695f56"
    permitted_user: str = "harmony"

    def is_valid_digest(self) -> bool:
        """Verify that executable_sha256 is a valid non-placeholder 64-char hex digest."""
        if not self.executable_sha256 or len(self.executable_sha256) != 64:
            return False
        if not all(c in "0123456789abcdef" for c in self.executable_sha256.lower()):
            return False
        if self.executable_sha256.lower() in ("0" * 64, "f" * 64):
            return False
        return True

    def resolve_permitted_uid(self) -> int:
        """Resolve permitted_user via the OS identity database (no hardcoding)."""
        import pwd
        try:
            return pwd.getpwnam(self.permitted_user).pw_uid
        except KeyError as exc:
            raise RuntimeError(
                f"profile.permitted_user {self.permitted_user!r} not found in OS identity database"
            ) from exc

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "executable": self.executable,
            "executable_sha256": self.executable_sha256,
            "permitted_user": self.permitted_user,
            "fixed_args": list(self.fixed_args),
            "timeout_seconds": self.timeout_seconds,
            "allowed_env_names": list(self.allowed_env_names),
            "execution_policy": self.execution_policy,
        }


# ---------------------------------------------------------------------------
# Production profiles: Antigravity / agy headless worker
# ---------------------------------------------------------------------------

_AGY_PROFILE = BuilderProfile(
    profile_id="agy-builder-v1",
    executable="/home/r200dev/.local/bin/agy",
    executable_sha256="ec7cf797ecb0e1d91ddf3b6d9d6c1d616bb89f78a5b0e43536b72a7fce695f56",
    permitted_user="harmony",
    fixed_args=(
        "--print",
    ),
    timeout_seconds=3600,  # 1-hour hard ceiling; no retry on timeout
    allowed_env_names=(
        "HOME",
        "PATH",
    ),
    execution_policy=(
        "SINGLE_LAUNCH; NO_RETRY; NO_SHELL; ARGV_LIST_ONLY; "
        "PACKET_DATA_NEVER_EXECUTABLE; NO_PERMISSION_BYPASS"
    ),
)

_AGY_CLOSURE_PROFILE = BuilderProfile(
    profile_id="agy-closure-v1",
    executable="/home/harmony/.local/bin/agy",
    executable_sha256="ec7cf797ecb0e1d91ddf3b6d9d6c1d616bb89f78a5b0e43536b72a7fce695f56",
    permitted_user="harmony",
    fixed_args=(
        "--print",
    ),
    timeout_seconds=3600,
    allowed_env_names=(
        "HOME",
        "PATH",
    ),
    execution_policy=(
        "SINGLE_LAUNCH; NO_RETRY; NO_SHELL; ARGV_LIST_ONLY; "
        "PACKET_DATA_NEVER_EXECUTABLE; NO_PERMISSION_BYPASS"
    ),
)


# ---------------------------------------------------------------------------
# Closed registry — sole source of truth for valid profile_ids
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, BuilderProfile] = {
    _AGY_PROFILE.profile_id: _AGY_PROFILE,
    _AGY_CLOSURE_PROFILE.profile_id: _AGY_CLOSURE_PROFILE,
}


def get_profile(profile_id: str) -> BuilderProfile | None:
    """
    Return the BuilderProfile for *profile_id*, or None if unknown.

    Packet content must never construct or modify a profile outside this registry.
    """
    return _REGISTRY.get(profile_id)


def known_profile_ids() -> frozenset[str]:
    """Return all registered profile_ids."""
    return frozenset(_REGISTRY.keys())
