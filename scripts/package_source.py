"""Build deterministic, allowlisted public source packages from a committed ref."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
DEMO = (
    'demo/__init__.py', 'demo/server.py', 'Dockerfile', '.dockerignore',
    'compose.yaml', 'demo/compose.kvm.override.yaml',
    'docs/index.html', 'docs/demo.html', 'docs/style.css', 'docs/demo.js',
    'docs/assets/logo.svg', 'docs/assets/logo-mark.svg',
    'docs/assets/logo-mono.svg', 'docs/assets/landscape.svg',
    'docs/assets/hero-wordmark.svg', 'docs/DEMO_001_PACKAGING.md',
    'LICENSE', 'NOTICE',
)
RUNTIME_DOCS = ('README.md', 'README_KO.md', 'RELEASE_STATUS.md',
                'KNOWN_LIMITATIONS.md', 'SECURITY.md', 'INFLUENCES.md',
                'VISION.md', 'VISION_KO.md', 'docs/ARCHITECTURE.md',
                'docs/CONCEPTS.md', 'docs/BRAND.md', 'docs/PUBLIC_RELEASE.md')


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def build(ref, output):
    sha = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
    tree = git('ls-tree', '-r', '-z', sha)
    entries = {}
    for item in tree.split(b'\0'):
        if item:
            meta, path = item.split(b'\t', 1)
            mode, kind, blob = meta.decode().split()
            entries[path.decode()] = (mode, kind, blob)
    runtime = set(DEMO + RUNTIME_DOCS + ('examples/mission.json',))
    runtime.update(p for p in entries if p.startswith(('telegraph/', 'tests/')) and p.endswith('.py'))
    output.mkdir(parents=True, exist_ok=True)
    packages = []
    for name, paths in [('demo', set(DEMO)), ('runtime', runtime)]:
        files = {}
        for path in sorted(paths):
            mode, kind, blob = entries[path]
            if mode != '100644' or kind != 'blob':
                raise ValueError('Package requires regular non-executable source file: ' + path)
            files[path] = git('cat-file', 'blob', blob)
        manifest = dict(source_commit=sha, package=name, full_release_activation='HOLD',
                        files={p:dict(sha256=hashlib.sha256(b).hexdigest(), size=len(b))
                               for p, b in files.items()})
        files['PACKAGE_MANIFEST.json'] = (json.dumps(manifest, sort_keys=True, indent=2)+'\n').encode()
        archive = output / f'harmony-{name}-{sha[:12]}.zip'
        with archive.open('xb') as stream, ZipFile(stream, 'w', compression=ZIP_DEFLATED) as bundle:
            for path, data in sorted(files.items()):
                info = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, data)
        packages.append(archive)
    checksums = output / 'SHA256SUMS'
    with checksums.open('x') as stream:
        for archive in packages:
            stream.write(f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n')
    return packages


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD', help='Committed source ref (default HEAD)')
    parser.add_argument('--output', type=Path, required=True, help='New package output directory')
    args = parser.parse_args()
    for package in build(args.ref, args.output.resolve()):
        print(package)
