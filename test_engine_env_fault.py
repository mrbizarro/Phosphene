"""A half-built engine venv must be caught before a render, not during one.

Fleet, 2026-09-20: three installs on 4.13.0 / 4.14.3 / 4.15.0 died at helper
start with `ModuleNotFoundError: No module named 'ltx_pipelines_mlx'` — one of
them 28 renders in a row. Every file check the panel and pinokio.js had looked
at the INTERPRETER, which in that shape exists; what is missing is the package
pip writes into the venv last. So nothing offered the repair, and nothing said
what was wrong until half a minute into each render.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import mlx_ltx_panel as P


class _Venv:
    """Build a venv tree with the pieces you ask for."""

    def __init__(self, python=True, package=True):
        self.python, self.package = python, package

    def __enter__(self):
        self.tmp = TemporaryDirectory()
        env = Path(self.tmp.name) / "ltx-2-mlx" / "env"
        (env / "bin").mkdir(parents=True)
        (env / "pyvenv.cfg").write_text("home = /usr/bin\n")
        site = env / "lib" / "python3.11" / "site-packages"
        site.mkdir(parents=True)
        if self.python:
            (env / "bin" / "python3.11").write_text("#!/bin/sh\n")
        if self.package:
            (site / "ltx_pipelines_mlx").mkdir()
        self.prev = P.HELPER_PYTHON
        P.HELPER_PYTHON = env / "bin" / "python3.11"
        return self

    def __exit__(self, *a):
        P.HELPER_PYTHON = self.prev
        self.tmp.cleanup()


class TheProbe(unittest.TestCase):

    def test_a_complete_venv_is_no_fault(self):
        with _Venv():
            self.assertEqual(P.engine_env_fault(), "")

    def test_a_missing_interpreter_is_named(self):
        with _Venv(python=False):
            self.assertIn("no Python", P.engine_env_fault())

    def test_the_half_built_venv_is_named(self):
        """THE REGRESSION: interpreter present, packages never installed."""
        with _Venv(package=False):
            fault = P.engine_env_fault()
            self.assertTrue(fault, "a venv with no packages read as healthy")
            self.assertIn("ltx_pipelines_mlx", fault)

    def test_an_interpreter_that_is_not_a_venv_is_not_judged(self):
        """LTX_HELPER_PYTHON exists so a developer can point the panel at an
        interpreter elsewhere. For /opt/homebrew/bin/python3.11 the venv
        arithmetic lands on /opt/homebrew, whose site-packages legitimately
        has no ltx_pipelines_mlx — faulting there would block renders on a
        machine where everything works."""
        with _Venv(package=False):
            (P.HELPER_PYTHON.parent.parent / "pyvenv.cfg").unlink()
            self.assertEqual(P.engine_env_fault(), "")

    def test_the_repair_text_names_the_sidebar_entry(self):
        """The user's next click. If pinokio.js ever renames that menu item,
        this string has to move with it."""
        self.assertIn("Repair Phosphene engine", P.ENGINE_ENV_REPAIR)

    def test_pinokio_offers_the_repair_for_the_half_built_shape(self):
        """The panel can only describe the fault; pinokio.js is what puts the
        button on screen. Pin the probe it uses."""
        js = Path(__file__).with_name("pinokio.js").read_text()
        self.assertIn("site-packages/ltx_pipelines_mlx", js)
        self.assertIn("!ltx_python || !ltx_pkg", js)


class ItIsFiledUnderTheRightHeading(unittest.TestCase):
    """Wording that a higher row of the taxonomy would claim sends this fault
    to the wrong bucket in the fleet view — which is how it stays invisible.
    Both of these were caught that way: "missing (" reads as model_missing and
    "fetch"/"download" as download_failed."""

    def messages(self):
        with _Venv(python=False) as _:
            no_python = P.engine_env_fault()
        with _Venv(package=False) as _:
            no_pkg = P.engine_env_fault()
        return [f"engine venv is not usable: {f}. {P.ENGINE_ENV_REPAIR}"
                for f in (no_python, no_pkg)]

    def test_both_shapes_classify_as_venv_broken(self):
        for msg in self.messages():
            self.assertEqual(P._analytics_error_class(msg), "venv_broken", msg)

    def test_no_message_says_download_or_fetch(self):
        for msg in self.messages():
            low = msg.lower()
            self.assertNotIn("download", low)
            self.assertNotIn("fetch", low)

    def test_no_message_spells_the_model_missing_needle(self):
        for msg in self.messages():
            self.assertNotIn("missing (", msg.lower())


def _menu(root: Path, running: bool) -> list:
    """Run the REAL pinokio.js menu against a fake install root."""
    import json, subprocess
    probe = (
        "const path=require('path'),fs=require('fs');const root=process.argv[1];"
        "const running=process.argv[2]==='1';"
        "const mod=require(path.join(root,'pinokio.js'));"
        "const info={path:(...a)=>path.join(root,...a),exists:p=>fs.existsSync(path.join(root,p)),"
        "running:s=>running&&s==='start.js',local:s=>s==='start.js'?{url:'http://127.0.0.1:1'}:{}};"
        "mod.menu({},info).then(m=>console.log(JSON.stringify(m.map(i=>({text:i.text,default:!!i.default})))))"
    )
    out = subprocess.run(["node", "-e", probe, str(root), "1" if running else "0"],
                         capture_output=True, text=True, errors="replace", timeout=30)
    if out.returncode != 0:
        raise AssertionError(out.stderr[-800:])
    return json.loads(out.stdout)


@unittest.skipUnless(__import__("shutil").which("node"), "node not on PATH")
class TheSidebarOffersTheRepairItsErrorNames(unittest.TestCase):
    """Codex INST-05 (2026-09-24): complete weights, venv with a Python and no
    ltx_pipelines_mlx. Start stayed `default: true` (auto-runs a panel that
    cannot render), and once it ran both running menus hid the Repair entry
    the render error tells the user to click."""

    def setUp(self):
        import json, shutil
        self.tmp = TemporaryDirectory()
        root = self.root = Path(self.tmp.name)
        src = Path(__file__).resolve().parent
        shutil.copy(src / "pinokio.js", root / "pinokio.js")
        shutil.copy(src / "required_files.json", root / "required_files.json")
        req = json.loads((src / "required_files.json").read_text())
        minb = req.get("min_size_bytes", 1024)

        def touch(rel, size=minb):
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "wb") as fh:
                fh.truncate(size)
        touch("ltx-2-mlx/env/pyvenv.cfg", 10)
        touch("ltx-2-mlx/env/bin/python3.11", 10)
        (root / "ltx-2-mlx/env/lib/python3.11/site-packages").mkdir(parents=True)
        render = req["capabilities"]["render"]
        for key in render["repos_by_version"][render["default_version"]]:
            repo = next(r for r in req["repos"] if r["key"] == key)
            for f in repo.get("files", []):
                touch(f"{repo['local_dir']}/{f}")

    def tearDown(self):
        self.tmp.cleanup()

    def _heal(self):
        (self.root / "ltx-2-mlx/env/lib/python3.11/site-packages/ltx_pipelines_mlx").mkdir()

    def test_stopped_broken_engine_does_not_auto_start(self):
        m = _menu(self.root, running=False)
        self.assertEqual(m[0]["text"], "Repair Phosphene engine (models kept)", m)
        self.assertFalse([i for i in m if i["default"]], m)
        self.assertIn("Start", [i["text"] for i in m])

    def test_running_broken_engine_says_how_to_reach_the_repair(self):
        texts = [i["text"] for i in _menu(self.root, running=True)]
        hint = [t for t in texts if "Repair Phosphene engine" in t]
        self.assertTrue(hint, texts)
        self.assertIn("Stop the panel", hint[0])
        self.assertIn("Stop Phosphene", P.ENGINE_ENV_REPAIR)

    def test_healthy_engine_is_unchanged(self):
        self._heal()
        stopped = _menu(self.root, running=False)
        self.assertEqual(stopped[0], {"text": "Start", "default": True})
        self.assertFalse([i for i in stopped if "Repair Phosphene" in i["text"]])
        running = [i["text"] for i in _menu(self.root, running=True)]
        self.assertFalse([t for t in running if "Repair Phosphene" in t], running)


if __name__ == "__main__":
    unittest.main(verbosity=2)
