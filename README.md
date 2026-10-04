# harmony runtime
![harmony runtime](docs/assets/logo.svg)

[한국어](README_KO.md) · [Vision](VISION.md) · [Core concepts](docs/CONCEPTS.md) · [Architecture](docs/ARCHITECTURE.md)

**AI can propose. Humans authorize. Player executes. Evidence proves.**


**Big Tech builds better Players. Harmony builds a better Play.**

Harmony is a **MOK-centered operating layer for Human + AI + Service**, not
another multi-agent orchestrator. **You already have the Players. Now build a better Play.**

**Use the AI you already have. Give each Player the MOK it does best.**

Product direction: **MADANG → JUMUN → MOK → PLAYER → PLAY → ROK → REVIEW**.
MADANG is the shared field; JUMUN expresses intent; MOK makes the work contract explicit.
The current implementation is the governed runtime and bounded local demo described below.

## More Than

| Harmony | More than |
|---|---|
| MADANG | WORKSPACE — a shared field of intent, authority and work |
| JUMUN | PROMPT — an intent that becomes a work contract |
| MOK | SHARE + ROLE — outcome, scope, authority, capacity, done and evidence |
| PLAYER | AI + HUMAN + SERVICE — whoever can best carry out the MOK |
| ROK | LOG — connected work, decisions, evidence and outcomes for Review |

## MOK Designer · Product direction

**Don't start by choosing an AI. Design the MOK first.**

**MINIMUM INSTRUCTION, MAXIMUM CONTRACT.** Define WHAT, WHY, BOUNDARY,
CAPACITY, AUTHORITY, DONE, EVIDENCE and HANDOFF. Leave HOW to the Player.
MOK Designer is a product direction, not a shipped designer UI in this runtime.
User-facing work records are called **ROK**. Existing internal SILROK protocol
names, where present, remain stable; this language change does not migrate APIs or data.

## Personal Free and Team / Enterprise · Planned offering

**Free for individuals. Bring your own Players.**

Personal is intended to be BYO AI and local-first: use your own AI accounts,
APIs or local models, subject to each provider's supported integration and terms.
The planned free experience includes core MOK, Player, Evidence, Baton and ROK;
Harmony does not include or fund provider inference. This release supplies a
local source runtime and demo, not the complete Personal product or integrations.

**Your infrastructure. Your policy. Your ROK.**

**Your Players may belong to them. The Play belongs to you.**

Team / Enterprise is planned for independent installation: self-hosted, VPC or
on-prem. Paid value comes from organizational complexity, shared ROK, policies,
RBAC / SSO, support and SLA, rather than withholding the personal core loop.
These are roadmap capabilities and a pricing direction, not available paid plans,
verified enterprise packages or an SLA commitment. The intended independent mode
requires no Harmony Cloud, ROK upload or mandatory telemetry; provider data egress
still depends on the Players you choose. Docker demo packaging is not enterprise deployment.


## Today: for AI-agent development teams of 1–10

**A governed execution layer for AI agents.**

For teams starting to give AI agents shell, GitHub, staging or infrastructure access.

Stop copy-pasting AI commands into privileged terminals. Let AI propose. Keep humans in authority. Preserve evidence of every execution. Inspect the approver, execution identity and result without rebuilding your workflow around a new platform.

## 60-second Baton demo

From the repository root, with Python 3.12 and no dependencies or credentials:

```bash
python3 -m demo.server
```

Open **http://localhost:8765/demo.html**. Inspect Proposal → Baton → Human Gate, choose **REJECT** (no file), then **New proposal** and **APPROVE** (one fixed file in a private temporary sandbox). Inspect output text, SHA-256 evidence, exit status, packet ID and FINAL receipt. Stop with Ctrl+C; temporary demo files are removed. One shared session per server; this is a single-user educational demo.

Docker packaging is included:

```bash
docker compose up --build
```

The container runs without root, publishes only to loopback, uses a read-only filesystem and a temporary writable sandbox. Docker config/build/up and browser decisions were verified on KVM. If port 8765 is occupied, use the tested isolated demo override (Compose must support `!override`):

```bash
docker compose -p harmony-demo-001-verification -f compose.yaml -f demo/compose.kvm.override.yaml up --build -d
```

Open **http://localhost:18765/demo.html** on the host. Stop with `docker compose -p harmony-demo-001-verification -f compose.yaml -f demo/compose.kvm.override.yaml down`. The Python command above is the dependency-free local fallback when port 8765 is free. Do not expose this demo server publicly.

[Try the 60-second Baton demo](docs/demo.html) · [Validation report](docs/DEMO_001_EVIDENCE.md)

[Demo packaging, configurable loopback ports, Python fallback and scoped cleanup](docs/DEMO_001_PACKAGING.md)

The UI is implemented and connected to the local demo server. It is **not an installable PWA or remote phone approval service**. On static hosting it cannot execute actions. Demo approval and executor names are unauthenticated labels, and hashes are educational receipts, not signed telegraph Human Gate proofs. The demo does not call or change telegraph's canonical authority logic. No arbitrary commands, production deployment, credentials or payments are accepted.

**FULL_RELEASE_ACTIVATION=HOLD**. This demonstration does not certify compliance or production readiness.

## Better Players. A better Play.

The product vision starts with the work: **MADANG → JUMUN → MOK → PLAYER → PLAY → ROK → REVIEW**.
A MOK defines the outcome, scope, authority and evidence. Human, AI and Service
are possible Players. Execution produces evidence and a work record (ROK);
review should improve the next MOK and Player selection.

Player selection considers capability, authority, privacy, cost and capacity.
Quota availability is a signal, not permission to execute. Automated Player Pool
routing, Challenge workflows, ROK analytics and Review-driven learning are future
directions, not features supplied by this release. See [Vision](VISION.md).

## Vision
A shared workspace where human intent, bounded authority, execution and evidence remain connected.

> 마지막 토큰 한 개까지, 너의 몫을 다해라

## Why harmony
AI collaboration needs explicit responsibility and verifiable handoffs. harmony runtime develops a deterministic, fail-closed foundation for approved mission packets, execution claims and chained receipts.

## Current status
This is a development source baseline. [R5C-D2 evidence package verification passed](RELEASE_STATUS.md); **full production release activation remains HOLD**. See [known limitations](KNOWN_LIMITATIONS.md), including worker auto-update and canonical output evidence. Public source packaging does not certify production readiness.

## Core Concepts
| Concept | Meaning |
|---|---|
| Madang (마당) | A shared field where Human, AI and Service Players connect intent, authority and work. |
| Mok (몫) | A boundary of responsibility and authority. |
| Player (선수) | An active participant who executes. |
| Baton (바통) | A trusted handoff of authority. |

These are product philosophy terms, not additional implemented APIs. [Read the bilingual guide](docs/CONCEPTS.md).

## Architecture
The source includes packet validation, a SQLite ledger, receipt chains, signed Human Gate verification, Actuator and Runner boundaries, and builder integration/evidence. [Architecture details](docs/ARCHITECTURE.md).

The runtime source baseline is preserved at `0990117303d98cb78805d6a1dbfff6121b8644b3`. Python 3.12 is the documented target. Governed execution also requires host authority provisioning, external trust roots and a pinned OpenSSH verifier.

## Getting started
Run from the repository root; no Python package installation is required for the CLI.

```bash
python3 -m telegraph --help
python3 -m telegraph ingest examples/mission.json --state-dir /tmp/harmony-demo
# Use PACKET_SHA256 returned by ingest:
python3 -m telegraph show <PACKET_SHA256> --state-dir /tmp/harmony-demo
python3 -m telegraph verify <PACKET_SHA256> --state-dir /tmp/harmony-demo
```

This demonstrates packet ingestion and inspection, not worker execution or production approval. Review source and host requirements before using governed execution. Keep runtime state and credentials outside the repository.

## Validation
```bash
python3 -m unittest discover -v
python3 -m compileall -q telegraph tests
```

## Deployment and roadmap

Today: local Python runtime and sandbox demo; Docker demo packaging is included
but execution remains unverified on HQ02. The static homepage is `docs/index.html`.

Planned: Personal Free (BYO AI / local-first), Team collaboration and independent
Enterprise installation (self-host / VPC / on-prem), shared ROK, policy and RBAC / SSO.
Worker pinning and canonical output evidence remain release blockers.
See [known limitations](KNOWN_LIMITATIONS.md) and [public release preparation](docs/PUBLIC_RELEASE.md).

## Community
Use this repository's issues for discussion of the source. Contact: [harmony.rapal@gmail.com](mailto:harmony.rapal@gmail.com).
Read [security guidance](SECURITY.md) before sharing operational artifacts.

## Branding and license
Original geometric h mark and landscape inspired by 유영국's geometric abstraction; no artwork reproduction or endorsement is claimed. See [brand guidance](docs/BRAND.md).
Code and documentation are licensed under [Apache License 2.0](LICENSE). See [NOTICE](NOTICE). Trademark rights are not granted; brand use is described in [brand guidance](docs/BRAND.md).
