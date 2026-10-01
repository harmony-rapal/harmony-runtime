# Harmony Telegraph Core

Deterministic fail-closed state machine for Harmony's Actuator layer.

## What This Is

Telegraph Core is **not** an AI agent. It does not interpret natural language.

It:
- Validates approved Mission Packets against a strict schema
- Fixes packet identity with SHA256
- Records state transitions in a SQLite ledger
- Enforces exactly-once execution claims per stage
- Produces immutable, chained Receipts for every transition

## What This Is Not

- Not a command executor
- Not a subprocess launcher
- Not a shell wrapper
- Not a network client
- Not an AI agent runner

## Scope — DRY-RUN CORE ONLY

This implementation uses **Python 3.12 standard library only**.

No external dependencies. No network. No shell. No subprocess.

## Installation

```
# No install required. Run directly from source.
python -m telegraph --help
```

## Usage

```bash
# Ingest a mission packet
python -m telegraph ingest examples/mission.json --state-dir /tmp/state

# Approve a packet (READY → APPROVED)
python -m telegraph approve <PACKET_SHA256> --approver human-name --state-dir /tmp/state

# Claim a stage (APPROVED → DISPATCHED)
python -m telegraph claim <PACKET_SHA256> --stage BUILD --state-dir /tmp/state

# Show packet status
python -m telegraph show <PACKET_SHA256> --state-dir /tmp/state

# Place a packet on hold
python -m telegraph hold <PACKET_SHA256> --reason "manual review" --state-dir /tmp/state

# Verify receipt chain integrity
python -m telegraph verify <PACKET_SHA256> --state-dir /tmp/state
```

## State Machine

```
NEW ──[INGEST]──► READY ──[APPROVE]──► APPROVED ──[CLAIM]──► DISPATCHED
                     │                     │                      │
                  [HOLD]               [HOLD]                 [HOLD]
                     └─────────────────────┴──────────────────────┘
                                           ▼
                                         HOLD
```

States: `READY`, `APPROVED`, `DISPATCHED`, `HOLD`, `HUMAN_GATE`, `FINAL`, `RETIRE`

Implemented automatic transitions:
- `INGEST`: NEW → READY
- `APPROVE`: READY → APPROVED
- `CLAIM`: APPROVED → DISPATCHED
- `HOLD`: READY | APPROVED | DISPATCHED → HOLD

## Mission Packet V1 Schema

Required fields:

| Field | Type | Constraint |
|---|---|---|
| `schema_version` | integer | Must be `1` |
| `mission_id` | string | Non-empty |
| `revision` | integer | Positive |
| `project_id` | string | Non-empty |
| `objective` | string | Non-empty |
| `action` | enum | See allowed values below |
| `profile_id` | string | Non-empty |
| `target` | object | See below |
| `retry_policy` | string | Must be `"NEVER"` |
| `on_failure` | string | Must be `"HOLD"` |

Allowed `action` values: `CREATE_WORKTREE`, `START_BUILDER`, `RUN_VALIDATION`,
`FREEZE_RESULT`, `CREATE_AUDIT_CAPSULE`, `START_AUDITOR`, `CAPTURE`, `FINALIZE`, `RETIRE`

`target` fields: `repo_root` (non-empty string), `base_commit_oid` (40-char lowercase hex)

Explicitly forbidden fields (rejected anywhere in packet tree):
`shell_command`, `command`, `argv`, `environment`, `env`, `sudo`, `retry`,
`bypass_permissions`, `callback_url`

Unknown fields at any level are rejected.

## Canonical SHA256

```python
json.dumps(
    packet,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
```

SHA256 is computed over these canonical bytes. The SHA is **not** stored inside the packet body.

Same packet content → same SHA regardless of key ordering in the source JSON.

## Claim Idempotency

Claiming the same `(packet_sha256, stage)` a second time:
- Does **not** create a new claim row
- Does **not** execute anything
- Returns `CLAIM_STATUS=EXISTING` and `NEW_LAUNCH=NO`

## Receipt Chain

Every successful state transition appends an immutable Receipt:

```
receipt_id              UUID
mission_id
revision
packet_sha256
sequence                monotonically increasing from 1
previous_receipt_sha256 SHA256 of previous receipt body (None for first)
from_state
to_state
stage
timestamp               ISO 8601 UTC
```

Receipt SHA256 is computed from canonical JSON of the receipt body (not stored inside the body).

Receipts are never updated or deleted.

## Fail-Closed Behaviour

All of the following exit non-zero and emit `ERROR=...` to stderr:

- Invalid schema
- Unknown field
- Forbidden field
- Invalid SHA
- Unknown packet
- Invalid state transition
- SQLite integrity violation
- Receipt chain mismatch

No silent fallback. No retry. No alternative path.

## Tests

```bash
python -m unittest discover -v
```

## Compile Check

```bash
python -m compileall -q telegraph tests
```

---

## FUTURE SECURITY BOUNDARY

> **Approval is currently a human-gate simulation only.**
>
> `approve` accepts a plain `approver` string and records it in the ledger.
> There is **no cryptographic signature verification** in this version.
>
> In a production deployment, the approval step must be replaced with a
> verifiable cryptographic mechanism — for example:
>
> - Ed25519 or ECDSA signature over the packet SHA256
> - HSM-backed signing
> - Threshold multi-party approval with verifiable signatures
>
> Until cryptographic signatures are implemented, the approval gate provides
> **audit trail only**, not tamper-evident authorisation.
> This is a known security boundary, not an oversight.
