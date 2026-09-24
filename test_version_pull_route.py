"""The in-panel Update (POST /version/pull) against REAL throwaway git repos.

Codex INST-01 (2026-09-24): any `pull --ff-only` failure was read as history
divergence and answered with `reset --hard origin/main` — including git's own
"untracked working tree files would be overwritten", so the reset destroyed the
file git had just refused to touch. And `pull --ff-only` itself silently
overwrites an IGNORED file where upstream now tracks one. The route now runs the
sidebar's obstruction guard first and resets only on divergence proven against
the fetched origin/main.

Codex INST-12: a pull that changes only the music-engine pin must ask for the
full Pinokio Update, since only that runs music_checkout.sh.

Nothing here touches the real checkout: P.ROOT and _git_capture are pointed at
a temp clone for the duration of each test.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P                                            # noqa: E402

routes_meta = sys.modules["panel.routes_meta"]
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), env=GIT_ENV, check=True,
                          capture_output=True, text=True, errors="replace").stdout.strip()


class _H:
    def __init__(self):
        self.status, self.payload = None, None

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


@unittest.skipUnless(shutil.which("git"), "git not on PATH")
class VersionPull(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.up, self.clone = base / "up", base / "clone"
        self.up.mkdir()
        git(self.up, "init", "-q", "-b", "main", ".")
        (self.up / "f.txt").write_text("v1\n")
        guard = ROOT / "scripts" / "pinokio" / "update_obstruction_guard.sh"
        (self.up / "scripts" / "pinokio").mkdir(parents=True)
        shutil.copy(guard, self.up / "scripts" / "pinokio" / guard.name)
        git(self.up, "add", ".")
        git(self.up, "commit", "-qm", "v1")
        git(base, "clone", "-q", str(self.up), str(self.clone))
        real_capture = P._git_capture
        self._patches = [
            mock.patch.object(P, "ROOT", self.clone),
            mock.patch.object(P, "_git_capture",
                              lambda args, cwd=None: real_capture(args, cwd=self.clone)),
            mock.patch.object(P, "_detect_local_install_state", lambda: None),
            mock.patch.object(P, "_check_remote_once", lambda: None),
            mock.patch.object(P, "get_version_state", lambda: {}),
            mock.patch.dict(P._VERSION_STATE, {}, clear=False),
            mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null",
                                         "GIT_CONFIG_SYSTEM": "/dev/null"}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def _upstream_adds(self, rel: str, text: str = "upstream\n"):
        f = self.up / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        git(self.up, "add", rel)
        git(self.up, "commit", "-qm", f"add {rel}")

    def _pull(self) -> _H:
        h = _H()
        routes_meta.post_version_pull(h, "/version/pull", {}, "")
        return h

    def test_untracked_obstruction_is_refused_not_reset(self):
        self._upstream_adds("notes.txt")
        (self.clone / "notes.txt").write_text("MINE\n")
        head = git(self.clone, "rev-parse", "HEAD")
        h = self._pull()
        self.assertFalse(h.payload["ok"], h.payload)
        self.assertEqual((self.clone / "notes.txt").read_text(), "MINE\n")
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), head)

    def test_ignored_obstruction_is_not_overwritten_by_the_fast_forward(self):
        self._upstream_adds("ignored.txt")
        with open(self.clone / ".git" / "info" / "exclude", "a") as fh:
            fh.write("\nignored.txt\n")
        (self.clone / "ignored.txt").write_text("MINE\n")
        h = self._pull()
        self.assertFalse(h.payload["ok"], h.payload)
        self.assertEqual((self.clone / "ignored.txt").read_text(), "MINE\n")

    def test_diverged_history_with_an_obstruction_is_not_reset(self):
        # upstream rewrote v1 (force-push) AND now tracks notes.txt
        git(self.up, "commit", "-q", "--amend", "-m", "v1 rewritten")
        self._upstream_adds("notes.txt")
        (self.clone / "notes.txt").write_text("MINE\n")
        h = self._pull()
        self.assertFalse(h.payload["ok"], h.payload)
        self.assertEqual((self.clone / "notes.txt").read_text(), "MINE\n")

    def test_proven_divergence_on_a_clean_tree_still_converges(self):
        git(self.up, "commit", "-q", "--amend", "-m", "v1 rewritten")
        h = self._pull()
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"),
                         git(self.up, "rev-parse", "HEAD"))

    def test_plain_fast_forward(self):
        (self.up / "f.txt").write_text("v2\n")
        git(self.up, "commit", "-qam", "v2")
        h = self._pull()
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertEqual((self.clone / "f.txt").read_text(), "v2\n")
        self.assertFalse(P._VERSION_STATE["pull_requires_full_update"])

    def test_music_pin_only_pull_requires_the_full_update(self):
        self._upstream_adds("scripts/music/engine_pin.txt", "a" * 40 + "\n")
        h = self._pull()
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertTrue(P._VERSION_STATE["pull_requires_full_update"])

    def test_build_constraints_and_engine_installers_require_the_full_update(self):
        for rel in ("pip-build-constraints.txt", "install_music.js"):
            with self.subTest(rel=rel):
                self._upstream_adds(rel, f"{rel}\n")
                h = self._pull()
                self.assertTrue(h.payload["ok"], h.payload)
                self.assertTrue(P._VERSION_STATE["pull_requires_full_update"])


if __name__ == "__main__":
    unittest.main()
