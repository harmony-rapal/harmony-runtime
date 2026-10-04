# Runtime / packaging portability patch — 2026-10-04

Base: origin/main 79c2f1e. Branch: harmony/runtime-packaging-portability-001.
Separate worktree; existing positioning checkout preserved.

## Fix and authority boundary

The B4 resolver unit test previously resolved the production harmony username
on every test host. It now uses dataclasses.replace with the actual effective
user and exercises the real OS resolver. A separate configuration test checks
both production profiles remain permitted_user=harmony. Missing-user rejection,
wrong-UID startup/request rejection and real Unix peer checks remain active.
No production code, account, permissions, service or authority semantics changed.
Runtime unit test portability does not grant runtime authority to the test user.
Actual production deployment still requires its configured harmony identity.

## Packaging

Allowlisted committed source ZIPs for runtime and demo, source revision and
per-file SHA-256 manifest, archive SHA256SUMS, deterministic archive metadata.
No untracked local files, credentials, private runtime evidence or runtime state.
CI checks the full suite, extracted runtime package, Docker config/build/up and
actual packaged Reject/Approve with independent filesystem/content hash checks.
Verification artifacts only; no registry upload, main merge or production release.

## Evidence

- B4 focused tests: 5/5 PASS on HQ02 without a harmony account; 3.069s.
- Final full suite / package checks: pending validation.
- HQ02 Docker unavailable. Fresh Docker result must come from the isolated CI
  run, not from historical KVM reports.
- KVM production integration was not rerun in this patch session.

FULL_RELEASE_ACTIVATION=HOLD. Source patch release approval is separate from
production activation. No all-green Docker or KVM claim without observed results.
