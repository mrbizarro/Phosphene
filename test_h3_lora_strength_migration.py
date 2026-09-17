#!/usr/bin/env python3
"""An H3 LoRA imported before its scale was folded keeps its strength (ship review 2026-09-17).

Before 4.13.1, importing an adapter whose per-module alpha/rank were 0.5 and
1.0 saved a recommended strength of 0.5. 4.13.1 folds those ratios into each
lora_B at render time, so the file itself now carries them — but the sidecar
(and every job already queued with that recommendation) still said 0.5, and
the modules rendered at 0.25 and 0.5: the adapter, weakened. The listing
only corrected kohya imports.

Now: a folded (or about-to-be-folded) file lists at 1.0; its sidecar is moved
to 1.0 with the old number kept; a job queued by an older build at that old
number renders at 1.0; a strength anyone chose deliberately is kept.

MLX runs on the CPU (tiny tensors); no model, no GPU."""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_TMP = Path(tempfile.mkdtemp(prefix="phos-h3-lora-mig-"))
for _k, _d in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "out"),
               ("LTX_UPLOADS_DIR", "uploads")):
    (_TMP / _d).mkdir(parents=True, exist_ok=True)
    os.environ[_k] = str(_TMP / _d)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ["LTX_H3_FORCE_CAPABLE"] = "1"
os.environ.setdefault("LTX_PORT", "8299")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


def _safetensors(path: Path, tensors: dict, meta: dict | None = None) -> None:
    header, blobs, off = {}, [], 0
    for name, (shape, vals) in tensors.items():
        raw = struct.pack(f"<{len(vals)}f", *vals)
        header[name] = {"dtype": "F32", "shape": list(shape),
                        "data_offsets": [off, off + len(raw)]}
        blobs.append(raw)
        off += len(raw)
    if meta:
        header["__metadata__"] = {k: str(v) for k, v in meta.items()}
    hb = json.dumps(header).encode()
    hb += b" " * (-len(hb) % 8)
    path.write_bytes(struct.pack("<Q", len(hb)) + hb + b"".join(blobs))


def _module(name: str, rank: int, alpha: float | None) -> dict:
    t = {f"{name}.lora_A.weight": ((rank, 3), [1.0] * (rank * 3)),
         f"{name}.lora_B.weight": ((4, rank), [1.0] * (4 * rank))}
    if alpha is not None:
        t[f"{name}.alpha"] = ((), [alpha])
    return t


M1, M2 = "blocks.1.attn.qkv_proj", "blocks.2.attn.qkv_proj"


class _LoraDir(unittest.TestCase):
    def setUp(self):
        import mlx.core as mx
        mx.set_default_device(mx.cpu)
        self.dir = Path(tempfile.mkdtemp(prefix="h3loras-", dir=_TMP))
        self.st = ExitStack()
        self.st.enter_context(unittest.mock.patch.object(P, "_h3_loras_dir", lambda: self.dir))
        self.logs = []
        self.st.enter_context(unittest.mock.patch.object(P, "push", self.logs.append))
        P._H3_LORA_LAYOUT_CACHE.clear()

    def tearDown(self):
        self.st.close()
        P._H3_LORA_LAYOUT_CACHE.clear()

    def imported(self, name, tensors, strength, meta=None, layout="bare"):
        """A file + the sidecar a pre-4.13.1 import wrote for it."""
        f = self.dir / f"{name}.safetensors"
        _safetensors(f, tensors, meta)
        f.with_suffix(".json").write_text(json.dumps({
            "name": name, "kind": "import", "lora_layout": layout,
            "recommended_strength": strength}))
        return f

    def mixed(self, name="mixed"):
        # alpha/rank 0.5 and 1.0; the old import picked 0.5 (farthest from 1).
        return self.imported(name, {**_module(M1, 4, 2.0), **_module(M2, 4, 4.0)}, 0.5)

    def listed(self, f):
        P._H3_LORA_LAYOUT_CACHE.clear()
        return next(e for e in P.list_h3_user_loras()
                    if e["filename"] == f.name)["recommended_strength"]

    def sidecar(self, f):
        return json.loads(f.with_suffix(".json").read_text())

    def effective(self, f, strength):
        import mlx.core as mx
        w = mx.load(str(f))
        return [round(float(w[f"{m}.lora_B.weight"][0, 0].item()) * strength, 6)
                for m in (M1, M2)]

    def dispatch(self, f, strength, params):
        P._h3_lora_prepare(f)
        return P._h3_lora_migrate_strength(f, strength, params)


class AFoldedFileReadsAsTrained(_LoraDir):
    def test_the_old_import_lists_at_one_before_any_render(self):
        f = self.mixed()
        self.assertEqual(P._h3_lora_scale_state(f), "mixed")
        self.assertEqual(self.listed(f), 1.0)
        self.assertEqual(self.sidecar(f)["recommended_strength"], 0.5)   # untouched by a read

    def test_a_job_queued_before_the_update_renders_as_trained(self):
        f = self.mixed()
        st = self.dispatch(f, 0.5, {})                  # no h3_lora_scale_v: old build
        self.assertEqual(st, 1.0)
        self.assertEqual(self.effective(f, st), [0.5, 1.0])   # as trained, not [0.25, 0.5]
        sc = self.sidecar(f)
        self.assertEqual((sc["recommended_strength"], sc["recommended_strength_before_fold"]),
                         (1.0, 0.5))
        self.assertTrue(sc.get("strength_migrated_at"))
        self.assertEqual(self.listed(f), 1.0)
        self.assertTrue(any("renders at 1.0" in m for m in self.logs))

    def test_a_file_4_13_1_already_folded_is_corrected_too(self):
        f = self.mixed()
        P._h3_lora_fold_mixed_alphas(f)                 # what 4.13.1 did, sidecar untouched
        self.assertEqual(P._h3_lora_scale_state(f), "folded")
        self.assertEqual(self.sidecar(f)["recommended_strength"], 0.5)
        self.assertEqual(self.listed(f), 1.0)
        self.assertEqual(self.dispatch(f, 0.5, {}), 1.0)
        # and a second job from the same old queue, after the sidecar moved
        self.assertEqual(self.dispatch(f, 0.5, {}), 1.0)

    def test_a_deliberate_strength_is_kept(self):
        f = self.mixed()
        self.assertEqual(self.dispatch(f, 0.8, {}), 0.8)
        self.assertEqual(self.dispatch(f, 1.0, {}), 1.0)
        self.assertEqual(self.dispatch(f, -0.5, {}), -0.5)

    def test_a_job_queued_by_this_build_keeps_its_strength_and_says_so(self):
        f = self.mixed()
        self.logs.clear()
        v = {"h3_lora_scale_v": P.H3_LORA_SCALE_VERSION}
        self.assertEqual(self.dispatch(f, 0.5, v), 0.5)
        self.assertTrue(any("renders it weaker" in m for m in self.logs))
        self.assertEqual(self.dispatch(f, 1.0, v), 1.0)

    def test_a_uniform_alpha_keeps_its_strength(self):
        # Not folded: the strength IS where alpha/rank gets applied.
        f = self.imported("uniform", {**_module(M1, 4, 2.0), **_module(M2, 4, 2.0)}, 0.5)
        before = f.read_bytes()
        self.assertEqual(P._h3_lora_scale_state(f), "plain")
        self.assertEqual(self.listed(f), 0.5)
        self.assertEqual(self.dispatch(f, 0.5, {}), 0.5)
        self.assertEqual(f.read_bytes(), before)
        self.assertNotIn("recommended_strength_before_fold", self.sidecar(f))
        self.assertEqual(self.effective(f, 0.5), [0.5, 0.5])

    def test_a_kohya_conversion_saved_at_its_alpha_is_corrected(self):
        f = self.imported("kohya", {**_module(M1, 4, None), **_module(M2, 4, None)}, 0.25,
                          meta={"converted_by": "phosphene", "converted_from": "kohya",
                                "ss_network_alpha": "1"},
                          layout="kohya")
        self.assertEqual(self.listed(f), 1.0)
        self.assertEqual(self.dispatch(f, 0.25, {}), 1.0)
        self.assertEqual(self.sidecar(f)["recommended_strength_before_fold"], 0.25)

    def test_an_exporter_folded_file_at_one_is_left_alone(self):
        f = self.imported("baked", {**_module(M1, 4, None)}, 1.0,
                          meta={"baked_scale": "0.0625"})
        before = f.with_suffix(".json").read_text()
        self.assertEqual(self.dispatch(f, 1.0, {}), 1.0)
        self.assertEqual(self.dispatch(f, 0.3, {}), 0.3)
        self.assertEqual(f.with_suffix(".json").read_text(), before)

    def test_no_sidecar_is_fine(self):
        f = self.dir / "bare.safetensors"
        _safetensors(f, {**_module(M1, 4, 2.0), **_module(M2, 4, 4.0)})
        self.assertEqual(self.listed(f), 1.0)
        self.assertEqual(self.dispatch(f, 0.5, {}), 0.5)
        self.assertFalse(f.with_suffix(".json").exists())


class TheRenderPathUsesIt(_LoraDir):
    def test_the_runner_gets_one_for_an_old_queued_job(self):
        f = self.mixed()
        job = {"id": "lora-old", "params": {
            "engine": "h3", "mode": "t2v", "h3_tier": "standard_5s",
            "prompt": "a dancer", "seed": "7", "h3_turbo": False,
            "loras": [{"path": str(f), "strength": 0.5}]}}
        seen = {}

        class Intercept(Exception):
            pass

        def popen(cmd, **kw):
            seen["cmd"] = cmd
            raise Intercept()

        paths = dict(missing=[], repairable=False, dit=_TMP / "dit", python=sys.executable,
                     runner=_TMP / "gen.py", compact_root=_TMP / "c", text_config=_TMP / "t")
        ff = _TMP / "ffmpeg"
        ff.write_text("")
        with ExitStack() as st:
            for name, val in {
                    "h3_paths": lambda: paths, "h3_capable": lambda: True,
                    "h3_supports_lora": lambda: True, "h3_supports_lora_stack": lambda: False,
                    "h3_lora_max_stack": lambda: 1, "h3_dit_choice": lambda: ("bf16", None),
                    "output_codec_settings": lambda: {"crf": "18", "pix_fmt": "yuv420p"},
                    "_h3_runner_has_flag": lambda flag: False, "FFMPEG": ff}.items():
                st.enter_context(unittest.mock.patch.object(P, name, val))
            for name in ("h3_supports_memory_limits", "h3_supports_prompt_cache",
                         "h3_live_preview_ready", "h3_supports_stage_a",
                         "h3_supports_tae_draft"):
                st.enter_context(unittest.mock.patch.object(P, name, lambda: False))
            st.enter_context(unittest.mock.patch.object(P.HELPER, "is_alive", lambda: False))
            st.enter_context(unittest.mock.patch.object(P.subprocess, "Popen", popen))
            with self.assertRaises(Intercept):
                P.run_h3_job_inner(job)
        cmd = seen["cmd"]
        spec = cmd[cmd.index("--lora") + 1]
        self.assertEqual(spec, f"{f.resolve()}:1")
        self.assertEqual(job["params"]["h3_lora_strength"], 1.0)
        self.assertEqual(self.effective(f, 1.0), [0.5, 1.0])

    def _dispatch_argv(self, job, stack=False):
        seen = {}

        class Intercept(Exception):
            pass

        def popen(cmd, **kw):
            seen["cmd"] = cmd
            raise Intercept()

        paths = dict(missing=[], repairable=False, dit=_TMP / "dit", python=sys.executable,
                     runner=_TMP / "gen.py", compact_root=_TMP / "c", text_config=_TMP / "t")
        ff = _TMP / "ffmpeg"
        ff.write_text("")
        with ExitStack() as st:
            for name, val in {
                    "h3_paths": lambda: paths, "h3_capable": lambda: True,
                    "h3_supports_lora": lambda: True,
                    "h3_supports_lora_stack": lambda: stack,
                    "h3_lora_max_stack": lambda: 4 if stack else 1,
                    "h3_dit_choice": lambda: ("bf16", None),
                    "output_codec_settings": lambda: {"crf": "18", "pix_fmt": "yuv420p"},
                    "_h3_runner_has_flag": lambda flag: False, "FFMPEG": ff}.items():
                st.enter_context(unittest.mock.patch.object(P, name, val))
            for name in ("h3_supports_memory_limits", "h3_supports_prompt_cache",
                         "h3_live_preview_ready", "h3_supports_stage_a",
                         "h3_supports_tae_draft"):
                st.enter_context(unittest.mock.patch.object(P, name, lambda: False))
            st.enter_context(unittest.mock.patch.object(P.HELPER, "is_alive", lambda: False))
            st.enter_context(unittest.mock.patch.object(P.subprocess, "Popen", popen))
            with self.assertRaises(Intercept):
                P.run_h3_job_inner(job)
        cmd = seen["cmd"]
        got = [float(cmd[i + 1].rsplit(":", 1)[1]) for i, a in enumerate(cmd) if a == "--lora"]
        return got if stack else got[0]

    def test_a_repeated_file_in_a_stack_migrates_only_its_own_entry(self):
        # Codex hotfix review round 2: the same file twice, once at the old
        # recommendation and once deliberately.
        f = self.mixed()
        job = {"id": "lora-dup", "params": {
            "engine": "h3", "mode": "t2v", "h3_tier": "standard_5s",
            "prompt": "a dancer", "seed": "7", "h3_turbo": False,
            "loras": [{"path": str(f), "strength": 0.5},
                      {"path": str(f), "strength": 0.7}]}}
        self.assertEqual(self._dispatch_argv(job, stack=True), [1.0, 0.7])
        self.assertEqual([l["strength"] for l in job["params"]["loras"]], [1.0, 0.7])
        replay = P.make_job({k: [v] for k, v in {
            "mode": "t2v", "engine": "h3", "prompt": "a dancer", "h3_turbo": "0",
            "loras": json.dumps(job["params"]["loras"])}.items()})
        replay["id"] = "lora-dup2"
        self.assertEqual(self._dispatch_argv(replay, stack=True), [1.0, 0.7])

    def test_the_corrected_strength_survives_load_params_and_finish(self):
        # Codex hotfix review #1: the recipe the sidecar copies (and Load
        # Params / Finish replay) must carry the migrated strength.
        f = self.mixed()
        job = {"id": "lora-rt", "params": {
            "engine": "h3", "mode": "t2v", "h3_tier": "standard_5s",
            "prompt": "a dancer", "seed": "7", "h3_turbo": False,
            "loras": [{"path": str(f), "strength": 0.5, "name": "mixed"}]}}
        self.assertEqual(self._dispatch_argv(job), 1.0)
        rec = job["params"]
        self.assertEqual(rec["loras"][0]["strength"], 1.0)
        self.assertEqual(rec["loras"][0]["name"], "mixed")          # entry kept
        self.assertEqual(rec["h3_lora_scale_v"], P.H3_LORA_SCALE_VERSION)
        # What queue.js restores from the sidecar, through a real make_job.
        replay = P.make_job({k: [v] for k, v in {
            "mode": "t2v", "engine": "h3", "prompt": "a dancer", "h3_turbo": "0",
            "loras": json.dumps([{"path": l["path"], "strength": l["strength"]}
                                 for l in rec["loras"]])}.items()})
        self.assertEqual(replay["params"]["loras"][0]["strength"], 1.0)
        replay["id"] = "lora-rt2"
        self.assertEqual(self._dispatch_argv(replay), 1.0)

    def test_a_deliberate_strength_is_not_rewritten_in_the_recipe(self):
        f = self.mixed()
        job = {"id": "lora-keep", "params": {
            "engine": "h3", "mode": "t2v", "h3_tier": "standard_5s",
            "prompt": "a dancer", "seed": "7", "h3_turbo": False,
            "loras": [{"path": str(f), "strength": 0.7}]}}
        self.assertEqual(self._dispatch_argv(job), 0.7)
        self.assertEqual(job["params"]["loras"][0]["strength"], 0.7)

    def test_make_job_stamps_the_scale_version(self):
        j = P.make_job({k: [v] for k, v in {
            "mode": "t2v", "engine": "h3", "prompt": "a dancer", "h3_turbo": "0"}.items()})
        self.assertEqual(j["params"].get("h3_lora_scale_v"), P.H3_LORA_SCALE_VERSION)
        ltx = P.make_job({k: [v] for k, v in {
            "mode": "t2v", "engine": "ltx", "prompt": "a dancer"}.items()})
        self.assertNotIn("h3_lora_scale_v", ltx["params"])


if __name__ == "__main__":
    unittest.main()
