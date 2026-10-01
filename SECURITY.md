# Security

Runtime authority and private operational state must not be committed here.

Do not commit:

- private keys
- API or OAuth credentials
- AI-worker session databases
- conversation logs or cache
- runtime ledger databases
- private Human Gate receipts
- private host/network information
- sealed worker executable binaries

Harmony execution boundaries are intended to fail closed when identity,
digest, authority, or receipt binding cannot be verified.

Long-lived shared API keys are not an intended authority mechanism.
Prefer scoped, revocable identities and narrow privilege boundaries.
