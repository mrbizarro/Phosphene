"""Automatic updates are opt-out; explicit updates remain possible. No network."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

_scratch = tempfile.TemporaryDirectory(prefix="update-settings-")
os.environ.update(LTX_STATE_DIR=_scratch.name, LTX_OUTPUT_DIR=_scratch.name + '/outputs',
                  LTX_UPLOADS_DIR=_scratch.name + '/uploads', PHOSPHENE_ANALYTICS_DISABLED='1',
                  PHOSPHENE_DISABLE_VERSION_CHECK='1', PHOSPHENE_DISCOVERY_DIR=_scratch.name + '/discovery')
import mlx_ltx_panel as P
import pro_parts as parts

class PreferenceTests(unittest.TestCase):
    def test_default_on_and_false_survives_unrelated_save_and_reload(self):
        self.assertTrue(P._settings_defaults()['update_automatically'])
        _, error = P.update_settings({'update_automatically': 'false'})
        self.assertIsNone(error)
        P.update_settings({'notify_done': 'false'})
        self.assertFalse(P.get_settings_public()['update_automatically'])
        import json
        self.assertFalse(json.loads(P.SETTINGS_FILE.read_text())['update_automatically'])
        self.assertFalse(P._load_settings()['update_automatically'])

    def test_invalid_choice_does_not_enable_updates(self):
        P.update_settings({'update_automatically': False})
        _, error = P.update_settings({'update_automatically': 'maybe'})
        self.assertIsNotNone(error)
        self.assertFalse(P.get_settings()['update_automatically'])
        _, error = P.update_settings({'update_automatically': 'true'})
        self.assertIsNone(error)
        self.assertTrue(P.get_settings()['update_automatically'])

class PinokioUpdates(unittest.TestCase):
    def test_background_off_makes_no_offer_or_download(self):
        host = SimpleNamespace(get_settings=lambda: {'update_automatically': False})
        with patch.object(parts._dist, 'is_mac_app', return_value=False), patch.object(parts, '_licence', return_value={'present': True}), patch.object(parts, 'fetch') as fetch:
            parts.start(host, sleep=lambda _: None, rounds=3)
            fetch.assert_not_called()

    def test_background_on_and_legacy_settings_still_update(self):
        for settings in ({}, {'update_automatically': True}):
            host = SimpleNamespace(get_settings=lambda: settings)
            with patch.object(parts._dist, 'is_mac_app', return_value=False), patch.object(parts, '_licence', return_value={'present': True}), patch.object(parts, 'fetch', return_value={'ok': False}) as fetch:
                parts.start(host, sleep=lambda _: None, rounds=1)
                fetch.assert_called_once_with(automatic=True)

    def test_switch_off_before_fetch_and_during_download(self):
        settings = {'update_automatically': False}
        parts._P[0] = SimpleNamespace(get_settings=lambda: settings)
        with patch.object(parts._dist, 'is_mac_app', return_value=False), patch.object(parts, '_licence') as licence:
            self.assertEqual(parts.fetch(automatic=True)['error'], 'automatic_updates_off')
            licence.assert_not_called()
        # Exercise real fetch through verified offer/download/probe with disposable bytes.
        settings['update_automatically'] = True
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return b'{"url":"http://127.0.0.1/test","key_id":"test","manifest":"fixture","signature":"fixture"}'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def download(url, out, size, key, opener):
                out.write_bytes(b'fixture')
                settings['update_automatically'] = False
            manifest = dict(version='5.0.99', bytes=7, sha256='fixture', strip_components=0)
            with patch.object(parts._dist, 'is_mac_app', return_value=False), patch.object(parts, '_licence', return_value=dict(key='fixture',instance_id='fixture',device='fixture')), patch.object(parts,'root',return_value=root), patch.object(parts,'_verify',return_value=(manifest,b'')), patch.object(parts,'_record',return_value=None), patch.object(parts,'_download',side_effect=download), patch.object(parts,'_sha',return_value='fixture'), patch.object(parts,'_extract',return_value={}), patch.object(parts,'_probe',return_value=True):
                result = parts.fetch(lambda *a, **k: Response(), automatic=True)
                self.assertEqual(result['error'], 'automatic_updates_off')
                self.assertFalse((root/'current.json').exists())

    def test_manual_retry_is_not_gated(self):
        parts._P[0] = SimpleNamespace(get_settings=lambda: {'update_automatically': False})
        with patch.object(parts, 'fetch', return_value={'ok': False}) as fetch:
            parts._manual_run()
            fetch.assert_called_once_with()


class ResumeDownloads(unittest.TestCase):
    def response(self, data, status=200, headers=None):
        import io
        stream = io.BytesIO(data)
        stream.status = status
        stream.headers = headers or {}
        return stream

    def test_interrupted_transfer_continues_at_saved_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'archive'
            with patch.object(parts, '_server', return_value='http://127.0.0.1'):
                with self.assertRaises(ValueError):
                    parts._download('http://127.0.0.1/a', path, 6, 'fixture', lambda *a, **k: self.response(b'abc'))
                self.assertEqual(path.read_bytes(), b'abc')
                def resumed(request, **kwargs):
                    self.assertEqual(request.headers['Range'], 'bytes=3-')
                    return self.response(b'def', 206, {'Content-Range':'bytes 3-5/6'})
                parts._download('http://127.0.0.1/a', path, 6, 'fixture', resumed)
                self.assertEqual(path.read_bytes(), b'abcdef')
                parts._download('http://127.0.0.1/a', path, 6, 'fixture', lambda *a, **k: self.fail('complete archive downloaded again'))

    def test_ignored_range_replaces_partial_instead_of_appending(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'archive'; path.write_bytes(b'abc')
            with patch.object(parts, '_server', return_value='http://127.0.0.1'):
                parts._download('http://127.0.0.1/a', path, 6, 'fixture', lambda *a, **k: self.response(b'abcdef'))
            self.assertEqual(path.read_bytes(), b'abcdef')

    def test_bad_range_keeps_saved_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'archive'; path.write_bytes(b'abc')
            with patch.object(parts, '_server', return_value='http://127.0.0.1'), self.assertRaises(ValueError):
                parts._download('http://127.0.0.1/a', path, 6, 'fixture', lambda *a, **k: self.response(b'bad',206,{'Content-Range':'bytes 0-2/6'}))
            self.assertEqual(path.read_bytes(),b'abc')

if __name__ == '__main__': unittest.main()
