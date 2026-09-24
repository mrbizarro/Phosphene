"""A 48 GB Mac can install and run Hailuo H3 — and every surface says so.

THE REPORT (X, 2026-09-24). A user bought a Mac for Phosphene, a 48 GB one,
and wrote: "Since it requires 64 GB, I couldn't even install it [H3] from the
UI." Every gate on public v4.15.2 would have let that Mac install and render
H3 (pinokio.js 36e9, the preflight 36e9, the panel's Q8 floor 36). What
stopped them was text and a missing button:

  1. `size_note` — the line under the H3 install card and the Models row —
     said "~75 GB · needs 64 GB unified memory" from v3.4.0 to v4.15.2. No
     gate in the codebase was ever 64. Every OTHER sentence had been moved to
     the real floors; this one was a literal nobody grepped.
  2. The install card says "open the Phosphene entry in the Pinokio sidebar
     and click Install Hailuo H3". While the panel runs — which is when anyone
     reads that card — pinokio.js returned the Open Panel menu, which had no
     H3 entry at all. The button existed only with the panel stopped.
  3. The Settings card and the switcher note for this band said "no extra
     download" and "isn't built yet" to a Mac that had never installed H3.
  4. A Mac that HAD the weights but not the local Q8 build was told to click
     "Install Hailuo H3" — an entry pinokio.js hides once the pack is complete.

Every assertion below is keyed to one of those.
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
STATE = Path(tempfile.mkdtemp(prefix="phos-48gb-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

GIB48 = 51539607552          # hw.memsize / os.totalmem() of a 48 GB Mac
GIB32 = 34359738368
GIB64 = 68719476736
PANEL_JS = "\n".join(p.read_text() for p in sorted((ROOT / "webapp/js").glob("*.js")))
PINOKIO_JS = (ROOT / "pinokio.js").read_text()


class TestPanelTellsA48GBMacTheTruth(unittest.TestCase):
    def setUp(self):
        P.h3_status_invalidate()
        self.saved = (P.SYSTEM_RAM_GB, P._h3_q8_dit_dir, P.h3_paths)
        P.SYSTEM_RAM_GB = GIB48 / 1024 ** 3
        P._h3_q8_dit_dir = lambda: None

    def tearDown(self):
        P.SYSTEM_RAM_GB, P._h3_q8_dit_dir, P.h3_paths = self.saved
        P.h3_status_invalidate()

    def test_size_note_states_the_real_floor(self):
        note = P.h3_status()["size_note"]
        self.assertNotIn("64", note)
        self.assertIn(f"{P.H3_MIN_RAM_GB_Q8:.0f} GB+", note)
        self.assertIn(f"{P.H3_MIN_RAM_GB:.0f} GB+", note)
        self.assertNotIn("needs 64 GB unified memory", (ROOT / "mlx_ltx_panel.py").read_text())

    def _paths(self, missing, weights_ok):
        real = self.saved[2]
        def fake():
            d = dict(real())
            d.update(missing=list(missing), weights_ok=weights_ok)
            return d
        P.h3_paths = fake

    def test_never_installed_is_an_install_offer_not_a_build_step(self):
        self._paths(["runner x", "pruned bf16 DiT"], weights_ok=False)
        s = P.h3_status()
        self.assertTrue(s["capable"], "the switcher must still draw the offer")
        self.assertFalse(s["available"])
        self.assertEqual(s["ram_lane"], "q8")
        note = s["ram_note"]
        self.assertIn(P.H3_INSTALL_MENU_TEXT, note)
        self.assertIn("~75 GB", note)
        self.assertNotIn("no extra download", note)
        self.assertNotIn("64", note)
        # The refusal series must not fall back into `other`.
        self.assertEqual(P._analytics_refusal_reason(note), "h3_ram")

    def test_weights_on_disk_names_the_build_entry_that_exists(self):
        self._paths([], weights_ok=True)
        note = P.h3_ram_verdict()["message"]
        self.assertIn(P.H3_BUILD_MENU_TEXT, note)
        self.assertIn("no extra download", note)
        self.assertNotIn(P.H3_INSTALL_MENU_TEXT, note)
        self.assertEqual(P._analytics_refusal_reason(note), "h3_ram")

    def test_weights_on_disk_but_broken_engine_names_repair(self):
        # Codex review 4.15.3: the build sentence named an entry pinokio.js
        # does not show when h3_ready is false. The sentence follows the menu.
        self._paths(["venv python under /x/.venv"], weights_ok=True)
        note = P.h3_ram_verdict()["message"]
        self.assertIn(P.H3_REPAIR_MENU_TEXT, note)
        self.assertNotIn(P.H3_BUILD_MENU_TEXT, note)
        self.assertEqual(P._analytics_refusal_reason(note), "h3_ram")

    def test_the_panel_quotes_the_sidebar_verbatim(self):
        self.assertIn(f'text: "{P.H3_INSTALL_MENU_TEXT}"', PINOKIO_JS)
        self.assertIn(f'text: "{P.H3_BUILD_MENU_TEXT}"', PINOKIO_JS)
        self.assertIn(f'text: "{P.H3_REPAIR_MENU_TEXT}"', PINOKIO_JS)
        self.assertIn(P.H3_INSTALL_MENU_TEXT, PANEL_JS)

    def test_settings_card_splits_the_band(self):
        branch = PANEL_JS.split("if (h3s.needs_q8_dit) {")[1][:1600]
        self.assertIn("h3s.reason === 'missing_q8_dit'", branch)
        self.assertIn("How to install H3", branch)

    def test_install_card_says_this_mac_runs_it_and_where_the_button_is(self):
        self.assertIn("H3.ram_lane === 'q8'", PANEL_JS)
        self.assertIn("while the panel is running", PANEL_JS)


def _menu(root: Path, totalmem: int, running: bool) -> list[str]:
    probe = (
        "const os=require('os');os.totalmem=()=>Number(process.argv[2]);"
        "const path=require('path'),fs=require('fs');const root=process.argv[1];"
        "const running=process.argv[3]==='1';"
        "const mod=require(path.join(root,'pinokio.js'));"
        "const info={path:(...a)=>path.join(root,...a),exists:p=>fs.existsSync(path.join(root,p)),"
        "running:s=>running&&s==='start.js',local:s=>s==='start.js'?{url:'http://127.0.0.1:1'}:{}};"
        "mod.menu({},info).then(m=>console.log(JSON.stringify(m.map(i=>i.text))))"
    )
    out = subprocess.run(["node", "-e", probe, str(root), str(totalmem), "1" if running else "0"],
                         capture_output=True, text=True, errors="replace", timeout=30)
    if out.returncode != 0:
        raise AssertionError(out.stderr[-800:])
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "node not on PATH")
class TestSidebarOffersH3WhereTheCardSaysItIs(unittest.TestCase):
    """A fake INSTALLED app root: base models present, H3 in three states."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="phos-menu-"))
        shutil.copy(ROOT / "pinokio.js", self.root / "pinokio.js")
        shutil.copy(ROOT / "required_files.json", self.root / "required_files.json")
        req = json.loads((ROOT / "required_files.json").read_text())
        minb = req.get("min_size_bytes", 1024)

        def touch(rel, size=minb):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as f:
                f.truncate(size)

        touch("ltx-2-mlx/env/pyvenv.cfg", 10)
        touch("ltx-2-mlx/env/bin/python3.11", 10)
        (self.root / "ltx-2-mlx/env/lib/python3.11/site-packages/ltx_pipelines_mlx").mkdir(parents=True)
        render = req["capabilities"]["render"]
        for key in render["repos_by_version"][render["default_version"]]:
            repo = next(r for r in req["repos"] if r["key"] == key)
            for f in repo.get("files", []):
                touch(f"{repo['local_dir']}/{f}")
        self.touch = touch
        self.h3 = req["capabilities"]["h3"]

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _install_h3(self, q8: bool):
        self.touch(self.h3["paths"][0], 10)
        self.touch(self.h3["venv_any"][0], 10)
        root = "mlx_models/hailuo-h3"
        for rel in self.h3["models"]:
            self.touch(f"{root}/{rel}", 10)
        if q8:
            for n in ("config.json", "quant_config.json", "model-00001-of-00002.safetensors"):
                self.touch(f"{root}/h3-dit-q8/{n}", 10)

    def test_48gb_fresh_install_is_offered_with_the_panel_running(self):
        items = _menu(self.root, GIB48, running=True)
        self.assertIn("Open Panel", items)
        self.assertIn(P.H3_INSTALL_MENU_TEXT, items)

    def test_48gb_fresh_install_is_offered_with_the_panel_stopped(self):
        items = _menu(self.root, GIB48, running=False)
        self.assertIn("Start", items)
        self.assertIn(P.H3_INSTALL_MENU_TEXT, items)

    def test_48gb_weights_without_q8_build_gets_the_build_entry(self):
        self._install_h3(q8=False)
        for running in (True, False):
            items = _menu(self.root, GIB48, running=running)
            self.assertIn(P.H3_BUILD_MENU_TEXT, items, running)
            self.assertNotIn(P.H3_INSTALL_MENU_TEXT, items)

    def test_48gb_complete_install_has_nothing_to_offer_while_running(self):
        self._install_h3(q8=True)
        running = _menu(self.root, GIB48, running=True)
        self.assertFalse([t for t in running if "Hailuo" in t], running)
        stopped = _menu(self.root, GIB48, running=False)
        self.assertIn("Update Hailuo H3 runner (weights kept — no re-download)", stopped)

    def test_64gb_without_q8_is_not_told_to_build(self):
        # bf16 fits there; the runner-update entry (which also builds Q8) stays.
        self._install_h3(q8=False)
        items = _menu(self.root, GIB64, running=False)
        self.assertNotIn(P.H3_BUILD_MENU_TEXT, items)
        self.assertIn("Update Hailuo H3 runner (weights kept — no re-download)", items)

    def test_32gb_is_never_offered_h3(self):
        for running in (True, False):
            items = _menu(self.root, GIB32, running=running)
            self.assertFalse([t for t in items if "Hailuo" in t], items)


class TestPublicIdentity(unittest.TestCase):
    """v4.8.2 committed the beta checkout's local title into public main."""

    def test_title_and_description_are_the_public_ones(self):
        head = PINOKIO_JS.split("menu: async")[0]
        self.assertIn('title: "Phosphene",', head)
        self.assertNotIn('title: "Phosphene BETA"', head)
        desc = [l for l in head.splitlines() if l.strip().startswith("description:")]
        self.assertEqual(len(desc), 1)
        self.assertNotIn("BETA", desc[0])
        self.assertNotIn("8199", desc[0])
        self.assertEqual(json.loads((ROOT / "pinokio.json").read_text())["title"], "Phosphene")


class TestQ8BuildWaitsForARunningRender(unittest.TestCase):
    """The sidebar now offers the install while the panel runs, and the Q8
    build's closing checks load the whole ~20 GB DiT (Codex review, 4.15.3)."""

    def test_both_whole_model_loads_wait_for_an_idle_panel(self):
        src = (ROOT / "scripts/pinokio/h3_build_q8.sh").read_text()
        self.assertIn('"$STATE_DIR/panel_queue.json"', src)
        q = src.index('.venv/bin/python scripts/quantize_stream.py')
        v = src.index('if pack_shards_present && validate_pack_fresh; then')
        self.assertLess(src.rindex('wait_for_idle_panel', 0, q), q)
        self.assertLess(src.rindex('wait_for_idle_panel', 0, v), v)
        self.assertIn('-ge 120', src, "the wait is capped, never forever")


class TestPreflightLetsA48GBMacThrough(unittest.TestCase):
    def test_preflight_at_48gb(self):
        d = Path(tempfile.mkdtemp(prefix="phos-sysctl-"))
        (d / "sysctl").write_text(f"#!/bin/sh\necho {GIB48}\n")
        (d / "sysctl").chmod(0o755)
        out = subprocess.run(["bash", str(ROOT / "scripts/pinokio/h3_preflight.sh")],
                             capture_output=True, text=True, errors="replace", timeout=30,
                             env={**os.environ, "PATH": f"{d}:{os.environ['PATH']}"})
        shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("36-60 GB class", out.stdout)
        self.assertNotIn("LTX-2.3", (ROOT / "scripts/pinokio/h3_preflight.sh").read_text())


if __name__ == "__main__":
    unittest.main()
