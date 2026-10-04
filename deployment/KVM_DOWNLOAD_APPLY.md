# Approved KVM static download addition

The owner approved adding only /download/ to the existing hab.rapal.tech Nginx
HTTPS server. The current gungdev/r200dev SSH sessions lack administrator access;
this step must run in an existing administrator session. Do not provision new
accounts, change sudo permissions or stop unrelated services.

1. Confirm the verified version directory staged under the r200dev user's
   harmony-demo-download-staging directory. Verify SHA256SUMS and DOWNLOAD.json.
2. Create /var/www/harmony-downloads/download/VERSION as a NEW directory and copy
   only the staged public static files there (regular files, no symlinks). Use
   readable static-file permissions for these new directories/files only.
3. Back up /etc/nginx/sites-available/hab.rapal.tech before editing. Add the
   location block from hab-download.nginx.conf inside its existing HTTPS server.
   The existing server-level Harmony Control Room Basic Auth is intentionally
   disabled only inside the public /download and /download/ locations; the API
   bridge, /ops/, TLS settings, default 404 and all other protected paths remain
   unchanged. Check nginx -t; restore the backup if validation fails.
4. After a successful configuration check, reload Nginx (not restart). Publish
   /download/index.html linking the verified version, so the base URL is usable.
5. Check the public HTTPS URLs for index.html, install.sh, install_demo.py,
   DOWNLOAD.json, both ZIPs and SHA256SUMS. Independently compare served hashes.
6. On a normal Linux account, run the published installer into a fresh directory.
   Verify REJECT no-op, APPROVE fixed sandbox file, Evidence hash, exit status 0,
   FINAL and Ctrl+C cleanup. Confirm existing API bridge and /ops/ still respond.

Rollback only this added location and this new static version directory; restore
the prior site backup, validate and reload. Never remove existing operational
files or use global prune. FULL_RELEASE_ACTIVATION=HOLD.
