"""H3 Image mode: a broken engine venv is not a bad picture (fleet 4.15.2).

One 128 GB Mac on 4.15.2 failed six H3 Image renders with "The reference image
can't be read (ModuleNotFoundError). Export it as a JPEG or PNG" — the panel
could not import PIL because its engine venv was half installed, and the same
install also reported "the engine venv has a Python but no packages". The
sentence sent the user to re-export a good image instead of to Repair.
"""
from __future__ import annotations

import builtins
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402


class H3ReferenceImageError(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_readable_image_passes(self):
        from PIL import Image
        f = self.dir / "ok.png"
        Image.new("RGB", (8, 8)).save(f)
        self.assertEqual(panel.h3_reference_image_error(f), "")

    def test_a_broken_file_asks_for_a_jpeg_or_png(self):
        f = self.dir / "bad.png"
        f.write_bytes(b"not an image")
        msg = panel.h3_reference_image_error(f)
        self.assertIn("can't be read", msg)
        self.assertIn("JPEG or PNG", msg)

    def test_a_venv_without_pillow_sends_the_user_to_repair(self):
        f = self.dir / "ok.png"
        f.write_bytes(b"\\x89PNG")
        real_import = builtins.__import__

        def no_pil(name, *a, **k):
            if name == "PIL" or name.startswith("PIL."):
                raise ModuleNotFoundError("No module named 'PIL'")
            return real_import(name, *a, **k)

        with mock.patch.dict(sys.modules, {k: None for k in list(sys.modules)
                                           if k == "PIL" or k.startswith("PIL.")}), \
                mock.patch.object(builtins, "__import__", no_pil):
            msg = panel.h3_reference_image_error(f)
        self.assertNotIn("JPEG or PNG", msg)
        self.assertIn("venv", msg)                 # venv_broken in the taxonomy
        self.assertIn(panel.ENGINE_ENV_REPAIR, msg)
        self.assertEqual(panel._analytics_error_class(msg), "venv_broken")

    def test_the_render_path_uses_the_helper(self):
        import inspect
        src = inspect.getsource(panel.run_h3_job_inner)
        self.assertIn("h3_reference_image_error(", src)
        self.assertNotIn("from PIL import Image as _PilImg", src)


if __name__ == "__main__":
    unittest.main()
