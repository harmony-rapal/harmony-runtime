# HARMONY-DEMO-001 validation — 2026-10-02 (Asia/Seoul)

## Scope and baseline

Repository: harmony-rapal/harmony-runtime; issue #2; branch harmony/demo-001.
Starting HEAD: 7d6f6bf; origin/main...HEAD: 0 behind, 0 ahead; clean checkout.
No telegraph core, existing tests, CI, release status or brand assets changed.
FULL_RELEASE_ACTIVATION=HOLD. No automatic merge to main.

## Implemented and observed

- Landing hero names AI-agent development teams of 1–10, command copy/paste pain,
  human authority, execution identity and evidence, with demo and local-run CTAs.
  Existing Madang/Mok/Player/Baton philosophy remains below the product section.
- Python 3.12 standard-library local server, no dependencies or credentials.
  Fixed content and file name; no shell, subprocess, arbitrary command/path or
  production API. Private temporary session directory; exclusive file creation.
- Actual HTTP test: REJECT leaves the sandbox empty, executed=false, exit_status=null.
- New proposal then APPROVE writes only hello.txt, returns exit_status=0 and FINAL.
- Content SHA-256: 5d85cd5d5cda1e38f568184f2ef0e8381861d7594591caab6d7f4cf4ced5f0b3.
  Tests independently verify file bytes, evidence and canonical JSON receipt hash.
- Consumed Baton cannot execute again. Invalid token, cross-origin request, hostile
  Host, unknown decision and non-allowlisted file paths are rejected.
- Direct browser interactions confirmed REJECT → New proposal → APPROVE and visible
  output text, approver/executor labels, packet/evidence/receipt IDs and FINAL.
- Desktop landing inspected; mobile viewport override 390×844 inspected (375 CSS
  pixels excluding scrollbar, scrollWidth=clientWidth: no horizontal overflow).
  Mobile screenshot retained outside repo, containing synthetic demo data only.

## Validation results

- `python3 -m unittest tests.test_demo -v`: PASS, 3 tests, 0.567 seconds on final code.
- `python3 -m unittest discover -v`: 228 tests in 127.005 seconds; 225 passed,
  3 errors due to missing OS identity `harmony`. Full-suite PASS is NOT claimed.
- Same three errors reproduced from unchanged origin/main archived under /tmp:
  `test_b4_profile_resolve_permitted_uid`,
  `test_b4_runner_handle_request_rejects_wrong_effective_uid_before_launch`,
  `test_c1_runner_server_rejects_same_uid_peer_connection`.
- `python3 -m compileall -q telegraph tests demo`: PASS.
- `git diff --check`: PASS.
- First sandbox test run: 8 errors because local sockets are blocked and the
  harmony OS account is absent. Retried with authorized host execution; socket
  tests then passed, leaving the three pre-existing account-dependent errors.
- Actual local server and UI: PASS after authorized host execution (sandbox denied
  socket creation). Local fallback: `python3 -m demo.server`, then
  http://localhost:8765/demo.html. Ctrl+C cleans temporary session state.

## Docker / PWA / product limits

Docker executable is absent on HQ02. Dockerfile and Compose packaging are supplied
but config/build/up have NOT been run: Docker status UNVERIFIED. No Docker PASS.
The verified Python fallback requires no install and binds only loopback.

This is a single-user educational demo, with one shared session per server.
Mobile-shaped UI is implemented; installable PWA, remote phone pairing, offline
approval and cloud deployment are NOT implemented. Static hosting is a visual
preview with disabled execution controls and local startup guidance.
Approver and execution identities are unauthenticated demo labels; no signed
telegraph Human Gate or production authorization is claimed. Hashes bind the
synthetic demo output and receipt; they are not compliance certification.
No operational runtime evidence or secrets are included.

A fresh-user 10-second recognition study and timed human 60-second usability study
have not been performed. The short interactive cycle is implemented and observed.

## Human Gate verdict

DEMO_FLOW=PASS; LOCAL_QUICKSTART=PASS; MOBILE_UI=PASS;
FULL_SUITE=HOLD (missing harmony OS identity); DOCKER=UNVERIFIED;
HUMAN_GATE_READY=HOLD for all-green acceptance, while the implemented branch is
ready for human review. FULL_RELEASE_ACTIVATION=HOLD remains unchanged.
