"""4.19.5: the Hailuo H3 venv is locked to a known-good package set.

Through 4.19.4 the H3 venv (minimax-h3-mlx/.venv) was resolved from the
runner's requirements.txt alone - floors only (mlx>=0.32.0, transformers>=4.57,
huggingface_hub>=0.30, mlx-vlm>=0.4.4) - in two places: install_h3.js and
scripts/pinokio/h3_checkout.sh. Every new H3 install got whatever PyPI held
that day (2026-10-14: mlx 0.32.3; 2026-10-15: mlx 0.32.4), so an upstream
release could break every new H3 install the way tokenizers 0.23.3 broke LTX
renders in 4.19.3, with no Phosphene release behind it.

What 4.19.5 holds, and what these tests pin:
  * scripts/pinokio/h3_constraints.txt - the set, every package exact;
  * scripts/pinokio/h3_deps.sh - the ONE install into the H3 venv, -c that
    file, no eager --upgrade; install_h3.js and every Update run it;
  * Update repairs a drifted venv (uv moves a package back under -c);
  * the panel refuses an H3 render on a venv off the set, before any GPU time,
    with a one-click repair (POST /h3/repair -> h3_deps.sh).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P                                            # noqa: E402
from extract_panel_js import extract_function                        # noqa: E402

HC = ROOT / "scripts" / "pinokio" / "h3_constraints.txt"
DEPS = ROOT / "scripts" / "pinokio" / "h3_deps.sh"
CHECK = ROOT / "scripts" / "pinokio" / "h3_env_check.py"
NODE = shutil.which("node")


def _code_lines(rel: str) -> list[str]:
    text = (ROOT / rel).read_text(encoding="utf-8").replace("\\\n", " ")
    return [l for l in text.splitlines() if not re.match(r"^\s*(#|//|\*)", l)]


def _check_module():
    spec = importlib.util.spec_from_file_location("h3_env_check", CHECK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TheKnownGoodSet(unittest.TestCase):
    def test_every_line_is_an_exact_pin(self):
        lines = [l.split("#", 1)[0].strip() for l in HC.read_text().splitlines()]
        lines = [l for l in lines if l]
        self.assertGreater(len(lines), 20)
        for l in lines:
            self.assertRegex(l, r"^[A-Za-z0-9][A-Za-z0-9._-]*==[^\s,;]+$")
        names = [l.split("==")[0].lower() for l in lines]
        self.assertEqual(len(names), len(set(names)), "a package pinned twice")
        for seed in ("pip", "setuptools", "wheel"):
            self.assertNotIn(seed, names, "the venv's --seed is not part of the resolve")

    def test_it_pins_what_the_preflight_judges_and_h3_needs_mlx_032(self):
        pins = P.h3_constraint_pins()
        for name in P.H3_ENV_JUDGED:
            self.assertIn(name, pins)
        mlx = tuple(int(x) for x in pins["mlx"].split(".")[:2])
        self.assertGreaterEqual(mlx, P.H3_MLX_MIN)
        self.assertEqual(pins["mlx-metal"], pins["mlx"])

    def test_the_panel_and_the_install_check_judge_the_same_packages(self):
        self.assertEqual(tuple(_check_module().REQUIRED), P.H3_ENV_JUDGED)
        self.assertEqual(_check_module().pins(HC), P.h3_constraint_pins())

    def test_not_the_ltx_venvs_set(self):
        # Separate venvs on purpose: LTX holds mlx 0.31.1, H3 needs >=0.32.
        ec = (ROOT / "scripts/pinokio/engine_constraints.txt").read_text()
        self.assertIn("mlx==0.31.1", ec)
        self.assertNotEqual(P.h3_constraint_pins()["mlx"], "0.31.1")


class ConstraintsOnEveryPath(unittest.TestCase):
    def test_h3_deps_is_the_one_constrained_install(self):
        installs = [l for l in _code_lines("scripts/pinokio/h3_deps.sh") if re.search(r"\bpip install\b", l)]
        self.assertEqual(len(installs), 1, installs)
        self.assertRegex(installs[0], r'-c "\$HC"')
        self.assertIn("-r requirements.txt", installs[0])
        self.assertNotRegex(installs[0], r"--upgrade|\s-U\s")
        self.assertTrue(any('HC="$HERE/h3_constraints.txt"' in l for l in _code_lines("scripts/pinokio/h3_deps.sh")))

    def test_nothing_else_installs_into_the_h3_venv(self):
        for rel in ("install_h3.js", "update.js", "scripts/pinokio/h3_checkout.sh",
                    "scripts/pinokio/h3_venv.sh", "scripts/pinokio/h3_build_q8.sh",
                    "scripts/pinokio/h3_roots.sh"):
            with self.subTest(rel=rel):
                self.assertEqual([l for l in _code_lines(rel) if re.search(r"\bpip install\b", l)], [])

    def test_install_and_update_run_it(self):
        self.assertTrue(any("scripts/pinokio/h3_deps.sh" in l for l in _code_lines("install_h3.js")))
        post = _code_lines("scripts/post_update.sh")
        hits = [i for i, l in enumerate(post) if "h3_deps.sh" in l]
        self.assertEqual(len(hits), 1)
        gate = next(i for i, l in enumerate(post) if "import ltx_core_mlx, ltx_pipelines_mlx, mlx" in l)
        self.assertLess(hits[0], gate, "the LTX import gate must stay the last step")
        # The optional engine's "error:" lines cannot end the Update (Pinokio
        # 8.2.0 stops a run on one) - and the WARN still fires (pipefail).
        self.assertIn("2>&1", post[hits[0]])
        self.assertIn("s/[Ee][Rr][Rr][Oo][Rr]:/problem -/g", post[hits[0]])
        self.assertIn("|| echo 'WARN", post[hits[0]])
        self.assertIn("set -o pipefail", post[hits[0] - 1])

    def test_the_panel_repair_runs_h3_deps(self):
        seen = {}

        class FakeProc:
            pid = 0
            stdout = iter(["H3 check: ok\n"])
            def wait(self, timeout=None):
                return 0

        def fake_popen(cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw.get("env")
            return FakeProc()

        with mock.patch.object(P.subprocess, "Popen", fake_popen), \
             mock.patch.object(P, "_kill_group_after", lambda *a: None), \
             mock.patch.object(P, "h3_env_fault", lambda: ""), \
             mock.patch.object(P, "push", lambda *a, **k: None):
            P.H3_REPAIR.update({"state": "running", "active": True, "log": __import__("collections").deque()})
            P._h3_env_repair_thread()
        self.assertEqual(seen["cmd"][:2], ["bash", str(ROOT / "scripts/pinokio/h3_deps.sh")])
        self.assertEqual(seen["cmd"][2], str(P.H3_ROOT))
        self.assertEqual(P.H3_REPAIR["state"], "done")
        self.assertFalse(P.H3_REPAIR["active"])

    def test_the_js_gate_passes_and_catches_each_way_back(self):
        if NODE is None:
            self.skipTest("node not on PATH")
        r = subprocess.run([NODE, str(ROOT / "scripts/check_post_update.js")],
                           capture_output=True, text=True, errors="replace")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        files = ("scripts/check_post_update.js", "scripts/post_update.sh",
                 "scripts/pinokio/ltx_engine_env.sh", "scripts/pinokio/mflux_pack.sh",
                 "scripts/pinokio/a2v_stems_deps.sh", "scripts/pinokio/engine_constraints.txt",
                 "scripts/pinokio/h3_constraints.txt", "scripts/pinokio/h3_deps.sh",
                 "scripts/pinokio/h3_checkout.sh", "scripts/pinokio/h3_venv.sh",
                 "scripts/pinokio/h3_build_q8.sh", "scripts/pinokio/h3_roots.sh",
                 "install.js", "install_qwen.js", "install_sharp.js", "install_h3.js", "update.js")
        breaks = {
            # the 4.19.4 install line, back in install_h3.js
            "install_h3.js": ('RH3 + "bash \\"$APP_ROOT/scripts/pinokio/h3_deps.sh\\" \\"$(pwd)\\""',
                              'RH3 + "uv pip install --python .venv/bin/python -r requirements.txt"'),
            "scripts/pinokio/h3_deps.sh": (' -c "$HC" -r requirements.txt', ' -r requirements.txt'),
            "scripts/pinokio/h3_constraints.txt": ("transformers==5.19.0", "transformers>=4.57"),
            "scripts/pinokio/h3_checkout.sh": ('echo "H3 engine at',
                                               'uv pip install --python .venv/bin/python -r requirements.txt\necho "H3 engine at'),
        }
        for target, (old, new) in breaks.items():
            with self.subTest(target=target), TemporaryDirectory() as tmp:
                t = Path(tmp)
                for rel in files:
                    (t / rel).parent.mkdir(parents=True, exist_ok=True)
                    (t / rel).write_text((ROOT / rel).read_text())
                f = t / target
                self.assertIn(old, f.read_text())
                f.write_text(f.read_text().replace(old, new, 1))
                r = subprocess.run([NODE, str(t / "scripts/check_post_update.js")],
                                   capture_output=True, text=True, errors="replace")
                self.assertNotEqual(r.returncode, 0, r.stdout[-1500:])
                self.assertIn(Path(target).name, r.stdout)


class H3DepsRuns(unittest.TestCase):
    """h3_deps.sh executed for real, with recording stubs for uv and the
    venv's python - the arguments ARE the contract."""

    def _app(self, tmp: Path, uv_rc=0, check_rc=0):
        app = tmp / "app"
        (app / "scripts" / "pinokio").mkdir(parents=True)
        for f in ("h3_deps.sh", "h3_constraints.txt", "h3_env_check.py"):
            shutil.copy(ROOT / "scripts/pinokio" / f, app / "scripts/pinokio" / f)
        ck = tmp / "My H3 checkout"
        (ck / ".venv" / "bin").mkdir(parents=True)
        (ck / "requirements.txt").write_text("mlx>=0.32.0\n")
        calls = tmp / "calls.log"
        py = ck / ".venv" / "bin" / "python"
        py.write_text(f'#!/bin/bash\nprintf "python %s\\n" "$*" >> "{calls}"\n'
                      f'case "$1" in *h3_env_check.py) exit {check_rc};; esac\nexit 0\n')
        bin_ = tmp / "bin"
        bin_.mkdir()
        (bin_ / "uv").write_text(f'#!/bin/bash\nprintf "uv %s\\n" "$*" >> "{calls}"\nexit {uv_rc}\n')
        for f in (py, bin_ / "uv"):
            f.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}")
        return app, ck, calls, env

    def _run(self, **kw):
        tmp = Path(self.enterContext(TemporaryDirectory()))
        app, ck, calls, env = self._app(tmp, **kw)
        r = subprocess.run(["bash", str(app / "scripts/pinokio/h3_deps.sh"), str(ck)],
                           cwd=tmp, env=env, capture_output=True, text=True, errors="replace")
        return r, (calls.read_text() if calls.exists() else ""), app

    @unittest.skipUnless(sys.platform == "darwin", "the set is macOS wheels")
    def test_installs_requirements_under_the_constraints_then_checks(self):
        r, calls, app = self._run()
        self.assertEqual(r.returncode, 0, r.stdout)
        uv = [l for l in calls.splitlines() if l.startswith("uv ")]
        self.assertEqual(len(uv), 1)
        self.assertIn("pip install --python .venv/bin/python -c "
                      f"{app}/scripts/pinokio/h3_constraints.txt -r requirements.txt", uv[0])
        self.assertNotIn("--upgrade", uv[0])
        self.assertTrue(any("h3_env_check.py" in l and "h3_constraints.txt" in l
                            for l in calls.splitlines()), calls)

    @unittest.skipUnless(sys.platform == "darwin", "the set is macOS wheels")
    def test_a_failed_install_or_check_says_error_and_exits_nonzero(self):
        for kw, words in (({"uv_rc": 1}, "did not install"), ({"check_rc": 1}, "still not on its known-good set")):
            with self.subTest(**kw):
                r, _, _ = self._run(**kw)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("error:", r.stdout)
                self.assertIn(words, r.stdout)

    @unittest.skipUnless(sys.platform == "darwin", "the set is macOS wheels")
    def test_one_install_at_a_time(self):
        # Codex 4.19.5: Install / Update / the panel's Repair can all reach the
        # venv. A second run while the first holds the lock refuses; the lock
        # is gone afterwards; a dead holder's lock is taken over.
        tmp = Path(self.enterContext(TemporaryDirectory()))
        app, ck, calls, env = self._app(tmp)
        uv = tmp / "bin" / "uv"
        uv.write_text(uv.read_text().replace("exit 0", "sleep 2; exit 0"))
        cmd = ["bash", str(app / "scripts/pinokio/h3_deps.sh"), str(ck)]
        a = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, text=True)
        import time as _t
        for _ in range(50):
            if (ck / ".venv" / P.H3_DEPS_LOCK_NAME / "pid").exists():
                break
            _t.sleep(0.1)
        b = subprocess.run(cmd, env=env, capture_output=True, text=True)
        a.communicate(timeout=30)
        self.assertEqual(a.returncode, 0)
        self.assertEqual(b.returncode, 1)
        self.assertIn("another install into the H3 venv is running", b.stdout)
        self.assertFalse((ck / ".venv" / P.H3_DEPS_LOCK_NAME).exists())
        (ck / ".venv" / P.H3_DEPS_LOCK_NAME).mkdir()
        (ck / ".venv" / P.H3_DEPS_LOCK_NAME / "pid").write_text("999999\n")
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("taking over", r.stdout)

    @unittest.skipUnless(shutil.which("uv") or Path.home().joinpath("pinokio/bin/miniforge/bin/uv").exists(), "no uv")
    def test_uv_moves_a_drifted_package_back_under_c(self):
        """The property the repair stands on, with real uv, offline from the
        local cache: a venv holding a version OUTSIDE the constraint, asked to
        install requirements that are already satisfied, is moved back."""
        uv = shutil.which("uv") or str(Path.home() / "pinokio/bin/miniforge/bin/uv")
        tmp = Path(self.enterContext(TemporaryDirectory()))
        env = dict(os.environ)
        cache = Path.home() / "pinokio/cache/UV_CACHE_DIR"
        if cache.is_dir():
            env["UV_CACHE_DIR"] = str(cache)
        def uvr(*a):
            return subprocess.run([uv, *a], cwd=tmp, env=env, capture_output=True, text=True, errors="replace")
        if uvr("venv", "-q", "-p", sys.executable, "v").returncode != 0:
            self.skipTest("uv venv failed here")
        if uvr("pip", "install", "--offline", "--python", "v/bin/python", "six==1.17.0").returncode != 0:
            self.skipTest("six 1.17.0 not in the local uv cache")
        (tmp / "req.txt").write_text("six>=1.0\n")
        (tmp / "c.txt").write_text("six==1.16.0\n")
        r = uvr("pip", "install", "--offline", "--python", "v/bin/python", "-c", "c.txt", "-r", "req.txt")
        # uv words the same condition two ways: older builds say "offline", newer
        # ones say "the network was disabled" (--offline). Either means the
        # package is not in the local cache, which is a skip, not a failure.
        _out = (r.stderr + r.stdout).lower()
        if r.returncode != 0 and ("offline" in _out or "network was disabled" in _out
                                  or "network is disabled" in _out):
            self.skipTest("six 1.16.0 not in the local uv cache")
        self.assertEqual(r.returncode, 0, r.stderr)
        freeze = uvr("pip", "freeze", "--python", "v/bin/python").stdout
        self.assertIn("six==1.16.0", freeze)


class _H3Venv:
    """A fake H3 venv whose dist-info folders say `versions`."""

    def __init__(self, **over):
        self.versions = dict(P.h3_constraint_pins())
        self.versions.update(over)

    def __enter__(self):
        self.tmp = TemporaryDirectory()
        venv = Path(self.tmp.name) / ".venv"
        self.site = site = venv / "lib" / "python3.11" / "site-packages"
        site.mkdir(parents=True)
        (venv / "bin").mkdir()
        self.python = venv / "bin" / "python3.11"
        self.python.write_text("#!/bin/sh\n")
        for name, ver in self.versions.items():
            if ver:
                (site / f"{name.replace('-', '_')}-{ver}.dist-info").mkdir()
        self.patches = [mock.patch.object(P, "_h3_python", lambda: self.python),
                        mock.patch.object(P.sys, "platform", "darwin"),
                        mock.patch.dict(os.environ, {"LTX_H3_PYTHON": "", "LTX_H3_ENV_CHECK": ""})]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class CaughtBeforeAnH3Render(unittest.TestCase):
    def test_the_set_itself_is_not_a_fault(self):
        with _H3Venv():
            self.assertEqual(P.h3_env_offset(), "")
            self.assertEqual(P.h3_env_fault(), "")
            self.assertTrue(P.h3_env_status()["ok"])

    def test_a_drifted_venv_is_refused_with_the_repair(self):
        with _H3Venv(transformers="5.20.0", **{"mlx-vlm": "0.7.9"}):
            fault = P.h3_env_fault()
            self.assertIn("not on the package set this release was tested with", fault)
            self.assertIn("transformers 5.20.0 (tested: 5.19.0)", fault)
            self.assertIn("mlx-vlm 0.7.9 (tested: 0.7.8)", fault)
            self.assertFalse(P.h3_env_status()["ok"])
            msg = f"H3 cannot start: {fault}. {P.H3_ENV_REPAIR}"
            self.assertEqual(P._analytics_error_class(msg), "venv_broken")
            self.assertIn("Repair Hailuo H3", msg)
            self.assertIn("Update Hailuo H3 runner", msg)

    def test_a_package_outside_the_judged_ones_does_not_block_a_render(self):
        with _H3Venv(rich="99.0.0"):
            self.assertEqual(P.h3_env_fault(), "")

    def test_transformers_refusing_its_own_dependencies_is_named(self):
        with _H3Venv(tokenizers="0.24.0") as v:
            (v.site / "transformers").mkdir()
            (v.site / "transformers" / "dependency_versions_table.py").write_text(
                'deps = {"tokenizers": "tokenizers>=0.23.1,<0.24.0"}\n')
            fault = P.h3_env_fault()
            self.assertIn("do not fit together", fault)
            self.assertIn("tokenizers 0.24.0", fault)
            self.assertEqual(P._analytics_error_class(f"H3 cannot start: {fault}. {P.H3_ENV_REPAIR}"),
                             "venv_broken")

    def test_old_mlx_keeps_its_own_sentence(self):
        with _H3Venv(mlx="0.31.1", **{"mlx-metal": "0.31.1"}):
            self.assertIn("seed(): incompatible function arguments", P.h3_env_fault())

    def test_not_judged_where_the_set_does_not_apply(self):
        with _H3Venv(transformers="5.20.0"):
            with mock.patch.dict(os.environ, {"LTX_H3_ENV_CHECK": "0"}):
                self.assertEqual(P.h3_env_offset(), "")
            with mock.patch.dict(os.environ, {"LTX_H3_PYTHON": "/elsewhere/python"}):
                self.assertEqual(P.h3_env_offset(), "")
            with mock.patch.object(P.sys, "platform", "linux"):
                self.assertEqual(P.h3_env_offset(), "")
            with mock.patch.object(P, "H3_CONSTRAINTS", Path("/nonexistent/h3_constraints.txt")):
                self.assertEqual(P.h3_env_offset(), "", "an unreadable lock judges nothing")

    def test_the_render_path_refuses_before_the_helper_kill(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text()
        body = src[src.index("def run_h3_job_inner"):]
        guard = body.index("_h3_env = h3_env_fault()")
        busy = body.index("_h3_busy = h3_env_busy()")
        kill = body.index("stopping the LTX warm helper")
        spawn = body.index("str(paths[\"python\"]), str(paths[\"runner\"])")
        self.assertLess(busy, kill)
        self.assertLess(guard, kill)
        self.assertLess(guard, spawn)
        self.assertIn("H3_ENV_REPAIR", body[guard:guard + 200])

    def test_status_and_route_carry_it(self):
        rq = (ROOT / "panel" / "routes_queue.py").read_text()
        self.assertIn('payload["h3_env"] = P.h3_env_status()', rq)
        rm = (ROOT / "panel" / "routes_models.py").read_text()
        self.assertIn('@post("/h3/repair")', rm)
        self.assertIn("P.h3_env_repair_start()", rm)


class RepairStart(unittest.TestCase):
    def setUp(self):
        P.H3_REPAIR.clear()
        P.H3_REPAIR.update({"state": "idle", "active": False})

    def test_refused_without_h3_or_under_an_h3_render(self):
        with mock.patch.object(P, "_h3_python", lambda: None):
            self.assertEqual(P.h3_env_repair_start()[0], 409)
        with mock.patch.object(P, "_h3_python", lambda: Path("/x/python")), \
             mock.patch.dict(P.STATE, {"current": {"id": "j", "params": {"engine": "h3"}}}):
            code, body = P.h3_env_repair_start()
            self.assertEqual(code, 409)
            self.assertIn("H3 render is running", body["error"])

    def test_refused_under_a_q8_build_or_another_install(self):
        with mock.patch.object(P, "_h3_python", lambda: Path("/x/python")), \
             mock.patch.dict(P.STATE, {"current": None}):
            with mock.patch.object(P, "_external_build_hold", lambda: "the Hailuo H3 compact-engine build"):
                self.assertEqual(P.h3_env_repair_start()[0], 409)
            with mock.patch.object(P, "_h3_deps_running", lambda: True):
                self.assertEqual(P.h3_env_repair_start()[0], 409)
        self.assertFalse(P.H3_REPAIR.get("active"))

    def test_the_worker_holds_an_h3_job_not_an_ltx_one(self):
        # Codex 4.19.5: dequeued during a repair, an H3 job would only be
        # refused, and three identical refusals trip the queue breaker.
        h3 = {"params": {"engine": "h3"}}
        ltx = {"params": {"engine": "ltx"}}
        with mock.patch.object(P, "h3_env_busy", lambda: "busy"):
            self.assertIn("Queue held", P._h3_repair_hold(h3))
            self.assertEqual(P._h3_repair_hold(ltx), "")
        with mock.patch.object(P, "h3_env_busy", lambda: ""):
            self.assertEqual(P._h3_repair_hold(h3), "")
        src = (ROOT / "mlx_ltx_panel.py").read_text()
        loop = src[src.index("def worker_loop"):][:1500]
        self.assertIn('_h3_repair_hold(STATE["queue"][0])', loop)

    def test_a_live_deps_lock_reads_as_busy(self):
        with TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".venv" / P.H3_DEPS_LOCK_NAME
            lock.mkdir(parents=True)
            with mock.patch.object(P, "H3_ROOT", Path(tmp)):
                (lock / "pid").write_text(f"{os.getpid()}\n")
                self.assertTrue(P._h3_deps_running())
                self.assertTrue(P.h3_env_busy())
                (lock / "pid").write_text("999999\n")
                self.assertFalse(P._h3_deps_running())

    def test_starts_once_and_blocks_h3_renders_while_it_runs(self):
        started = []
        with mock.patch.object(P, "_h3_python", lambda: Path("/x/python")), \
             mock.patch.dict(P.STATE, {"current": None}), \
             mock.patch.object(P, "_h3_env_repair_thread", lambda: started.append(1)), \
             mock.patch.object(P, "push", lambda *a, **k: None):
            self.assertEqual(P.h3_env_repair_start()[0], 202)
            self.assertTrue(P.h3_env_busy())
            self.assertEqual(P.h3_env_repair_start()[0], 409)
        P.H3_REPAIR.update(active=False)


def _node(fns: list[str], calls: list[str], prelude: str = "") -> list:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
    code = ("function escapeHtml(s){return String(s).replace(/[&<>\"]/g,'');}\n" + prelude + "\n"
            + "\n".join(extract_function(n, src) for n in fns)
            + "\nconsole.log(JSON.stringify([" + ",".join(calls) + "]));")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(code)
        path = fh.name
    r = subprocess.run([NODE, path], capture_output=True, text=True, errors="replace", timeout=60)
    os.unlink(path)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


class TheCard(unittest.TestCase):
    def test_the_failure_card_offers_the_h3_repair_not_retry(self):
        with _H3Venv(transformers="5.20.0"):
            raw = f"H3 cannot start: {P.h3_env_fault()}. {P.H3_ENV_REPAIR}"
        job = {"id": "j1", "status": "failed", "error": raw}
        fr, html = _node(
            ["_redactLocalPaths", "friendlyJobError", "nowCardFailureActions"],
            [f"friendlyJobError({json.dumps(raw)}, 'h3')",
             f"nowCardFailureActions({json.dumps(job)}, friendlyJobError({json.dumps(raw)}, 'h3'))"])
        self.assertEqual(fr["action"], "repair_h3")
        self.assertIn('data-action="repair-h3"', html)
        self.assertIn("Repair Hailuo H3", html)
        self.assertNotIn('data-action="retry"', html)

    def test_after_the_repair_the_card_offers_retry(self):
        # Codex 4.19.5: the toast says "Press Retry"; the card must have it.
        with _H3Venv(transformers="5.20.0"):
            raw = f"H3 cannot start: {P.h3_env_fault()}. {P.H3_ENV_REPAIR}"
        job = {"id": "j1", "status": "failed", "error": raw}
        call = f"nowCardFailureActions({json.dumps(job)}, friendlyJobError({json.dumps(raw)}, 'h3'))"
        before, after = _node(
            ["_redactLocalPaths", "friendlyJobError", "nowCardFailureActions"],
            [call, "(_h3EnvLast = {ok: true, repair: {state: 'done'}}, " + call + ")"],
            prelude="var _h3EnvLast = null;")
        self.assertIn('data-action="repair-h3"', before)
        self.assertNotIn('data-action="repair-h3"', after)
        self.assertIn('data-action="retry"', after)

    def test_the_click_and_the_toast_are_wired(self):
        main = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn('[data-action="repair-h3"]', main)
        self.assertIn("h3RepairStart()", main)
        q = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        self.assertIn("fetch('/h3/repair'", q)
        self.assertIn("noteH3EnvRepair(s.h3_env)", q)


if __name__ == "__main__":
    unittest.main()
