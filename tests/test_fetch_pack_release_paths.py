"""Offline downloader regression tests; no model weights or third-party packages.

Run: python3 -B -m unittest discover -s tests -p 'test_fetch_pack_release_paths.py'
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'fetch_pack_release.py'
_spec = importlib.util.spec_from_file_location('fetch_pack_release', MODULE_PATH)
fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch)


class PackPathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dest = self.root / 'models'
        self.dest.mkdir()
        self.data = b'first shard\nsecond shard\n'
        self.chunks = [b'first shard\n', b'second shard\n']
        self.spec = {
            'bytes': len(self.data), 'sha256': self.sha(self.data),
            'shards': [{'asset': f'pack__model.part{i:03}', 'bytes': len(chunk),
                        'sha256': self.sha(chunk)} for i, chunk in enumerate(self.chunks)],
        }
        self.mirror = {'kind': 'github-release', 'release_repo': 'example/models',
                       'tag': 'v1', 'manifest_asset': 'manifest.json'}
        self.addCleanup(patch.stopall)
        patch.object(fetch, 'log').start()
        self.download = patch.object(fetch, 'download_shard', side_effect=self.fake_download).start()

    @staticmethod
    def sha(data):
        return hashlib.sha256(data).hexdigest()

    def fake_download(self, url, dest, size, sha, **kwargs):
        chunk = next(c for c in self.chunks if self.sha(c) == sha)
        dest.write_bytes(chunk)

    def assemble(self, name='model.safetensors', spec=None, dest=None):
        fetch.assemble_file(name, self.spec if spec is None else spec,
                            self.dest if dest is None else dest, self.mirror,
                            attempts=1, timeout=1)

    def test_flat_download_and_repeat(self):
        self.assemble()
        self.assertEqual((self.dest / 'model.safetensors').read_bytes(), self.data)
        self.assertEqual(self.download.call_count, 2)
        self.assemble()
        self.assertEqual(self.download.call_count, 2)
        self.assertFalse((self.dest / 'model.safetensors.partial').exists())
        self.assertFalse((self.dest / 'model.safetensors.partial.progress').exists())
        self.assertEqual(fetch.check_pack({'files': {'model.safetensors': self.spec}}, self.dest), [])

    def test_resume(self):
        partial = self.dest / 'model.safetensors.partial'
        partial.write_bytes(self.chunks[0])
        fetch._write_progress(partial, 1)
        self.assemble()
        self.assertEqual(self.download.call_count, 1)
        self.assertEqual((self.dest / 'model.safetensors').read_bytes(), self.data)

    def test_bad_whole_file_hash_still_discards_partial(self):
        spec = copy.deepcopy(self.spec)
        spec['sha256'] = '0' * 64
        with self.assertRaises(RuntimeError):
            self.assemble(spec=spec)
        self.assertFalse((self.dest / 'model.safetensors').exists())
        self.assertFalse((self.dest / 'model.safetensors.partial').exists())
        self.assertFalse((self.dest / 'model.safetensors.partial.progress').exists())

    def test_unsafe_file_and_shard_names(self):
        victim = self.root / 'victim'
        victim.write_bytes(b'original')
        names = ['../victim', str(victim), '.', '..', '', 'sub/model',
                 '..\\victim', 'C:\\victim', 'C:victim', 'bad\x00name', 'bad\nname']
        for name in names:
            with self.subTest(file=name), self.assertRaises(ValueError):
                self.assemble(name=name)
            spec = copy.deepcopy(self.spec)
            spec['shards'][0]['asset'] = name
            with self.subTest(shard=name), self.assertRaises(ValueError):
                self.assemble(spec=spec)
        self.download.assert_not_called()
        self.assertEqual(victim.read_bytes(), b'original')
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_existing_and_dangling_symlinks_are_rejected(self):
        for filename in ['model.safetensors', 'model.safetensors.partial',
                         'model.safetensors.partial.progress', fetch.PARTS_DIRNAME,
                         fetch.PARTS_DIRNAME + '/pack__model.part000']:
            for exists in (False, True):
                with self.subTest(filename=filename, exists=exists):
                    victim = self.root / 'victim'
                    if exists:
                        victim.write_bytes(b'original')
                    link = self.dest / filename
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(victim)
                    with self.assertRaises(ValueError):
                        self.assemble()
                    self.download.assert_not_called()
                    if exists:
                        self.assertEqual(victim.read_bytes(), b'original')
                        victim.unlink()
                    else:
                        self.assertFalse(victim.exists())
                    link.unlink()

    def test_user_selected_symlink_destination_is_supported(self):
        alias = self.root / 'shared-models'
        alias.symlink_to(self.dest, target_is_directory=True)
        self.assemble(dest=alias)
        self.assertEqual((self.dest / 'model.safetensors').read_bytes(), self.data)

    def test_check_only_rejects_escape_before_hashing(self):
        with patch.object(fetch, 'sha256_file') as hashed:
            with self.assertRaises(ValueError):
                fetch.check_pack({'files': {'../victim': self.spec}}, self.dest)
            hashed.assert_not_called()

    def test_fetch_validates_entire_manifest_before_creating_destination(self):
        repo = {'key': 'test', 'local_dir': 'new-models', 'files': ['model.safetensors'],
                'mirror': self.mirror}
        (self.root / fetch.REQUIRED_FILES).write_text(json.dumps({'repos': [repo]}))
        files = {'model.safetensors': self.spec, '../victim': self.spec}
        with patch.object(fetch, 'fetch_json', return_value={'files': files}):
            for check_only in (True, False):
                with self.subTest(check_only=check_only), self.assertRaises(ValueError):
                    fetch.fetch_pack('test', self.root, None, attempts=1, timeout=1,
                                     check_only=check_only)
        self.download.assert_not_called()
        self.assertFalse((self.root / 'new-models').exists())
        self.assertFalse((self.root / '.new-models.fetchlock').exists())

    def test_extra_safe_sidecar_is_supported(self):
        files = {'model.safetensors': self.spec, 'extra_config.json': self.spec}
        self.assertEqual(fetch._fetch_locked('test', files, self.dest, self.mirror,
                                            attempts=1, timeout=1), 0)
        self.assertEqual((self.dest / 'extra_config.json').read_bytes(), self.data)

    def test_reserved_parts_directory_is_rejected(self):
        with self.assertRaises(ValueError):
            self.assemble(name=fetch.PARTS_DIRNAME)
        self.download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
