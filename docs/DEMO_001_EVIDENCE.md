# HARMONY-DEMO-001 KVM validation — 2026-10-02 (Asia/Seoul)

## Current authoritative KVM results

Follow-up: [demo-only packaging verification and lifecycle](DEMO_001_PACKAGING.md).
The isolated demo validation was approved with the limitations below acknowledged;
FULL_RELEASE_ACTIVATION=HOLD remains unchanged.

Repository: harmony-rapal/harmony-runtime; branch: harmony/demo-001; issue: #2.
Exact starting HEAD: 32f9386a52015b679f8b794dad84491cf39f0d5e, clean.
A separate demo checkout was used. The existing main checkout remained clean at
0990117303d98cb78805d6a1dbfff6121b8644b3. No main merge. No existing operational
files, settings, accounts, permissions or services were changed. No telegraph/core
code or authority semantics changed. Player remains the English spelling of 선수.
FULL_RELEASE_ACTIVATION=HOLD.

Observed: Linux x86_64, Python 3.12.3, Docker client/server 29.7.1, Compose 5.4.0.
Docker daemon responded; harmony OS account exists. No installation or account
creation was performed. Public evidence is synthetic demo data and de-identified
validation summary only; private runtime evidence and credentials are excluded.

TARGET_USER: AI-agent development teams of 1–10 already granting real tool access.
Chromium found target-user copy, command-copying pain, 60-second demo CTA, Player
wording and preserved Madang philosophy on the landing page.
PWA_STATUS: interactive prototype/demo, local sandbox backend, responsive Human
Gate. No installable PWA, remote phone approval/pairing, authenticated approval,
offline execution or production integration. Static hosting is visual preview
only. One shared session per server. Demo receipts are not signed telegraph proofs.

### Executed tests

| Command | Exact target commit | After demo port fix |
| --- | --- | --- |
| `python3 -m unittest discover -v` | exit 1; 228 tests, 226 passed, 2 errors; 4.481s | exit 1; 229 tests, 227 passed, same 2 errors; 4.529s |
| `python3 -m unittest tests.test_demo -v` | exit 0; 3 tests; 0.037s; PASS | exit 0; 4 tests; 0.539s; PASS |
| `python3 -m compileall -q telegraph tests demo` | exit 0; PASS | exit 0; PASS |

`git diff --check`: exit 0, PASS. FULL_SUITE=HOLD.
Errors are in existing tests:

- `tests.test_audit_blockers_b1_b4.TestB4EnforcePermittedUser.test_b4_runner_handle_request_rejects_wrong_effective_uid_before_launch`
- `tests.test_closure_c1_c7.TestClosureC1.test_c1_runner_server_rejects_same_uid_peer_connection`

Both instantiate RunnerServer under the validation account, which differs from
the profile's permitted harmony identity. Startup rejects the mismatch before
the intended request/peer checks. The account exists; missing-account errors in
the historical report below do not describe this KVM run. No core semantics,
host accounts, permissions or identity provisioning were altered to force PASS.

### Docker and quickstart

Existing port 8765 was occupied. A demo-only override publishes 127.0.0.1:18765
with a separate project name. The demo accepts its explicitly configured
published loopback port and requires exact same-origin POST requests; foreign
hosts/origins and replay remain rejected.

Actual commands, each exit 0:

```bash
docker compose -p harmony-demo-001-verification -f compose.yaml -f demo/compose.kvm.override.yaml config
docker compose -p harmony-demo-001-verification -f compose.yaml -f demo/compose.kvm.override.yaml build
docker compose -p harmony-demo-001-verification -f compose.yaml -f demo/compose.kvm.override.yaml up -d
```

`compose ps` reported Up, only 127.0.0.1:18765 -> 8765. Inspection confirmed
non-root user 65534:65534, read-only root, temporary /tmp, cap_drop=ALL and
no-new-privileges. HTTP API and actual browser decisions succeeded.
DOCKER_CONFIG=PASS; DOCKER_BUILD=PASS; DOCKER_UP=PASS; QUICKSTART=PASS.
Open http://localhost:18765/demo.html on the KVM host; loopback only.
The validation container/project network was removed after evidence collection.
For a free default port, `python3 -m demo.server` remains the dependency-free
fallback at port 8765. That standalone command was not started on this KVM
because the port belongs to an existing service.

### Actual browser and sandbox results

Installed headless Chromium clicked REJECT → New proposal → APPROVE on desktop
1440×900 and emulated mobile 390×844 against the actual Docker backend.
Browser verification exited 0. Container filesystem inspection after each
decision independently confirmed zero files after REJECT and exactly `hello.txt`
after APPROVE. File bytes: `harmony runtime: human-approved demo execution\n`.
REJECT: FINAL, executed=false, exit_status=null, evidence_sha256=null; UI No-op.
APPROVE: FINAL, executed=true, exit_status=0, output=hello.txt.
Actual file SHA-256 independently recomputed and matched receipt evidence:
`5d85cd5d5cda1e38f568184f2ef0e8381861d7594591caab6d7f4cf4ced5f0b3`.
Receipt digests were independently recomputed from sorted canonical JSON,
excluding receipt_sha256, and matched both decisions at both viewports.

Synthetic approved receipt identifiers:

| Viewport | Packet SHA-256 | Receipt SHA-256 |
| --- | --- | --- |
| Desktop | cc754749a9a2831264341ec90db97e238d85610485769cfac6522cc2918ff144 | 6d86ad81c537cc2b9e13a670737472b89aeee1664891c5544bcd6c817bea8265 |
| Mobile | 13b3d13ec578a6c39200f72a9b1cb8a3e6d74f7b3c6ef70577217e53b5bec376 | 224396a5dd9f2e012863912bf61e02a38181cd909e94b90028a33cad4aa30f98 |

Desktop clientWidth=1425, scrollWidth=1425; mobile clientWidth=390,
scrollWidth=390. No horizontal overflow; mobile screenshot visually inspected.
Synthetic screenshots and full receipts retained outside the public repository.
No session tokens published. Mobile check is viewport emulation, not a physical
phone or remote approval test. DEMO_FLOW=PASS; DESKTOP_BROWSER=PASS;
MOBILE_VIEWPORT=PASS.

### Changed files and blockers

Changed: demo/server.py, demo/compose.kvm.override.yaml, tests/test_demo.py,
README.md (demo quickstart only), docs/DEMO_001_EVIDENCE.md.
Blockers: two existing Runner identity test errors; timed human recognition
within 10 seconds and 60-second completion remain unmeasured.
HUMAN_GATE_READY=YES for isolated demo review with blockers disclosed.
All-green acceptance remains HOLD. FULL_RELEASE_ACTIVATION=HOLD.

## Historical validation from the earlier environment — superseded above

The following original report is retained as history. Its missing-account and
missing-Docker findings apply only to that earlier environment and are not the
current KVM results.

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
