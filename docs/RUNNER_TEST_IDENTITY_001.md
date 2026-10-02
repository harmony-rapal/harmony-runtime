# HARMONY-RUNNER-TEST-IDENTITY-001

Repository: harmony-rapal/harmony-runtime; issue #4.
Branch: harmony/runner-test-identity-001.
Base: 33ceab2f34f79b2308b979442559f6f152c6a96a, clean separate checkout.
FULL_RELEASE_ACTIVATION=HOLD. No automatic main merge or production activation.

## Reproduction and cause

Before modification, the two focused tests both errored, exit 1, 0.037 seconds.
The validation account's effective UID differs from the OS-resolved UID of the
production profile's permitted_user="harmony"; the harmony account exists.
RunnerServer.__init__ calls startup_profile.resolve_permitted_uid(), compares
it with os.geteuid(), and raises RunnerAuthorityError before either test reaches
the intended request/peer rejection. Both tracebacks end in runner_service.py
lines 195/200; callers were test_audit_blockers_b1_b4.py:797 and
test_closure_c1_c7.py:185 at the base commit.
The observed failure was correct fail-closed startup behavior, not a production
authority defect. Public evidence is a de-identified validation summary; no
private runtime state, credentials or operational evidence is included.

## Test-only changes and patch boundaries

Both fixtures use dataclasses.replace to create an immutable test-local copy of
the production profile with permitted_user set to pwd.getpwuid(os.geteuid()).pw_name.
The test lookup of telegraph.runner_service.get_profile is patched narrowly to
return this copy. No profile registry entry, OS account or actual process UID is
changed. resolve_permitted_uid, pwd resolution, os.geteuid, startup identity
comparison, filesystem ownership/mode checks and sealed-file checks remain real.

This establishes the same startup precondition as a correctly provisioned
production Runner: effective UID equals the OS-resolved UID of its configured
permitted user. The username is test-local; production still requires harmony.
It does not certify or grant production execution authority to the test account.
Distinct test Actuator/user identities are selected from pwd.getpwall(), filtering
out the actual effective UID. No numeric UID is hardcoded or fabricated.

### B4: wrong effective UID before launch

- A lookup mapping returns the current-account test profile for startup and a
  second profile, naming a different existing OS account, for the request.
- Actual OS resolution and UID comparison return PERMITTED_USER_MISMATCH.
- Existing verify_git_head patch is retained because this fixture supplies a
  synthetic .git marker/HEAD, not a complete Git repository. It models a passed
  target preflight, isolating the identity check; it does not patch identity or
  authorization. Sealed executable validation still uses real temporary bytes.
- launch_runner_process is mocked as a guard and asserted never called;
  launched=false and the claim is not consumed.
- The old root-only conditional is removed: assertions run on any test account,
  using a dynamically selected different identity instead of assuming root differs.

### C1: same UID peer rejection

- get_profile is patched only during Runner construction, then restored before
  binding/handling the socket.
- A real Unix socket and RunnerClient use kernel SO_PEERCRED. Socket ownership,
  effective UID, peer credentials and same-UID comparison are unpatched.
- SAME_UID_REJECTED and its authority-boundary message are asserted.
- _handle_request uses a wrapping spy, not a replacement implementation, and is
  asserted never called. launch_runner_process is a guard asserted never called.
  This confirms rejection before request processing or Player launch.
- finally closes the socket and joins the serving thread; thread termination is
  asserted. The test's temporary resources are cleaned up without touching services.

No skip/xfail or startup/peer rejection bypass was added. Existing production
startup rejection and permitted-user resolution tests remain in the related suite.

## Actual verification — KVM, 2026-10-02

Python 3.12.3. Commands executed in the requested order after fixture changes:

1. `python3 -m unittest tests.test_audit_blockers_b1_b4.TestB4EnforcePermittedUser.test_b4_runner_handle_request_rejects_wrong_effective_uid_before_launch tests.test_closure_c1_c7.TestClosureC1.test_c1_runner_server_rejects_same_uid_peer_connection -v`
   — exit 0, 2/2 PASS, 0.034 seconds.
2. `python3 -m unittest tests.test_audit_blockers_b1_b4.TestB4EnforcePermittedUser tests.test_closure_c1_c7.TestClosureC1 -v`
   — exit 0, 7/7 PASS, 0.100 seconds.
3. `python3 -m unittest discover -v`
   — exit 0, 229/229 PASS, 5.317 seconds; no errors, failures or skips.
4. `python3 -m compileall -q telegraph tests demo` — exit 0, PASS.
5. `git diff --check` — exit 0, PASS.

`git diff --exit-code` against the base confirmed no changes to telegraph,
demo, Dockerfile, Compose or RELEASE_STATUS.md. A direct profile assertion
confirmed production get_profile("agy-builder-v1").permitted_user == "harmony".
Existing checkout changes, operational settings, accounts, permissions and
services were preserved. No sudo grants or runtime authority changes.

Changed files:

- tests/test_audit_blockers_b1_b4.py
- tests/test_closure_c1_c7.py
- docs/RUNNER_TEST_IDENTITY_001.md

PRODUCTION_CODE_CHANGED=NO. AUTHORITY_SEMANTICS_CHANGED=NO.
BLOCKERS=NONE for this test mission on the observed KVM.
The full suite still expects the pre-existing harmony account; this mission does
not provision it or claim portability to hosts lacking production prerequisites.
HUMAN_GATE_READY=YES. FULL_RELEASE_ACTIVATION=HOLD.
