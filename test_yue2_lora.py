"""YuE2 LoRA inference: the rules an adapter has to obey before it runs.

CPU only, tiny fake models, no YuE2 weights. What is pinned here:

  * one canonical spelling for the half-dozen key layouts an adapter arrives in;
  * a delta is refused unless its shapes match the model that will run it;
  * the wrapped projection computes exactly `W x + strength · B (A x)` — checked
    against the arithmetic written out by hand, on a plain AND a quantised base;
  * wrapping does not rename one parameter, because `lyra.nar.load_nar()`
    validates the shared AR model by exact tensor name;
  * `joint` never touches the decoder's I/O projections, `separate` replaces
    them with a strength-weighted blend;
  * the instrumental recipe is Maestro's: AR adapter at 1.0, `cot=full`,
    `[instrumental]` in place of sung prose;
  * the panel's form → argv round trip carries the picks and refuses paths
    from outside the LoRA folder.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlencode

import mlx.core as mx
import mlx.nn as nn
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts" / "music"))
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="yue2-lora-state-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"

import yue2_lora as L                                            # noqa: E402
from scripts.pinokio import music_lora_fetch as F                # noqa: E402

HIDDEN, KV, INTERMEDIATE, LAYERS, RANK = 32, 16, 64, 2, 4
LATENT = 8


class _Attention(nn.Module):
    def __init__(self):
        super().__init__()
        self.q_proj = nn.Linear(HIDDEN, HIDDEN, bias=False)
        self.k_proj = nn.Linear(HIDDEN, KV, bias=False)
        self.v_proj = nn.Linear(HIDDEN, KV, bias=False)
        self.o_proj = nn.Linear(HIDDEN, HIDDEN, bias=False)


class _MLP(nn.Module):
    def __init__(self):
        super().__init__()
        self.gate_proj = nn.Linear(HIDDEN, INTERMEDIATE, bias=False)
        self.up_proj = nn.Linear(HIDDEN, INTERMEDIATE, bias=False)
        self.down_proj = nn.Linear(INTERMEDIATE, HIDDEN, bias=False)


class _Layer(nn.Module):
    def __init__(self, branch):
        super().__init__()
        attn, mlp = L.BRANCH_GROUPS[branch]
        setattr(self, attn, _Attention())
        setattr(self, mlp, _MLP())


class _Backbone(nn.Module):
    def __init__(self, branch):
        super().__init__()
        self.layers = [_Layer(branch) for _ in range(LAYERS)]


class _Model(nn.Module):
    """Small stand-in with lyra's attribute names (`model.layers[i]....`)."""

    def __init__(self, branch="ar", *, io=False):
        super().__init__()
        self.model = _Backbone(branch)
        if io:
            self.llm2vae = nn.Linear(HIDDEN, LATENT)
            self.vae2llm = nn.Linear(LATENT, HIDDEN)
        self.set_dtype(mx.bfloat16)


def _pair(out_features, in_features, rank=RANK, seed=0):
    rng = np.random.default_rng(seed)
    a = mx.array(rng.standard_normal((rank, in_features)) * 0.05, dtype=mx.bfloat16)
    b = mx.array(rng.standard_normal((out_features, rank)) * 0.05, dtype=mx.bfloat16)
    return a, b


def _write(path, tensors):
    mx.save_safetensors(str(path), tensors)
    return Path(str(path) + ("" if str(path).endswith(".safetensors") else ".safetensors"))


def _full_file(path, branch, *, rank=RANK, io=False, spelling="lora_A", prefix=""):
    attn, mlp = L.BRANCH_GROUPS[branch]
    tensors = {}
    widths = {"q_proj": (HIDDEN, HIDDEN), "k_proj": (KV, HIDDEN), "v_proj": (KV, HIDDEN),
              "o_proj": (HIDDEN, HIDDEN)}
    mlp_widths = {"gate_proj": (INTERMEDIATE, HIDDEN), "up_proj": (INTERMEDIATE, HIDDEN),
                  "down_proj": (HIDDEN, INTERMEDIATE)}
    lo, hi = ("lora_A", "lora_B") if spelling == "lora_A" else ("A", "B")
    for index in range(LAYERS):
        for group, table in ((attn, widths), (mlp, mlp_widths)):
            for name, (out_features, in_features) in table.items():
                a, b = _pair(out_features, in_features, rank, seed=index * 17 + len(tensors))
                stem = f"{prefix}layers.{index}.{group}.{name}"
                tensors[f"{stem}.{lo}"] = a
                tensors[f"{stem}.{hi}"] = b
    if io:
        tensors["vae2llm.weight"] = mx.zeros((HIDDEN, LATENT), dtype=mx.bfloat16) + 0.25
        tensors["vae2llm.bias"] = mx.zeros((HIDDEN,), dtype=mx.bfloat16) + 0.5
        tensors["llm2vae.weight"] = mx.zeros((LATENT, HIDDEN), dtype=mx.bfloat16) + 0.125
        tensors["llm2vae.bias"] = mx.zeros((LATENT,), dtype=mx.bfloat16) + 0.75
    mx.save_safetensors(str(path), tensors)
    return Path(path)


class KeyMapping(unittest.TestCase):

    def test_every_published_spelling_lands_on_one_name(self):
        for spelling in (
            "layers.3.mlp.up_proj.lora_A",
            "model.layers.3.mlp.up_proj.lora_A",
            "base_model.model.model.layers.3.mlp.up_proj.lora_A.default.weight",
            "base_model.model.layers.3.mlp.up_proj.lora_A.weight",
            "transformer.layers.3.mlp.up_proj.lora_down.weight",
            "model.layers.3.mlp.up_proj.A",
        ):
            self.assertEqual(L.normalize_key(spelling), "layers.3.mlp.up_proj.lora_A",
                             f"{spelling} mapped wrong")
        self.assertEqual(L.normalize_key("layers.0.nar_mlp.down_proj.lora_up.weight"),
                         "layers.0.nar_mlp.down_proj.lora_B")

    def test_the_branch_is_read_off_the_keys(self):
        self.assertEqual(L.branch_of(["layers.0.mlp.up_proj.lora_A"]), "ar")
        self.assertEqual(L.branch_of(["layers.0.nar_self_attn.q_proj.lora_A"]), "nar")

    def test_a_strength_outside_the_dial_is_refused(self):
        for bad in (-0.1, 1.6, "loud", None):
            with self.assertRaises(ValueError):
                L.validate_strength(bad)
        self.assertEqual(L.validate_strength("0.75"), 0.75)

    def test_a_spec_splits_only_on_a_real_number(self):
        self.assertEqual(L.parse_spec("/x/y.safetensors:0.4"), ("/x/y.safetensors", 0.4))
        self.assertEqual(L.parse_spec("/x/a:b.safetensors"), ("/x/a:b.safetensors", 1.0))


class Reading(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_a_full_ar_file_reads_with_one_rank_and_every_target(self):
        path = _full_file(self.dir / "ar.safetensors", "ar")
        adapter = L.read_adapter(path, strength=0.5)
        self.assertEqual(adapter.branch, "ar")
        self.assertEqual(adapter.rank, RANK)
        self.assertEqual(len(adapter.deltas), LAYERS * 7)
        self.assertEqual(adapter.strength, 0.5)
        self.assertEqual(adapter.summary()["file"], "ar.safetensors")

    def test_maestros_own_spelling_and_a_model_prefix_read_the_same(self):
        plain = L.read_adapter(_full_file(self.dir / "a.safetensors", "ar"))
        other = L.read_adapter(_full_file(self.dir / "b.safetensors", "ar",
                                          spelling="A", prefix="model."))
        self.assertEqual(sorted(plain.deltas), sorted(other.deltas))

    def test_a_half_pair_and_a_mixed_rank_are_refused(self):
        tensors = {"layers.0.mlp.up_proj.lora_A": _pair(INTERMEDIATE, HIDDEN)[0]}
        mx.save_safetensors(str(self.dir / "half.safetensors"), tensors)
        with self.assertRaisesRegex(ValueError, "missing its lora_B"):
            L.read_adapter(self.dir / "half.safetensors")
        a1, b1 = _pair(INTERMEDIATE, HIDDEN, rank=4)
        a2, b2 = _pair(INTERMEDIATE, HIDDEN, rank=8)
        mx.save_safetensors(str(self.dir / "ranks.safetensors"), {
            "layers.0.mlp.up_proj.lora_A": a1, "layers.0.mlp.up_proj.lora_B": b1,
            "layers.0.mlp.gate_proj.lora_A": a2, "layers.0.mlp.gate_proj.lora_B": b2})
        with self.assertRaisesRegex(ValueError, "one rank per adapter"):
            L.read_adapter(self.dir / "ranks.safetensors")

    def test_a_tensor_we_do_not_understand_stops_the_song(self):
        a, b = _pair(INTERMEDIATE, HIDDEN)
        mx.save_safetensors(str(self.dir / "odd.safetensors"), {
            "layers.0.mlp.up_proj.lora_A": a, "layers.0.mlp.up_proj.lora_B": b,
            "some.other.tensor": mx.zeros((2, 2))})
        with self.assertRaisesRegex(ValueError, "unsupported tensor targets"):
            L.read_adapter(self.dir / "odd.safetensors")

    def test_a_peft_alpha_is_folded_into_b(self):
        a, b = _pair(INTERMEDIATE, HIDDEN, rank=4, seed=3)
        base = {"layers.0.mlp.up_proj.lora_A": a, "layers.0.mlp.up_proj.lora_B": b}
        mx.save_safetensors(str(self.dir / "plain.safetensors"), base)
        mx.save_safetensors(str(self.dir / "alpha.safetensors"),
                            {**base, "layers.0.mlp.up_proj.alpha": mx.array([8.0])})
        without = L.read_adapter(self.dir / "plain.safetensors").deltas["layers.0.mlp.up_proj"][1]
        with_alpha = L.read_adapter(self.dir / "alpha.safetensors").deltas["layers.0.mlp.up_proj"][1]
        self.assertLess(float(mx.abs(with_alpha.astype(mx.float32)
                                     - without.astype(mx.float32) * 2.0).max()), 0.02)

    def test_a_decoder_companion_has_to_be_complete_and_acoustic(self):
        path = _full_file(self.dir / "nar.safetensors", "nar", io=True)
        adapter = L.read_adapter(path, mode="separate")
        self.assertEqual(adapter.branch, "nar")
        self.assertEqual(len(adapter.io), 4)
        joint = L.read_adapter(path, mode="joint")
        self.assertEqual(joint.io, {})
        self.assertTrue(joint.ignored_io)


class WrapperMath(unittest.TestCase):

    def _manual(self, weight, x, stack):
        out = x.astype(mx.float32) @ weight.astype(mx.float32).T
        for a, b, scale in stack:
            delta = (x.astype(mx.bfloat16) @ a.T) @ b.T
            out = out + delta.astype(mx.float32) * scale
        return out

    def test_a_wrapped_projection_is_w_x_plus_scale_b_a_x(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _full_file(Path(tmp.name) / "ar.safetensors", "ar")
        model = _Model("ar")
        adapter = L.read_adapter(path, strength=0.7)
        weight = model.model.layers[0].mlp.up_proj.weight
        L.apply_adapters(model, "ar", [adapter])
        rng = np.random.default_rng(11)
        x = mx.array(rng.standard_normal((1, 3, HIDDEN)), dtype=mx.bfloat16)
        got = model.model.layers[0].mlp.up_proj(x)
        a, b = adapter.deltas["layers.0.mlp.up_proj"]
        want = self._manual(weight, x, [(a, b, 0.7)])
        self.assertLess(float(mx.abs(got.astype(mx.float32) - want).max()), 0.05)

    def test_two_adapters_add_their_deltas(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        first = L.read_adapter(_full_file(Path(tmp.name) / "1.safetensors", "ar"), strength=0.3)
        second = L.read_adapter(_full_file(Path(tmp.name) / "2.safetensors", "ar"), strength=0.9)
        model = _Model("ar")
        weight = model.model.layers[1].self_attn.k_proj.weight
        L.apply_adapters(model, "ar", [first, second])
        rng = np.random.default_rng(5)
        x = mx.array(rng.standard_normal((1, 2, HIDDEN)), dtype=mx.bfloat16)
        got = model.model.layers[1].self_attn.k_proj(x)
        stack = [(*first.deltas["layers.1.self_attn.k_proj"], 0.3),
                 (*second.deltas["layers.1.self_attn.k_proj"], 0.9)]
        self.assertLess(float(mx.abs(got.astype(mx.float32) - self._manual(weight, x, stack)).max()), 0.05)

    def test_it_works_on_a_quantised_base_too(self):
        """The AR model runs 8-bit by default — a merge would not be possible."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        adapter = L.read_adapter(_full_file(Path(tmp.name) / "q.safetensors", "ar"), strength=1.0)
        model = _Model("ar")
        nn.quantize(model, group_size=32, bits=8, mode="affine",
                    class_predicate=lambda _p, m: isinstance(m, nn.Linear))
        module = model.model.layers[0].mlp.up_proj
        self.assertIsInstance(module, nn.QuantizedLinear)
        self.assertEqual(L.linear_shape(module), (INTERMEDIATE, HIDDEN))
        rng = np.random.default_rng(7)
        x = mx.array(rng.standard_normal((1, 2, HIDDEN)), dtype=mx.bfloat16)
        before = module(x)
        L.apply_adapters(model, "ar", [adapter])
        after = model.model.layers[0].mlp.up_proj(x)
        a, b = adapter.deltas["layers.0.mlp.up_proj"]
        delta = ((x @ a.T) @ b.T).astype(mx.float32)
        self.assertLess(float(mx.abs((after - before).astype(mx.float32) - delta).max()), 0.05)

    def test_applying_twice_replaces_the_stack_rather_than_doubling_it(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        adapter = L.read_adapter(_full_file(Path(tmp.name) / "a.safetensors", "ar"))
        model = _Model("ar")
        rng = np.random.default_rng(2)
        x = mx.array(rng.standard_normal((1, 2, HIDDEN)), dtype=mx.bfloat16)
        L.apply_adapters(model, "ar", [adapter])
        once = model.model.layers[0].mlp.up_proj(x)
        L.apply_adapters(model, "ar", [adapter])
        twice = model.model.layers[0].mlp.up_proj(x)
        self.assertLess(float(mx.abs((once - twice).astype(mx.float32)).max()), 1e-6)


class Attachment(unittest.TestCase):

    def test_wrapping_renames_nothing_and_unwraps_clean(self):
        """`lyra.nar.load_nar()` validates the AR model by exact tensor name."""
        from mlx.utils import tree_flatten
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        adapter = L.read_adapter(_full_file(Path(tmp.name) / "a.safetensors", "ar"))
        model = _Model("ar")
        before = sorted(k for k, _ in tree_flatten(model.parameters()))
        L.apply_adapters(model, "ar", [adapter])
        self.assertTrue(L.is_adapted(model))
        self.assertEqual(sorted(k for k, _ in tree_flatten(model.parameters())), before)
        self.assertEqual(L.remove_adapters(model), LAYERS * 7)
        self.assertFalse(L.is_adapted(model))
        self.assertEqual(sorted(k for k, _ in tree_flatten(model.parameters())), before)

    def test_a_shape_that_does_not_fit_the_model_is_named(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        a, b = _pair(INTERMEDIATE + 8, HIDDEN)
        mx.save_safetensors(str(Path(tmp.name) / "bad.safetensors"),
                            {"layers.0.mlp.up_proj.lora_A": a, "layers.0.mlp.up_proj.lora_B": b})
        adapter = L.read_adapter(Path(tmp.name) / "bad.safetensors")
        with self.assertRaisesRegex(ValueError, "layers.0.mlp.up_proj expects"):
            L.apply_adapters(_Model("ar"), "ar", [adapter])

    def test_joint_leaves_the_decoder_projections_alone(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _full_file(Path(tmp.name) / "nar.safetensors", "nar", io=True)
        model = _Model("nar", io=True)
        original = mx.array(model.vae2llm.weight)
        L.apply_adapters(model, "nar", [L.read_adapter(path, mode="joint")])
        self.assertLess(float(mx.abs((model.vae2llm.weight - original).astype(mx.float32)).max()), 1e-6)

    def test_separate_replaces_them_with_a_strength_weighted_blend(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        one = _full_file(Path(tmp.name) / "one.safetensors", "nar", io=True)
        model = _Model("nar", io=True)
        original = mx.array(model.vae2llm.weight)
        first = L.read_adapter(one, strength=0.25, mode="separate")
        second = L.read_adapter(one, strength=0.75, mode="separate")
        L.apply_adapters(model, "nar", [first, second])
        # Both companions carry the same 0.25 weight; a convex blend is 0.25,
        # and summing them (the bug this rule exists to prevent) would be 0.5.
        self.assertLess(abs(float(model.vae2llm.weight[0, 0]) - 0.25), 0.01)
        L.remove_adapters(model)
        self.assertLess(float(mx.abs((model.vae2llm.weight - original).astype(mx.float32)).max()), 1e-6)

    def test_the_runtime_adapts_every_model_the_pipeline_loads(self):
        """The acoustic model is built lazily, halfway through the song."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ar = L.read_adapter(_full_file(Path(tmp.name) / "ar.safetensors", "ar"))
        nar = L.read_adapter(_full_file(Path(tmp.name) / "nar.safetensors", "nar"))

        class FakePipeline:
            def __init__(self):
                self._ar = self._bf16_ar = self._nar = None

            def _load_model(self, for_nar=False):
                if for_nar:
                    self._bf16_ar = self._bf16_ar or _Model("ar")
                    self._nar = self._nar or _Model("nar")
                    return self._nar
                self._ar = self._ar or _Model("ar")
                return self._ar

        pipeline = FakePipeline()
        runtime = L.LoRARuntime([ar, nar]).attach(pipeline)
        pipeline._load_model()
        self.assertTrue(L.is_adapted(pipeline._ar))
        self.assertIsNone(pipeline._nar)
        pipeline._load_model(for_nar=True)
        self.assertTrue(L.is_adapted(pipeline._nar))
        self.assertTrue(L.is_adapted(pipeline._bf16_ar))
        runtime.detach()
        for model in (pipeline._ar, pipeline._bf16_ar, pipeline._nar):
            self.assertFalse(L.is_adapted(model))


class InstrumentalRecipe(unittest.TestCase):

    def test_sung_prose_never_reaches_the_instrumental_adapter(self):
        self.assertEqual(L.instrumental_lyrics("I was born in a small town"), "[instrumental]")
        self.assertEqual(L.instrumental_lyrics(""), "[instrumental]")
        self.assertEqual(L.instrumental_lyrics("[Verse]\nsomething sung"), "[instrumental]")

    def test_bare_and_timed_section_tags_pass_through_lowercased(self):
        self.assertEqual(L.instrumental_lyrics("[Intro]\n[Chorus]"), "[intro]\n[chorus]")
        self.assertEqual(L.instrumental_lyrics("[intro 0:00-0:15]\n[chorus 0:15-0:40]"),
                         "[intro 0:00-0:15]\n[chorus 0:15-0:40]")

    def test_overlapping_or_backwards_times_fall_back(self):
        self.assertEqual(L.instrumental_lyrics("[intro 0:10-0:05]"), "[instrumental]")
        self.assertEqual(L.instrumental_lyrics("[intro 0:00-0:20]\n[verse 0:10-0:30]"),
                         "[instrumental]")

    def test_the_recipe_is_the_adapter_at_one_full_planning_and_a_stock_decoder(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        empty = L.instrumental_recipe(root, "whatever")
        self.assertIsNone(empty["adapter"])
        self.assertIsNone(empty["lyrics"])       # fall back to the v4.16 skeleton
        (root / L.INSTRUMENTAL_ADAPTER).write_bytes(b"")
        recipe = L.instrumental_recipe(root, "a song about rain")
        self.assertEqual(recipe["adapter"], root / L.INSTRUMENTAL_ADAPTER)
        self.assertEqual(recipe["strength"], 1.0)
        self.assertEqual(recipe["cot"], "full")
        self.assertEqual(recipe["lyrics"], "[instrumental]")
        self.assertEqual(recipe["decoder"], "stock")
        self.assertTrue(recipe["pauses_other_loras"])

    def test_a_trigger_joins_the_style_once(self):
        class Stub:
            def __init__(self, trigger):
                self.trigger = trigger
        self.assertEqual(L.style_with_triggers("warm soul", [Stub("mtrsprr")]),
                         "mtrsprr, warm soul")
        self.assertEqual(L.style_with_triggers("MTRSPRR ballad", [Stub("mtrsprr")]),
                         "MTRSPRR ballad")
        self.assertEqual(L.style_with_triggers("", [Stub("a"), Stub("a"), Stub("b")]), "a, b")


class ThePack(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_the_pinned_pack_and_the_user_folder_both_list(self):
        pinned_stand_in(self.root / L.INSTRUMENTAL_ADAPTER,
                        F.SOURCES[L.INSTRUMENTAL_ADAPTER])
        pinned_stand_in(self.root / L.JOINT_NAR_ADAPTER,
                        F.SOURCES[L.JOINT_NAR_ADAPTER])
        (self.root / L.USER_SUBDIR).mkdir()
        (self.root / L.USER_SUBDIR / "my_voice.safetensors").write_bytes(b"xxx")
        listed = L.pack_adapters(self.root)
        self.assertEqual([a["id"] for a in listed],
                         [L.INSTRUMENTAL_ADAPTER, L.JOINT_NAR_ADAPTER,
                          f"{L.USER_SUBDIR}/my_voice.safetensors"])
        self.assertTrue(listed[0]["instrumental"])
        self.assertEqual(listed[1]["branch"], "nar")
        self.assertFalse(listed[2]["builtin"])

    def test_a_pick_from_outside_the_folder_is_refused(self):
        (self.root / L.INSTRUMENTAL_ADAPTER).write_bytes(b"x")
        self.assertEqual(L.resolve_in_pack(self.root, L.INSTRUMENTAL_ADAPTER).name,
                         L.INSTRUMENTAL_ADAPTER)
        with self.assertRaises(ValueError):
            L.resolve_in_pack(self.root, "../../etc/passwd")
        with self.assertRaises(FileNotFoundError):
            L.resolve_in_pack(self.root, "nothing.safetensors")


class _FormHandler:
    """Enough of the panel's handler for a route to answer into."""

    def __init__(self, form: dict):
        self._form = {k: [v] for k, v in form.items()}
        self.status, self.body = 200, None

    def _read_form_body(self):
        return urlencode(self._form, doseq=True), self._form

    def _json(self, payload, status=200):
        self.body, self.status = payload, status


def pinned_stand_in(path: Path, source: dict) -> None:
    """A file of the PINNED size, for a test that needs a pinned name to read
    as present. Readiness is size-checked now (Codex review, 2026-09-22), so a
    one-byte placeholder IS a truncated download. Sparse — instant, no disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(int(source["bytes"]))


class ThePackIsEitherWholeOrNot(unittest.TestCase):
    """A file with the pinned name is not the pinned file.

    Readiness accepted ANY non-empty adapter and the fetch endpoint returned
    early the moment both adapter names existed (Codex review, 2026-09-22), so
    a download that dropped halfway through left a truncated adapter that
    nothing would ever repair: the card said ready, Download did nothing, and
    the first song with that adapter failed to load it. The same early return
    stranded a pack whose tokenizer head never arrived.
    """

    BODY = b"the pinned bytes, all of them"
    DIGEST = hashlib.sha256(BODY).hexdigest()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "pack"
        self.root.mkdir()
        self.pins = self.enterContext(mock.patch.multiple(
            F,
            SOURCES={n: {**src, "bytes": len(self.BODY), "sha256": self.DIGEST}
                     for n, src in F.SOURCES.items()},
            HEAD_SOURCES={n: {**src, "bytes": len(self.BODY), "sha256": self.DIGEST}
                          for n, src in F.HEAD_SOURCES.items()}))

    def _write(self, path: Path, body=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.BODY if body is None else body)

    def _whole_adapters(self):
        for name in F.SOURCES:
            self._write(self.root / name)

    def _whole_head(self):
        for name, src in F.HEAD_SOURCES.items():
            if src["required"]:
                self._write(F.head_root(self.root) / name)

    def test_a_truncated_adapter_is_not_a_ready_pack(self):
        self._whole_adapters()
        self.assertEqual(F.lora_problems(self.root), [])
        self._write(self.root / L.INSTRUMENTAL_ADAPTER, b"x")
        self.assertEqual(F.lora_problems(self.root), [L.INSTRUMENTAL_ADAPTER])

    def test_a_truncated_adapter_is_not_offered_in_the_picker(self):
        self._whole_adapters()
        self._write(self.root / L.INSTRUMENTAL_ADAPTER, b"x")
        listed = [a["id"] for a in F.pack_adapters(self.root)]
        self.assertNotIn(L.INSTRUMENTAL_ADAPTER, listed)
        self.assertIn(L.JOINT_NAR_ADAPTER, listed)

    def test_a_users_own_adapter_is_never_size_checked(self):
        # Nothing is pinned about it; any non-empty file is theirs to try.
        (self.root / L.USER_SUBDIR).mkdir()
        self._write(self.root / L.USER_SUBDIR / "mine.safetensors", b"x")
        self.assertEqual([a["id"] for a in F.pack_adapters(self.root)],
                         [f"{L.USER_SUBDIR}/mine.safetensors"])

    def test_usable_adapters_and_a_whole_pack_are_two_questions(self):
        self._whole_adapters()
        self.assertEqual(F.lora_problems(self.root), [])          # pickable
        self.assertEqual(F.pack_problems(self.root),              # not whole
                         [f"{F.HEAD_SUBDIR}/{F.TOKENIZER_HEAD}"])
        self._whole_head()
        self.assertEqual(F.pack_problems(self.root), [])

    def test_a_deep_check_catches_bytes_the_size_cannot(self):
        self._write(self.root / L.INSTRUMENTAL_ADAPTER, b"x" * len(self.BODY))
        self._write(self.root / L.JOINT_NAR_ADAPTER)
        self.assertEqual(F.lora_problems(self.root), [])
        self.assertEqual(F.lora_problems(self.root, deep=True),
                         [L.INSTRUMENTAL_ADAPTER])

    # ---- the download itself ------------------------------------------
    def _fetch(self, staged: dict, **kw):
        """Run `fetch()` with a stand-in hub that serves `staged` bodies."""
        store = Path(self.tmp.name) / "hub"
        store.mkdir(exist_ok=True)
        served = []

        def hf_hub_download(repo_id, revision, filename, **_):
            served.append(filename)
            out = store / f"{len(served)}-{Path(filename).name}"
            body = staged.get(Path(filename).name)
            if body is None:
                raise RuntimeError("not in this fake repo")
            out.write_bytes(body)
            return str(out)

        with mock.patch.dict(sys.modules, {"huggingface_hub": types.SimpleNamespace(
                hf_hub_download=hf_hub_download)}):
            F.fetch(self.root, **kw)
        return served

    def test_an_interrupted_head_is_resumed_not_declared_done(self):
        self._whole_adapters()
        served = self._fetch({n: self.BODY for n in
                              {**F.SOURCES, **F.HEAD_SOURCES}})
        # The adapters were already whole, so only the head was fetched.
        self.assertNotIn(L.INSTRUMENTAL_ADAPTER, served)
        self.assertIn(F.TOKENIZER_HEAD, served)
        self.assertEqual(F.pack_problems(self.root), [])

    def test_a_truncated_adapter_is_fetched_again(self):
        self._whole_adapters()
        self._write(self.root / L.JOINT_NAR_ADAPTER, b"x")
        served = self._fetch({n: self.BODY for n in
                              {**F.SOURCES, **F.HEAD_SOURCES}})
        self.assertIn(L.JOINT_NAR_ADAPTER, served)
        self.assertNotIn(L.INSTRUMENTAL_ADAPTER, served)
        self.assertEqual(F.pack_problems(self.root), [])

    def test_a_file_that_fails_its_checksum_never_wears_the_pinned_name(self):
        bad = {n: b"corrupted-on-the-wire!!!!!!!!" for n in F.SOURCES}
        self.assertEqual(len(bad[L.INSTRUMENTAL_ADAPTER]), len(self.BODY))
        with self.assertRaises(SystemExit):
            self._fetch(bad)
        self.assertFalse((self.root / L.INSTRUMENTAL_ADAPTER).exists())
        self.assertEqual(list(self.root.glob("*.partial")), [])

    def test_a_good_file_survives_a_failed_re_download_beside_it(self):
        # Atomic: the verified adapter on disk is not destroyed by a second
        # file arriving corrupted.
        self._whole_adapters()
        self._write(self.root / L.JOINT_NAR_ADAPTER, b"x")
        with self.assertRaises(SystemExit):
            self._fetch({L.JOINT_NAR_ADAPTER: b"corrupted-on-the-wire!!!!!!!!"})
        self.assertEqual(
            (self.root / L.INSTRUMENTAL_ADAPTER).read_bytes(), self.BODY)
        self.assertEqual(list(self.root.glob("*.partial")), [])

    # ---- what the panel does with all that -----------------------------
    def test_the_panel_reports_usable_and_whole_separately(self):
        import mlx_ltx_panel as panel
        with mock.patch.object(panel, "MUSIC_LORAS", self.root):
            self._whole_adapters()
            status = panel.music_lora_status()
            self.assertTrue(status["ready"])
            self.assertFalse(status["complete"])
            self.assertTrue(status["incomplete"])
            self._whole_head()
            self.assertTrue(panel.music_lora_status()["complete"])

    def test_the_download_button_still_works_on_a_half_a_pack(self):
        import mlx_ltx_panel as panel
        self._whole_adapters()                    # head still missing
        started = []
        with mock.patch.object(panel, "MUSIC_LORAS", self.root), \
             mock.patch.object(panel.threading, "Thread",
                               side_effect=lambda **kw: started.append(kw)
                               or mock.MagicMock()):
            code, payload = panel.music_lora_fetch_start()
        self.assertEqual(code, 202, payload)
        self.assertTrue(started, "the endpoint returned ready and downloaded nothing")

    def test_a_whole_pack_asks_for_no_download(self):
        import mlx_ltx_panel as panel
        self._whole_adapters()
        self._whole_head()
        with mock.patch.object(panel, "MUSIC_LORAS", self.root):
            code, payload = panel.music_lora_fetch_start()
        self.assertEqual((code, payload), (200, {"ok": True, "ready": True}))


class ThePanelRoundTrip(unittest.TestCase):
    """The form the studio posts, through `music_params` and into argv."""

    @classmethod
    def setUpClass(cls):
        import mlx_ltx_panel
        cls.P = mlx_ltx_panel

    def _argv(self, form, tmp):
        job = {"id": "job-lora-1", "params": form}
        paths = {"python": Path("/bin/true"), "runner": Path("/dev/null"),
                 "generator": Path(tmp) / "gen", "vae": Path(tmp) / "vae"}
        return self.P.music_argv(job, paths, Path(tmp) / "song.wav")

    def test_picks_become_lora_flags_with_their_strengths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loras"
            (root / L.USER_SUBDIR).mkdir(parents=True)
            (root / L.JOINT_NAR_ADAPTER).write_bytes(b"x")
            (root / L.USER_SUBDIR / "mine.safetensors").write_bytes(b"x")
            self.P.MUSIC_LORAS = root
            argv = self._argv({"music_style": "dub", "music_loras":
                               f"{L.JOINT_NAR_ADAPTER}:0.8,{L.USER_SUBDIR}/mine.safetensors:1.2"}, tmp)
            specs = [argv[i + 1] for i, a in enumerate(argv) if a == "--lora"]
            real = root.resolve()
            self.assertEqual(specs, [f"{real / L.JOINT_NAR_ADAPTER}:0.8",
                                     f"{real / L.USER_SUBDIR}/mine.safetensors:1.2"])
            self.assertIn("--lora-dir", argv)

    def test_a_pick_that_is_not_in_the_folder_never_reaches_the_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loras"
            root.mkdir(parents=True)
            self.P.MUSIC_LORAS = root
            params = self.P.music_params({"music_loras": "../../../etc/passwd:1.0"})
            self.assertEqual(params["music_loras"], [])
            argv = self._argv({"music_style": "dub",
                               "music_loras": "../../../etc/passwd:1.0"}, tmp)
            self.assertNotIn("--lora", argv)

    def test_instrumental_still_sends_its_own_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loras"
            root.mkdir(parents=True)
            self.P.MUSIC_LORAS = root
            argv = self._argv({"music_style": "dub", "music_instrumental": "on"}, tmp)
            self.assertIn("--instrumental", argv)
            self.assertIn("--lora-dir", argv)

    def test_the_status_block_says_what_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loras"
            root.mkdir(parents=True)
            self.P.MUSIC_LORAS = root
            status = self.P.music_lora_status()
            self.assertFalse(status["ready"])
            self.assertTrue(status["missing"])
            self.assertEqual(status["adapters"], [])
            self.assertGreater(status["bytes"], 0)

    def test_the_runner_advertises_the_flags_the_panel_sends(self):
        runner = (ROOT / "scripts/music/yue2_run.py").read_text()
        for flag in ("--lora", "--lora-mode", "--lora-dir", "--lora-trigger", "--instrumental"):
            self.assertIn(f'"{flag}"', runner, f"{flag} is not a runner flag")

    def test_the_browser_sends_ONE_field_and_both_picks_survive_it(self):
        """The bug a plain string could never show (Codex review, 2026-09-22).

        The studio joins its picks into one comma-separated field, and
        `parse_qs` hands the panel `{"music_loras": ["a:0.8,b:1.2"]}` — a LIST
        with the whole selection inside its single element. Read as a list of
        specs, those two adapters became one filename nobody has, and every
        pick was silently dropped: choosing two voices turned them both off.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loras"
            (root / L.USER_SUBDIR).mkdir(parents=True)
            (root / L.JOINT_NAR_ADAPTER).write_bytes(b"x")
            (root / L.USER_SUBDIR / "mine.safetensors").write_bytes(b"x")
            self.P.MUSIC_LORAS = root
            field = f"{L.JOINT_NAR_ADAPTER}:0.8,{L.USER_SUBDIR}/mine.safetensors:1.2"
            form = parse_qs(urlencode({"music_style": "dub", "music_loras": field}))
            self.assertEqual(form["music_loras"], [field])   # one field, one list
            picks = self.P.music_params(form)["music_loras"]
            self.assertEqual([(p["id"], p["strength"]) for p in picks],
                             [(L.JOINT_NAR_ADAPTER, 0.8),
                              (f"{L.USER_SUBDIR}/mine.safetensors", 1.2)])
            argv = self._argv(form, tmp)
            real = root.resolve()
            self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "--lora"],
                             [f"{real / L.JOINT_NAR_ADAPTER}:0.8",
                              f"{real / L.USER_SUBDIR}/mine.safetensors:1.2"])

    def test_one_pick_through_parse_qs_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loras"
            root.mkdir(parents=True)
            (root / L.JOINT_NAR_ADAPTER).write_bytes(b"x")
            self.P.MUSIC_LORAS = root
            form = parse_qs(urlencode({"music_loras": f"{L.JOINT_NAR_ADAPTER}:0.5"}))
            picks = self.P.music_params(form)["music_loras"]
            self.assertEqual([(p["id"], p["strength"]) for p in picks],
                             [(L.JOINT_NAR_ADAPTER, 0.5)])

    def test_a_job_reparsed_from_its_own_params_keeps_its_picks(self):
        """`music_argv` runs `music_params` over `job["params"]`, where the
        picks are ALREADY a list of dicts. A normalised list and one HTTP
        field are two different shapes and both have to survive."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loras"
            root.mkdir(parents=True)
            (root / L.JOINT_NAR_ADAPTER).write_bytes(b"x")
            self.P.MUSIC_LORAS = root
            once = self.P.music_params(
                parse_qs(urlencode({"music_loras": f"{L.JOINT_NAR_ADAPTER}:0.8"})))
            twice = self.P.music_params(once)
            self.assertEqual(once["music_loras"], twice["music_loras"])


class TheFormFieldIsNotOneSpec(unittest.TestCase):
    """`parse_field` (what a browser posted) vs `parse_picks` (a normalised
    list). Keeping them apart is the whole fix: an HTTP field's list is one
    element PER REPEAT of the field, and each element carries a whole
    comma-separated selection."""

    def test_a_single_field_value_splits_on_the_commas(self):
        self.assertEqual(L.parse_field(["a.safetensors:0.8,b.safetensors:1.2"]),
                         [("a.safetensors", 0.8), ("b.safetensors", 1.2)])

    def test_a_repeated_field_is_every_value_together(self):
        self.assertEqual(L.parse_field(["a.safetensors:0.8", "b.safetensors:1.2"]),
                         [("a.safetensors", 0.8), ("b.safetensors", 1.2)])

    def test_a_bare_string_is_the_same_answer(self):
        self.assertEqual(L.parse_field("a.safetensors:0.8,b.safetensors:1.2"),
                         L.parse_picks("a.safetensors:0.8,b.safetensors:1.2"))

    def test_json_in_the_field_is_still_json(self):
        self.assertEqual(
            L.parse_field(['[{"id": "a.safetensors", "strength": 0.4}]']),
            [("a.safetensors", 0.4)])

    def test_a_normalised_list_is_taken_element_by_element(self):
        # This is what `parse_picks` is FOR, and why the field needs its own
        # door: here each element really is one spec.
        self.assertEqual(L.parse_picks(["a.safetensors:0.8", "b.safetensors"]),
                         [("a.safetensors", 0.8), ("b.safetensors", 1.0)])
        self.assertEqual(L.parse_field([{"id": "a.safetensors", "strength": 0.3}]),
                         [("a.safetensors", 0.3)])

    def test_nothing_at_all_is_no_picks(self):
        for raw in (None, "", [], ["", "  "], "   "):
            self.assertEqual(L.parse_field(raw), [])


class TheRunnerArgvRoundTrip(unittest.TestCase):
    """`--lora-trigger` from argv to the style prompt and the sidecar.

    The study measured the trigger as load-bearing (0.846 with, 0.772 without,
    same checkpoint and seed), so what is pinned here is the wiring that makes
    it reachable: the flag repeats, the Nth trigger belongs to the Nth
    `--lora`, a trigger with no adapter is dropped rather than misapplied, and
    the word reaches the style prompt exactly once in pick order.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts" / "music"))
        import yue2_run
        cls.R = yue2_run

    def _args(self, *extra):
        return self.R.parse_args(["--model-dir", "m", "--vae-dir", "v",
                                  "--output", "song.wav", *extra])

    def test_the_flag_repeats_and_keeps_its_order(self):
        args = self._args("--lora", "a.safetensors:0.8", "--lora-trigger", "frddtrn",
                          "--lora", "b.safetensors", "--lora-trigger", "mtrsprr")
        self.assertEqual(args.lora_trigger, ["frddtrn", "mtrsprr"])
        self.assertEqual(args.lora, ["a.safetensors:0.8", "b.safetensors"])
        self.assertEqual(self._args().lora_trigger, [])

    def test_the_nth_trigger_belongs_to_the_nth_adapter(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        first = _full_file(root / "a.safetensors", "ar")
        second = _full_file(root / "b.safetensors", "ar")
        args = self._args("--lora", f"{first}", "--lora-trigger", "frddtrn",
                          "--lora", f"{second}")
        specs = [L.parse_spec(one) for one in args.lora]
        adapters = L.load_stack(specs, mode=args.lora_mode,
                                triggers=args.lora_trigger, with_sha=False)
        self.assertEqual([a.trigger for a in adapters], ["frddtrn", ""])
        # A trigger with no adapter of its own is dropped, never slid onto
        # the wrong file.
        args = self._args("--lora", f"{first}",
                          "--lora-trigger", "frddtrn", "--lora-trigger", "spare")
        adapters = L.load_stack([L.parse_spec(one) for one in args.lora],
                                mode=args.lora_mode, triggers=args.lora_trigger,
                                with_sha=False)
        self.assertEqual([a.trigger for a in adapters], ["frddtrn"])

    def test_the_trigger_reaches_the_style_and_the_sidecar(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        first = _full_file(root / "a.safetensors", "ar")
        second = _full_file(root / "b.safetensors", "ar")
        args = self._args("--style", "MTV Unplugged, acoustic",
                          "--lora", f"{first}", "--lora-trigger", "frddtrn",
                          "--lora", f"{second}", "--lora-trigger", "mtrsprr")
        adapters = L.load_stack([L.parse_spec(one) for one in args.lora],
                                mode=args.lora_mode, triggers=args.lora_trigger,
                                with_sha=False)
        # The prototype's shape: "<trigger>, <style>", in pick order, once.
        self.assertEqual(L.style_with_triggers(args.style, adapters),
                         "frddtrn, mtrsprr, MTV Unplugged, acoustic")
        block = L.describe_stack(adapters)
        self.assertEqual(block["triggers"], ["frddtrn", "mtrsprr"])
        self.assertEqual([a["trigger"] for a in block["adapters"]],
                         ["frddtrn", "mtrsprr"])
        json.dumps(block)


class TheVariationKeepsTheVoice(unittest.TestCase):
    """`/music/variation` — a new take of a song is the SAME singer.

    A take keeps the parent's words and style, and a re-roll keeps its
    performance, so the parent's adapters are part of what is being repeated.
    They were not carried (Codex review, 2026-09-22): the new runner applies
    adapters only when they are named, so New take handed back a song in a
    voice the user had never picked, and Re-roll the sound dropped the
    acoustic adapter that made the recording sound the way it did.
    """

    def setUp(self):
        import mlx_ltx_panel
        from panel import routes_music
        self.P, self.R = mlx_ltx_panel, routes_music
        routes_music.P = mlx_ltx_panel
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.loras = root / "loras"
        (self.loras / L.USER_SUBDIR).mkdir(parents=True)
        (self.loras / L.USER_SUBDIR / "freddie.safetensors").write_bytes(b"x")
        self._saved_loras = self.P.MUSIC_LORAS
        self.P.MUSIC_LORAS = self.loras
        self.addCleanup(lambda: setattr(self.P, "MUSIC_LORAS", self._saved_loras))
        # A finished song in the outputs, with the artifacts a variation
        # restarts from and the params the runner recorded.
        self.P.OUTPUT.mkdir(parents=True, exist_ok=True)
        self.song = (self.P.OUTPUT / "_loratest_song.wav").resolve()
        self.song.write_bytes(b"RIFF....WAVE")
        self.addCleanup(self.song.unlink, True)
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir()
        (self.artifacts / "result.json").write_text("{}")
        self.side = Path(str(self.song) + ".json")
        self.addCleanup(self.side.unlink, True)
        self.pick = f"{L.USER_SUBDIR}/freddie.safetensors"
        self.side.write_text(json.dumps({
            "engine": "music", "title": "Unplugged", "style": "warm soul",
            "lyrics": "[verse]\nooh", "mode": "full", "instrumental": False,
            "artifacts": str(self.artifacts),
            "params": {"music_quality": "final", "music_max_seconds": "120",
                       "music_precision": "8bit", "music_lora_mode": "separate",
                       "music_loras": [{"id": self.pick, "strength": 0.9,
                                        "path": str(self.loras / self.pick)}]},
        }))

    def _vary(self, kind):
        queued = []
        with mock.patch.object(self.R, "_queue",
                               side_effect=lambda form: queued.append(form)
                               or self.P.make_job(form)), \
             mock.patch.object(self.P, "push"):
            h = _FormHandler({"kind": kind, "path": str(self.song)})
            self.R.post_music_variation(h, "/music/variation", {}, "")
        self.assertEqual(h.status, 200, h.body)
        return queued[0], self.P.make_job(queued[0])["params"]

    def test_a_new_take_carries_the_parents_adapter_and_its_strength(self):
        _form, params = self._vary("take")
        self.assertEqual([(p["id"], p["strength"]) for p in params["music_loras"]],
                         [(self.pick, 0.9)])

    def test_a_re_roll_of_the_sound_carries_the_mode_too(self):
        _form, params = self._vary("sound")
        self.assertEqual(params["music_lora_mode"], "separate")
        self.assertTrue(params["music_loras"])

    def test_the_carried_picks_are_resolved_inside_the_lora_folder_again(self):
        # A sidecar is a file on disk; it names an adapter, never a path.
        # It is not resolved, and since 2026-09-24 (M6-05) it is not silently
        # DROPPED either: a take without the parent's singer is refused with
        # the id named, instead of queued with a different voice.
        self.side.write_text(self.side.read_text().replace(
            json.dumps(self.pick), json.dumps("../../../etc/passwd")))
        queued = []
        with mock.patch.object(self.R, "_queue",
                               side_effect=lambda form: queued.append(form)
                               or self.P.make_job(form)), \
             mock.patch.object(self.P, "push"):
            h = _FormHandler({"kind": "take", "path": str(self.song)})
            self.R.post_music_variation(h, "/music/variation", {}, "")
        self.assertEqual(h.status, 409, h.body)
        self.assertIn("../../../etc/passwd", h.body["error"])
        self.assertEqual(self.P.music_params(queued[0])["music_loras"], [])

    def test_a_song_written_with_no_adapter_queues_none(self):
        self.side.write_text(self.side.read_text().replace(
            json.dumps([{"id": self.pick, "strength": 0.9,
                         "path": str(self.loras / self.pick)}]), "[]"))
        _form, params = self._vary("take")
        self.assertEqual(params["music_loras"], [])

    def test_a_restyle_keeps_them_as_well(self):
        # The words and the style are new; who is singing them is not.
        meta = json.loads(self.side.read_text())
        meta["score_abc"] = "X:1"
        self.side.write_text(json.dumps(meta))
        queued = []
        with mock.patch.object(self.R, "_queue",
                               side_effect=lambda form: queued.append(form)
                               or self.P.make_job(form)), \
             mock.patch.object(self.P, "push"):
            h = _FormHandler({"kind": "restyle", "path": str(self.song),
                              "style": "bossa nova"})
            self.R.post_music_variation(h, "/music/variation", {}, "")
        self.assertEqual(h.status, 200, h.body)
        params = self.P.make_job(queued[0])["params"]
        self.assertEqual(params["music_style"], "bossa nova")
        self.assertTrue(params["music_loras"])


class TheSidecarBlock(unittest.TestCase):

    def test_it_records_every_adapter_and_the_instrumental_recipe(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        adapter = L.read_adapter(_full_file(Path(tmp.name) / "a.safetensors", "ar"),
                                 strength=0.6, trigger="mtrsprr", with_sha=True)
        block = L.describe_stack([adapter], instrumental={"adapter": "ar_lora_inst", "strength": 1.0})
        self.assertEqual(block["adapters"][0]["strength"], 0.6)
        self.assertEqual(block["adapters"][0]["rank"], RANK)
        self.assertEqual(len(block["adapters"][0]["sha256"]), 64)
        self.assertEqual(block["triggers"], ["mtrsprr"])
        self.assertEqual(block["branches"], ["ar"])
        json.dumps(block)          # the sidecar has to serialise


if __name__ == "__main__":
    unittest.main()
