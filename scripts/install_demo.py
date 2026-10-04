"""Install a verified demo source ZIP without changing OS accounts or services."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import subprocess
import tempfile
from urllib.parse import urlparse
from urllib.request import urlopen
from zipfile import ZipFile, BadZipFile

MAX_ARCHIVE = 8 * 1024 * 1024
MAX_FILES = 64
MAX_CONTENT = 16 * 1024 * 1024


def install(archive, expected_sha256, destination):
    if len(expected_sha256) != 64 or any(c not in '0123456789abcdef' for c in expected_sha256):
        raise ValueError('A lowercase SHA-256 from the release checksum is required')
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Destination already exists; choose a new directory')
    parsed = urlparse(str(archive))
    if parsed.scheme and parsed.scheme != 'https':
        raise ValueError('Downloads require HTTPS; local archive paths are also supported')
    if parsed.scheme:
        with urlopen(str(archive), timeout=30) as response:
            if urlparse(response.geturl()).scheme != 'https':
                raise ValueError('Download redirected outside HTTPS')
            payload = response.read(MAX_ARCHIVE + 1)
    else:
        with Path(archive).open('rb') as stream:
            payload = stream.read(MAX_ARCHIVE + 1)
    if len(payload) > MAX_ARCHIVE or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError('Archive size or SHA-256 verification failed')
    import io
    with ZipFile(io.BytesIO(payload)) as bundle:
        infos = bundle.infolist()
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_CONTENT:
            raise ValueError('Archive content exceeds the demo bounds')
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive entry')
        for info in infos:
            path = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if (path.is_absolute() or '..' in path.parts or '\\' in info.filename
                    or info.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                raise ValueError('Unsafe archive entry')
        manifest = json.loads(bundle.read('PACKAGE_MANIFEST.json'))
        sha = manifest.get('source_commit', '')
        if (manifest.get('package') != 'demo' or manifest.get('full_release_activation') != 'HOLD'
                or len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha)):
            raise ValueError('Only a versioned HOLD demo package can be installed')
        files = manifest['files']
        if set(names) != set(files) | {'PACKAGE_MANIFEST.json'}:
            raise ValueError('Manifest does not match archive entries')
        data = {name: bundle.read(name) for name in files}
        for name, content in data.items():
            if (len(content) != files[name]['size'] or
                    hashlib.sha256(content).hexdigest() != files[name]['sha256']):
                raise ValueError('Content verification failed: ' + name)
        if not {'demo/server.py', 'demo/__init__.py', 'docs/demo.html', 'docs/demo.js'} <= set(data):
            raise ValueError('Required demo files missing')
        # Validate everything before creating installation contents. Never overwrite.
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.harmony-install-', dir=destination.parent))
        try:
            for name, content in data.items():
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            (staging / 'PACKAGE_MANIFEST.json').write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
            # mkdir reserves the target atomically; a concurrent install cannot be replaced.
            destination.mkdir()
            try:
                for item in staging.iterdir():
                    shutil.move(str(item), str(destination / item.name))
            except BaseException:
                shutil.rmtree(destination)
                raise
        finally:
            shutil.rmtree(staging)
    return sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, help='Fixed-version HTTPS URL or local demo ZIP')
    parser.add_argument('--sha256', required=True, help='Expected archive SHA-256 from the release')
    parser.add_argument('--destination', default=str(Path.home()/'.local/share/harmony/demo'), help='New user-owned directory')
    parser.add_argument('--run', action='store_true', help='Start the loopback demo in the foreground after install')
    parser.add_argument('--port', type=int, default=18770, help='Local demo port (default 18770)')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535')
    if sys.version_info < (3, 12):
        parser.error('Python 3.12 or newer is required; no system packages will be installed')
    if not sys.platform.startswith('linux'):
        parser.error('This first installer release supports Linux only')
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        parser.error('Run as your normal user; this demo needs no root or sudo')
    try:
        revision = install(args.archive, args.sha256, args.destination)
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        parser.exit(1, f'Installation stopped: {error}\n')
    destination = Path(args.destination).expanduser().absolute()
    print(f'Installed demo source {revision} in {destination}')
    print('Interactive prototype/demo. FULL_RELEASE_ACTIVATION=HOLD.')
    print(f'From that directory run: python3 -m demo.server --port {args.port}')
    print(f'Open http://localhost:{args.port}/demo.html; Ctrl+C stops and cleans the sandbox.')
    print('To uninstall, remove only this installation directory after stopping the demo.', flush=True)
    if args.run:
        raise SystemExit(subprocess.call([sys.executable, '-B', '-m', 'demo.server', '--port', str(args.port)], cwd=destination))


if __name__ == '__main__':
    main()
