"""Browser-installed H3 LoRAs get the same strength as uploaded ones (Codex H3-01).

The upload importer writes the adapter's alpha/rank into recommended_strength;
the Hugging Face and CivitAI installers wrote the listing's number or 1.0, and
`_h3_lora_prepare` only folds MIXED ratios — so a uniform-alpha adapter from a
browser was applied rank/alpha times too strong. Network is faked; every file
lives in a temp dir.
"""
from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P                                            # noqa: E402

RANK, ALPHA = 4, 2.0                     # uniform alpha/rank = 0.5 on every module


def _uniform_alpha_lora() -> bytes:
    values, header = [], {}
    for module in ("blocks.0.attn.qkv_proj", "blocks.1.attn.qkv_proj"):
        values.append((module + ".lora_A.weight", b"\0" * (4 * RANK), [RANK, 1]))
        values.append((module + ".lora_B.weight", b"\0" * (4 * RANK), [1, RANK]))
        values.append((module + ".alpha", struct.pack("<f", ALPHA), [1]))
    offset = 0
    for key, raw, shape in values:
        header[key] = {"dtype": "F32", "shape": shape, "data_offsets": [offset, offset + len(raw)]}
        offset += len(raw)
    enc = json.dumps(header).encode()
    return len(enc).to_bytes(8, "little") + enc + b"".join(r for _, r, _ in values)


class BrowserInstallsCarryTheTrainingScale(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.h3dir = base / "h3loras"
        self.h3dir.mkdir()
        self.payload = _uniform_alpha_lora()
        self._patches = [
            mock.patch.object(P, "_safe_h3_loras_dir", lambda: self.h3dir),
            mock.patch.object(P, "STATE_DIR", base / "state"),
            mock.patch.object(P, "push", lambda *a, **k: None),
            mock.patch.object(P, "_active_hf_token", lambda: None),
            mock.patch.object(P, "_active_civitai_key", lambda: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def _upload_strength(self) -> float:
        return P.import_h3_lora_file("uploaded.safetensors", self.payload)["recommended_strength"]

    def _hf(self, meta: dict) -> dict:
        import huggingface_hub

        def fake_dl(repo_id, filename, token=None, local_dir=None):
            d = Path(local_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / filename).write_bytes(self.payload)
            return str(d / filename)
        with mock.patch.object(huggingface_hub, "hf_hub_download", fake_dl):
            r = P._hf_lora_download("someone/h3-uniform", "uniform.safetensors",
                                    dict(meta, lane="h3", base_model="MiniMax H3"))
        return json.loads(Path(r["sidecar_path"]).read_text())

    def _civitai(self, meta: dict) -> dict:
        payload = self.payload

        class Resp(io.BytesIO):
            headers = {"Content-Length": str(len(payload))}

        opener = mock.Mock()
        opener.open = lambda req, timeout=60: Resp(payload)
        with mock.patch.object(P, "_civitai_opener", lambda: opener):
            r = P._civitai_download("https://civitai.com/api/download/models/1",
                                    dict(meta, base_model="MiniMax H3",
                                         filename="civ_uniform.safetensors"))
        return json.loads(Path(r["sidecar_path"]).read_text())

    def test_all_three_install_paths_recommend_the_same_scale(self):
        with mock.patch.object(P, "_validate_h3_lora_payload", lambda *a, **k: None):
            upload = self._upload_strength()
        self.assertAlmostEqual(upload, ALPHA / RANK)
        self.assertAlmostEqual(self._hf({})["recommended_strength"], upload)
        self.assertAlmostEqual(self._civitai({})["recommended_strength"], upload)

    def test_a_listing_strength_multiplies_the_training_scale(self):
        self.assertAlmostEqual(self._hf({"recommended_strength": 0.8})["recommended_strength"],
                               0.8 * ALPHA / RANK)

    def test_an_unreadable_file_keeps_the_listing_number(self):
        self.payload = b"0" * 4096
        self.assertEqual(self._hf({"recommended_strength": 0.7})["recommended_strength"], 0.7)


if __name__ == "__main__":
    unittest.main()
