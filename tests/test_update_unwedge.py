"""Update with a local commit on main, and the interrupted updates it must not change.

Every test builds a real upstream (a bare repository a maintainer clone pushes
to) and an app clone in a temporary directory, with Git's global and system
config switched off, so nothing here reads the developer's Git settings, this
checkout or the network. The app's scripts/post_update.sh is a stub that only
counts its runs in a file outside the repository: no pip, uv or npm runs.

    python3 -B -m unittest discover -s tests -p 'test_update_unwedge.py' -v
"""
from __future__ import annotations

import json
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

DIVERGED = 'Update cannot fast-forward'
CHANGED = 'Source changed outside this update.'
MOVED = 'Source moved outside this update.'
EDITS = 'Local source edits are present.'


class Checkout:
    """An upstream, a maintainer clone that publishes to it, and the app clone."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bare, self.dev, self.app = tmp / 'upstream.git', tmp / 'dev', tmp / 'app'
        self.deps_log = tmp / 'deps.log'
        self.n = 0
        self.git(tmp, 'init', '-q', '--bare', '-b', 'main', str(self.bare))
        self.git(tmp, 'clone', '-q', str(self.bare), str(self.dev))
        self.write(self.dev, 'scripts/post_update.sh', POST_UPDATE_STUB)
        self.publish('release 1')
        self.git(tmp, 'clone', '-q', str(self.bare), str(self.app))

    def git(self, cwd, *args):
        p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
        if p.returncode:
            raise AssertionError(f'git {" ".join(args)}: {p.stdout}{p.stderr}')
        return p.stdout.strip()

    def write(self, repo, name, text):
        path = Path(repo) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, repo, name, text):
        self.n += 1
        self.write(repo, name, text + '\n')
        self.git(repo, 'add', '-A')
        # Fixed dates give the same SHAs on every run.
        stamp = f'{1700000000 + self.n * 60} +0000'
        with mock.patch.dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp):
            self.git(repo, 'commit', '-q', '-m', f'{name}: {text}')
        return self.git(repo, 'rev-parse', 'HEAD')

    def publish(self, text):
        sha = self.commit(self.dev, 'VERSION', text)
        self.git(self.dev, 'push', '-q', 'origin', 'main')
        return sha

    def local_commit(self, text='my local change'):
        return self.commit(self.app, 'local.txt', text)

    def head(self):
        return self.git(self.app, 'rev-parse', 'HEAD')

    def tip(self):
        return self.git(self.bare, 'rev-parse', 'main')

    def contains(self, commit):
        return subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD'],
                              cwd=self.app).returncode == 0

    def tx(self):
        return ut.Transaction(self.app)

    def journal(self):
        path = Path(self.git(self.app, 'rev-parse', '--absolute-git-dir')) / 'phosphene-update.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def write_journal(self, **fields):
        rec = dict(schema=1, id='0' * 32, previous='', previous_ref='', candidate='',
                   upstream='origin/main', phase='fetching', dependencies_started=False)
        rec.update(fields)
        if rec['previous_ref']:
            self.git(self.app, 'update-ref', rec['previous_ref'], rec['previous'])
        return self.tx().save(rec)

    def deps_runs(self):
        return len(self.deps_log.read_text().splitlines()) if self.deps_log.exists() else 0

    def update(self):
        """One Update click, as update_transaction.main() runs it: (phase, error)."""
        try:
            rec = self.tx().prepare()
            if rec['phase'] != 'current':
                rec = self.tx().dependencies(output=lambda line: None)
            return rec['phase'], ''
        except ut.UpdateError as exc:
            return 'error', str(exc)

    def boot(self):
        """Restart and let the candidate acknowledge itself, as the panel does."""
        tx = self.tx()
        tx.request_restart()
        return tx.acknowledge(self.head())['phase']


class Base(unittest.TestCase):
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
        self.c = Checkout(root)

    def assertOpen(self, outcome, *fragments):
        phase, error = outcome
        self.assertEqual(phase, 'error', outcome)
        for fragment in fragments:
            self.assertIn(fragment, error)

    def assertNoHold(self):
        # status() feeds the panel's render hold and restart refusal.
        self.assertEqual(self.c.tx().status(), {})


class LocalCommitOnMain(Base):
    """HEAD already contains upstream, or has diverged from it."""

    def test_ahead_is_current_on_every_click(self):
        head = self.c.local_commit()
        for _ in range(3):
            self.assertEqual(self.c.update(), ('current', ''))
            self.assertEqual(self.c.head(), head)
            self.assertEqual(self.c.journal()['candidate'], head)
            self.assertNoHold()
        self.assertEqual(self.c.deps_runs(), 0)

    def test_ahead_with_a_local_merge(self):
        self.c.publish('release 2')
        self.c.git(self.c.app, 'pull', '-q', '--ff-only')
        self.c.git(self.c.app, 'checkout', '-q', '-b', 'topic')
        self.c.commit(self.c.app, 'topic.txt', 'branch work')
        self.c.git(self.c.app, 'checkout', '-q', 'main')
        self.c.local_commit()
        self.c.git(self.c.app, 'merge', '-q', '--no-edit', 'topic')
        head = self.c.head()
        self.assertEqual(self.c.update(), ('current', ''))
        self.assertEqual(self.c.head(), head)

    def test_ahead_then_upstream_advances_then_rebase(self):
        self.c.local_commit()
        self.assertEqual(self.c.update(), ('current', ''))
        release = self.c.publish('release 2')
        before = self.c.head()
        for _ in range(3):
            self.assertOpen(self.c.update(), DIVERGED, 'Nothing was changed', 'git pull --rebase')
            self.assertEqual(self.c.head(), before)
            self.assertNoHold()
        self.c.git(self.c.app, 'pull', '-q', '--rebase')
        self.assertTrue(self.c.contains(release))
        self.assertEqual(self.c.update(), ('current', ''))
        self.assertEqual(self.c.deps_runs(), 0)

    def test_ahead_then_upstream_advances_then_merge(self):
        self.c.local_commit()
        self.c.publish('release 2')
        self.assertOpen(self.c.update(), DIVERGED)
        self.c.git(self.c.app, 'pull', '-q', '--no-rebase', '--no-edit')
        self.assertEqual(self.c.update(), ('current', ''))

    def test_diverged_refuses_without_pinning_a_candidate(self):
        local = self.c.local_commit()
        self.c.publish('release 2')
        outcome = self.c.update()
        self.assertOpen(outcome, DIVERGED, 'Nothing was changed', 'origin/main')
        self.assertNotIn('error:', outcome[1].lower())   # Pinokio stops on that word
        self.assertEqual(self.c.head(), local)
        rec = self.c.journal()
        self.assertEqual((rec['phase'], rec['candidate'], rec['previous_ref']), ('current', local, ''))
        self.assertNoHold()
        self.assertEqual(self.c.git(self.c.app, 'for-each-ref', 'refs/phosphene'), '')

    def test_diverged_retries_while_upstream_keeps_moving(self):
        local = self.c.local_commit()
        for n in (2, 3, 4):
            self.c.publish(f'release {n}')
            self.assertOpen(self.c.update(), DIVERGED)
            self.assertOpen(self.c.update(), DIVERGED)
            self.assertEqual(self.c.head(), local)
        self.c.git(self.c.app, 'pull', '-q', '--rebase')
        self.assertEqual(self.c.update(), ('current', ''))
        self.c.publish('release 5')
        self.assertOpen(self.c.update(), DIVERGED)
        self.c.git(self.c.app, 'pull', '-q', '--rebase')
        self.assertEqual(self.c.update(), ('current', ''))

    def test_diverged_then_commit_moved_to_a_branch(self):
        local = self.c.local_commit()
        self.c.publish('release 2')
        self.assertOpen(self.c.update(), DIVERGED)
        self.c.git(self.c.app, 'branch', 'mine')
        self.c.git(self.c.app, 'reset', '-q', '--hard', 'HEAD~1')
        release = self.c.publish('release 3')
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.head(), release)
        self.assertEqual(self.c.git(self.c.app, 'rev-parse', 'mine'), local)
        self.assertEqual(self.c.deps_runs(), 1)
        self.assertEqual(self.c.boot(), 'succeeded')

    def test_unrelated_history_is_refused_like_divergence(self):
        self.c.git(self.c.app, 'checkout', '-q', '--orphan', 'other')
        self.c.git(self.c.app, 'rm', '-q', '-r', '--cached', '.')
        self.c.commit(self.c.app, 'other.txt', 'remade history')
        self.c.git(self.c.app, 'branch', '-q', '-u', 'origin/main')
        self.assertOpen(self.c.update(), DIVERGED)
        self.assertNoHold()

    def test_dirty_tree_on_a_local_commit_keeps_the_edit(self):
        self.c.local_commit()
        self.c.write(self.c.app, 'local.txt', 'unsaved\n')
        self.assertOpen(self.c.update(), EDITS)
        self.assertEqual((self.c.app / 'local.txt').read_text(), 'unsaved\n')
        self.c.git(self.c.app, 'commit', '-q', '-am', 'saved')
        self.assertEqual(self.c.update(), ('current', ''))

    def test_succeeded_update_then_local_commit(self):
        self.c.publish('release 2')
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.boot(), 'succeeded')
        self.c.local_commit()
        for _ in range(2):
            self.assertEqual(self.c.update(), ('current', ''))
        self.c.publish('release 3')
        self.assertOpen(self.c.update(), DIVERGED)
        self.c.git(self.c.app, 'pull', '-q', '--rebase')
        self.assertEqual(self.c.update(), ('current', ''))
        self.assertEqual(self.c.deps_runs(), 1)


class RecordFromAnEarlierVersion(Base):
    """Journals the old code left behind on a local commit no longer block Update."""

    def test_ahead_record_at_source_ready(self):
        upstream = self.c.tip()
        local = self.c.local_commit()
        self.c.write_journal(phase='source_ready', previous=local, candidate=upstream,
                             previous_ref='refs/phosphene/updates/' + '0' * 32 + '/previous')
        self.assertEqual(self.c.tx().status()['state'], 'failed')   # held renders before
        self.assertEqual(self.c.update(), ('current', ''))
        self.assertEqual(self.c.head(), local)
        self.assertNoHold()

    def test_ahead_record_after_upstream_advanced_and_user_pulled(self):
        upstream = self.c.tip()
        local = self.c.local_commit()
        self.c.write_journal(phase='source_ready', previous=local, candidate=upstream)
        self.c.publish('release 2')
        self.c.git(self.c.app, 'pull', '-q', '--rebase')
        self.assertEqual(self.c.update(), ('current', ''))

    def test_diverged_record_at_source_pending(self):
        local = self.c.local_commit()
        release = self.c.publish('release 2')
        self.c.git(self.c.app, 'fetch', '-q')
        self.c.write_journal(phase='source_pending', previous=local, candidate=release,
                             previous_ref='refs/phosphene/updates/' + '0' * 32 + '/previous')
        self.assertOpen(self.c.update(), DIVERGED)
        self.assertNoHold()
        self.c.git(self.c.app, 'pull', '-q', '--rebase')
        self.assertEqual(self.c.update(), ('current', ''))

    def test_diverged_record_then_user_follows_upstream(self):
        local = self.c.local_commit()
        release = self.c.publish('release 2')
        self.c.git(self.c.app, 'fetch', '-q')
        self.c.write_journal(phase='source_pending', previous=local, candidate=release)
        self.c.git(self.c.app, 'reset', '-q', '--hard', 'HEAD~1')
        newer = self.c.publish('release 3')
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.head(), newer)

    def test_record_with_repair_started_is_never_released(self):
        upstream = self.c.tip()
        local = self.c.local_commit()
        for phase in ('source_ready', 'dependencies', 'dependencies_failed'):
            self.c.write_journal(phase=phase, previous=local, candidate=upstream,
                                 dependencies_started=True)
            self.assertOpen(self.c.update(), 'outside this update')
            self.assertEqual(self.c.journal()['phase'], phase)


class InterruptedUpdateUnchanged(Base):
    """Paths that behave the same before and after this change."""

    def setUp(self):
        super().setUp()
        self.previous = self.c.head()
        self.release = self.c.publish('release 2')
        self.c.git(self.c.app, 'fetch', '-q')
        self.ref = 'refs/phosphene/updates/' + '0' * 32 + '/previous'

    def pinned(self, phase, **fields):
        return self.c.write_journal(phase=phase, previous=self.previous, candidate=self.release,
                                    previous_ref=self.ref, **fields)

    def test_fresh_equal_is_current(self):
        self.c.git(self.c.app, 'pull', '-q', '--ff-only')
        for _ in range(3):
            self.assertEqual(self.c.update(), ('current', ''))
            self.assertEqual(self.c.journal()['candidate'], self.release)
        self.assertEqual(self.c.deps_runs(), 0)

    def test_fresh_behind_updates_and_then_reports_success(self):
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        rec = self.c.journal()
        self.assertEqual((self.c.head(), rec['previous'], rec['candidate']),
                         (self.release, self.previous, self.release))
        self.assertEqual(self.c.git(self.c.app, 'rev-parse', rec['previous_ref']), self.previous)
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.deps_runs(), 1)
        self.assertEqual(self.c.boot(), 'succeeded')
        self.assertEqual(self.c.tx().prepare()['phase'], 'succeeded')
        newer = self.c.publish('release 3')
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.head(), newer)
        self.assertEqual(self.c.deps_runs(), 2)

    def test_fresh_behind_with_dirty_tree(self):
        self.c.write(self.c.app, 'VERSION', 'edited\n')
        self.assertOpen(self.c.update(), EDITS)
        self.assertEqual(self.c.head(), self.previous)
        self.assertEqual(self.c.journal()['phase'], 'fetching')
        self.c.git(self.c.app, 'checkout', '--', 'VERSION')
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))

    def test_fetching_resumes(self):
        self.c.write_journal(phase='fetching', previous=self.previous)
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.head(), self.release)

    def test_source_pending_at_previous_uses_the_pinned_candidate(self):
        self.pinned('source_pending')
        self.c.publish('release 3')
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.head(), self.release)

    def test_source_pending_at_candidate(self):
        self.pinned('source_pending')
        self.c.git(self.c.app, 'merge', '-q', '--ff-only', self.release)
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.deps_runs(), 1)

    def test_source_pending_moved_elsewhere_is_refused(self):
        self.pinned('source_pending')
        moved = self.c.local_commit()
        for _ in range(2):
            self.assertOpen(self.c.update(), CHANGED, self.ref)
            self.assertEqual(self.c.head(), moved)
            self.assertEqual(self.c.journal()['phase'], 'source_pending')

    def test_source_ready_resumes_repair(self):
        self.pinned('source_ready')
        self.c.git(self.c.app, 'merge', '-q', '--ff-only', self.release)
        self.c.publish('release 3')
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.head(), self.release)

    def test_source_ready_moved_is_refused(self):
        self.pinned('source_ready')
        # Back at previous, and a commit on top of the candidate: both refused.
        self.assertOpen(self.c.update(), MOVED)
        self.c.git(self.c.app, 'merge', '-q', '--ff-only', self.release)
        self.c.local_commit()
        self.assertOpen(self.c.update(), MOVED)
        self.assertEqual(self.c.journal()['phase'], 'source_ready')
        self.assertEqual(self.c.deps_runs(), 0)

    def test_repair_interrupted_or_failed_reruns(self):
        self.c.git(self.c.app, 'merge', '-q', '--ff-only', self.release)
        for phase in ('dependencies', 'dependencies_failed'):
            self.pinned(phase, dependencies_started=True)
            self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.deps_runs(), 2)

    def test_repair_started_then_moved_is_refused(self):
        self.pinned('dependencies_failed', dependencies_started=True)
        self.assertOpen(self.c.update(), MOVED, 'Python packages may have changed')
        self.assertEqual(self.c.deps_runs(), 0)

    def test_repair_failure_is_recorded_and_retried(self):
        with mock.patch.dict(os.environ, PHOS_TEST_DEPS_EXIT='3'):
            self.assertOpen(self.c.update(), 'Dependency repair stopped (exit 3)')
            self.assertEqual(self.c.journal()['phase'], 'dependencies_failed')
            self.assertOpen(self.c.update(), 'Dependency repair stopped (exit 3)')
        self.assertEqual(self.c.update(), ('awaiting_restart', ''))
        self.assertEqual(self.c.deps_runs(), 3)
        self.assertEqual(self.c.head(), self.release)

    def test_awaiting_restart_keeps_its_candidate(self):
        self.c.git(self.c.app, 'merge', '-q', '--ff-only', self.release)
        for phase in ('awaiting_restart', 'restart_requested'):
            self.pinned(phase, dependencies_started=True)
            self.c.publish(f'release after {phase}')
            self.assertEqual(self.c.update(), (phase, ''))
            self.assertEqual(self.c.head(), self.release)
        self.c.local_commit()
        self.assertOpen(self.c.update(), MOVED)
        self.assertEqual(self.c.deps_runs(), 0)


if __name__ == '__main__':
    unittest.main()
