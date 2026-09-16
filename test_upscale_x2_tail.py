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
             "_int32_safe_decode_tiling"}
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



if __name__ == "__main__":
    unittest.main()
