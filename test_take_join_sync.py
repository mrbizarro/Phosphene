"""The one-shot join keeps the sound locked to the picture.

Each rendered part's audio runs a few hundredths of a second longer than its
frames; the old concat-demuxer join summed those, so a five-part take ended
with the voice ~130 ms behind the lips. `_join_take_parts` trims every part's
audio to its own frame count first. Synthetic parts: 241 frames of picture,
10.10 s of tone — the join must come out with audio == video."""
import os, shutil, subprocess, tempfile, unittest
from pathlib import Path

import mlx_ltx_panel as P

FF = shutil.which("ffmpeg") or str(getattr(P, "FFMPEG", "ffmpeg"))
FP = shutil.which("ffprobe") or str(getattr(P, "FFPROBE", "ffprobe"))


def _dur(path, kind):
    out = subprocess.run([FP, "-v", "error", "-select_streams", f"{kind}:0", "-show_entries",
                          "stream=duration", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return float(out.strip().split("\n")[0].strip(","))


@unittest.skipUnless(shutil.which("ffmpeg") or os.path.exists(str(getattr(P, "FFMPEG", ""))), "ffmpeg needed")
class TakeJoinKeepsSync(unittest.TestCase):
    def test_audio_matches_video_after_join(self):
        with tempfile.TemporaryDirectory() as td:
            parts = []
            for k in range(5):
                p = os.path.join(td, f"part{k + 1}.mp4")
                # 241 frames @24 = 10.0417 s of picture; 10.10 s of sound (model-style excess)
                vid, aud = os.path.join(td, f"v{k}.mp4"), os.path.join(td, f"a{k}.wav")
                subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24",
                                "-frames:v", "241", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                                "-pix_fmt", "yuv420p", vid], check=True)
                subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                                "-t", "10.10", "-c:a", "pcm_s16le", aud], check=True)
                subprocess.run([FF, "-v", "error", "-y", "-i", vid, "-i", aud, "-map", "0:v", "-map", "1:a",
                                "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", p], check=True)
                parts.append(p)
            excess = sum(_dur(p, "a") - _dur(p, "v") for p in parts)
            self.assertGreater(excess, 0.15, "the synthetic parts must carry the audio excess the model produces")
            final = Path(td) / "take.mp4"
            P._join_take_parts(FF, parts, final)
            v, a = _dur(str(final), "v"), _dur(str(final), "a")
            self.assertAlmostEqual(v, 5 * 241 / 24.0, delta=0.05)
            self.assertLess(abs(a - v), 0.03, f"audio {a:.3f}s vs video {v:.3f}s — drift survived the join")


if __name__ == "__main__":
    unittest.main()
