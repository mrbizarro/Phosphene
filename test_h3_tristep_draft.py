#!/usr/bin/env python3
"""Contract gate: the H3 Speed switch — ⚡ Fast (TaoMate-H3's 3-step adapter) | ✦ Best.

Owner rulings 2026-09-17: the 3-step draft (+ Upscale & Face Fix) is "a much
better option for a draft", it works for image-to-video at higher quality too,
and it ships as ONE switch. So Draft, Standard and High render with the
TaoMate adapter and its own sigma ladder (`--steps 4 --sigma-subset
50:0,16,33,49`, 3 forwards) by default whenever the adapter is installed;
Best (h3_tristep=0) is each shape's own sampler; Native and the dense single
pass always render Best; Turbo is never stacked on Fast; the file is fetched
from its author's repo at a pinned revision + digest, resumably.

No model, no GPU, no network: the runner is intercepted at Popen and the
download reads from an in-memory opener.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
import unittest.mock
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_TMP = Path(tempfile.mkdtemp(prefix="phos-h3-tristep-"))
for _k, _d in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "out"),
               ("LTX_UPLOADS_DIR", "uploads")):
    (_TMP / _d).mkdir(parents=True, exist_ok=True)
    os.environ[_k] = str(_TMP / _d)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ["LTX_H3_FORCE_CAPABLE"] = "1"
os.environ["PHOSPHENE_SPEED_FACTOR"] = "1"
os.environ.setdefault("LTX_PORT", "8302")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

SUBSET = "50:0,16,33,49"


def _safetensors(path: Path, tensors: dict) -> None:
    header, blobs, off = {}, [], 0
    for name, (shape, vals) in tensors.items():
        raw = struct.pack(f"<{len(vals)}f", *vals)
        header[name] = {"dtype": "F32", "shape": list(shape),
                        "data_offsets": [off, off + len(raw)]}
        blobs.append(raw)
        off += len(raw)
    hb = json.dumps(header).encode()
    hb += b" " * (-len(hb) % 8)
    path.write_bytes(struct.pack("<Q", len(hb)) + hb + b"".join(blobs))


class _Env(unittest.TestCase):
    """A fake install: H3 present, a stacking runner with --sigma-subset, the
    adapter directory in a temp dir, tiny size floors."""
    runner_flags = ("--lora", "--sigma-subset", "--lora-adaln")
    stack = True

    def setUp(self):
        self.st = ExitStack()
        self.adir = Path(tempfile.mkdtemp(prefix="adapters-", dir=_TMP))
        self.logs: list[str] = []
        flags = self.runner_flags
        for name, val in {
                "h3_available": lambda: True,
                "h3_supports_lora": lambda: True,
                "h3_supports_lora_stack": lambda: self.stack,
                "_h3_runner_has_flag": lambda flag: flag in flags,
                "_h3_turbo_dir": lambda: self.adir,
                "H3_TRISTEP_CANDIDATES": tuple((f, v, 1) for f, v, _ in P.H3_TRISTEP_CANDIDATES),
                "H3_TURBO_LORA_MIN_BYTES": 1,
                "push": self.logs.append}.items():
            self.st.enter_context(unittest.mock.patch.object(P, name, val))
        P._set_h3_tristep_dl(status="idle", mb=0, total_mb=0, error=None)

    def tearDown(self):
        self.st.close()
        P._set_h3_tristep_dl(status="idle", mb=0, total_mb=0, error=None)

    def install(self, filename=P.H3_TRISTEP_FILE) -> Path:
        f = self.adir / filename
        f.write_bytes(b"adapter fixture")
        return f

    def job(self, **form) -> dict:
        base = {"mode": "t2v", "engine": "h3", "prompt": "a man lifts a dumbbell",
                "h3_quality": "draft", "h3_length": "5s", "h3_turbo": "0"}
        base.update(form)
        return P.make_job({k: [v] for k, v in base.items()})


class TestPins(unittest.TestCase):
    def test_the_file_comes_from_its_author_at_a_pinned_revision(self):
        self.assertEqual(P.H3_TRISTEP_REPO, "Kijai/MiniMax-H3_comfy")
        self.assertRegex(P.H3_TRISTEP_REVISION, r"^[0-9a-f]{40}$")
        self.assertIn("/resolve/" + P.H3_TRISTEP_REVISION + "/loras/", P.H3_TRISTEP_URL)
        self.assertTrue(P.H3_TRISTEP_URL.startswith("https://huggingface.co/"))
        self.assertTrue(P.H3_TRISTEP_URL.endswith(P.H3_TRISTEP_FILE))
        self.assertRegex(P.H3_TRISTEP_SHA256, r"^[0-9a-f]{64}$")
        self.assertEqual(P.H3_TRISTEP_BYTES, 181697688)
        self.assertEqual(P.H3_TRISTEP_LICENSE, "MiniMax H3 Community License")
        self.assertEqual(P.H3_TRISTEP_SOURCE_REPO, "TaoLiveAIGC/TaoMate-H3")

    def test_the_ladder_is_taomates(self):
        self.assertEqual(P.H3_TRISTEP_SIGMA_SUBSET, SUBSET)
        self.assertEqual(P.H3_TRISTEP_STEPS, 4)
        self.assertEqual(P.H3_TRISTEP_FORWARDS, 3)
        idx = [int(i) for i in SUBSET.split(":")[1].split(",")]
        self.assertEqual(len(idx), P.H3_TRISTEP_STEPS)

    def test_the_local_copy_matches_the_pin_when_present(self):
        # The install's own adapter folder (LTX_H3_MODELS decides where it is).
        local = P._h3_turbo_dir() / P.H3_TRISTEP_FILE
        if not local.is_file():
            self.skipTest("adapter not installed on this machine")
        self.assertEqual(local.stat().st_size, P.H3_TRISTEP_BYTES)
        h = hashlib.sha256()
        with open(local, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        self.assertEqual(h.hexdigest(), P.H3_TRISTEP_SHA256)


class TestResolverAndStatus(_Env):
    def test_nothing_installed(self):
        r = P.h3_tristep_paths()
        self.assertFalse(r["files_ok"])
        self.assertIn(P.H3_TRISTEP_FILE, r["missing"][0])
        s = P.h3_tristep_status()
        self.assertFalse(s["available"])
        self.assertEqual(s["reason"], "not_downloaded")
        self.assertTrue(s["install_available"])
        with self.assertRaises(RuntimeError):
            P.h3_tristep_argv(r)

    def test_the_managed_file_resolves_and_emits_the_ladder(self):
        f = self.install()
        r = P.h3_tristep_paths()
        self.assertEqual((r["lora"], r["version"]), (f, "r19-kijai"))
        self.assertEqual(P.h3_tristep_argv(r),
                         ["--sigma-subset", SUBSET, "--lora", f"{f}:1.0"])
        s = P.h3_tristep_status()
        self.assertTrue(s["available"])
        self.assertEqual(s["steps"], 4)
        self.assertEqual(s["forwards"], 3)

    def test_a_full_rank_copy_wins(self):
        self.install()
        full = self.install(P.H3_TRISTEP_FULL_FILE)
        r = P.h3_tristep_paths()
        self.assertEqual((r["lora"], r["version"]), (full, "r128-full"))

    def test_turbo_never_resolves_the_tristep_file(self):
        self.install()
        self.assertFalse(P.h3_turbo_paths()["files_ok"])

    def test_an_old_runner_hides_it(self):
        self.install()
        with unittest.mock.patch.object(P, "_h3_runner_has_flag", lambda flag: flag == "--lora"):
            s = P.h3_tristep_status()
        self.assertFalse(s["available"])
        self.assertEqual(s["reason"], "runner_too_old")
        self.assertFalse(s["install_available"])

    def test_a_single_slot_runner_hides_it(self):
        self.install()
        with unittest.mock.patch.object(P, "h3_supports_lora_stack", lambda: False):
            self.assertFalse(P.h3_tristep_status()["available"])


class TestMakeJob(_Env):
    def test_draft_defaults_to_three_steps_when_installed(self):
        self.install()
        p = self.job()["params"]
        self.assertTrue(p["h3_tristep"])
        self.assertEqual(p["steps"], 4)
        self.assertFalse(p["h3_turbo"])

    def test_every_draft_canvas_and_length(self):
        self.install()
        for key, cell in P.H3_TIERS.items():
            if not cell["draft"] or cell["dense"]:
                continue
            with self.subTest(cell=key):
                p = self.job(h3_quality=cell["quality"], h3_length=cell["length"],
                             h3_tier=key)["params"]
                if p["h3_tier"] != key:
                    continue          # a lab cell this install can't render
                self.assertTrue(p["h3_tristep"])
                self.assertEqual(p["steps"], 4)

    def test_the_dense_single_pass_keeps_its_own_sampler(self):
        self.install()
        p = self.job(h3_length="10s_dense", h3_tier="draft_10s_dense", h3_tristep="1")["params"]
        if p["h3_tier"] == "draft_10s_dense":
            self.assertFalse(p["h3_tristep"])
            self.assertEqual(p["steps"], 16)
        self.assertNotIn("tristep_min", P.H3_TIERS["draft_10s_dense"])

    def test_i2v_drafts_too(self):
        self.install()
        p = self.job(mode="i2v")["params"]
        self.assertTrue(p["h3_tristep"])

    def test_without_the_adapter_the_draft_is_unchanged(self):
        p = self.job()["params"]
        self.assertFalse(p["h3_tristep"])
        self.assertEqual(p["steps"], P.H3_TIERS["draft_5s"]["steps"])

    def test_asked_for_but_missing_says_so(self):
        p = self.job(h3_tristep="1")["params"]
        self.assertFalse(p["h3_tristep"])
        self.assertTrue(any("Fast (3 steps) requested" in m for m in self.logs))

    def test_the_eight_forward_draft_stays_reachable(self):
        self.install()
        p = self.job(h3_tristep="0")["params"]
        self.assertFalse(p["h3_tristep"])
        self.assertEqual(p["steps"], 9)
        # A Steps pin with no explicit choice keeps the standard sampler too.
        p = self.job(h3_steps="12")["params"]
        self.assertFalse(p["h3_tristep"])
        self.assertEqual(p["steps"], 12)

    def test_explicit_three_step_wins_over_a_leftover_steps_pin(self):
        self.install()
        p = self.job(h3_tristep="1", h3_steps="12")["params"]
        self.assertTrue(p["h3_tristep"])
        self.assertEqual((p["steps"], p["h3_steps"]), (4, 0))

    def test_standard_and_high_default_to_fast_and_best_is_one_field_away(self):
        self.install()
        for q in ("standard", "high"):
            with self.subTest(quality=q):
                p = self.job(h3_quality=q)["params"]
                self.assertTrue(p["h3_tristep"])
                self.assertEqual(p["steps"], 4)
                p = self.job(h3_quality=q, h3_tristep="1", mode="i2v")["params"]
                self.assertTrue(p["h3_tristep"])
                p = self.job(h3_quality=q, h3_length="10s", h3_tristep="1")["params"]
                self.assertTrue(p["h3_tristep"])              # chained 5 s windows
                p = self.job(h3_quality=q, h3_tristep="0")["params"]  # Best
                self.assertFalse(p["h3_tristep"])
                self.assertEqual(p["steps"], P.H3_TIERS[f"{q}_5s"]["steps"])

    def test_never_on_native(self):
        self.install()
        p = self.job(h3_quality="native", h3_tristep="1")["params"]
        self.assertFalse(p["h3_tristep"])
        self.assertEqual(p["steps"], P.H3_TIERS["native_5s"]["steps"])
        self.assertTrue(any("Draft, Standard and High" in m for m in self.logs))
        self.assertFalse(self.job(h3_quality="native")["params"]["h3_tristep"])

    def test_turbo_is_never_stacked_on_it(self):
        self.install()
        with unittest.mock.patch.object(P, "h3_turbo_status",
                                        lambda: {"available": True, "supported": True,
                                                 "missing": []}):
            p = self.job(h3_turbo="1")["params"]
            self.assertTrue(p["h3_tristep"])
            self.assertFalse(p["h3_turbo"])
            self.assertEqual(p["steps"], 4)
            self.assertTrue(any("Turbo is off" in m for m in self.logs))
            # High too: Fast replaces Turbo; Best (h3_tristep=0) keeps an API
            # caller's Turbo; Native keeps Turbo (Fast never runs there).
            p = self.job(h3_turbo="1", h3_quality="high")["params"]
            self.assertFalse(p["h3_turbo"])
            self.assertTrue(p["h3_tristep"])
            p = self.job(h3_turbo="1", h3_quality="high", h3_tristep="0")["params"]
            self.assertTrue(p["h3_turbo"])
            self.assertFalse(p["h3_tristep"])
            p = self.job(h3_turbo="1", h3_quality="native")["params"]
            self.assertTrue(p["h3_turbo"])
            self.assertFalse(p["h3_tristep"])
            # Picking Turbo on a Draft (h3_tristep=0) renders Turbo.
            p = self.job(h3_turbo="1", h3_tristep="0")["params"]
            self.assertTrue(p["h3_turbo"])
            self.assertFalse(p["h3_tristep"])

    def test_ltx_jobs_never_carry_it(self):
        self.install()
        p = P.make_job({"mode": ["t2v"], "engine": ["ltx"], "prompt": ["x"],
                        "h3_tristep": ["1"]})["params"]
        self.assertFalse(p["h3_tristep"])


class TestEstimates(unittest.TestCase):
    def test_draft_cells_are_priced(self):
        cell = P.H3_TIERS["draft_5s"]
        self.assertEqual(cell["tristep_min_i2v"], 3.0)
        self.assertEqual(cell["tristep_eta_i2v"], "~3 min")
        self.assertTrue(cell["tristep_measured_i2v"])
        # No T2V receipt at 640×384: the model prices it (and agrees).
        self.assertFalse(cell["tristep_measured"])
        self.assertEqual(cell["tristep_min"], round(P.h3_estimate_minutes(640, 384, 124, 1, 3), 2))
        self.assertEqual(cell["tristep_eta"], "~3 min")
        self.assertEqual(cell["tristep_forwards"], 3)
        self.assertEqual(cell["facefix_min"], P.FACE_FIX_DRAFT_5S_MIN)
        self.assertAlmostEqual(cell["tristep_min_i2v"] + cell["facefix_min"], 5.5)
        self.assertLess(cell["tristep_min"], cell["eta_min"])
        short = P.H3_TIERS["draft_3s"]
        self.assertFalse(short["tristep_measured"])
        self.assertLess(short["tristep_min"], short["eta_min"])
        self.assertEqual(short["tristep_min"], round(P.h3_estimate_minutes(640, 384, 73, 1, 3), 2))
        self.assertEqual(P.H3_TIERS["draft_10s"]["tristep_forwards"], 6)
        self.assertTrue(cell["tristep_default"])

    def test_standard_and_high_are_priced_from_their_receipts(self):
        std, high = P.H3_TIERS["standard_5s"], P.H3_TIERS["high_5s"]
        self.assertEqual((std["tristep_min"], std["tristep_eta"]), (4.9, "~5 min"))
        self.assertEqual((std["tristep_min_i2v"], std["tristep_eta_i2v"]), (4.8, "~5 min"))
        self.assertEqual((high["tristep_min"], high["tristep_eta"]), (8.4, "~8 min"))
        self.assertEqual((high["tristep_min_i2v"], high["tristep_eta_i2v"]), (9.0, "~9 min"))
        for c in (std, high):
            self.assertTrue(c["tristep_measured"] and c["tristep_measured_i2v"])
            self.assertTrue(c["tristep_default"])
        self.assertEqual(std["facefix_min"], 4.0)
        self.assertEqual(high["facefix_min"], 9.0)
        self.assertLess(high["tristep_min_i2v"], high["eta_min"] / 3)
        self.assertIn("tristep_min", P.H3_TIERS["high_15s"])
        self.assertNotIn("tristep_min", P.H3_TIERS["native_5s"])
        self.assertNotIn("tristep_min", P.H3_TIERS["high_10s_dense"])

    def test_other_canvases_are_not(self):
        for key, cell in P.H3_TIERS.items():
            if P.h3_cell_takes_tristep(cell):
                continue
            with self.subTest(cell=key):
                self.assertNotIn("tristep_min", cell)
                self.assertNotIn("facefix_min", cell)

    def test_the_face_fix_note_prices_the_draft(self):
        P._H3_EXPORT_NOTES.clear()
        self.assertIn(f"About {P.FACE_FIX_DRAFT_5S_MIN:g} min more",
                      P._h3_export_notes(640, 384)["ltx_x2"])
        self.assertIn("draft's time again", P._h3_export_notes(768, 448)["ltx_x2"])

    def test_storyboard_prices_a_draft_at_three_steps_when_installed(self):
        with unittest.mock.patch.object(P, "h3_tristep_status", lambda: {"available": True}), \
                unittest.mock.patch.object(P, "h3_turbo_status", lambda: {"available": False}):
            self.assertEqual(P._sb_h3_cost("draft", "5s"),
                             P.H3_TIERS["draft_5s"]["tristep_min"] * 60.0)
            self.assertEqual(P._sb_h3_cost("high", "5s"),
                             P.H3_TIERS["high_5s"]["tristep_min"] * 60.0)
            self.assertEqual(P._sb_h3_cost("native", "5s"),
                             P.H3_TIERS["native_5s"]["eta_min"] * 60.0)
        with unittest.mock.patch.object(P, "h3_tristep_status", lambda: {"available": False}), \
                unittest.mock.patch.object(P, "h3_turbo_status", lambda: {"available": False}):
            self.assertEqual(P._sb_h3_cost("draft", "5s"),
                             P.H3_TIERS["draft_5s"]["eta_min"] * 60.0)


class TestRenderArgv(_Env):
    """The runner argv, intercepted at Popen."""

    def dispatch(self, params: dict) -> tuple[list[str], dict]:
        seen = {}

        class Intercept(Exception):
            pass

        def popen(cmd, **kw):
            seen["cmd"] = cmd
            raise Intercept()

        job = {"id": "tri-1", "params": {
            "engine": "h3", "mode": "t2v", "prompt": "a dancer", "seed": "7",
            "h3_tier": "draft_5s", "h3_turbo": False, **params}}
        paths = dict(missing=[], repairable=False, dit=_TMP / "dit", python=sys.executable,
                     runner=_TMP / "gen.py", compact_root=_TMP / "c", text_config=_TMP / "t")
        ff = _TMP / "ffmpeg"
        ff.write_text("")
        with ExitStack() as st:
            for name, val in {
                    "h3_paths": lambda: paths, "h3_capable": lambda: True,
                    "h3_lora_max_stack": lambda: 4,
                    "h3_dit_choice": lambda: ("bf16", None),
                    "output_codec_settings": lambda: {"crf": "18", "pix_fmt": "yuv420p"},
                    "h3_tae_checkpoint": lambda: _TMP / "tae.safetensors",
                    "FFMPEG": ff}.items():
                st.enter_context(unittest.mock.patch.object(P, name, val))
            for name in ("h3_supports_memory_limits", "h3_supports_prompt_cache",
                         "h3_live_preview_ready", "h3_supports_stage_a"):
                st.enter_context(unittest.mock.patch.object(P, name, lambda: False))
            st.enter_context(unittest.mock.patch.object(P, "h3_supports_tae_draft", lambda: True))
            st.enter_context(unittest.mock.patch.object(P.HELPER, "is_alive", lambda: False))
            st.enter_context(unittest.mock.patch.object(P.subprocess, "Popen", popen))
            with self.assertRaises(Intercept):
                P.run_h3_job_inner(job)
        return seen["cmd"], job["params"]

    @staticmethod
    def loras(cmd):
        return [cmd[i + 1] for i, a in enumerate(cmd) if a == "--lora"]

    def test_three_step_argv(self):
        f = self.install()
        cmd, p = self.dispatch({"h3_tristep": True, "steps": 4})
        self.assertEqual(cmd[cmd.index("--steps") + 1], "4")
        self.assertEqual(cmd[cmd.index("--sigma-subset") + 1], SUBSET)
        self.assertEqual(self.loras(cmd), [f"{f}:1.0"])
        # Full VAE decode — the approved look, and what Face Fix starts from.
        self.assertNotIn("--draft-decode", cmd)
        self.assertTrue(any("Fast, 3 steps (TaoMate adapter" in m for m in self.logs))

    def test_the_eight_forward_draft_argv_is_unchanged(self):
        self.install()
        cmd, _ = self.dispatch({"h3_tristep": False, "steps": 9})
        self.assertEqual(cmd[cmd.index("--steps") + 1], "9")
        self.assertNotIn("--sigma-subset", cmd)
        self.assertEqual(self.loras(cmd), [])
        self.assertIn("--draft-decode", cmd)

    def test_a_hand_edited_job_never_stacks_turbo(self):
        f = self.install()
        (self.adir / P.H3_TURBO_V4_FILE).write_bytes(b"turbo fixture")
        cmd, p = self.dispatch({"h3_tristep": True, "h3_turbo": True, "steps": 4})
        self.assertEqual(self.loras(cmd), [f"{f}:1.0"])
        self.assertFalse(p["h3_turbo"])
        self.assertEqual(cmd[cmd.index("--steps") + 1], "4")

    def test_high_i2v_takes_it_when_asked(self):
        f = self.install()
        img = _TMP / "frame.png"
        from PIL import Image
        Image.new("RGB", (1024, 576)).save(img)
        with unittest.mock.patch.object(P, "h3_supports_first_frame", lambda: True):
            cmd, p = self.dispatch({"h3_tier": "high_5s", "h3_tristep": True, "steps": 4,
                                    "mode": "i2v", "image": str(img)})
        self.assertEqual(cmd[cmd.index("--steps") + 1], "4")
        self.assertEqual(cmd[cmd.index("--sigma-subset") + 1], SUBSET)
        self.assertEqual(self.loras(cmd), [f"{f}:1.0"])
        self.assertIn("--first-frame", cmd)
        self.assertEqual(cmd[cmd.index("--width") + 1], "1024")

    def test_native_drops_it(self):
        self.install()
        cmd, p = self.dispatch({"h3_tier": "native_5s", "h3_tristep": True, "steps": 9})
        self.assertNotIn("--sigma-subset", cmd)
        self.assertFalse(p["h3_tristep"])
        self.assertEqual(cmd[cmd.index("--steps") + 1], "9")

    def test_a_vanished_adapter_fails_with_the_remedy(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.dispatch({"h3_tristep": True, "steps": 4})
        self.assertIn("pick Best under Speed", str(ctx.exception))

    def test_it_stacks_with_character_and_style_loras(self):
        f = self.install()
        ldir = Path(tempfile.mkdtemp(prefix="h3loras-", dir=_TMP))
        users = []
        for name in ("bizarro", "vhs"):
            u = ldir / f"{name}.safetensors"
            _safetensors(u, {"blocks.1.attn.qkv_proj.lora_A.weight": ((4, 3), [1.0] * 12),
                             "blocks.1.attn.qkv_proj.lora_B.weight": ((4, 4), [1.0] * 16)})
            u.with_suffix(".json").write_text(json.dumps(
                {"name": name, "kind": "import", "lora_layout": "bare",
                 "recommended_strength": 1.0}))
            users.append(u)
        import mlx.core as mx
        mx.set_default_device(mx.cpu)
        with unittest.mock.patch.object(P, "_h3_loras_dir", lambda: ldir):
            P._H3_LORA_LAYOUT_CACHE.clear()
            cmd, _ = self.dispatch({"h3_tristep": True, "steps": 4, "mode": "t2v",
                                    "loras": [{"path": str(users[0]), "strength": 1.0},
                                              {"path": str(users[1]), "strength": 0.8}]})
        got = self.loras(cmd)
        self.assertEqual(got[0], f"{f}:1.0")          # the adapter first
        self.assertEqual(len(got), 3)
        self.assertEqual([float(s.rsplit(":", 1)[1]) for s in got[1:]], [1.0, 0.8])


class _FakeResp(io.BytesIO):
    def __init__(self, data: bytes, status: int):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class TestDownload(unittest.TestCase):
    PAYLOAD = bytes(range(256)) * 40         # 10,240 bytes

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="tri-dl-", dir=_TMP))
        self.asset = {"file": "tri.safetensors", "url": "https://example.invalid/tri",
                      "sha256": hashlib.sha256(self.PAYLOAD).hexdigest(),
                      "bytes": len(self.PAYLOAD)}
        self.requests: list[dict] = []
        self.logs: list[str] = []

    def opener(self, honour_range=True):
        def _open(req, timeout=None):
            rng = req.get_header("Range")
            self.requests.append({"range": rng})
            if rng and honour_range:
                start = int(rng.split("=")[1].rstrip("-"))
                return _FakeResp(self.PAYLOAD[start:], 206)
            return _FakeResp(self.PAYLOAD, 200)
        return _open

    def run_dl(self, **kw):
        P._h3_tristep_download_bg(self.dir, self.logs.append, asset=self.asset,
                                  opener=kw.get("opener") or self.opener())

    def test_fresh(self):
        self.run_dl()
        self.assertEqual((self.dir / "tri.safetensors").read_bytes(), self.PAYLOAD)
        self.assertEqual(P._h3_tristep_dl_state["status"], "done")
        self.assertIsNone(self.requests[0]["range"])

    def test_resumes_a_partial(self):
        (self.dir / "tri.safetensors.partial").write_bytes(self.PAYLOAD[:4000])
        self.run_dl()
        self.assertEqual(self.requests[0]["range"], "bytes=4000-")
        self.assertEqual((self.dir / "tri.safetensors").read_bytes(), self.PAYLOAD)
        self.assertFalse((self.dir / "tri.safetensors.partial").exists())

    def test_a_server_that_ignores_range_restarts_cleanly(self):
        (self.dir / "tri.safetensors.partial").write_bytes(self.PAYLOAD[:4000])
        self.run_dl(opener=self.opener(honour_range=False))
        self.assertEqual((self.dir / "tri.safetensors").read_bytes(), self.PAYLOAD)

    def test_a_whole_partial_needs_no_request(self):
        (self.dir / "tri.safetensors.partial").write_bytes(self.PAYLOAD)
        self.run_dl()
        self.assertEqual(self.requests, [])
        self.assertTrue((self.dir / "tri.safetensors").is_file())

    def test_a_corrupt_download_is_refused_and_dropped(self):
        self.asset["sha256"] = "0" * 64
        self.run_dl()
        self.assertFalse((self.dir / "tri.safetensors").exists())
        self.assertFalse((self.dir / "tri.safetensors.partial").exists())
        self.assertEqual(P._h3_tristep_dl_state["status"], "error")
        self.assertIn("checksum", P._h3_tristep_dl_state["error"])

    def test_a_cut_download_keeps_its_partial_for_the_next_press(self):
        def short(req, timeout=None):
            return _FakeResp(self.PAYLOAD[:3000], 200)
        self.run_dl(opener=short)
        self.assertFalse((self.dir / "tri.safetensors").exists())
        self.assertEqual((self.dir / "tri.safetensors.partial").stat().st_size, 3000)
        self.assertEqual(P._h3_tristep_dl_state["status"], "error")
        self.run_dl()
        self.assertEqual((self.dir / "tri.safetensors").read_bytes(), self.PAYLOAD)


class TestInstall(_Env):
    def test_refuses_without_h3_or_with_an_old_runner(self):
        with unittest.mock.patch.object(P, "h3_paths", lambda: {"missing": ["dit"]}):
            r = P._h3_install_tristep(lambda _m: None, download_fn=lambda *a: None)
        self.assertFalse(r["ok"])
        self.assertIn("isn't fully installed", r["error"])
        with unittest.mock.patch.object(P, "h3_paths", lambda: {"missing": []}), \
                unittest.mock.patch.object(P, "_h3_runner_has_flag", lambda flag: flag == "--lora"):
            r = P._h3_install_tristep(lambda _m: None, download_fn=lambda *a: None)
        self.assertFalse(r["ok"])
        self.assertIn("--sigma-subset", r["error"])

    def test_starts_once_and_short_circuits_when_present(self):
        gate, calls = threading.Event(), []

        def blocked(target, push_log):
            calls.append(target)
            gate.wait(timeout=10)

        with unittest.mock.patch.object(P, "h3_paths", lambda: {"missing": []}):
            first = P._h3_install_tristep(lambda _m: None, download_fn=blocked)
            second = P._h3_install_tristep(lambda _m: None, download_fn=blocked)
            gate.set()
            self.assertTrue(first["ok"] and first["started"])
            self.assertEqual(first["sha256"], P.H3_TRISTEP_SHA256)
            self.assertEqual(first["asset"], P.H3_TRISTEP_URL)
            self.assertFalse(second["ok"])
            self.assertIn("active", second["error"])
            P._set_h3_tristep_dl(status="idle", mb=0, total_mb=0, error=None)
            self.install()
            third = P._h3_install_tristep(lambda _m: None, download_fn=blocked)
        self.assertTrue(third["already_installed"])
        self.assertFalse(third["started"])

    def test_the_route_is_registered(self):
        src = (ROOT / "panel" / "routes_image.py").read_text(encoding="utf-8")
        self.assertIn('@post("/h3/tristep/install")', src)
        self.assertIn("P._h3_install_tristep(P.push)", src)


class TestUiContract(unittest.TestCase):
    """The form posts the choice; the pill exists; the modal and Finish know it."""

    def test_markup(self):
        html = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn('name="h3_tristep" id="h3_tristep"', html)
        # ONE switch, two halves, ABOVE the Quality cards; the old Turbo row is gone.
        self.assertIn('data-h3-speed="fast"', html)
        self.assertIn('data-h3-speed="best"', html)
        self.assertLess(html.index('id="h3SpeedRow"'), html.index('id="qualityLabelName"'))
        self.assertNotIn('id="h3TurboGroup"', html)
        self.assertIn("3 steps — about 4× faster, great for drafts and most shots.", html)
        # Upscale & Face Fix beside Generate, bound to the same state.
        self.assertIn('id="h3FaceFixFooter" onchange="setH3FaceFixAfter(this.checked)"', html)
        self.assertLess(html.index('id="h3FaceFixFooter"'), html.index('id="genBtn"'))

    def test_engines_js(self):
        js = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")
        for needle in ("function h3TriStepOn", "tristep_min_i2v", "/h3/tristep/install",
                       "phos_h3_speed", "Install (180 MB)", "facefix_min",
                       "function h3EstimateLine", "function setH3Speed",
                       "if (tbIn) tbIn.value = '0';"):
            self.assertIn(needle, js)

    def test_queue_js(self):
        js = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        self.assertIn("h3_speed: (p.h3_tristep || p.h3_turbo) ? 'fast' : 'best'", js)
        self.assertIn("setH3Speed(h3SpeedOfParams(p))", js)
        self.assertIn("h3EstimateLine()", js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
