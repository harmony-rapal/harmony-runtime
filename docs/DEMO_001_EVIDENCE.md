# HARMONY-DEMO-001 validation — 2026-10-02 (Asia/Seoul)

## Scope

Repository harmony-rapal/harmony-runtime, issue #2, branch harmony/demo-001.
Fetched origin successfully; clean starting HEAD d61e9dee98dbf99838dd72e4764c2db8995879e6,
already synchronized with origin/harmony/demo-001. No main merge.
No telegraph core, authority semantics, brand assets or release gates changed.
Player is the English spelling of 선수, as requested.
FULL_RELEASE_ACTIVATION=HOLD.

## Implemented and verified now

- Hero immediately identifies teams of 1–10 granting AI agents shell, GitHub,
  staging or infra access; leads with governed execution and command-copying pain.
- README and landing include Try the 60-second Baton demo. Existing work-cycle,
  Player Pool and Madang/Mok/Player/Baton philosophy remain below the introduction.
- Existing isolated demo backend executes no shell, network, credential, payment
  or production operation. APPROVE writes fixed bytes to hello.txt in a private
  temporary directory; REJECT writes nothing. No canonical runtime API changes.
- HTTP tests independently verify the empty rejected sandbox, exact approved file
  bytes and sole filename, exit status 0, FINAL, evidence hash and receipt digest.
  Replay, invalid token, foreign origin/Host, unknown decision and path access fail.
- Actual browser: REJECT displays No-op / Not executed / None; New proposal then
  APPROVE displays one sandbox file, exit status 0, FINAL and evidence SHA-256:
  5d85cd5d5cda1e38f568184f2ef0e8381861d7594591caab6d7f4cf4ced5f0b3.
- Mobile result summary shows decision, Player execution, exit status, evidence,
  receipt hash and output with full JSON available under Full demo receipt.
- Browser viewport 390×844: clientWidth=375, scrollWidth=375 (no horizontal overflow).
  Mobile result screenshot retained outside repository with synthetic data only.
- Browser also confirms target, pain, CTA and preserved philosophy on landing.

## Checks

- python3 -m unittest tests.test_demo -v: PASS, 3 tests, 1.058 seconds.
- python3 -m compileall -q telegraph tests demo: PASS.
- git diff --check: PASS.
- Socket creation is denied in the execution sandbox; HTTP tests and actual demo
  server verified using approved host execution.
- python3 -m unittest discover -v: 228 tests in 120.377 seconds,
  225 passed, 3 errors: missing OS account harmony. FULL_SUITE=HOLD.
  Errors: test_b4_profile_resolve_permitted_uid,
  test_b4_runner_handle_request_rejects_wrong_effective_uid_before_launch,
  test_c1_runner_server_rejects_same_uid_peer_connection.
  Core and existing tests are unchanged; no account or authority provisioning
  was altered to force a green result. An earlier run ended with signal 143;
  the completed rerun above is the authoritative result.

## Docker and PWA limits

Actual docker compose config and docker compose up --build attempts: exit 127, docker: command not found.
Docker config/build/up are UNVERIFIED; no Docker PASS claimed. Dockerfile and
Compose configuration remain supplied. No Docker installation was attempted.
Verified deterministic fallback: python3 -m demo.server, then
http://localhost:8765/demo.html; no dependencies or credentials. Loopback only.

Interactive prototype/demo with local sandbox backend and responsive Human Gate.
No installable PWA, remote phone pairing, authenticated approval or offline
execution. Static hosting is only a visual preview with execution disabled.
One shared session per server, for single-user educational use. Hashes are demo
receipts, not signed telegraph receipts or compliance certification.
No secret or private operational evidence is added to the public repository.
A timed human 60-second usability study has not been performed.

## Human Gate

DEMO_FLOW=PASS; LOCAL_QUICKSTART=PASS; MOBILE_UI=PASS.
DOCKER=UNVERIFIED; FULL_RELEASE_ACTIVATION=HOLD.
Branch is ready for human review; all-green release acceptance remains HOLD.
