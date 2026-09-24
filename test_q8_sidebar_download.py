"""The sidebar's Q8 download picks its transport from the registry (Codex INST-06).

With LTX_MODEL_VERSION=ltx23 the script correctly chose the 2.3 pack (`q8`) and
then handed it to the GitHub-release fetcher, which only knows the mirrored 2.5
packs — so the download failed before a byte moved, under a banner blaming the
network. Runs the REAL q8_weights.sh in a throwaway app root whose python,
fetcher and `hf` are recording stubs: nothing is downloaded.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class Q8SidebarTransport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app = self.app = Path(self.tmp.name)
        (app / "scripts" / "pinokio").mkdir(parents=True)
        shutil.copy(ROOT / "required_files.json", app / "required_files.json")
        shutil.copy(ROOT / "scripts/pinokio/q8_weights.sh", app / "scripts/pinokio/q8_weights.sh")
        bindir = app / "ltx-2-mlx" / "env" / "bin"
        bindir.mkdir(parents=True)
        self.log = app / "calls.log"
        (bindir / "python3.11").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        (bindir / "hf").write_text(
            '#!/bin/sh\nprintf "hf" >> calls.log\n'
            'for a in "$@"; do printf " [%s]" "$a" >> calls.log; done\necho >> calls.log\n')
        (app / "scripts" / "fetch_pack_release.py").write_text(
            "import sys\nopen('calls.log','a').write('release ' + ' '.join(sys.argv[1:]) + '\\n')\n")
        for f in bindir.iterdir():
            f.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, version: str) -> str:
        env = {k: v for k, v in os.environ.items() if k != "LTX_MODEL_VERSION"}
        env["LTX_MODEL_VERSION"] = version
        r = subprocess.run(["bash", "scripts/pinokio/q8_weights.sh"], cwd=self.app, env=env,
                           capture_output=True, text=True, errors="replace", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return self.log.read_text()

    def test_ltx23_q8_goes_through_hf_with_the_registry_allowlist(self):
        calls = self._run("ltx23")
        repo = next(r for r in json.loads((ROOT / "required_files.json").read_text())["repos"]
                    if r["key"] == "q8")
        self.assertNotIn("release", calls)
        self.assertTrue(calls.startswith(f"hf [download] [{repo['repo_id']}] "
                                         f"[--local-dir] [{repo['local_dir']}]"), calls)
        for pat in repo["download_include"]:
            self.assertIn(f"[--include] [{pat}]", calls)

    def test_ltx25_q8_still_uses_the_release_mirror(self):
        calls = self._run("ltx25")
        self.assertEqual(calls.strip(), "release --repo-key q8_25")


if __name__ == "__main__":
    unittest.main()
