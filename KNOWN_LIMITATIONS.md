# Known Limitations

## Worker auto-update

The worker used by the verified R5C-D2 execution was digest-bound before
launch. Its own updater replaced the current executable after execution.

The executed worker artifact remained recoverable and hash-verifiable, but
the post-run current worker was not validated by that mission.

Production activation therefore requires worker version pinning or disabling
auto-update during the sealed execution window.

## Canonical output evidence

The current builder adapter directs worker stdout and stderr to DEVNULL.

The expected R5C-D2 model response was independently recovered from the
worker transcript, but that transcript is not part of canonical Harmony
evidence.

A later revision should bind bounded worker output to the final receipt,
including at minimum:

- stdout length
- stdout SHA-256
- bounded stdout artifact
- bounded stderr artifact or digest

## Runtime state

The AI worker normally writes cache, session, conversation, log, and updater
state outside the repository.

Tests should therefore require repository working-tree immutability rather
than literal filesystem-wide immutability.
