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
- HQ02 full suite: 230/230 PASS, 107.380s; no skips, failures or errors.
- Extracted runtime ZIP full suite: 230/230 PASS, 118.278s.
- Compileall telegraph/tests/demo/scripts and git diff --check: PASS.
- Two independent builds from the same committed tree: byte-for-byte equal
  runtime and demo ZIPs; every manifest file hash and size independently checked.
- Public package contents checked for .git, pycache, key/runtime-state directories:
  absent. Packages read only allowlisted committed blobs, never the local worktree.
- Workflow YAML, shell and embedded Python syntax checks: PASS.
- Git diff confirms telegraph and RELEASE_STATUS.md unchanged.
- GitHub Actions run 37169257230 (PR verification ref for source head 1b25c100):
  full suite 230/230 PASS (2.864s), extracted suite 230/230 PASS (2.846s),
  Docker config/build/up + packaged Reject/Approve/filesystem/evidence PASS.
  Job 111338666011 completed success, PACKAGED_DOCKER_FLOW=PASS observed.
  Runtime/demo archives plus SHA256SUMS uploaded as verification artifact.
  [Run and logs](https://github.com/harmony-rapal/harmony-runtime/actions/runs/37169257230).
- Follow-up workflow pins checkout to the PR head SHA so archive source_commit
  names the patch commit directly rather than GitHub's synthetic merge ref.
- HQ02 Docker unavailable. Fresh Docker PASS was obtained in isolated CI, not inferred
  from historical KVM reports.
- KVM production integration was not rerun in this patch session.

FULL_RELEASE_ACTIVATION=HOLD. Source patch release approval is separate from
production activation. No all-green Docker or KVM claim without observed results.
