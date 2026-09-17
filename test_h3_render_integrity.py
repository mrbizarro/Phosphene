"""An H3 render that decoded to a flat black clip must never be reported done.

j-1a0b02ef0b0-002 (2026-09-17): an F16 user LoRA overflowed inside the
runner's runtime adapter, every latent went NaN, 124 frames of luma 16 were
written, and the job said "done". Two nets: the runner's own refusal message
(read from metrics) and a panel-side blank-clip check for older runners.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import mlx_ltx_panel as p


def _clip(path: Path, source: str) -> Path:
    subprocess.run(
        [str(p.FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", source,
         "-frames:v", "24", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True)
    return path


class RunnerErrorMessage(unittest.TestCase):
    def test_reads_the_runner_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = Path(tmp) / "m.json"
            m.write_text(json.dumps({
                "status": "error", "error_kind": "nonfinite_latents",
                "error_message": "H3 render failed: the latents became NaN/inf after denoise step 1/3"}))
            self.assertIn("NaN/inf", p._h3_runner_error_message(m))

    def test_silent_on_plain_errors_and_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = Path(tmp) / "m.json"
            m.write_text(json.dumps({"status": "error", "error": "ValueError: x"}))
            self.assertIsNone(p._h3_runner_error_message(m))
            m.write_text(json.dumps({"status": "done", "error_message": "stale"}))
            self.assertIsNone(p._h3_runner_error_message(m))
            self.assertIsNone(p._h3_runner_error_message(Path(tmp) / "missing.json"))


@unittest.skipUnless(Path(str(p.FFMPEG)).is_file(), "ffmpeg not available")
class BlankClip(unittest.TestCase):
    def test_black_clip_is_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = _clip(Path(tmp) / "black.mp4", "color=c=black:s=320x180:r=24")
            self.assertTrue(p._h3_clip_is_blank(clip))

    def test_real_and_dark_clips_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = _clip(Path(tmp) / "src.mp4", "testsrc2=s=320x180:r=24")
            self.assertFalse(p._h3_clip_is_blank(clip))
            # A dim scene with a little movement is a real clip.
            dim = _clip(Path(tmp) / "dim.mp4",
                        "color=c=0x101010:s=320x180:r=24,drawbox=x=100:y=60:w=60:h=60:c=0x404040:t=fill")
            self.assertFalse(p._h3_clip_is_blank(dim))

    def test_unreadable_file_is_not_called_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.mp4"
            bad.write_bytes(b"not a video")
            self.assertFalse(p._h3_clip_is_blank(bad))


class Wiring(unittest.TestCase):
    def test_h3_job_uses_both_nets(self):
        import inspect
        src = inspect.getsource(p.run_h3_job_inner)
        self.assertIn("_h3_runner_error_message(metrics_path)", src)
        self.assertIn("_h3_clip_is_blank(out_path)", src)
        # The blank check must sit before the export pass and the sidecar,
        # or the black clip is already published as done.
        self.assertLess(src.index("_h3_clip_is_blank(out_path)"), src.index("---- export pass: the SAME"))


if __name__ == "__main__":
    unittest.main()
