"""A 720p / 1080p export of a near-16:9 render fills the frame — no black bars.

THE REPORT (X, 2026-09-24, v4.15.3, M5 Pro 48 GB): "Is it a bug that black
bars appear on the left and right when the resolution is set to 720p?" H3
Image->Video from a photo. H3's Draft canvas is 640x384 (5:3) and Standard
768x448 (12:7); neither is 16:9, and `compute_upscale_plan` fitted them INSIDE
1280x720 and padded the rest: 40 px of black each side on Draft, 23 on
Standard — measured with a luma scan on real exports from this Mac. LTX's
Standard (1280x704) got 8 px bars top and bottom the same way.

Since 4.16.1 a source within EXPORT_FILL_MAX_TRIM (8%) of the target aspect is
scaled to cover the canvas and centre-cropped. Anything further off (square,
4:3) keeps its bars — cutting a third of the picture is worse.

The ffmpeg case runs the REAL filtergraph on a real clip and scans the output's
columns, so it proves the pixels, not the string.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-fill-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402


class TestPlanFillsNearSixteenNine(unittest.TestCase):
    def test_h3_draft_and_standard_fill_720p_and_1080p(self):
        for w, h in ((640, 384), (768, 448)):
            for mode, tw, th in (("fit_720p", 1280, 720), ("fit_1080p", 1920, 1080)):
                plan = P.compute_upscale_plan(w, h, mode)
                self.assertEqual((plan["target_w"], plan["target_h"]), (tw, th))
                self.assertTrue(plan["fill"], (w, h, mode))
                self.assertFalse(plan["pad"], (w, h, mode))
                self.assertNotIn("pad=", plan["vf"])
                self.assertIn("force_original_aspect_ratio=increase", plan["vf"])
                self.assertIn(f"crop={tw}:{th}", plan["vf"])
                self.assertEqual((plan["fit_w"], plan["fit_h"]), (tw, th))
                self.assertGreater(plan["trim_h"], 0)
                self.assertEqual(plan["trim_w"], 0)

    def test_ltx_standard_and_its_portrait_twin_fill_too(self):
        for w, h in ((1280, 704), (704, 1280)):
            plan = P.compute_upscale_plan(w, h, "fit_720p")
            self.assertTrue(plan["fill"])
            self.assertLessEqual(max(plan["trim_w"], plan["trim_h"]), 31)

    def test_exact_16x9_is_still_a_pure_scale(self):
        for w, h in ((1024, 576), (576, 1024)):
            plan = P.compute_upscale_plan(w, h, "fit_720p")
            self.assertFalse(plan["fill"])
            self.assertFalse(plan["pad"])
            self.assertEqual(plan["vf"], f"scale={plan['target_w']}:{plan['target_h']}:flags=lanczos")

    def test_far_from_16x9_keeps_its_bars(self):
        for w, h in ((768, 768), (640, 480)):
            plan = P.compute_upscale_plan(w, h, "fit_720p")
            self.assertTrue(plan["pad"])
            self.assertFalse(plan["fill"])
            self.assertIn("pad=1280:720", plan["vf"])

    def test_the_trim_cap_is_what_separates_them(self):
        self.assertLessEqual(P._export_trim_fraction(640, 384, 1280, 720), P.EXPORT_FILL_MAX_TRIM)
        self.assertGreater(P._export_trim_fraction(640, 480, 1280, 720), P.EXPORT_FILL_MAX_TRIM)

    def test_native_never_shrinks_and_x2_is_untouched(self):
        self.assertIsNone(P.compute_upscale_plan(1344, 768, "fit_720p"))
        x2 = P.compute_upscale_plan(768, 448, "x2")
        self.assertFalse(x2["fill"])
        self.assertEqual(x2["vf"], "scale=1536:896:flags=lanczos")

    def test_export_note_says_trimmed_not_bars(self):
        notes = P._h3_export_notes(768, 448)
        self.assertIn("fills 1280×720", notes["fit_720p"])
        self.assertIn("no bars", notes["fit_720p"])
        self.assertNotIn("px bars", notes["fit_720p"])
        self.assertNotIn("px bars", P._h3_export_notes(640, 384)["fit_1080p"])

    def test_tier_blurbs_no_longer_promise_bars(self):
        for q in P.H3_QUALITIES.values():
            self.assertNotIn("pads bars", q.get("blurb", ""))
            self.assertNotIn("adds thin bars", q.get("blurb", ""))


class TestCodexReviewFindings(unittest.TestCase):
    """One Codex pass on 4.16.1: two places still promised or produced bars."""

    SRC = (ROOT / "mlx_ltx_panel.py").read_text()

    def test_clean_audio_mux_does_not_pad_before_a_fill(self):
        # i2v_clean_audio pads 1280x704 -> 1280x720 at the mux; the export
        # then cover-cropped a frame that already had the bars in it.
        self.assertEqual(P.compute_pad(1280, 704)[2], "pad=1280:720:0:8:color=black")
        block = self.SRC.split("pad_w, pad_h, pad_filter = compute_pad(width, height)")[1][:1200]
        self.assertIn('mode == "i2v_clean_audio"', block)
        self.assertIn('_fill_plan.get("fill")', block)
        self.assertIn("pad_w, pad_h, pad_filter = width, height, None", block)
        self.assertTrue(P.compute_upscale_plan(1280, 704, "fit_720p")["fill"])
        self.assertIsNone(P.compute_upscale_plan(1280, 704, "off"))

    def test_ui_no_longer_promises_no_crop_on_a_fill(self):
        js = (ROOT / "webapp/js/queue.js").read_text()
        html = (ROOT / "webapp/index.html").read_text()
        self.assertNotIn("'720p fit (no crop)'", js)
        self.assertNotIn("'1080p fit (no crop)'", js)
        self.assertIn("up.fill ? 'filled, edges trimmed'", js)
        self.assertNotIn("post-render, no crop", html)
        self.assertNotIn("after the render · no crop", html)
        self.assertNotIn('class="sub">scale + pad<', html)


class TestSharpPathUsesTheSameRule(unittest.TestCase):
    """The Sharp (PiperSR) export runs a standalone script with its own copy."""

    def test_same_cap_and_same_decision(self):
        import upscale_compare_pipersr as S
        self.assertEqual(S.EXPORT_FILL_MAX_TRIM, P.EXPORT_FILL_MAX_TRIM)
        self.assertIn("crop=1280:720", S.fit_filter(1280, 720, 1536, 896))
        self.assertIn("pad=1280:720", S.fit_filter(1280, 720, 768, 768))
        self.assertIn("pad=1280:720", S.fit_filter(1280, 720))   # unknown source: old behaviour
        src = (ROOT / "scripts" / "upscale_compare_pipersr.py").read_text()
        self.assertEqual(src.count('fit_filter(target_w, target_h, int(info["width"]), int(info["height"]))'), 2)


def _ffmpeg() -> str | None:
    f = Path(str(P.FFMPEG))
    return str(f) if f.is_file() else shutil.which("ffmpeg")


@unittest.skipUnless(_ffmpeg(), "ffmpeg not available")
class TestRealPixelsHaveNoBlackColumns(unittest.TestCase):
    def _edge_luma(self, path: Path, w: int, h: int) -> tuple[float, float, float, float]:
        raw = subprocess.run([_ffmpeg(), "-v", "error", "-i", str(path), "-frames:v", "1",
                              "-vf", "format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, timeout=60).stdout
        self.assertEqual(len(raw), w * h)
        col = lambda x: sum(raw[y * w + x] for y in range(h)) / h
        row = lambda y: sum(raw[y * w:(y + 1) * w]) / w
        return col(0), col(w - 1), row(0), row(h - 1)

    def test_h3_draft_canvas_exports_edge_to_edge(self):
        d = Path(tempfile.mkdtemp(prefix="phos-fill-ff-"))
        try:
            src = d / "draft.mp4"
            # A flat mid-grey 640x384 clip stands in for an H3 Draft render.
            subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i",
                            "color=c=0x808080:s=640x384:d=0.5:r=24",
                            "-pix_fmt", "yuv420p", "-c:v", "libx264", str(src)],
                           check=True, timeout=60)
            for mode, (tw, th) in (("fit_720p", (1280, 720)), ("fit_1080p", (1920, 1080))):
                plan = P.compute_upscale_plan(640, 384, mode)
                out = d / f"out_{mode}.mp4"
                subprocess.run([_ffmpeg(), "-v", "error", "-y", "-i", str(src),
                                "-vf", plan["vf"], "-pix_fmt", "yuv420p",
                                "-c:v", "libx264", str(out)], check=True, timeout=60)
                probe = subprocess.run([_ffmpeg().replace("ffmpeg", "ffprobe"), "-v", "error",
                                        "-select_streams", "v:0", "-show_entries",
                                        "stream=width,height", "-of", "csv=p=0", str(out)],
                                       capture_output=True, text=True, timeout=30)
                if probe.returncode == 0 and probe.stdout.strip():
                    self.assertEqual(probe.stdout.strip(), f"{tw},{th}")
                for edge in self._edge_luma(out, tw, th):
                    self.assertGreater(edge, 100, f"{mode}: a black edge survived ({edge})")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
