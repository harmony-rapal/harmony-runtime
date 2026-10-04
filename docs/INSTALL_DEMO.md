# Linux demo installation and KVM static downloads

Scope: the bounded local Baton interactive prototype/demo. No production runtime
activation, installable PWA, remote approval or AI-provider integration is added.
FULL_RELEASE_ACTIVATION=HOLD. No accounts, services, sudo grants, PATH changes,
OS packages or existing installations are modified. Python 3.12+, curl and
sha256sum must already be available. First supported platform: Linux.

## Publish from KVM using its existing webserver

The user selected https://hab.rapal.tech/download as the download base. The actual
web root and access path must be confirmed before publishing; no existing server
configuration is changed. Generate a new version directory from committed source:

```bash
python3 scripts/prepare_download.py --ref COMMIT_SHA --base-url https://hab.rapal.tech/download/COMMIT_SHA --output /tmp/harmony-download-COMMIT_SHA
```

Replace COMMIT_SHA with the exact verified commit in all three places. Publish
only the generated static files into a NEW matching directory beneath the
existing download web root. Do not overwrite an existing published version,
change webserver config, expose the demo server or stop existing services.
The source runtime/demo ZIPs, checksums, installer, pinned install.sh and
DOWNLOAD.json are public synthetic-source artifacts, not runtime state.

Before sharing, verify HTTPS access to every file and compare served bytes with
local hashes. Test install from that exact published URL into a fresh user
installation, then exercise Reject and Approve. Publishing is not verified
until those actual HTTPS checks and end-to-end install have passed.

## User installation

Use the exact published version URL; no changing latest link is needed. Download
the shell entry point to a file, inspect it if desired, then run it:

```bash
curl --fail --location --proto '=https' --proto-redir '=https' https://hab.rapal.tech/download/COMMIT_SHA/install.sh -o harmony-install.sh
sh harmony-install.sh
```

The launcher uses curl for the public HTTPS transport of both the Python installer
and demo archive, verifies both pinned SHA-256 values locally, then hands the local
archive to Python. Python rechecks the archive SHA-256, per-file manifest hashes,
size bounds and ZIP path safety. This keeps public network transport on one pinned
client path while preserving fail-closed content verification.
Installation is confined to ~/.local/share/harmony/demo-COMMIT_SHA_PREFIX.
An existing installation is preserved and causes a clear stop. To install
elsewhere pass --destination /your/new/directory. The demo runs in the foreground
on loopback port 18770; pass --port 18771 if that port is in use. No other process
is stopped. Visit http://localhost:18770/demo.html and try Reject, New proposal,
Approve. Ctrl+C stops the server and removes only its temporary sandbox.

For subsequent runs, enter the installed directory and run:

```bash
python3 -m demo.server --port 18770
```

To uninstall, stop this demo and remove only its installation directory. No
uninstaller touches shared files, OS identity, services or other runtime state.
No feedback collection or telemetry is added. The owner collects feedback
personally: first-run time, confusing step, approval/evidence clarity and a real
work task the user would want to repeat. Do not solicit secrets or private logs.

## Current verification and publication blocker — 2026-10-04

Linux installer tests: 5/5 PASS, including corrupt checksum/content, unsafe paths
and preserving an existing/symlink installation. Full HQ02 suite: 235/235 PASS,
109.375 seconds. Real committed demo ZIP installed into a fresh /tmp directory;
its actual HTTP Reject/Approve/Evidence/FINAL cycle passed, then its server exited
normally on SIGINT. Generated install.sh passed shell syntax validation.

Read-only KVM inspection: hab.rapal.tech has Nginx, existing /ops/ static content,
and location / returning 404. There is no /download/ mapping. Existing gungdev
and r200dev SSH sessions do not have non-interactive sudo access. No webserver
setting, service, account or permission was modified. Publishing requires an
explicitly approved administrator addition for /download/, then real HTTPS
checks. Prepared files are not evidence of a working public download.
