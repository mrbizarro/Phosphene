"""Install / Repair Hailuo H3 works on the roots the panel uses (Codex INST-09).

The panel and the sidebar honour LTX_H3_ROOT / LTX_H3_MODELS; install_h3.js
cloned, installed and downloaded into the literal defaults, so Repair fixed a
checkout the panel never uses and could fetch ~75 GB of duplicate weights.

Drives the REAL install_h3.js dispatches, one shell per message element as
Pinokio 8.2.0 runs them, in a throwaway app root whose git, uv, venv python
and heavy scripts are recording stubs. Nothing is cloned or downloaded.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DISPATCHES_JS = r"""
const mod = require(process.argv[1]);
const out = [];
for (const st of mod.run) {
  if (st.method !== "shell.run") continue;
  const m = st.params.message;
  for (const d of (Array.isArray(m) ? m : [m])) out.push({d: String(d), env: st.params.env || {}});
}
console.log(JSON.stringify(out));
"""

STUB = """#!/bin/bash
printf '%s|%s|%s' "$(basename "$0")" "$PWD" "${LTX_H3_MODELS:-}" >> "$CALLS"
for a in "$@"; do printf '|%s' "$a" >> "$CALLS"; done
echo >> "$CALLS"
"""


@unittest.skipUnless(shutil.which("node"), "node not on PATH")
class H3InstallRoots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = self.base = Path(self.tmp.name).resolve()
        self.app = base / "app"
        (self.app / "scripts" / "pinokio").mkdir(parents=True)
        for f in ("h3_roots.sh", "h3_venv.sh"):
            shutil.copy(ROOT / "scripts/pinokio" / f, self.app / "scripts/pinokio" / f)
        self.calls = base / "calls.log"
        self.bin = base / "bin"
        self.bin.mkdir()
        for name in ("git", "uv"):
            (self.bin / name).write_text(STUB + {
                # git clone <url> <dest>: make the checkout exist
                "git": 'if [ "$1" = clone ]; then mkdir -p "${@: -1}/.git"; fi\n',
                # uv venv ... <dir>: give it a python that records its calls
                "uv": 'if [ "$1" = venv ]; then d="${@: -1}"; mkdir -p "$d/bin"; '
                      f'cp "{self.bin}/pystub" "$d/bin/python"; fi\n',
            }[name])
        (self.bin / "pystub").write_text(STUB)
        for f in ("h3_preflight.sh", "h3_checkout.sh", "h3_build_q8.sh"):
            (self.app / "scripts/pinokio" / f).write_text(STUB.replace('"$(basename "$0")"', f'"{f}"'))
        for f in self.bin.iterdir():
            f.chmod(0o755)
        out = subprocess.run(["node", "-e", DISPATCHES_JS, str(ROOT / "install_h3.js")],
                             capture_output=True, text=True, errors="replace", check=True)
        self.dispatches = json.loads(out.stdout)

    def tearDown(self):
        self.tmp.cleanup()

    def _install(self, h3_root: str, h3_models: str) -> list[list[str]]:
        for d in self.dispatches:
            cmd = d["d"].replace("{{cwd}}", str(self.app))
            env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", CALLS=str(self.calls),
                       LTX_H3_ROOT=h3_root, LTX_H3_MODELS=h3_models)
            for k, v in d["env"].items():
                env[k] = v.replace("{{cwd}}", str(self.app))
            subprocess.run(["bash", "-c", cmd], cwd=self.app, env=env,
                           capture_output=True, text=True, errors="replace", timeout=30)
        return [line.split("|") for line in self.calls.read_text().splitlines()]

    def test_relocated_roots_with_spaces_and_japanese_are_used_everywhere(self):
        ckout = self.base / "Shared Drive" / "日本語 H3"
        models = self.base / "Shared Drive" / "重み h3"
        calls = self._install(str(ckout), str(models))
        clone = [c for c in calls if c[0] == "git" and c[3] == "clone"]
        self.assertEqual(len(clone), 1, calls)
        self.assertEqual(clone[0][-1], str(ckout))
        in_checkout = [c for c in calls if c[0] in ("python", "uv", "h3_checkout.sh", "h3_build_q8.sh")]
        self.assertTrue(in_checkout)
        for c in in_checkout:
            self.assertEqual(c[1], str(ckout), c)
        dl = [c for c in calls if "scripts/download_selected.py" in c]
        self.assertEqual(dl[0][-2:], ["--root", str(models)])
        turbo = [c for c in calls if any(a.endswith("fetch_h3_turbo.py") for a in c)]
        self.assertEqual(turbo[0][-1], str(models / "models" / "turbo-lora"))
        tae = [c for c in calls if any(a.endswith("h3_fetch_tae.py") for a in c)]
        self.assertEqual(tae[0][-1], str(models / "models" / "tae" / "taeh3.safetensors"))
        build = [c for c in calls if c[0] == "h3_build_q8.sh"]
        self.assertEqual(build[0][2], str(models), "the Q8 build must read the relocated weights")
        self.assertFalse((self.app / "minimax-h3-mlx").exists(), "cloned into the default root")
        self.assertFalse((self.app / "mlx_models").exists(), "wrote into the default weights root")

    def test_relative_overrides_resolve_against_the_app_root(self):
        calls = self._install("engines/h3", "weights/h3")
        clone = [c for c in calls if c[0] == "git" and c[3] == "clone"]
        self.assertEqual(clone[0][-1], str(self.app / "engines/h3"))
        dl = [c for c in calls if "scripts/download_selected.py" in c]
        self.assertEqual(dl[0][-1], str(self.app / "weights/h3"))

    def test_defaults_are_unchanged(self):
        calls = self._install("", "")
        clone = [c for c in calls if c[0] == "git" and c[3] == "clone"]
        self.assertEqual(clone[0][-1], str(self.app / "minimax-h3-mlx"))
        dl = [c for c in calls if "scripts/download_selected.py" in c]
        self.assertEqual(dl[0][-1], str(self.app / "mlx_models/hailuo-h3"))

    FLAT_FILES = ("deepbeep-pruned-bf16/MiniMax-H3-FL2VA-pruned_bf16.safetensors",
                  "ddalcu-q8/text_encoder.safetensors", "ddalcu-q8/video_vae.safetensors",
                  "ddalcu-q8/audio_vae.safetensors",
                  "upstream-meta/FL2VA/text_encoder/config.json")

    def _flat(self, skip=()):
        models = self.base / "flat"
        for rel in self.FLAT_FILES:
            if rel in skip:
                continue
            f = models / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")
        return models

    def test_an_incomplete_flat_tree_stops_the_install_and_names_what_is_missing(self):
        # Codex 4.16.0: the flat branch checked only the DiT, printed "not
        # downloading", and Install/Repair could never supply the rest.
        models = self._flat(skip=("ddalcu-q8/video_vae.safetensors",))
        step = [d for d in self.dispatches if "h3_fetch_weights" in d["d"]]
        self.assertEqual(len(step), 1)
        self.assertLessEqual(len(step[0]["d"]), 350)
        env = dict(os.environ, LTX_H3_ROOT=str(self.base / "ck"), LTX_H3_MODELS=str(models))
        (self.base / "ck").mkdir()
        r = subprocess.run(["bash", "-c", step[0]["d"]], cwd=self.app, env=env,
                           capture_output=True, text=True, errors="replace", timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error:", r.stdout)
        self.assertIn("ddalcu-q8/video_vae.safetensors", r.stdout)
        self.assertNotIn("text_encoder.safetensors", r.stdout.split("missing:")[1].split(" - ")[0])

    def test_an_existing_flat_tree_is_not_duplicated_under_models(self):
        models = self._flat()
        calls = self._install(str(self.base / "ck"), str(models))
        self.assertFalse([c for c in calls if "scripts/download_selected.py" in c])
        turbo = [c for c in calls if any(a.endswith("fetch_h3_turbo.py") for a in c)]
        self.assertEqual(turbo[0][-1], str(models / "turbo-lora"))


if __name__ == "__main__":
    unittest.main()
