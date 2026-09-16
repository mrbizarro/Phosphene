#!/usr/bin/env python3
"""Contract gate for the H3 review round 2 fixes (PM hub notes/h3-review/FINDINGS_R2.md).

 1  H3 -> LTX x2 handoff carries the draft's prompt and resolved seed
 2  Finish restores the source's LoRAs, orientation and adapter slot
 3  a nonzero exit is only salvaged for a verified, full-length video file
 4  a Phosphene kohya conversion loads at 1.0 (no second alpha/rank)
 5  disagreeing per-module alphas are folded per module, not collapsed
 6  No Music reaches written chain-window prompts
 7  (worker) the sidecar keeps the user's own reference image
 8  a replayed pinned step count (4-30) survives Load Params and Finish
 9  measured and pinned-step ETAs carry this Mac's speed factor
10  Turbo estimates follow the adapter installed now, both directions
11  (worker) the codec record says what encoded the file
12  Stop early is not offered after the final window's last forward

MLX runs on the CPU here, on purpose: this gate must never touch the GPU.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-h3-r2-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8294")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

PANEL_SRC = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _safetensors(path: Path, tensors: dict, meta: dict | None = None) -> None:
    """Tiny F32 safetensors writer (no mlx): {name: (shape, [values])}."""
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


def _pair(prefix: str, rank: int, fill: float = 0.5) -> dict:
    return {f"{prefix}.lora_A.weight": ((rank, 3), [fill] * (rank * 3)),
            f"{prefix}.lora_B.weight": ((4, rank), [fill] * (4 * rank))}


# ---------------------------------------------------------------- 1
class TestUpscaleHandoff(unittest.TestCase):
    def test_empty_upscale_prompt_stays_empty(self):
        j = P.make_job({k: [v] for k, v in {
            "mode": "upscale", "engine": "ltx", "upscale_source_path": "/x.mp4",
            "prompt": "", "seed": "-1"}.items()})
        self.assertEqual(j["params"]["prompt"], "")

    def test_other_modes_keep_the_filler(self):
        j = P.make_job({k: [v] for k, v in {
            "mode": "t2v", "engine": "ltx", "prompt": ""}.items()})
        self.assertTrue(j["params"]["prompt"])

    def test_handoff_carries_prompt_and_resolved_seed(self):
        seen = {}
        old_mj, old_pq = P.make_job, P.persist_queue

        def fake(form):
            seen.update(form)
            return {"id": "x2job", "params": {}}
        P.make_job, P.persist_queue = fake, (lambda: None)
        try:
            P._chain_upscale_after_h3(
                {"id": "draft1"},
                {"prompt": "A woman lifts a dumbbell", "seed": "-1",
                 "seed_used": 52190, "label": "gym"},
                Path("/tmp/gym.mp4"))
        finally:
            P.make_job, P.persist_queue = old_mj, old_pq
            with P.QUEUE_COND:
                P.STATE["queue"] = [j for j in P.STATE["queue"] if j.get("id") != "x2job"]
        self.assertEqual(seen["prompt"], "A woman lifts a dumbbell")
        self.assertEqual(seen["seed"], "52190")


# ---------------------------------------------------------------- 3
@unittest.skipUnless(Path(str(P.FFMPEG)).is_file(), "ffmpeg missing")
class TestSalvageVerifiesTheFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="phos-h3-salvage-"))

        def mk(name, frames=None, audio_only=False):
            out = cls.dir / name
            if audio_only:
                cmd = [str(P.FFMPEG), "-y", "-loglevel", "error", "-f", "lavfi",
                       "-i", "anoisesrc=d=4:r=48000", "-c:a", "pcm_s16le",
                       "-threads", "1", str(out)]
            else:
                cmd = [str(P.FFMPEG), "-y", "-loglevel", "error", "-f", "lavfi",
                       "-i", "nullsrc=s=320x192:r=24,geq=random(1)*255:128:128",
                       "-frames:v", str(frames), "-c:v", "libx264", "-crf", "0",
                       "-preset", "ultrafast", "-pix_fmt", "yuv444p",
                       "-threads", "1", str(out)]
            subprocess.run(cmd, check=True, timeout=120)
            return out
        cls.full = mk("full.mp4", 73)
        cls.short1 = mk("short1.mp4", 72)
        cls.partial = mk("partial.mp4", 8)
        cls.audio = mk("audio.wav", audio_only=True)

    def test_sizes_pass_the_old_floor(self):
        for f in (self.full, self.partial, self.audio):
            self.assertGreaterEqual(f.stat().st_size, 200_000, f.name)

    def test_full_clip_is_salvaged(self):
        self.assertTrue(P._h3_clip_is_complete(self.full, time.time() - 60, 73))

    def test_one_short_is_tolerated_for_old_runners(self):
        self.assertTrue(P._h3_clip_is_complete(self.short1, time.time() - 60, 73))

    def test_partial_clip_is_not(self):
        self.assertFalse(P._h3_clip_is_complete(self.partial, time.time() - 60, 73))

    def test_audio_only_is_not(self):
        self.assertFalse(P._h3_clip_is_complete(self.audio, time.time() - 60, 73))

    def test_stale_file_is_not(self):
        self.assertFalse(P._h3_clip_is_complete(self.full, time.time() + 60, 73))

    def test_worker_passes_the_delivered_count(self):
        self.assertIn("_h3_clip_is_complete(out_path, t0, expect_frames=frames)", PANEL_SRC)


# ---------------------------------------------------------------- 4 / 5
class TestLoraScale(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phos-h3-scale-"))

    def _report(self, path):
        header, start = P._safetensors_header(path)
        return P._h3_lora_scale_report(path, header, start)

    def test_phosphene_kohya_conversion_is_folded(self):
        f = self.tmp / "old_convert.safetensors"
        _safetensors(f, _pair("blocks.3.attn.qkv_proj", 32),
                     {"converted_from": "kohya", "converted_by": "phosphene",
                      "alpha_over_rank": "0.5", "ss_network_alpha": "16"})
        r = self._report(f)
        self.assertEqual(r["source"], "folded")
        self.assertEqual(r["strength"], 1.0)

    def test_foreign_metadata_alpha_still_divides(self):
        f = self.tmp / "foreign.safetensors"
        _safetensors(f, _pair("blocks.3.attn.qkv_proj", 32), {"ss_network_alpha": "16"})
        r = self._report(f)
        self.assertEqual(r["source"], "uniform")
        self.assertAlmostEqual(r["strength"], 0.5)

    def test_converter_writes_the_marker(self):
        self.assertIn('"peft_scale_folded_into_B": (f"{quotients[0]:g}"', PANEL_SRC)

    def test_listing_corrects_saved_kohya_strength(self):
        d = self.tmp / "loras"
        d.mkdir()
        f = d / "person.safetensors"
        _safetensors(f, _pair("blocks.3.attn.qkv_proj", 32))
        f.with_suffix(".json").write_text(json.dumps(
            {"name": "person", "recommended_strength": 0.5,
             "lora_layout": "kohya", "kind": "import"}))
        old = P._h3_loras_dir
        P._h3_loras_dir = lambda: d
        try:
            rows = [r for r in P.list_h3_user_loras() if r["filename"] == f.name]
        finally:
            P._h3_loras_dir = old
        self.assertEqual(rows[0]["recommended_strength"], 1.0)

    def test_uniform_alphas_are_not_rewritten(self):
        f = self.tmp / "uniform.safetensors"
        t = {**_pair("blocks.1.attn.qkv_proj", 4), **_pair("blocks.2.attn.qkv_proj", 4)}
        t["blocks.1.attn.qkv_proj.alpha"] = ((), [2.0])
        t["blocks.2.attn.qkv_proj.alpha"] = ((), [2.0])
        _safetensors(f, t)
        before = f.read_bytes()
        self.assertIsNone(P._h3_lora_fold_mixed_alphas(f))
        self.assertEqual(f.read_bytes(), before)
        r = self._report(f)
        self.assertEqual(r["source"], "per_module")
        self.assertAlmostEqual(r["strength"], 0.5)

    def test_mixed_alphas_fold_per_module(self):
        import mlx.core as mx
        mx.set_default_device(mx.cpu)
        f = self.tmp / "mixed.safetensors"
        t = {**_pair("blocks.1.attn.qkv_proj", 4, 1.0),
             **_pair("blocks.2.attn.qkv_proj", 4, 1.0)}
        t["blocks.1.attn.qkv_proj.alpha"] = ((), [2.0])   # 0.5
        t["blocks.2.attn.qkv_proj.alpha"] = ((), [4.0])   # 1.0
        _safetensors(f, t)
        out = P._h3_lora_fold_mixed_alphas(f)
        self.assertEqual(out["modules"], 2)
        w = mx.load(str(f))
        self.assertFalse(any(k.endswith(".alpha") for k in w))
        self.assertAlmostEqual(float(w["blocks.1.attn.qkv_proj.lora_B.weight"][0, 0].item()), 0.5)
        self.assertAlmostEqual(float(w["blocks.2.attn.qkv_proj.lora_B.weight"][0, 0].item()), 1.0)
        self.assertAlmostEqual(float(w["blocks.1.attn.qkv_proj.lora_A.weight"][0, 0].item()), 1.0)
        r = self._report(f)
        self.assertEqual((r["source"], r["strength"]), ("folded", 1.0))
        # Idempotent: prepare again changes nothing.
        before = f.read_bytes()
        P._h3_lora_prepare(f)
        self.assertEqual(f.read_bytes(), before)


# ---------------------------------------------------------------- 9
class TestHardwareFactor(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("PHOSPHENE_SPEED_FACTOR", None)

    def test_measured_and_coefficients_scale(self):
        base = P._build_h3_tiers()
        os.environ["PHOSPHENE_SPEED_FACTOR"] = "2"
        slow = P._build_h3_tiers()
        self.assertTrue(slow["standard_5s"]["eta_measured"])
        self.assertAlmostEqual(slow["standard_5s"]["eta_min"],
                               2 * base["standard_5s"]["eta_min"], places=2)
        self.assertEqual(slow["standard_5s"]["eta"], "~18 min")
        self.assertAlmostEqual(slow["high_5s"]["per_forward_sec"],
                               2 * base["high_5s"]["per_forward_sec"], delta=0.02)
        self.assertAlmostEqual(slow["high_5s"]["fixed_sec"],
                               2 * base["high_5s"]["fixed_sec"], delta=0.02)
        # Browser arithmetic for Auto reproduces the server's model number.
        c = slow["high_5s"]
        js = (c["chain_windows"] * 15 * c["per_forward_sec"]
              + c["chain_windows"] * c["fixed_sec"]) / 60
        self.assertAlmostEqual(js, c["eta_min"], delta=0.05)

    def test_factor_one_keeps_the_receipt_string(self):
        os.environ["PHOSPHENE_SPEED_FACTOR"] = "1"
        self.assertEqual(P._build_h3_tiers()["standard_5s"]["eta"], "~9 min")


# ---------------------------------------------------------------- 10
class TestTurboRetune(unittest.TestCase):
    def test_install_and_removal_reprice(self):
        old_steps, old_fwd = P.h3_turbo_steps, P._H3_TURBO_PRICED_FWD
        saved = {k: dict(v) for k, v in P.H3_TIERS.items()}
        try:
            P._H3_TURBO_PRICED_FWD = P.H3_TURBO_FORWARDS
            P.h3_turbo_steps = lambda paths=None: 7
            P._h3_retune_turbo_estimates()
            self.assertEqual(P.H3_TIERS["standard_10s"]["turbo_forwards"], 12)
            v4_min = P.H3_TIERS["standard_5s"]["turbo_min"]
            P.h3_turbo_steps = lambda paths=None: P.H3_TURBO_STEPS
            P._h3_retune_turbo_estimates()
            self.assertEqual(P.H3_TIERS["standard_10s"]["turbo_forwards"],
                             2 * P.H3_TURBO_FORWARDS)
            self.assertLess(P.H3_TIERS["standard_5s"]["turbo_min"], v4_min)
        finally:
            P.h3_turbo_steps, P._H3_TURBO_PRICED_FWD = old_steps, old_fwd
            for k, v in saved.items():
                P.H3_TIERS[k].clear()
                P.H3_TIERS[k].update(v)

    def test_status_reprices(self):
        body = PANEL_SRC.split("def h3_turbo_status()", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_h3_retune_turbo_estimates()", body)


# ---------------------------------------------------------------- 12
class TestStopEarlyBoundary(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="phos-h3-lp-"))
        (self.dir / "preview_latest.png").write_bytes(b"x")
        (self.dir / "status.json").write_text(json.dumps({
            "schema": "h3-live-preview/1", "status": "running",
            "forward": 8, "total_forwards": 8, "window": 1, "total_windows": 1}))
        self.old = (P.h3_live_preview_ready, P.live_preview_dir)
        P.h3_live_preview_ready = lambda: True
        P.live_preview_dir = lambda job_id: self.dir

    def tearDown(self):
        P.h3_live_preview_ready, P.live_preview_dir = self.old

    def _abortable(self, phase, window=1, total=1):
        cur = {"id": "j", "progress": {"phase": phase, "window": window,
                                       "window_total": total}}
        return P._h3_preview_progress(cur)["live_preview"]["abortable"]

    def test_denoise_is_abortable(self):
        self.assertTrue(self._abortable("denoise"))

    def test_final_decode_is_not(self):
        self.assertFalse(self._abortable("decode", 1, 1))
        self.assertFalse(self._abortable("decode", 3, 3))

    def test_decode_before_another_window_is(self):
        self.assertTrue(self._abortable("decode", 1, 2))

    def test_worker_reports_decode_after_the_last_forward(self):
        self.assertIn('"decode" if post else', PANEL_SRC)
        self.assertIn('_H3_POST_PHASES = ("video_vae_decode", "audio_vae_decode", '
                      '"encode_mux", "stitch")', PANEL_SRC)


# ---------------------------------------------------------------- 7 / 11 (worker source)
class TestWorkerProvenance(unittest.TestCase):
    def test_sidecar_image_is_the_users_file(self):
        self.assertIn('"image": ((p.get("image") or "").strip() or str(first_frame))', PANEL_SRC)
        self.assertIn('"first_frame_source":', PANEL_SRC)

    def test_codec_record_names_the_encoder(self):
        self.assertIn('"native_codec": dict(native_codec)', PANEL_SRC)
        self.assertIn('dict(job_codec, encoder="panel_export") if upscale_plan', PANEL_SRC)
        self.assertNotIn('"output_codec": output_codec_settings(),\n        "h3"', PANEL_SRC)


# ---------------------------------------------------------------- 2 / 6 / 8 (browser)
_JS = r"""
const fs = require('fs'), vm = require('vm');
function extract(file, name) {
  const s = fs.readFileSync(file, 'utf8');
  const start = s.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('missing ' + name);
  return s.slice(start, s.indexOf('\n}', start) + 2);
}
const Q = process.argv[1] + '/webapp/js/queue.js', E = process.argv[1] + '/webapp/js/engines.js';
const fields = { h3_steps: {value: 'auto'}, steps: {value: 9} };
const hint = {textContent: ''};
const ctx = {
  document: {getElementById: id => fields[id],
             querySelectorAll: () => [], querySelector: () => hint},
  localStorage: {setItem() {}},
  h3TierByKeyExact: key => ({key, quality: 'high', length: '5s'}),
  h3CurrentCell: () => ({steps: 16}),
  out: {},
};
vm.createContext(ctx);
for (const [f, n] of [[Q, '_h3FinishSteps'], [Q, 'h3FinishFieldsFromSidecar'],
                      [E, 'h3NormalizeSteps'], [E, 'setH3Steps']]) {
  vm.runInContext(extract(f, n), ctx);
}
vm.runInContext(`
  const src = {engine: 'h3', mode: 'i2v', prompt: 'p', seed: '-1', seed_used: 52190,
    image: '/orig.png', h3_steps: 21, h3_orientation: 'portrait',
    loras: [{path: '/actorA.safetensors', strength: 0.8}], h3_lora_slot: 'user'};
  out.finish = h3FinishFieldsFromSidecar(src, 'high_5s');
  out.finishBare = h3FinishFieldsFromSidecar({engine: 'h3', mode: 't2v', prompt: 'q',
    seed_used: 3, h3_steps: 0}, 'high_5s');
  out.steps = {};
  for (const n of ['4', '7', '12', '16', '20', '21', '30', '31', '3', 'auto', 'x']) {
    setH3Steps(n);
    out.steps[n] = [document.getElementById('h3_steps').value,
                    document.getElementById('steps').value];
  }
`, ctx);
process.stdout.write(JSON.stringify(ctx.out));
"""


@unittest.skipUnless(NODE, "node missing")
class TestBrowserReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, "-e", _JS, str(ROOT)], capture_output=True,
                           text=True, timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr)
        cls.out = json.loads(r.stdout)

    def test_finish_carries_the_recipe(self):
        f = self.out["finish"]
        self.assertEqual(f["loras"], [{"path": "/actorA.safetensors", "strength": 0.8}])
        self.assertEqual(f["h3_orientation"], "portrait")
        self.assertEqual(f["h3_lora_slot"], "user")
        self.assertEqual(f["h3_steps"], "21")
        self.assertEqual(f["seed"], "52190")
        self.assertEqual(f["image"], "/orig.png")

    def test_finish_without_adapters_clears_the_picker(self):
        f = self.out["finishBare"]
        self.assertEqual(f["loras"], [])
        self.assertEqual(f["h3_orientation"], "landscape")
        self.assertEqual(f["h3_steps"], "auto")

    def test_every_server_valid_count_survives(self):
        s = self.out["steps"]
        for n in ("4", "7", "12", "16", "20", "21", "30"):
            self.assertEqual(s[n], [n, int(n)], n)
        for n in ("31", "3", "auto", "x"):
            self.assertEqual(s[n], ["auto", 16], n)

    def test_finish_restores_before_the_shape(self):
        js = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        body = js.split("async function h3FinishActive()", 1)[1]
        self.assertLess(body.index("_restoreLoraPicker(fields.loras)"),
                        body.index("setH3Tier(fields.h3_tier)"))
        self.assertLess(body.index("setH3Orientation(fields.h3_orientation)"),
                        body.index("setH3Tier(fields.h3_tier)"))
        self.assertIn("setH3LoraSlot(fields.h3_lora_slot)", body)

    def test_no_music_reaches_written_windows(self):
        js = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        start = js.index("const cpRaw = String(fd.get('h3_chain_prompts')")
        block = js[start:js.index("} catch (_)", start)]
        prog = ("const fd = new Map([['h3_chain_prompts', process.argv[1]]]);"
                "fd.set = fd.set.bind(fd);"
                "{" + block + "} catch (_) {} } }"
                "process.stdout.write(fd.get('h3_chain_prompts'));")
        raw = json.dumps(["Shot one", "", "Shot three\nnon_diegetic_music: soft piano"])
        r = subprocess.run([NODE, "-e", prog, raw], capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got[0], "Shot one\n\nnon_diegetic_music: N/A")
        self.assertEqual(got[1], "")          # blank inherits the main prompt
        self.assertEqual(got[2], "Shot three\nnon_diegetic_music: soft piano")


if __name__ == "__main__":
    unittest.main()
