"""The captioner must say WHICH Gemma folder is wrong.

Fleet: "Model type gemma4_unified not supported." — 19 events, 4 installs,
five releases. Two folders sit side by side in mlx_models and only one has a
vision tower:

    gemma-3-12b-it-4bit    model_type "gemma3"          the VLM
    gemma4-12b-ltx25-q4    model_type "gemma4_unified"  LTX-2.5's text encoder

mlx_vlm's message names neither the folder that was passed nor the one that
should have been.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).parent


def run_captioner(model_type):
    """Drive the real script far enough to reach the model-type gate."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        gemma = root / "gemma-x"
        gemma.mkdir()
        if model_type is not None:
            (gemma / "config.json").write_text(json.dumps({"model_type": model_type}))
        dataset = root / "ds"
        (dataset / "images").mkdir(parents=True)
        (dataset / "images" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        out = subprocess.run(
            [sys.executable, str(HERE / "caption_with_gemma.py"),
             "--dataset", str(dataset), "--trigger", "xtrn",
             "--gemma-path", str(gemma)],
            capture_output=True, text=True, timeout=120)
        return out.stdout + out.stderr


class TheWrongGemmaIsNamed(unittest.TestCase):

    def test_the_ltx25_text_encoder_is_refused_by_name(self):
        out = run_captioner("gemma4_unified")
        self.assertIn("gemma4_unified", out)
        self.assertIn("no vision tower", out)

    def test_the_message_names_the_folder_that_would_work(self):
        out = run_captioner("gemma4_unified")
        self.assertIn("gemma-3-12b-it-4bit", out)

    def test_a_config_we_cannot_read_does_not_block_the_load(self):
        """Unknown is not wrong. If config.json is missing we let mlx_vlm
        have its say rather than inventing a refusal."""
        out = run_captioner(None)
        self.assertNotIn("no vision tower", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
