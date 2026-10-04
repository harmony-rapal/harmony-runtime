# HARMONY-DEMO-001 Docker packaging

This package is the isolated interactive prototype/demo, not a production
runtime package, installable PWA or remote phone approval service.
Player is the English spelling of 선수. FULL_RELEASE_ACTIVATION=HOLD.
The preceding demo validation was approved with its existing Runner identity
test errors and unmeasured usability criteria acknowledged. Packaging PASS
does not change those limits or authorize production activation.

## Contents and boundaries

Dockerfile uses explicit COPY lists and .dockerignore uses an explicit allowlist.
The application directory contains exactly these 11 files:

```text
demo/__init__.py
demo/server.py
docs/index.html
docs/demo.html
docs/style.css
docs/demo.js
docs/assets/logo.svg
docs/assets/logo-mark.svg
docs/assets/logo-mono.svg
docs/assets/landscape.svg
docs/assets/hero-wordmark.svg
```

The base is the public python:3.12-slim image. Credentials, .git, operational
settings, telegraph core, tests, evidence reports and runtime state are excluded
from application COPY and the build context allowlist. No host runtime state or
credentials are mounted. Approvals create temporary synthetic data only.
The container runs as 65534:65534 with read-only root, a 16 MiB /tmp tmpfs,
all capabilities dropped, no-new-privileges, 32 PID and 128 MiB memory limits.
The only published address is loopback. Do not expose it as a public service.

## Build and run

Prerequisites: existing Docker daemon access and Compose supporting `!override`.
If Docker is absent or unavailable, report the blocker and use the Python
fallback below; do not install Docker or provision accounts for this demo.
Run from the repository root. Verify that the chosen project name is unused and
the port is free, for example with `docker compose ls` and `ss -ltn`.

```bash
docker compose -p harmony-demo-001-packaging -f compose.yaml -f demo/compose.kvm.override.yaml config
docker compose -p harmony-demo-001-packaging -f compose.yaml -f demo/compose.kvm.override.yaml build
docker compose -p harmony-demo-001-packaging -f compose.yaml -f demo/compose.kvm.override.yaml up -d
docker compose -p harmony-demo-001-packaging -f compose.yaml -f demo/compose.kvm.override.yaml ps
```

Open http://localhost:18765/demo.html on the Docker host. Inspect scope, choose
REJECT, then New proposal and APPROVE. Reject must produce no file or execution
exit status. Approve must create only hello.txt and display its content hash,
exit status 0 and FINAL receipt. Identities are unauthenticated demo labels;
receipts are educational hashes, not signed telegraph authority proofs.
Desktop/mobile viewport support does not implement remote phone approval.

If 18765 is occupied, select an available loopback port. Supply the same
HARMONY_DEMO_PORT value for config, build, up, stop and down, for example:

```bash
HARMONY_DEMO_PORT=18771 docker compose -p harmony-demo-001-packaging -f compose.yaml -f demo/compose.kvm.override.yaml up --build -d
```

Choose a distinct `-p` value if this project name is already in use. Keep that
name consistent for the entire lifecycle. Compose passes the published port
into the demo's Host/Origin validation. Exact same-origin POST is required.

## Stop and clean up this demo only

Stop without removing the container:

```bash
docker compose -p harmony-demo-001-packaging -f compose.yaml -f demo/compose.kvm.override.yaml stop
```

Remove this project's container, network and local demo image:

```bash
docker compose -p harmony-demo-001-packaging -f compose.yaml -f demo/compose.kvm.override.yaml down --rmi local
```

Stopping discards the tmpfs sandbox; restarting starts a new session. No named
volume or host data directory is created. Cleanup must use only the project name
and port chosen for this demo. Do not run global container/image/cache prune.
Shared base-image/build caches are not removed by this cleanup procedure.

## Python fallback

Python 3.12, no additional packages or credentials:

```bash
python3 -m demo.server
```

If its default port 8765 is occupied, choose a free local port:

```bash
python3 -m demo.server --port 18770
```

Open http://localhost:18770/demo.html. Stop with Ctrl+C; the process cleans up
its own temporary sandbox. Do not stop any pre-existing process to free a port.

## Actual KVM packaging verification — 2026-10-02

Starting HEAD: 68d372323af57d21ce3ad8b06fd737fcb6d0e3a0, clean demo branch.
Python 3.12.3; Docker client/server 29.7.1; Compose 5.4.0.
Existing main checkout, operational files/settings/accounts/permissions/services
and telegraph/core authority semantics were preserved. No public deployment,
registry upload or main merge was performed. Only synthetic demo evidence and
de-identified verification summaries are published.

Actual separate project: harmony-demo-001-packaging, loopback 18765. Before up,
that project was absent and port 18765 was free; occupied port 8765 was preserved.
Config → build → up each exited 0. `ps` showed Up at 127.0.0.1:18765 -> 8765.
Container inspection confirmed non-root, read-only root, cap_drop=ALL,
no-new-privileges and no host bind mounts. A filesystem assertion confirmed
that /app contains exactly the 11 allowed files above.
Alternate port 18771 config matched both the mapping and command argument;
that alternate port was not used for a container startup.

Installed headless Chromium clicked REJECT → New proposal → APPROVE against
the running package at desktop 1440×900 and emulated mobile 390×844. Exit 0.
Container inspection confirmed an empty sandbox after each rejection and only
hello.txt after each approval. Approve exit_status=0, status=FINAL.
File bytes: `harmony runtime: human-approved demo execution\n`.
Independently recomputed evidence SHA-256:
`5d85cd5d5cda1e38f568184f2ef0e8381861d7594591caab6d7f4cf4ced5f0b3`.
Canonical receipt digest recomputation matched both decisions at both viewports.
Desktop clientWidth/scrollWidth=1425/1425; mobile=390/390. No horizontal overflow.
Synthetic screenshots and receipts retained outside the repository; no session
tokens, credentials or private runtime evidence are published.

Synthetic approved receipt SHA-256:

- Desktop: ca4cec973b5f8b7be5b63738cf842a9f0fe3dd581f1f5cd98b865befedb772c2
- Mobile: 67f7adf1aee45b643ac6a71f51464fc870331fd00c037f15ed529b9628ce348d

Actual fallback `python3 -m demo.server --port 18770` started on loopback.
HTTP Reject/Approve, evidence SHA-256 and FINAL receipt digest assertions passed;
that exact validation process was stopped with SIGINT and exited normally.
`python3 -m unittest tests.test_demo -v`: exit 0, 4 tests, 1.040s.
`python3 -m compileall -q telegraph tests demo`: exit 0.
`git diff --check`: exit 0.
Previous full-suite result remains 227/229 passed, 2 existing Runner identity
errors; see [demo validation](DEMO_001_EVIDENCE.md). No full-runtime PASS claimed.

The packaging validation container, project network and local image were removed
using the scoped down command above. Existing resources were preserved.
DEMO_PACKAGING=PASS. FULL_RELEASE_ACTIVATION=HOLD.

## Portable source-package follow-up — 2026-10-04

From a committed repository checkout, generate both public source packages:

```bash
python3 scripts/package_source.py --output /tmp/harmony-source-packages
```

Use a new output directory. Files are read from the immutable committed HEAD,
not the working directory; untracked files and local runtime state are excluded.
The demo ZIP includes its exact application files, Docker/Compose packaging,
license and this startup guide. The runtime ZIP additionally includes telegraph,
unit tests, the synthetic mission fixture and selected public documentation.
Both contain PACKAGE_MANIFEST.json with source commit and each file's hash/size;
SHA256SUMS binds the resulting archives. Stable ZIP metadata makes rebuilding
the same commit byte-for-byte reproducible. These are source packages, not
prebuilt runtime images or production activation.

The runtime package contains the portable Runner resolver tests. The demo
container intentionally excludes runtime/tests and grants no production identity.
GitHub Actions validates the extracted runtime suite and actually builds/runs
the extracted demo package, checking Reject/Approve against the container and
its filesystem before uploading verification artifacts. No registry publication
or main merge is performed. Current evidence and remaining gates are recorded
in [RUNTIME_PACKAGING_PATCH_001](RUNTIME_PACKAGING_PATCH_001.md).
