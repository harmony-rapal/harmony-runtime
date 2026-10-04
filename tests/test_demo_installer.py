import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

from scripts.install_demo import install


class DemoInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / 'installed'

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, extra=None):
        files = {name: b'demo fixture' for name in
                 ('demo/server.py', 'demo/__init__.py', 'docs/demo.html', 'docs/demo.js')}
        if extra: files.update(extra)
        manifest = dict(package='demo', source_commit='a'*40, full_release_activation='HOLD',
                        files={p: dict(size=len(b), sha256=hashlib.sha256(b).hexdigest())
                               for p, b in files.items()})
        archive = self.root / 'demo.zip'
        with ZipFile(archive, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
            z.writestr('PACKAGE_MANIFEST.json', json.dumps(manifest))
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_verified_install_and_existing_install_preserved(self):
        archive, sha = self.archive()
        self.assertEqual(install(archive, sha, self.target), 'a'*40)
        self.assertEqual((self.target/'demo/server.py').read_bytes(), b'demo fixture')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            install(archive, sha, self.target)
        self.assertEqual((self.target/'demo/server.py').read_bytes(), b'demo fixture')

    def test_wrong_checksum_creates_no_installation(self):
        archive, _ = self.archive()
        with self.assertRaisesRegex(ValueError, 'verification failed'):
            install(archive, '0'*64, self.target)
        self.assertFalse(self.target.exists())

    def test_path_traversal_creates_nothing(self):
        archive, sha = self.archive({'../outside': b'must not be written'})
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            install(archive, sha, self.target)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root/'outside').exists())

    def test_tampered_content_rejected_before_install(self):
        archive, _ = self.archive()
        with ZipFile(archive) as original:
            contents = {name: original.read(name) for name in original.namelist()}
        contents['demo/server.py'] = b'tampered executable content'
        with ZipFile(archive, 'w') as modified:
            for name, data in contents.items(): modified.writestr(name, data)
        with self.assertRaisesRegex(ValueError, 'Content verification failed'):
            install(archive, hashlib.sha256(archive.read_bytes()).hexdigest(), self.target)
        self.assertFalse(self.target.exists())

    def test_symlink_destination_preserved(self):
        archive, sha = self.archive()
        existing = self.root/'existing'
        existing.mkdir()
        (existing/'sentinel').write_text('preserve')
        self.target.symlink_to(existing, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            install(archive, sha, self.target)
        self.assertEqual((existing/'sentinel').read_text(), 'preserve')


class DownloadCommandSafetyTests(unittest.TestCase):
    def test_rendered_install_url_is_shell_quoted(self):
        scripts_dir = Path(__file__).resolve().parents[1] / 'scripts'
        # prepare_download.py is a source/publication helper and is intentionally
        # not part of the extracted runtime package.
        if not (scripts_dir / 'prepare_download.py').exists():
            self.skipTest('source-only download preparation helper is not packaged')
        scripts = str(scripts_dir)
        sys.path.insert(0, scripts)
        try:
            prepare_download = importlib.import_module('prepare_download')
            command = prepare_download.render_install_command(
                'https://example.test/download/$(touch SHOULD_NOT_RUN)'
            )
        finally:
            sys.path.remove(scripts)
            sys.modules.pop('prepare_download', None)
        self.assertIn("'https://example.test/download/$(touch SHOULD_NOT_RUN)/install.sh'", command)
