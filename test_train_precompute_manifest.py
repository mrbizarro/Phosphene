"""Issue #62: the trainer's preprocessed cache was keyed by index only, so a
re-train from the same folder with a new trigger trained on the OLD captions
(and a dropped photo shifted every latent under the wrong caption). The
manifest reconcile invalidates exactly what changed."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import lora_lab.preprocess_images as PI


def _mk(tmp, names, captions, canvas=(768, 768, "center")):
    imgs = []
    for n in names:
        f = Path(tmp) / "images" / n
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * 10)
        imgs.append(f)
    pre = Path(tmp) / "out" / ".precomputed"
    return pre, imgs, list(captions), canvas


def _seed_cache(pre, n):
    for i in range(n):
        (pre / "latents").mkdir(parents=True, exist_ok=True)
        (pre / "conditions").mkdir(parents=True, exist_ok=True)
        (pre / "latents" / f"latent_{i:04d}.safetensors").write_bytes(b"l")
        (pre / "conditions" / f"condition_{i:04d}.safetensors").write_bytes(b"c")


class PrecomputeManifest(unittest.TestCase):
    def test_fresh_then_unchanged_reuses(self):
        with tempfile.TemporaryDirectory() as t:
            pre, imgs, caps, cv = _mk(t, ["a.png", "b.png"], ["trn a", "trn b"])
            self.assertEqual(PI._reconcile_precomputed(pre, imgs, caps, cv), "fresh")
            _seed_cache(pre, 2)
            self.assertIn("unchanged", PI._reconcile_precomputed(pre, imgs, caps, cv))
            self.assertTrue((pre / "conditions" / "condition_0001.safetensors").exists())

    def test_new_trigger_reencodes_only_changed_captions(self):
        with tempfile.TemporaryDirectory() as t:
            pre, imgs, caps, cv = _mk(t, ["a.png", "b.png", "c.png"], ["mmx26 a", "mmx26 b", "same"])
            PI._reconcile_precomputed(pre, imgs, caps, cv); _seed_cache(pre, 3)
            act = PI._reconcile_precomputed(pre, imgs, ["mrztrn a", "mrztrn b", "same"], cv)
            self.assertIn("2 caption(s) changed", act)
            self.assertFalse((pre / "conditions" / "condition_0000.safetensors").exists())
            self.assertFalse((pre / "conditions" / "condition_0001.safetensors").exists())
            self.assertTrue((pre / "conditions" / "condition_0002.safetensors").exists())
            self.assertTrue((pre / "latents" / "latent_0000.safetensors").exists())

    def test_dropped_photo_wipes_everything(self):
        with tempfile.TemporaryDirectory() as t:
            pre, imgs, caps, cv = _mk(t, ["a.png", "b.png", "c.png"], ["x", "y", "z"])
            PI._reconcile_precomputed(pre, imgs, caps, cv); _seed_cache(pre, 3)
            act = PI._reconcile_precomputed(pre, imgs[:1] + imgs[2:], ["x", "z"], cv)
            self.assertIn("images or canvas changed", act)
            self.assertFalse((pre / "latents").exists())
            self.assertFalse((pre / "conditions").exists())

    def test_cache_without_manifest_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as t:
            pre, imgs, caps, cv = _mk(t, ["a.png"], ["x"])
            _seed_cache(pre, 1)
            act = PI._reconcile_precomputed(pre, imgs, caps, cv)
            self.assertIn("predates manifests", act)
            self.assertFalse((pre / "latents").exists())
            self.assertTrue(json.loads((pre / "manifest.json").read_text())["captions"])

    def test_canvas_change_wipes_latents(self):
        with tempfile.TemporaryDirectory() as t:
            pre, imgs, caps, cv = _mk(t, ["a.png"], ["x"])
            PI._reconcile_precomputed(pre, imgs, caps, cv); _seed_cache(pre, 1)
            act = PI._reconcile_precomputed(pre, imgs, caps, (1024, 576, "center"))
            self.assertIn("images or canvas changed", act)
            self.assertFalse((pre / "latents").exists())


if __name__ == "__main__":
    unittest.main()
