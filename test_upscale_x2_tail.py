#!/usr/bin/env python3
"""LTX Upscale ×2 tail flash (2026-09-16) — and the frames it used to drop.

Symptom: every ×2 of a 5 s 1024×576 H3 clip (2048×1152, 121 frames) blew its
last 8 frames to near-white, with a clean horizontal edge 3/4 of the way
down frame 112, on every seed and every source. 3 s sources (65 frames) and
640×384 sources (1280×768) were clean.

Cause: MLX's Metal conv3d addresses its output with 32-bit ints. The VAE
decoder's last stage at that size is 128 × 121 × 288 × 512 = 2.28e9
elements > 2**31, so the tail of the tensor is never written. Upstream only
tiles a decode past an 8 GB MEMORY budget, and this one is 2.3 GB, so it ran
in one pass. The helper now caps the ELEMENT count too.

Second bug, same lane: the frame count rounded DOWN to LTX's 1+8k grid
(124 → 121), and the audio mux used `-shortest`. Now the model renders the
next grid length UP from a copy with the last frame held, and the result is
cut back to the source's exact count and duration.

No GPU: the helper functions are exec'd from source (the helper is a script
with a blocking main loop), the tiling is checked against the vendored
tiler's own tile layout, and the ffmpeg half runs on a tiny synthetic clip."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
HELPER_SRC = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")
INT32 = 2 ** 31

try:
    from ltx_core_mlx.model.video_vae import video_vae as _vv
    from ltx_core_mlx.model.video_vae.tiling import prepare_tiles_for_decoding
    HAVE_LTX = True
except Exception:                                          # noqa: BLE001
    HAVE_LTX = False


def _helper_ns() -> dict:
    tree = ast.parse(HELPER_SRC)
    names = {"_vae_decode_peak_elements", "_vae_decode_max_latent_frames",
             "_int32_safe_decode_tiling", "_decode_tiling_peak_elements",
             "_decode_temporal_config"}
    picked = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in picked} == names
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and any(getattr(t, "id", "") == "_DECODE_MAX_ELEMENTS" for t in n.targets)]
    assert len(consts) == 1
    ns: dict = {}
    exec(compile(ast.Module(body=consts + picked, type_ignores=[]), "helper", "exec"), ns)
    return ns


def _decoder_tensor_sizes(lat_f: int, h: int, w: int) -> list[int]:
    """Every conv input (padded) and output the conv VAE decoder builds,
    walked from VideoDecoder.__init__ / decode (video_vae.py)."""
    sizes = []

    def conv(c_in, c_out, f, hh, ww):
        sizes.append(c_in * (f + 2) * (hh + 2) * (ww + 2))
        sizes.append(c_out * f * hh * ww)

    f, hh, ww = lat_f, h, w
    conv(128, 1024, f, hh, ww)                         # conv_in
    for _ in range(4):
        conv(1024, 1024, f, hh, ww)                    # stage 0
    conv(1024, 4096, f, hh, ww)                        # block 1
    f, hh, ww = 2 * f - 1, 2 * hh, 2 * ww
    for _ in range(4):
        conv(512, 512, f, hh, ww)                      # stage 2
    conv(512, 4096, f, hh, ww)                         # block 3
    f, hh, ww = 2 * f - 1, 2 * hh, 2 * ww
    for _ in range(8):
        conv(512, 512, f, hh, ww)                      # stage 4
    conv(512, 512, f, hh, ww)                          # block 5 (time only)
    f = 2 * f - 1
    for _ in range(12):
        conv(256, 256, f, hh, ww)                      # stage 6
    conv(256, 512, f, hh, ww)                          # block 7 (space only)
    hh, ww = 2 * hh, 2 * ww
    for _ in range(8):
        conv(128, 128, f, hh, ww)                      # stage 8
    conv(128, 48, f, hh, ww)                           # conv_out
    sizes.append(3 * f * hh * 4 * ww * 4)              # unpatchified pixels
    return sizes


class TheWrapIsWhereTheFlashIs(unittest.TestCase):
    def test_the_measured_geometry_crosses_int32_and_the_clean_ones_do_not(self):
        ns = _helper_ns()
        peak = ns["_vae_decode_peak_elements"]
        self.assertGreater(peak(16, 36, 64), INT32)      # 2048×1152 × 121: flashes
        self.assertLess(peak(9, 36, 64), INT32)          # 2048×1152 × 65: clean
        self.assertLess(peak(16, 24, 40), INT32)         # 1280×768 × 121: clean
        # The wrap lands in frame 112/113 — where every bad render breaks.
        at = INT32 / (128 * 288 * 512)
        self.assertTrue(112 < at < 114, at)

    def test_the_peak_bounds_every_decoder_tensor(self):
        ns = _helper_ns()
        for lat_f, h, w in [(16, 36, 64), (17, 36, 64), (9, 36, 64), (16, 24, 40),
                            (13, 18, 32), (31, 45, 80), (2, 36, 64), (1, 12, 20)]:
            self.assertEqual(max(_decoder_tensor_sizes(lat_f, h, w)),
                             ns["_vae_decode_peak_elements"](lat_f, h, w),
                             (lat_f, h, w))

    def test_max_latent_frames_is_the_largest_safe_count(self):
        ns = _helper_ns()
        cap = ns["_DECODE_MAX_ELEMENTS"]
        self.assertLess(cap, INT32)
        for h, w in [(36, 64), (24, 40), (45, 80), (18, 32)]:
            n = ns["_vae_decode_max_latent_frames"](h, w)
            self.assertLessEqual(ns["_vae_decode_peak_elements"](n, h, w), cap)
            self.assertGreater(ns["_vae_decode_peak_elements"](n + 1, h, w), cap)


@unittest.skipUnless(HAVE_LTX, "ltx_core_mlx not importable (run with the repo venv)")
class TheGuardTilesOnlyWhatWouldWrap(unittest.TestCase):
    def _cfg(self, shape, fps=24.0):
        return _helper_ns()["_int32_safe_decode_tiling"](
            _vv._compute_decode_tiling, shape, frame_rate=fps)

    def test_upstream_alone_decodes_the_bad_clip_in_one_pass(self):
        # The bug: the byte budget does not see it.
        os.environ.pop("LTX2_VAE_DECODE_BUDGET_GB", None)
        self.assertIsNone(_vv._compute_decode_tiling((1, 128, 16, 36, 64), frame_rate=24.0))

    def test_small_decodes_keep_upstreams_answer(self):
        os.environ.pop("LTX2_VAE_DECODE_BUDGET_GB", None)
        for shape in [(1, 128, 16, 24, 40), (1, 128, 9, 36, 64), (1, 128, 16, 18, 32)]:
            self.assertIsNone(self._cfg(shape), shape)

    def test_every_tile_stays_under_the_cap_and_the_tiles_cover_the_clip(self):
        os.environ.pop("LTX2_VAE_DECODE_BUDGET_GB", None)
        ns = _helper_ns()
        for f_lat, h, w in [(16, 36, 64), (17, 36, 64), (31, 36, 64), (61, 36, 64),
                            (31, 45, 80)]:
            shape = (1, 128, f_lat, h, w)
            cfg = self._cfg(shape)
            self.assertIsNotNone(cfg, shape)
            tiles = prepare_tiles_for_decoding(shape, cfg)
            covered = set()
            for t in tiles:
                n = t.in_coords[2].stop - t.in_coords[2].start
                self.assertLessEqual(ns["_vae_decode_peak_elements"](n, h, w),
                                     ns["_DECODE_MAX_ELEMENTS"], (shape, n))
                covered.update(range(t.out_coords[2].start, t.out_coords[2].stop))
            self.assertEqual(covered, set(range(8 * f_lat - 7)), shape)

    def test_a_budget_tiling_already_small_enough_is_kept(self):
        os.environ["LTX2_VAE_DECODE_BUDGET_GB"] = "0.5"
        try:
            up = _vv._compute_decode_tiling((1, 128, 16, 36, 64), frame_rate=24.0)
            self.assertEqual(self._cfg((1, 128, 16, 36, 64)).temporal_config.tile_size_in_frames,
                          up.temporal_config.tile_size_in_frames)
        finally:
            os.environ.pop("LTX2_VAE_DECODE_BUDGET_GB", None)


class TheGuardIsWired(unittest.TestCase):
    def test_installed_at_the_generate_choke_point(self):
        i = HELPER_SRC.index("# ---- main loop ----")
        j = HELPER_SRC.index('if action == "generate":', i)
        block = HELPER_SRC[i:j]
        self.assertIn('action.startswith(("generate", "extend"))', block)
        self.assertIn("_install_decode_int32_guard()", block)

    def test_the_installer_replaces_the_module_global_decode_and_stream_reads(self):
        i = HELPER_SRC.index("def _install_decode_int32_guard")
        body = HELPER_SRC[i:i + 2000]
        self.assertIn("_vv._compute_decode_tiling = _guarded", body)
        if HAVE_LTX:
            import inspect
            src = inspect.getsource(_vv.VideoDecoder.decode_and_stream)
            self.assertIn("_compute_decode_tiling(", src)


def _pure_tiling():
    """The vendored tiler's config classes and split functions, exec'd from
    source without its MLX import — so the tile-extent checks below run on any
    machine, against the exact interval code the decoder uses."""
    import dataclasses
    import types
    src_dir = None
    for base in sys.path + [str(ROOT / "ltx-2-mlx" / "env" / "lib" / "python3.11" / "site-packages")]:
        cand = Path(base) / "ltx_core_mlx" / "model" / "video_vae"
        if (cand / "tiling.py").is_file():
            src_dir = cand
            break
    if src_dir is None:
        return None, None
    names = {"SpatialTilingConfig", "TemporalTilingConfig", "TilingConfig",
             "DimensionIntervals", "default_split_operation",
             "split_with_symmetric_overlaps", "split_temporal_latents"}
    tree = ast.parse((src_dir / "tiling.py").read_text(encoding="utf-8"))
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    body += [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
             and n.name in names]
    ns = {"dataclass": dataclasses.dataclass, "replace": dataclasses.replace,
          "field": dataclasses.field}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                 "tiling", "exec"), ns)
    ns["DEFAULT_SPLIT_OPERATION"] = ns["default_split_operation"]
    orig = [n for n in ast.parse((src_dir / "video_vae.py").read_text(encoding="utf-8")).body
            if isinstance(n, ast.FunctionDef) and n.name == "_compute_decode_tiling"]
    ons = {"os": os, "TilingConfig": ns["TilingConfig"],
           "TemporalTilingConfig": ns["TemporalTilingConfig"]}
    exec(compile(ast.Module(body=orig, type_ignores=[]), "orig", "exec"), ons)
    return types.SimpleNamespace(**ns), ons["_compute_decode_tiling"]


# (width, height, latent frames): the review's matrix plus the canvases the
# 4.13.1 temporal-only guard got wrong (2 latent frames + the causal third).
GUARD_CASES = [(2048, 1152, 17), (2688, 1536, 17), (4096, 2304, 17),
               (4096, 4096, 4), (4096, 4096, 17), (4096, 4096, 31),
               (4480, 4480, 17), (6144, 3456, 17), (1280, 768, 16),
               (2048, 1152, 16), (4096, 4096, 1), (4096, 4096, 2), (4096, 4096, 3)]


class TheGuardChecksTheTilesItReturns(unittest.TestCase):
    """Ship review 2026-09-17, P2: `size = max(2, max_lat - 1)` returned
    [2, 3]-latent tiles for a 4096×4096 decode — 2.56e9 elements, past the cap
    the guard exists to keep."""

    def setUp(self):
        self.T, self.orig = _pure_tiling()
        if self.T is None:
            self.skipTest("vendored ltx_core_mlx tiling.py not found")
        os.environ.pop("LTX2_VAE_DECODE_BUDGET_GB", None)
        self.ns = _helper_ns()

    def guard(self, shape):
        return self.ns["_int32_safe_decode_tiling"](self.orig, shape, 24.0, T=self.T)

    def extents(self, cfg, shape):
        """Tile lengths per axis, straight from the tiler's split functions,
        the way prepare_tiles_for_decoding derives them."""
        T = self.T
        _, _, f, h, w = shape
        out = {"t": [(0, f)], "h": [(0, h)], "w": [(0, w)]}
        sc = cfg.spatial_config
        if sc is not None:
            long_side, size, ov = max(h, w), sc.tile_size_in_pixels // 32, sc.tile_overlap_in_pixels // 32
            for axis, n in (("h", h), ("w", w)):
                iv = T.split_with_symmetric_overlaps(
                    max(max(2, ov + 1), round(size * n / long_side)), ov)(n)
                out[axis] = list(zip(iv.starts, iv.ends))
        tc = cfg.temporal_config
        iv = T.split_temporal_latents(tc.tile_size_in_frames // 8, tc.tile_overlap_in_frames // 8)(f)
        out["t"] = list(zip(iv.starts, iv.ends))
        return out

    def test_the_4_13_1_answer_overflowed_on_a_square_4k_decode(self):
        shape = (1, 128, 4, 128, 128)
        max_lat = self.ns["_vae_decode_max_latent_frames"](128, 128)
        self.assertEqual(max_lat, 2)
        old = self.T.TilingConfig(temporal_config=self.T.TemporalTilingConfig(16, 0))
        lengths = [e - s for s, e in self.extents(old, shape)["t"]]
        self.assertEqual(lengths, [2, 3])
        self.assertGreater(self.ns["_vae_decode_peak_elements"](3, 128, 128), INT32)
        self.assertGreater(self.ns["_decode_tiling_peak_elements"](old, shape, self.T), INT32)

    def test_every_returned_tiling_is_under_the_cap_on_every_tile(self):
        cap = self.ns["_DECODE_MAX_ELEMENTS"]
        peak = self.ns["_vae_decode_peak_elements"]
        for w, h, f in GUARD_CASES:
            shape = (1, 128, f, h // 32, w // 32)
            cfg = self.guard(shape)
            if cfg is None:
                self.assertLessEqual(peak(f, h // 32, w // 32), cap, (w, h, f))
                continue
            ext = self.extents(cfg, shape)
            for ts, te in ext["t"]:
                out_frames = 1 + (te - 1) * 8 - ts * 8
                self.assertLessEqual(3 * out_frames * h * w, cap, (w, h, f))  # the blend buffer
                for hs, he in ext["h"]:
                    for ws, we in ext["w"]:
                        self.assertLessEqual(peak(te - ts, he - hs, we - ws), cap,
                                             (w, h, f, (ts, te), (hs, he), (ws, we)))
            # The whole clip and the whole canvas are covered.
            self.assertEqual(ext["t"][0][0], 0)
            self.assertEqual(ext["t"][-1][1], f)
            for axis, n in (("h", h // 32), ("w", w // 32)):
                covered = set()
                for a, b in ext[axis]:
                    covered.update(range(a, b))
                self.assertEqual(covered, set(range(n)), (w, h, f, axis))

    def test_the_canvases_4_13_1_got_right_keep_their_tiles(self):
        # Same answers as the shipped guard where its tiles were blended.
        for (w, h, f), frames in [((2048, 1152, 17), 96), ((2688, 1536, 17), 48)]:
            cfg = self.guard((1, 128, f, h // 32, w // 32))
            self.assertIsNone(cfg.spatial_config)
            self.assertEqual(cfg.temporal_config.tile_size_in_frames, frames)

    def test_unblended_temporal_tiles_give_way_to_blended_spatial_ones(self):
        # 4096×2304: 4.13.1 cut 16-frame temporal tiles with no blend at all.
        cfg = self.guard((1, 128, 17, 72, 128))
        self.assertIsNotNone(cfg.spatial_config)
        self.assertGreaterEqual(cfg.temporal_config.tile_size_in_frames, 32)
        self.assertGreater(cfg.temporal_config.tile_overlap_in_frames, 0)
        self.assertGreater(cfg.spatial_config.tile_overlap_in_pixels, 0)

    def test_only_a_canvas_temporal_tiles_cannot_fit_is_tiled_in_space(self):
        cfg = self.guard((1, 128, 17, 128, 128))
        self.assertIsNotNone(cfg.spatial_config)
        self.assertLess(cfg.spatial_config.tile_size_in_pixels, 4096)
        self.assertGreaterEqual(cfg.temporal_config.tile_size_in_frames, 32)   # blended seams

    def test_a_canvas_no_tiling_can_fit_is_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.guard((1, 128, 17, 256, 256))              # 8192×8192
        self.assertIn("too large", str(ctx.exception))
        # The panel refuses the same canvas BEFORE the render, and admits the
        # ones the helper can tile.
        self.assertFalse(panel.upscale_canvas_decodable(8192, 8192, 129))
        for w, h, f in GUARD_CASES:
            self.assertTrue(panel.upscale_canvas_decodable(w, h, 8 * f - 7), (w, h, f))
        for w, h in [(6144, 6144), (8192, 4608), (7680, 4320)]:
            refused = not panel.upscale_canvas_decodable(w, h, 129)
            try:
                self.guard((1, 128, 17, h // 32, w // 32))
                helper_refused = False
            except RuntimeError:
                helper_refused = True
            if refused:
                self.assertTrue(helper_refused, (w, h))       # never admit what the helper refuses
            if not refused:
                self.assertFalse(helper_refused, (w, h))

    def test_admission_and_the_helper_agree_on_every_length(self):
        # Codex hotfix review #4: a 17-frame clip's shortest groups are 9
        # frames, not 17 — 8192×8192 × 17 tiles fine and must be admitted.
        self.assertEqual(panel._decode_min_group_frames(17), 9)
        self.assertEqual(panel._decode_min_group_frames(129), 17)
        self.assertEqual(panel._decode_min_group_frames(9), 9)
        self.assertEqual(panel._decode_min_group_frames(1), 1)
        self.assertTrue(panel.upscale_canvas_decodable(8192, 8192, 17))
        for w, h in [(8192, 8192), (6144, 6144), (8192, 4608), (9600, 9600),
                     (12800, 7200), (16384, 16384)]:
            for frames in (1, 9, 17, 25, 33, 41, 121, 129, 241):
                f = (frames - 1) // 8 + 1
                try:
                    self.guard((1, 128, f, h // 32, w // 32))
                    helper_ok = True
                except RuntimeError:
                    helper_ok = False
                self.assertEqual(panel.upscale_canvas_decodable(w, h, frames), helper_ok,
                                 (w, h, frames))

    @unittest.skipUnless(HAVE_LTX, "ltx_core_mlx not importable (run with the repo venv)")
    def test_the_real_tiler_cuts_what_the_check_measured(self):
        cap = self.ns["_DECODE_MAX_ELEMENTS"]
        peak = self.ns["_vae_decode_peak_elements"]
        from ltx_core_mlx.model.video_vae import tiling as RT
        for w, h, f in GUARD_CASES:
            shape = (1, 128, f, h // 32, w // 32)
            cfg = self.ns["_int32_safe_decode_tiling"](_vv._compute_decode_tiling, shape, 24.0)
            if cfg is None:
                continue
            real = RT.TilingConfig(
                spatial_config=(RT.SpatialTilingConfig(cfg.spatial_config.tile_size_in_pixels,
                                                       cfg.spatial_config.tile_overlap_in_pixels)
                                if cfg.spatial_config else None),
                temporal_config=RT.TemporalTilingConfig(cfg.temporal_config.tile_size_in_frames,
                                                        cfg.temporal_config.tile_overlap_in_frames))
            worst = 0
            for t in prepare_tiles_for_decoding(shape, real):
                n = [t.in_coords[i].stop - t.in_coords[i].start for i in (2, 3, 4)]
                worst = max(worst, peak(*n))
                o = t.out_coords[2]
                self.assertIsInstance(o.stop, int)          # tiled_decode needs a bounded slice
                self.assertLessEqual(3 * (o.stop - o.start) * h * w, cap)
            self.assertLessEqual(worst, cap, (w, h, f))
            # The pure check never under-counts what the real tiler cuts.
            self.assertLessEqual(worst, self.ns["_decode_tiling_peak_elements"](real, shape, RT))


import mlx_ltx_panel as panel                                        # noqa: E402


class TheFramePlan(unittest.TestCase):
    def test_grid_rounds_up(self):
        g = panel.ltx_grid_frames_up
        self.assertEqual([g(1), g(9), g(10), g(65), g(72), g(73), g(121), g(123), g(124), g(129)],
                         [9, 9, 17, 65, 73, 73, 121, 129, 129, 129])

    def test_the_source_length_is_delivered(self):
        plan = panel.upscale_frame_plan
        # The owner's clip: 124 in, 124 out, rendered on 129. The form's
        # stale 121 does not cut it any more.
        self.assertEqual(plan(124, 121), (124, 129))
        self.assertEqual(plan(124, None), (124, 129))
        self.assertEqual(plan(123, "121"), (123, 129))
        self.assertEqual(plan(73, 121), (73, 73))
        self.assertEqual(plan(72, 121), (72, 73))
        self.assertEqual(plan(121, 121), (121, 121))

    def test_long_clips_are_covered_from_the_start_on_a_grid_that_fits(self):
        plan = panel.upscale_frame_plan
        self.assertEqual(plan(243, 121), (129, 129))
        self.assertEqual(plan(243, 241), (241, 241))
        self.assertEqual(plan(200, 200), (193, 193))
        self.assertEqual(plan(0, 0), (49, 49))


def _ffprobe(path, *entries, stream="v:0"):
    out = subprocess.run(
        [str(panel.FFPROBE), "-v", "error", "-select_streams", stream, "-count_frames",
         "-show_entries", ",".join(entries), "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return (json.loads(out).get("streams") or [{}])[0]


def _rgb_frames(path, w, h):
    raw = subprocess.run([str(panel.FFMPEG), "-v", "error", "-i", str(path),
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    n = w * h * 3
    return [raw[i:i + n] for i in range(0, len(raw), n)]


@unittest.skipUnless(Path(str(panel.FFMPEG)).is_file(), "ffmpeg not installed")
class TheClipGoesInAndComesOutTheSameLength(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="x2tail_"))
        self.src = self.tmp / "src.mp4"
        # 124 frames at 24 fps, H3's 5 s shape, with a 5.2 s soundtrack.
        subprocess.run([str(panel.FFMPEG), "-y", "-v", "error",
                        "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=24",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                        "-frames:v", "124", "-t", "5.2", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(self.src)],
                       check=True, capture_output=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render(self, model_src, frames, name, audio_secs):
        """Stand-in for the helper: the model's clip at ×2, with the model's
        own (discarded) audio, `frames` long."""
        out = self.tmp / name
        subprocess.run([str(panel.FFMPEG), "-y", "-v", "error", "-i", str(model_src),
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-map", "0:v", "-map", "1:a", "-vf", "scale=128:72",
                        "-frames:v", str(frames), "-af", f"atrim=end={audio_secs}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
                       check=True, capture_output=True)
        return out

    def test_hold_tail_is_lossless_and_holds_the_last_frame(self):
        held = self.tmp / "held.mp4"
        subprocess.run(panel._upscale_hold_tail_cmd(str(self.src), str(held), 124, 129),
                       check=True, capture_output=True)
        self.assertEqual(panel._probe_video_frames(str(held)), 129)
        src = _rgb_frames(self.src, 64, 36)
        got = _rgb_frames(held, 64, 36)
        self.assertEqual(len(src), 124)
        self.assertEqual(got[:124], src)
        for i in range(124, 129):
            self.assertEqual(got[i], src[123], i)

    def test_trimmed_render_is_124_frames_with_the_source_sound(self):
        held = self.tmp / "held.mp4"
        subprocess.run(panel._upscale_hold_tail_cmd(str(self.src), str(held), 124, 129),
                       check=True, capture_output=True)
        render = self._render(held, 129, "x2.mp4", 5.375)
        self.assertEqual(panel._probe_video_frames(str(render)), 129)
        with unittest.mock.patch.object(panel, "output_codec_settings",
                                        return_value={"pix_fmt": "yuv444p", "crf": "0",
                                                      "preset": "lossless"}):
            kept = panel._upscale_finish(render, str(self.src), frames=124, fps=24.0,
                                         trim=True)
        self.assertTrue(kept)
        v = _ffprobe(render, "stream=nb_read_frames,width,height,duration")
        self.assertEqual(int(v["nb_read_frames"]), 124)
        self.assertEqual((v["width"], v["height"]), (128, 72))
        self.assertAlmostEqual(float(v["duration"]), 124 / 24, places=2)
        a = _ffprobe(render, "stream=duration,codec_type", stream="a:0")
        self.assertEqual(a.get("codec_type"), "audio")
        self.assertAlmostEqual(float(a["duration"]), 124 / 24, delta=0.03)
        self.assertEqual(len(_rgb_frames(render, 128, 72)), 124)

    def test_short_audio_no_longer_costs_a_frame(self):
        # A 121-frame source whose sound ends early: `-shortest` used to cut
        # the picture to the sound.
        src = self.tmp / "short_audio.mp4"
        subprocess.run([str(panel.FFMPEG), "-y", "-v", "error",
                        "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=24",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                        "-map", "0:v", "-map", "1:a", "-frames:v", "121",
                        "-af", "atrim=end=4.9", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(src)], check=True, capture_output=True)
        self.assertEqual(panel._probe_video_frames(str(src)), 121)
        render = self._render(src, 121, "x2b.mp4", 5.0)
        self.assertEqual(panel._probe_video_frames(str(render)), 121)
        self.assertTrue(panel._upscale_finish(render, str(src), frames=121, fps=24.0,
                                              trim=False))
        self.assertEqual(int(_ffprobe(render, "stream=nb_read_frames")["nb_read_frames"]), 121)
        a = _ffprobe(render, "stream=duration", stream="a:0")
        self.assertAlmostEqual(float(a["duration"]), 121 / 24, delta=0.03)

    def test_the_upscale_lane_feeds_the_held_copy_and_cuts_back(self):
        src = panel.Path(panel.__file__).read_text(encoding="utf-8")
        i = src.index('    if mode == "upscale":')
        j = src.index('    if mode == "ingredients":', i)
        body = src[i:j]
        self.assertIn("upscale_frame_plan(src_frames", body)
        self.assertIn('"video_conditioning": [[model_src, keep]]', body)
        self.assertIn('"source_video": model_src if start_from == "source"', body)
        self.assertIn("trim=frames != out_frames", body)
        self.assertNotIn("-shortest", src[src.index("def _upscale_finish_cmd"):
                                          src.index("def _upscale_finish(")].split('"""', 2)[2])



def _tiny(path, frames, tone):
    cmd = [str(panel.FFMPEG), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=32x32:rate=24"]
    if tone:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-map", "0:v", "-map", "1:a", "-af", f"atrim=end={frames / 24 + 0.3:.4f}",
                "-c:a", "aac"]
    cmd += ["-frames:v", str(frames), "-c:v", "libx264", "-threads", "1",
            "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@unittest.skipUnless(Path(str(panel.FFMPEG)).is_file(), "ffmpeg not installed")
class TheSoundIsTheSourcesOrNone(unittest.TestCase):
    """Ship review 2026-09-17, P2: the ×2 model writes a soundtrack of its own.
    A SILENT source kept it when no trim was needed (9/17/73/121/129 frames)
    and lost it when one was (124) — the result's sound depended on the
    frame count. And a failed probe read as "silent"."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="x2sound_"))
        self.codec = unittest.mock.patch.object(
            panel, "output_codec_settings",
            return_value={"pix_fmt": "yuv420p", "crf": "18", "preset": "medium"})
        self.codec.start()

    def tearDown(self):
        import shutil
        self.codec.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_silent_source_comes_back_silent_at_every_length(self):
        for n in (1, 8, 9, 10, 17, 72, 73, 121, 124, 129, 130, 243):
            src = _tiny(self.tmp / f"src{n}.mp4", n, tone=False)
            deliver, grid = panel.upscale_frame_plan(n, 121)
            model = _tiny(self.tmp / f"model{n}.mp4", grid, tone=True)   # the model's own sound
            self.assertTrue(panel._video_has_audio(str(model)))
            kept = panel._upscale_finish(model, str(src), frames=deliver, fps=24.0,
                                         trim=grid != deliver)
            self.assertFalse(kept, n)
            self.assertFalse(panel._video_has_audio(str(model)), n)
            self.assertEqual(panel._probe_video_frames(str(model)), deliver, n)
            self.assertFalse(list(self.tmp.glob("*.mux.mp4")))

    def test_a_source_with_sound_keeps_it_at_every_length(self):
        for n in (9, 121, 124, 129):
            src = _tiny(self.tmp / f"s{n}.mp4", n, tone=True)
            deliver, grid = panel.upscale_frame_plan(n, 121)
            model = _tiny(self.tmp / f"m{n}.mp4", grid, tone=False)
            self.assertTrue(panel._upscale_finish(model, str(src), frames=deliver, fps=24.0,
                                                  trim=grid != deliver), n)
            self.assertTrue(panel._video_has_audio(str(model)), n)
            self.assertEqual(panel._probe_video_frames(str(model)), deliver, n)

    def test_a_failed_probe_does_not_throw_the_sound_away(self):
        src = _tiny(self.tmp / "s.mp4", 121, tone=True)
        model = _tiny(self.tmp / "m.mp4", 121, tone=True)
        real = panel._probe_audio_state
        with unittest.mock.patch.object(
                panel, "_probe_audio_state",
                side_effect=lambda p: "unknown" if p == str(src) else real(p)):
            self.assertTrue(panel._upscale_finish(model, str(src), frames=121, fps=24.0,
                                                  trim=False))
        self.assertTrue(panel._video_has_audio(str(model)))
        # ...and a silent source behind a failed probe still ends silent.
        silent = _tiny(self.tmp / "q.mp4", 121, tone=False)
        model2 = _tiny(self.tmp / "m2.mp4", 121, tone=True)
        with unittest.mock.patch.object(
                panel, "_probe_audio_state",
                side_effect=lambda p: "unknown" if p == str(silent) else real(p)):
            self.assertFalse(panel._upscale_finish(model2, str(silent), frames=121,
                                                   fps=24.0, trim=False))
        self.assertFalse(panel._video_has_audio(str(model2)))

    def test_the_probe_tells_none_from_unknown(self):
        self.assertEqual(panel._probe_audio_state(str(_tiny(self.tmp / "a.mp4", 9, True))), "audio")
        self.assertEqual(panel._probe_audio_state(str(_tiny(self.tmp / "b.mp4", 9, False))), "none")
        self.assertEqual(panel._probe_audio_state(str(self.tmp / "missing.mp4")), "unknown")
        junk = self.tmp / "junk.mp4"
        junk.write_bytes(b"not a video")
        self.assertEqual(panel._probe_audio_state(str(junk)), "unknown")

    def test_an_unmuxable_source_still_drops_the_models_sound(self):
        src = _tiny(self.tmp / "s.mp4", 121, tone=True)
        model = _tiny(self.tmp / "m.mp4", 121, tone=True)
        broken = self.tmp / "broken.mp4"
        broken.write_bytes(b"x")
        with unittest.mock.patch.object(panel, "_probe_audio_state",
                                        side_effect=lambda p: "audio" if p == str(broken)
                                        else "none"):
            self.assertFalse(panel._upscale_finish(model, str(broken), frames=121,
                                                   fps=24.0, trim=False))
        self.assertFalse(panel._video_has_audio(str(model)))
        self.assertEqual(panel._probe_video_frames(str(model)), 121)

    def test_a_stopped_finish_leaves_no_temp_file_for_the_gallery(self):
        # Codex hotfix review #2: ffmpeg had opened <out>.mux.mp4 when Stop hit.
        src = _tiny(self.tmp / "s.mp4", 9, tone=False)
        model = _tiny(self.tmp / "m.mp4", 9, tone=True)

        def stopped_mid_write(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"half an mp4")
            raise panel.JobCancelled("Stopped during the ×2 finish.")

        with unittest.mock.patch.object(panel, "run_tracked_subprocess", stopped_mid_write):
            with self.assertRaises(panel.JobCancelled):
                panel._upscale_finish(model, str(src), frames=9, fps=24.0, trim=False)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["m.mp4", "s.mp4"])

        def boom(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"x")
            raise OSError("spawn failed")

        with unittest.mock.patch.object(panel, "run_tracked_subprocess", boom):
            with self.assertRaises(OSError):
                panel._upscale_finish(model, str(src), frames=9, fps=24.0, trim=False)
        self.assertFalse(list(self.tmp.glob("*.mux.mp4")))

    def test_stop_ends_the_finish(self):
        src = _tiny(self.tmp / "s.mp4", 9, tone=False)
        model = _tiny(self.tmp / "m.mp4", 9, tone=True)
        before = model.read_bytes()
        with self.assertRaises(panel.JobCancelled):
            panel._upscale_finish(model, str(src), frames=9, fps=24.0, trim=False,
                                  job={"id": "x", "cancel_requested": True})
        self.assertEqual(model.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
