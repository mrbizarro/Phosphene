"""What Pinokio's Update prints: update_transaction.main()'s message and exit code.

Every test builds a real upstream (a bare repository a maintainer clone pushes
to) and an app clone in a temporary directory, with Git's global and system
config switched off, so nothing here reads the developer's Git settings, this
checkout or the network. The app's scripts/post_update.sh is a stub that only
counts its runs in a file outside the repository: no pip, uv or npm runs.

    python3 -B -m unittest discover -s tests -p 'test_update_already_current.py' -v
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import update_transaction as ut  # noqa: E402

POST_UPDATE_STUB = """#!/usr/bin/env bash
echo run >> "$PHOS_TEST_DEPS_LOG"
exit "${PHOS_TEST_DEPS_EXIT:-0}"
"""

CURRENT = 'Already up to date. Nothing to restart.\n'
PREPARED = 'Update prepared. Restart Phosphene to check that the new version starts.\n'


class MainMessage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        (root / 'gitconfig').write_text('')
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=str(root / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1',
                   GIT_TERMINAL_PROMPT='0', GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                   GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid',
                   PHOS_TEST_DEPS_LOG=str(root / 'deps.log'))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.deps_log = root / 'deps.log'
        self.bare, self.dev, self.app = root / 'upstream.git', root / 'dev', root / 'app'
        self.n = 0
        self.git(root, 'init', '-q', '--bare', '-b', 'main', str(self.bare))
        self.git(root, 'clone', '-q', str(self.bare), str(self.dev))
        (self.dev / 'scripts').mkdir()
        (self.dev / 'scripts/post_update.sh').write_text(POST_UPDATE_STUB)
        self.publish('release 1')
        self.git(root, 'clone', '-q', str(self.bare), str(self.app))

    def git(self, cwd, *args):
        p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
        if p.returncode:
            raise AssertionError(f'git {" ".join(args)}: {p.stdout}{p.stderr}')
        return p.stdout.strip()

    def publish(self, text):
        self.n += 1
        (self.dev / 'VERSION').write_text(text + '\n')
        self.git(self.dev, 'add', '-A')
        self.git(self.dev, 'commit', '-q', '-m', text)
        self.git(self.dev, 'push', '-q', 'origin', 'main')
        return self.git(self.dev, 'rev-parse', 'HEAD')

    def head(self):
        return self.git(self.app, 'rev-parse', 'HEAD')

    def deps_runs(self):
        return len(self.deps_log.read_text().splitlines()) if self.deps_log.exists() else 0

    def main(self):
        """Run main() as the app's own update_transaction.py would: (exit code, output)."""
        out = io.StringIO()
        with mock.patch.object(ut, '__file__', str(self.app / 'update_transaction.py')), \
                contextlib.redirect_stdout(out):
            code = ut.main()
        return code, out.getvalue()

    def boot(self):
        """Restart and let the candidate acknowledge itself, as the panel does."""
        tx = ut.Transaction(self.app)
        tx.request_restart()
        self.assertEqual(tx.acknowledge(self.head())['phase'], 'succeeded')

    def phase(self):
        return ut.Transaction(self.app).read().get('phase')

    def test_nothing_new_on_a_fresh_checkout(self):
        for _ in range(2):
            self.assertEqual(self.main(), (0, CURRENT))
        self.assertEqual(self.phase(), 'current')
        self.assertEqual(self.deps_runs(), 0)

    def test_update_prepared_then_already_up_to_date_after_restart(self):
        release = self.publish('release 2')
        self.assertEqual(self.main(), (0, PREPARED))
        self.assertEqual((self.head(), self.phase(), self.deps_runs()),
                         (release, 'awaiting_restart', 1))
        # Not restarted yet: restarting is still the next step.
        self.assertEqual(self.main(), (0, PREPARED))
        ut.Transaction(self.app).request_restart()
        self.assertEqual(self.main(), (0, PREPARED))
        self.boot()
        # The new version started and upstream has nothing newer.
        for _ in range(3):
            self.assertEqual(self.main(), (0, CURRENT))
            self.assertEqual(self.phase(), 'succeeded')
        self.assertEqual(self.deps_runs(), 1)

    def test_after_success_a_newer_release_is_prepared(self):
        self.publish('release 2')
        self.assertEqual(self.main(), (0, PREPARED))
        self.boot()
        self.assertEqual(self.main(), (0, CURRENT))
        release = self.publish('release 3')
        self.assertEqual(self.main(), (0, PREPARED))
        self.assertEqual((self.head(), self.phase(), self.deps_runs()),
                         (release, 'awaiting_restart', 2))

    def test_failures_still_exit_1(self):
        self.publish('release 2')
        (self.app / 'VERSION').write_text('edited\n')
        code, out = self.main()
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith('FATAL error: Local source edits are present.'), out)
        self.git(self.app, 'checkout', '--', 'VERSION')
        with mock.patch.dict(os.environ, PHOS_TEST_DEPS_EXIT='3'):
            code, out = self.main()
        self.assertEqual(code, 1)
        self.assertIn('Dependency repair stopped (exit 3)', out)
        self.assertEqual(self.main(), (0, PREPARED))
        self.boot()
        (self.app / 'VERSION').write_text('edited\n')
        code, out = self.main()
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith('FATAL error: Local source edits are present.'), out)


if __name__ == '__main__':
    unittest.main()
