#!/usr/bin/env python3
"""Tests for scripts/quantize_ltx.py — the bf16 -> q4/q8 weight pipeline.

No downloads, no GPU, no 40 GB weights. Everything here builds a tiny
*synthetic* LTX DiT with the REAL vendored ``LTXModel``, dumps it as a bf16
source in the real MLX pack layout, quantizes it with the real script, and
then loads it back through the REAL vendored loader functions.

Run:  ltx-2-mlx/env/bin/python scripts/test_quantize_ltx.py
 or:  ltx-2-mlx/env/bin/python -m unittest scripts.test_quantize_ltx
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import mlx.core as mx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import quantize_ltx as qz  # noqa: E402

qz.add_vendored_to_path()

from ltx_core_mlx.model.transformer.model import LTXModel, LTXModelConfig  # noqa: E402

# A DiT small enough to build in a second, big enough to exercise every rule:
# 2 blocks, dims that are multiples of the 64-wide quant group, and the same
# module tree the 22B model has (34 quantizable linears per block).
TINY = LTXModelConfig(
    num_layers=2,
    video_dim=256,
    audio_dim=128,
    video_num_heads=2,
    audio_num_heads=2,
    video_head_dim=128,
    audio_head_dim=64,
    av_cross_num_heads=2,
    av_cross_head_dim=64,
    video_patch_channels=128,
    audio_patch_channels=128,
    ff_mult=2.0,
)


def flatten(params, prefix=""):
    """dict/list tree of mx.arrays -> flat {dotted.key: array}."""
    out = {}
    if isinstance(params, dict):
        items = params.items()
    elif isinstance(params, (list, tuple)):
        items = ((str(i), v) for i, v in enumerate(params))
    else:
        return {prefix: params}
    for k, v in items:
        out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    return out


def write_synthetic_bf16_dit(path: Path) -> dict[str, mx.array]:
    """Build a tiny LTXModel and save its params as a bf16 ``transformer.``-prefixed pack."""
    mx.random.seed(20260811)
    model = LTXModel(TINY)
    flat = flatten(model.parameters())
    tensors = {}
    for k, v in flat.items():
        # Randomize so the dequant probe sees a real distribution, not zeros
        # (a table of zeros would pass any error check).
        arr = mx.random.normal(v.shape) * 0.02 if v.ndim >= 1 else v
        # Mirror the dtypes of the SHIPPED pack: everything bf16 except the
        # scale/shift tables, which are F32 there.
        dtype = mx.float32 if "scale_shift_table" in k else mx.bfloat16
        tensors[f"transformer.{k}"] = arr.astype(dtype)
    mx.eval(list(tensors.values()))
    path.parent.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(path), tensors)
    return tensors


class RecipeExtraction(unittest.TestCase):
    """The recipe must match what the shipped 2.3 packs actually contain."""

    def test_ltx_recipe_matches_shipped_pack_shape(self):
        r = qz.RECIPES["ltx-dit"]
        self.assertEqual(r.group_size, 64)
        self.assertEqual(r.scales_dtype, "BF16")
        self.assertEqual(r.include_prefixes, ("transformer.transformer_blocks.",))
        self.assertEqual(r.loader_prefix, "transformer.")
        self.assertTrue(r.quant_config_extra["only_transformer_blocks"])

    def test_quantize_config_json_shape(self):
        with tempfile.TemporaryDirectory() as d:
            qz.write_quantize_config(Path(d), qz.RECIPES["ltx-dit"], 4)
            blob = json.loads((Path(d) / "quantize_config.json").read_text())
        # Byte-for-byte the same keys as mlx_models/ltx-2.3-mlx-q4/quantize_config.json
        self.assertEqual(blob, {"quantization": {"bits": 4, "group_size": 64, "only_transformer_blocks": True}})


class Planner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "transformer-distilled.safetensors"
        write_synthetic_bf16_dit(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_block_linears_are_quantized(self):
        plan = qz.build_plan(self.src, qz.RECIPES["ltx-dit"], 4)
        # 34 quantizable linears per block x 2 blocks (same count/block as the
        # shipped 48-block pack: 1632 / 48 == 34).
        self.assertEqual(len(plan.quantized_modules), 68)
        for m in plan.quantized_modules:
            self.assertTrue(m.startswith("transformer.transformer_blocks."))
        # Nothing outside the blocks was touched
        outside = [t for t in plan.outputs if ".transformer_blocks." not in t.key]
        self.assertTrue(outside)
        self.assertTrue(all(t.kind == "copy" for t in outside))
        # AdaLN + patch/out projections specifically stay bf16
        by_key = {t.key: t for t in plan.outputs}
        for k in (
            "transformer.patchify_proj.weight",
            "transformer.proj_out.weight",
            "transformer.adaln_single.linear.weight",
            "transformer.prompt_adaln_single.linear.weight",
        ):
            self.assertEqual(by_key[k].kind, "copy", k)
            self.assertEqual(by_key[k].dtype, "BF16", k)

    def test_closed_form_shapes(self):
        for bits in (4, 8):
            plan = qz.build_plan(self.src, qz.RECIPES["ltx-dit"], bits)
            src_index, _, _ = qz.read_header(self.src)
            by_key = {t.key: t for t in plan.outputs}
            for base in plan.quantized_modules:
                out_f, in_f = src_index[base + ".weight"]["shape"]
                self.assertEqual(by_key[base + ".weight"].shape, [out_f, in_f * bits // 32])
                self.assertEqual(by_key[base + ".weight"].dtype, "U32")
                self.assertEqual(by_key[base + ".scales"].shape, [out_f, in_f // 64])
                self.assertEqual(by_key[base + ".scales"].dtype, "BF16")
                self.assertEqual(by_key[base + ".biases"].shape, [out_f, in_f // 64])

    def test_bias_survives_next_to_biases(self):
        plan = qz.build_plan(self.src, qz.RECIPES["ltx-dit"], 4)
        keys = {t.key for t in plan.outputs}
        b = "transformer.transformer_blocks.0.attn1.to_q"
        self.assertIn(f"{b}.bias", keys)  # the Linear's real bias, bf16
        self.assertIn(f"{b}.biases", keys)  # the quantization zero-points
        by_key = {t.key: t for t in plan.outputs}
        self.assertEqual(by_key[f"{b}.bias"].kind, "copy")
        self.assertEqual(by_key[f"{b}.biases"].kind, "q_biases")


class RoundTrip(unittest.TestCase):
    """(c) — write a synthetic pack, then point the VENDORED loader at it."""

    def _build(self, bits: int, out: Path) -> Path:
        src = out / "src" / "transformer-distilled.safetensors"
        write_synthetic_bf16_dit(src)
        pack = out / f"pack-q{bits}"
        qz.main(
            [
                "--src", str(src),
                "--out-dir", str(pack),
                "--recipe", "ltx-dit",
                "--bits", str(bits),
                "--probes", "6",
            ]
        )
        return pack

    def test_vendored_loader_accepts_our_pack(self):
        for bits in (4, 8):
            with self.subTest(bits=bits), tempfile.TemporaryDirectory() as d:
                pack = self._build(bits, Path(d))
                f = pack / "transformer-distilled.safetensors"
                self.assertTrue(f.exists())
                self.assertFalse(list(pack.glob("*.partial*")), "partial left behind")

                info = qz.verify_load_ltx_dit(f, config=TINY)
                self.assertTrue(info["loaded"])
                # The loader auto-detects bit width from tensor shapes alone —
                # if our closed-form packing were wrong it would guess wrong.
                self.assertEqual(info["loader_detected_bits"], bits)

    def test_quantized_layers_actually_compute(self):
        """A pack that loads but produces garbage is worse than one that fails."""
        with tempfile.TemporaryDirectory() as d:
            pack = self._build(4, Path(d))
            qz.add_vendored_to_path()
            from ltx_core_mlx.utils.weights import apply_quantization, load_split_safetensors

            f = pack / "transformer-distilled.safetensors"
            weights = load_split_safetensors(f, prefix="transformer.")
            dit = LTXModel(TINY)
            apply_quantization(dit, weights)
            dit.load_weights(list(weights.items()))
            mx.eval(dit.parameters())

            layer = dit.transformer_blocks[0].attn1.to_q
            self.assertEqual(type(layer).__name__, "QuantizedLinear")
            src = Path(d) / "src" / "transformer-distilled.safetensors"
            ref_w = mx.load(str(src))["transformer.transformer_blocks.0.attn1.to_q.weight"].astype(mx.float32)
            ref_b = mx.load(str(src))["transformer.transformer_blocks.0.attn1.to_q.bias"].astype(mx.float32)
            x = mx.random.normal((3, ref_w.shape[1])).astype(mx.bfloat16)
            got = layer(x).astype(mx.float32)
            want = x.astype(mx.float32) @ ref_w.T + ref_b
            rel = float(mx.mean(mx.abs(got - want))) / float(mx.mean(mx.abs(want)))
            self.assertLess(rel, 0.15, f"q4 forward diverged from bf16 reference: rel={rel}")

    def test_manifest_and_configs(self):
        with tempfile.TemporaryDirectory() as d:
            pack = self._build(8, Path(d))
            man = json.loads((pack / qz.MANIFEST_NAME).read_text())
            entry = man["files"]["transformer-distilled.safetensors"]
            self.assertEqual(len(entry["sha256"]), 64)
            self.assertEqual(
                entry["sha256"], qz.sha256_file(pack / "transformer-distilled.safetensors"), "manifest sha is wrong"
            )
            self.assertEqual(entry["bits"], 8)
            self.assertEqual(entry["modules_quantized"], 68)
            cfg = json.loads((pack / "quantize_config.json").read_text())
            self.assertEqual(cfg["quantization"]["bits"], 8)


class FromPack(unittest.TestCase):
    """The whole-pack driver — the exact command shape used when 2.5 bf16 lands."""

    def _bf16_pack(self, root: Path) -> Path:
        """A miniature bf16 MLX-layout LTX pack: two transformer variants + the
        components the shipped packs never quantize."""
        src = root / "bf16"
        src.mkdir(parents=True)
        for variant in ("distilled", "dev"):
            write_synthetic_bf16_dit(src / f"transformer-{variant}.safetensors")
        mx.save_safetensors(
            str(src / "connector.safetensors"), {"connector.x.weight": mx.zeros((8, 128)).astype(mx.bfloat16)}
        )
        mx.save_safetensors(
            str(src / "vae_decoder.safetensors"), {"vae_decoder.y.weight": mx.zeros((8, 128)).astype(mx.bfloat16)}
        )
        (src / "config.json").write_text(json.dumps({"model_version": "2.5.0"}))
        (src / "split_model.json").write_text(
            json.dumps({"format": "split", "model_version": "2.5.0", "components": ["connector", "vae_decoder"]})
        )
        return src

    def test_builds_a_full_pack(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = self._bf16_pack(root)
            out = root / "q4"
            qz.main(
                [
                    "--from-pack", str(src),
                    "--out-dir", str(out),
                    "--recipe", "ltx-dit",
                    "--bits", "4",
                    "--variants", "distilled",
                ]
            )
            names = {p.name for p in out.iterdir()}
            # only the requested variant was quantized...
            self.assertIn("transformer-distilled.safetensors", names)
            self.assertNotIn("transformer-dev.safetensors", names)
            # ...never-quantized components came across verbatim...
            for n in ("connector.safetensors", "vae_decoder.safetensors", "config.json"):
                self.assertIn(n, names)
            self.assertEqual(
                qz.sha256_file(out / "connector.safetensors"),
                qz.sha256_file(src / "connector.safetensors"),
                "a never-quantized component was modified",
            )
            # ...and the pack-level JSONs match the shipped 2.3 shape.
            self.assertEqual(
                json.loads((out / "quantize_config.json").read_text()),
                {"quantization": {"bits": 4, "group_size": 64, "only_transformer_blocks": True}},
            )
            split = json.loads((out / "split_model.json").read_text())
            self.assertTrue(split["quantized"])
            self.assertEqual(split["quantization_bits"], 4)

            man = json.loads((out / qz.MANIFEST_NAME).read_text())
            for n in ("transformer-distilled.safetensors", "connector.safetensors", "quantize_config.json"):
                self.assertIn(n, man["files"], n)
                self.assertEqual(man["files"][n]["sha256"], qz.sha256_file(out / n), n)

            # the quantized transformer still loads through the vendored loader
            info = qz.verify_load_ltx_dit(out / "transformer-distilled.safetensors", config=TINY)
            self.assertEqual(info["loader_detected_bits"], 4)


class Determinism(unittest.TestCase):
    """(b) — the same input must produce byte-identical output, every run."""

    def test_two_runs_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "transformer-distilled.safetensors"
            write_synthetic_bf16_dit(src)
            shas = []
            for run in ("a", "b"):
                out = root / run
                qz.main(["--src", str(src), "--out-dir", str(out), "--recipe", "ltx-dit", "--bits", "4"])
                shas.append(qz.sha256_file(out / "transformer-distilled.safetensors"))
            self.assertEqual(shas[0], shas[1], "quantization is not reproducible")


class Robustness(unittest.TestCase):
    def test_truncated_output_is_never_published(self):
        """A plan/write mismatch must leave no final file."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "transformer-distilled.safetensors"
            write_synthetic_bf16_dit(src)
            plan = qz.build_plan(src, qz.RECIPES["ltx-dit"], 4)
            plan.outputs[-1].nbytes += 4096  # lie about the size
            out = root / "bad.safetensors"
            with self.assertRaises(SystemExit):
                qz.write_quantized(plan, out, qz.RECIPES["ltx-dit"], 4, stamp_metadata=False, verbose=False)
            self.assertFalse(out.exists())
            self.assertFalse(qz._partial_path(out).exists())

    def test_disk_preflight_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                qz.preflight_disk(Path(d), 10**18)

    def test_unaligned_weight_is_reported_not_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "t.safetensors"
            mx.save_safetensors(
                str(src),
                {
                    "transformer.transformer_blocks.0.odd.weight": mx.zeros((8, 100)).astype(mx.bfloat16),
                    "transformer.transformer_blocks.0.ok.weight": mx.zeros((8, 128)).astype(mx.bfloat16),
                },
            )
            plan = qz.build_plan(src, qz.RECIPES["ltx-dit"], 4)
            self.assertEqual(len(plan.skipped_unaligned), 1)
            self.assertEqual(len(plan.quantized_modules), 1)
            keys = {t.key: t for t in plan.outputs}
            # unaligned one is still present, at bf16 — never silently dropped
            self.assertEqual(keys["transformer.transformer_blocks.0.odd.weight"].dtype, "BF16")

    def test_header_is_deterministic_and_metadata_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "t.safetensors"
            mx.save_safetensors(
                str(src),
                {"transformer.transformer_blocks.0.ok.weight": mx.zeros((8, 128)).astype(mx.bfloat16)},
                metadata={"config": '{"model_version":"2.5.0"}'},
            )
            plan = qz.build_plan(src, qz.RECIPES["ltx-dit"], 8)
            out = Path(d) / "o.safetensors"
            qz.write_quantized(plan, out, qz.RECIPES["ltx-dit"], 8, stamp_metadata=True, verbose=False)
            _, meta, _ = qz.read_header(out)
            # the 2.5 metadata-driven config path depends on this surviving
            self.assertEqual(json.loads(meta["config"])["model_version"], "2.5.0")
            stamp = json.loads(meta["phosphene_quant"])
            self.assertEqual(stamp["bits"], 8)
            self.assertEqual(stamp["group_size"], 64)
            # no wall-clock anywhere in the safetensors metadata (would break determinism)
            self.assertNotIn("utc", json.dumps(meta).lower())

    def test_writes_are_atomic(self):
        """The in-flight name keeps a loadable extension and is never the final name."""
        p = qz._partial_path(Path("/x/transformer-distilled.safetensors"))
        self.assertEqual(p.name, "transformer-distilled.partial.safetensors")
        self.assertEqual(p.suffix, ".safetensors")


class SafetensorsFormat(unittest.TestCase):
    def test_our_writer_matches_mlx_reader(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "t.safetensors"
            ref = {
                "transformer.transformer_blocks.0.ok.weight": mx.random.normal((8, 128)).astype(mx.bfloat16),
                "transformer.scale_shift_table": mx.random.normal((2, 8)).astype(mx.float32),
            }
            mx.eval(list(ref.values()))
            mx.save_safetensors(str(src), ref)
            plan = qz.build_plan(src, qz.RECIPES["ltx-dit"], 8)
            out = Path(d) / "o.safetensors"
            qz.write_quantized(plan, out, qz.RECIPES["ltx-dit"], 8, stamp_metadata=False, verbose=False)
            got = mx.load(str(out))
            # F32 tables pass through untouched, bit for bit
            self.assertTrue(bool(mx.all(got["transformer.scale_shift_table"] == ref["transformer.scale_shift_table"])))
            self.assertEqual(got["transformer.scale_shift_table"].dtype, mx.float32)
            # offsets are contiguous and cover the file exactly
            index, _, start = qz.read_header(out)
            spans = sorted(v["data_offsets"] for v in index.values())
            self.assertEqual(spans[0][0], 0)
            for a, b in zip(spans, spans[1:]):
                self.assertEqual(a[1], b[0], "non-contiguous offsets")
            self.assertEqual(start + spans[-1][1], out.stat().st_size)
            with open(out, "rb") as f:
                self.assertEqual(struct.unpack("<Q", f.read(8))[0], start - 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
