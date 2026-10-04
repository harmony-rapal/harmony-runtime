"""Prepare a versioned static Linux demo download directory for an existing webserver."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shlex
from urllib.parse import urlparse
from package_source import build, git


def render_install_command(base):
    """Return a copy/paste-safe shell command for the published installer URL."""
    install_url = shlex.quote(base.rstrip('/') + '/install.sh')
    return (
        "curl --fail --location --proto '=https' --proto-redir '=https' "
        f"{install_url} -o harmony-install.sh && sh harmony-install.sh"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--base-url', required=True, help='Exact HTTPS directory serving these files')
    parser.add_argument('--output', type=Path, required=True, help='New staging directory; never an existing web root')
    args = parser.parse_args()
    url = urlparse(args.base_url)
    if (url.scheme != 'https' or not url.netloc or url.query or url.fragment
            or url.username or url.password or any(c in args.base_url for c in "'\\\n\r")):
        parser.error('An HTTPS directory URL without credentials/query or shell characters is required')
    if args.output.exists():
        parser.error('Choose a new output directory')
    packages = build(args.ref, args.output)
    sha = git('rev-parse', '--verify', args.ref+'^{commit}').decode().strip()
    installer = git('show', sha+':scripts/install_demo.py')
    (args.output/'install_demo.py').write_bytes(installer)
    demo = next(p for p in packages if p.name.startswith('harmony-demo-'))
    demo_hash = hashlib.sha256(demo.read_bytes()).hexdigest()
    installer_hash = hashlib.sha256(installer).hexdigest()
    base = args.base_url.rstrip('/')
    # URLs and hashes are fixed in this version's launcher, never resolved via latest.
    launcher = f"""#!/bin/sh
set -eu
[ "$(uname -s)" = Linux ] || {{ echo 'Linux only'; exit 1; }}
command -v python3 >/dev/null || {{ echo 'Python 3.12 required; install it separately'; exit 1; }}
command -v curl >/dev/null || {{ echo 'curl required; install it separately'; exit 1; }}
command -v sha256sum >/dev/null || {{ echo 'sha256sum required'; exit 1; }}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' '{base}/install_demo.py' -o "$work/install_demo.py"
printf '%s  %s\\n' '{installer_hash}' "$work/install_demo.py" | sha256sum --check --status
python3 "$work/install_demo.py" --archive '{base}/{demo.name}' --sha256 '{demo_hash}' --destination "$HOME/.local/share/harmony/demo-{sha[:12]}" --run "$@"
"""
    (args.output/'install.sh').write_text(launcher)
    (args.output/'DOWNLOAD.json').write_text(json.dumps(dict(source_commit=sha, base_url=base,
        platform='Linux', requires='Python 3.12, curl, sha256sum', full_release_activation='HOLD',
        demo_archive=demo.name, demo_sha256=demo_hash, installer_sha256=installer_hash), indent=2)+'\n')
    command = render_install_command(base)
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>harmony runtime · Linux demo download</title><body><h1>harmony runtime · Linux demo</h1><p>Interactive prototype/demo. Python 3.12+, curl and sha256sum required. No root or sudo. FULL_RELEASE_ACTIVATION=HOLD.</p><p>Version: {sha}</p><pre>{html.escape(command)}</pre><p>Visit localhost:18770/demo.html after installation. Ctrl+C stops the demo. No production authority or AI credentials are installed.</p><ul><li><a href="{demo.name}">Demo source ZIP</a></li><li><a href="{next(p.name for p in packages if p.name.startswith('harmony-runtime-'))}">Runtime source ZIP</a></li><li><a href="SHA256SUMS">Archive checksums</a></li><li><a href="install.sh">Inspect installer entry point</a></li></ul></body></html>"""
    (args.output/'index.html').write_text(page)
    print(args.output)
    print('Publish these static files only into a new version directory; do not modify webserver settings.')


if __name__ == '__main__':
    main()
