#!/usr/bin/env python3
"""An a2v dub shorter than its clip is padded with silence, not crashed.

Fleet 4.12.3: a 22.7 s track under a 553-frame (23.04 s) clip died with
"[broadcast_shapes] Shapes (1,32,567,32) and (1,32,576,32)" — the frame count
rounds UP to the 8k+1 grid, so the track ran out first. The helper now pads it.
The helper cannot be imported (it reads stdin at module level), so the function
is lifted out of its source.
"""
from __future__ import annotations

import ast
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

try:
    from ltx_core_mlx.utils.ffmpeg import find_ffmpeg, find_ffprobe
    FF, FP = find_ffmpeg(), find_ffprobe()
except Exception:                                                    # noqa: BLE001
    FF = FP = None


def _load_fn():
    tree = ast.parse((ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_a2v_pad_audio_to")
    ns: dict = {"os": os}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "mlx_warm_helper.py", "exec"), ns)
    return ns["_a2v_pad_audio_to"]


def _dur(path: str) -> float:
    out = subprocess.run([FP, "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return float(out.strip().splitlines()[0].strip(","))


@unittest.skipUnless(FF and FP, "the vendored engine's ffmpeg is needed")
class ShortTrackIsPadded(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pad = staticmethod(_load_fn())
        cls.dir = tempfile.mkdtemp(prefix="phos-a2v-")
        cls.track = os.path.join(cls.dir, "track.wav")
        subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=330:sample_rate=48000", "-t", "22.68",
                        cls.track], check=True)

    def test_the_fleet_case_is_padded_to_the_clip(self):
        need = 553 / 24.0
        path, padded = self.pad(self.track, need)
        self.assertTrue(padded)
        self.assertNotEqual(path, self.track)
        self.assertAlmostEqual(_dur(path), need, delta=0.03)

    def test_a_long_enough_track_is_left_alone(self):
        path, padded = self.pad(self.track, 20.0)
        self.assertFalse(padded)
        self.assertEqual(path, self.track)

    def test_a_start_offset_counts_against_the_track(self):
        path, padded = self.pad(self.track, 21.0, 2.0)
        self.assertTrue(padded)
        self.assertAlmostEqual(_dur(path), 21.0, delta=0.03)

    def test_an_unreadable_file_is_never_a_new_failure(self):
        path, padded = self.pad(os.path.join(self.dir, "missing.wav"), 5.0)
        self.assertFalse(padded)


if __name__ == "__main__":
    unittest.main()
