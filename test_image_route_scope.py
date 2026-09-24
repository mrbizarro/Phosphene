"""/image serves images, and only images (Codex UI-01, 2026-09-24).

The route's roots include STATE_DIR because live previews and character stills
live there. STATE_DIR also holds panel_settings.json, whose HF / CivitAI /
PostHog keys `/settings` is careful never to return. Before the fix
`/image?path=<state>/panel_settings.json` answered 200 with the raw file.
Every fixture below lives in a temp dir — never the real state.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402

routes_files = sys.modules["panel.routes_files"]


class _Handler:
    def __init__(self):
        self.status = None
        self.wfile = io.BytesIO()

    def send_error(self, code, *a):
        self.status = code

    def send_response(self, code):
        self.status = code

    def send_header(self, *a):
        pass

    def end_headers(self):
        pass

    def _inline_filename(self, path):
        return Path(path).name


class ImageRouteScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state, self.out, self.up = root / "state", root / "out", root / "up"
        for d in (self.state, self.out, self.up):
            d.mkdir()
        self._patches = [mock.patch.object(panel, "STATE_DIR", self.state),
                         mock.patch.object(panel, "OUTPUT", self.out),
                         mock.patch.object(panel, "UPLOADS", self.up),
                         mock.patch.object(panel, "push", lambda *a, **k: None)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def _get(self, path: Path, w: str = "") -> _Handler:
        h = _Handler()
        url = "/image?path=" + quote(str(path)) + (f"&w={w}" if w else "")
        routes_files.get_image(h, urlparse(url))
        return h

    def test_settings_and_other_state_files_are_refused(self):
        secret = b'{"hf_token":"FAKE_TEST_SECRET"}'
        for name in ("panel_settings.json", "image_engine.json", "fleet.jsonl",
                     "queue.json", "noext"):
            f = self.state / name
            f.write_bytes(secret)
            for w in ("", "480"):
                h = self._get(f, w)
                self.assertEqual(h.status, 403, (name, w))
                self.assertNotIn(b"FAKE_TEST_SECRET", h.wfile.getvalue())

    def test_non_images_in_outputs_and_uploads_are_refused(self):
        for d in (self.out, self.up):
            f = d / "clip.mp4.json"
            f.write_bytes(b"{}")
            self.assertEqual(self._get(f).status, 403)

    def test_real_images_still_serve_from_every_root(self):
        from PIL import Image
        for d in (self.state / "live", self.out, self.up):
            d.mkdir(exist_ok=True)
            f = d / "still.png"
            Image.new("RGB", (32, 16), (200, 10, 10)).save(f)
            h = self._get(f)
            self.assertEqual(h.status, 200, d)
            self.assertTrue(h.wfile.getvalue().startswith(b"\x89PNG"))
            f2 = d / "Still.JPG"
            Image.new("RGB", (32, 16)).save(f2, "JPEG")
            self.assertEqual(self._get(f2).status, 200)

    def test_less_common_raster_uploads_still_serve(self):
        from PIL import Image
        for ext, fmt in ((".bmp", "BMP"), (".tiff", "TIFF"), (".jfif", "JPEG"),
                         (".jpe", "JPEG")):
            f = self.up / f"ref{ext}"
            Image.new("RGB", (32, 16), (10, 200, 10)).save(f, fmt)
            self.assertEqual(self._get(f).status, 200, ext)

    def test_svg_is_not_an_image_here(self):
        f = self.up / "x.svg"
        f.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>")
        self.assertEqual(self._get(f).status, 403)

    def test_outside_the_roots_is_still_refused(self):
        self.assertEqual(self._get(Path("/etc/hosts")).status, 403)


if __name__ == "__main__":
    unittest.main()
