"""Update with uncommitted edits to tracked files: refuse, or stash/discard when asked.

Every test builds a real upstream (a bare repository a maintainer clone pushes
to) and an app clone in a temporary directory, with Git's global and system
config switched off, so nothing here reads the developer's Git settings, this
checkout or the network. The app's scripts/post_update.sh is a stub that only
counts its runs in a file outside the repository: no pip, uv or npm runs. The
route is driven through panel.routes_meta with a stub panel module (it never
imports mlx_ltx_panel); the dialog's decision function runs under node when
node is installed.

    python3 -B -m unittest discover -s tests -p 'test_update_local_edits.py' -v
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import update_transaction as ut  # noqa: E402

POST_UPDATE_STUB = """#!/usr/bin/env bash
echo run >> "$PHOS_TEST_DEPS_LOG"
exit "${PHOS_TEST_DEPS_EXIT:-0}"
"""
EDITS = ('Local source edits are present. Save or commit them before retrying Update. '
         'Nothing was discarded.')
KOREAN = '한글 메모.txt'
MANY = 30


class Checkout:
    """An upstream, a maintainer clone that publishes to it, and the app clone."""

    def __init__(self, tmp: Path):
        tmp.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp
        self.bare, self.dev, self.app = tmp / 'upstream.git', tmp / 'dev', tmp / 'app'
        self.deps_log = Path(os.environ['PHOS_TEST_DEPS_LOG'])
        self.n = 0
        self.git(tmp, 'init', '-q', '--bare', '-b', 'main', str(self.bare))
        self.git(tmp, 'clone', '-q', str(self.bare), str(self.dev))
        files = {
            'scripts/post_update.sh': POST_UPDATE_STUB.encode(),
            'ENVIRONMENT': b'MODEL_DIR=/models\n',
            'run_panel.sh': b'#!/usr/bin/env bash\necho panel\n',
            'crlf.txt': b'one\r\ntwo\r\n',
            'data.bin': bytes(range(256)),
            KOREAN: '원본\n'.encode(),
            '.gitignore': b'*.log\n',
        }
        files.update({f'many/f{i:02}.txt': f'{i}\n'.encode() for i in range(MANY)})
        for name, data in files.items():
            self.write(self.dev, name, data)
        self.publish('release 1')
        self.git(tmp, 'clone', '-q', str(self.bare), str(self.app))

    def git(self, cwd, *args, check=True):
        p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
        if check and p.returncode:
            raise AssertionError(f'git {" ".join(args)}: {p.stdout}{p.stderr}')
        return p.stdout.strip()

    def write(self, repo, name, data):
        path = Path(repo) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())

    def commit(self, repo, message):
        self.n += 1
        self.git(repo, 'add', '-A')
        # Fixed dates give the same SHAs in every checkout and on every run.
        stamp = f'{1700000000 + self.n * 60} +0000'
        with mock.patch.dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp):
            self.git(repo, 'commit', '-q', '-m', message)
        return self.git(repo, 'rev-parse', 'HEAD')

    def publish(self, text, name='VERSION'):
        self.write(self.dev, name, text + '\n')
        sha = self.commit(self.dev, text)
        self.git(self.dev, 'push', '-q', 'origin', 'main')
        return sha

    def head(self):
        return self.git(self.app, 'rev-parse', 'HEAD')

    def tx(self):
        return ut.Transaction(self.app)

    def journal(self):
        path = Path(self.git(self.app, 'rev-parse', '--absolute-git-dir')) / 'phosphene-update.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def stashes(self):
        return self.git(self.app, 'stash', 'list', '--format=%H %s').splitlines()

    def status(self):
        return self.git(self.app, 'status', '--porcelain', '-uno')

    def deps_runs(self):
        return len(self.deps_log.read_text().splitlines()) if self.deps_log.exists() else 0

    def files(self):
        """Every file in the app folder outside .git, as bytes."""
        out = {}
        for path in self.app.rglob('*'):
            rel = path.relative_to(self.app)
            if rel.parts[0] != '.git' and path.is_file():
                out[rel.as_posix()] = path.read_bytes()
        return out

    def tracked_match(self, commit):
        """True when every tracked path's content equals `commit`'s tree."""
        tracked = self.git(self.app, 'ls-tree', '-r', '--name-only', '-z', commit).split('\0')
        for name in filter(None, tracked):
            want = subprocess.run(['git', 'show', f'{commit}:{name}'], cwd=self.app,
                                  capture_output=True, check=True).stdout
            path = self.app / name
            if not path.is_file() or path.read_bytes() != want:
                return False
        return True

    def update(self, edits=None):
        """One Update click: (phase or 'error', message, exception)."""
        try:
            rec = self.tx().prepare() if edits is None else self.tx().prepare(edits)
            if rec['phase'] not in ('current', 'succeeded'):
                rec = self.tx().dependencies(output=lambda line: None)
            return rec['phase'], '', None
        except ut.UpdateError as exc:
            return 'error', str(exc), exc

    def add_bystanders(self):
        """An untracked file, an untracked folder and an ignored file."""
        self.write(self.app, 'notes.txt', 'mine, untracked\n')
        self.write(self.app, 'scratch/idea.txt', 'untracked folder\n')
        self.write(self.app, 'debug.log', 'ignored\n')

    def make_edits(self, kind):
        """Tracked-file edits of one kind; returns the paths Update should name."""
        app = self.app
        if kind == 'unstaged':
            self.write(app, 'ENVIRONMENT', 'MODEL_DIR=/Volumes/AI/models\n')
            return ['ENVIRONMENT']
        if kind == 'staged':
            self.write(app, 'run_panel.sh', '#!/usr/bin/env bash\necho mine\n')
            self.git(app, 'add', 'run_panel.sh')
            return ['run_panel.sh']
        if kind == 'both':
            self.write(app, 'ENVIRONMENT', 'STAGED=1\n')
            self.git(app, 'add', 'ENVIRONMENT')
            self.write(app, 'ENVIRONMENT', 'STAGED=1\nUNSTAGED=2\n')
            self.write(app, 'run_panel.sh', 'echo unstaged\n')
            return ['ENVIRONMENT', 'run_panel.sh']
        if kind == 'deleted':
            (app / 'run_panel.sh').unlink()
            return ['run_panel.sh']
        if kind == 'renamed':
            self.git(app, 'mv', 'run_panel.sh', 'start_panel.sh')
            return ['start_panel.sh', 'run_panel.sh']
        if kind == 'crlf_binary':
            self.write(app, 'crlf.txt', b'one\r\nchanged\r\n')
            self.write(app, 'data.bin', bytes(reversed(range(256))) + b'\0\xff')
            return ['crlf.txt', 'data.bin']
        if kind == 'korean':
            self.write(app, KOREAN, '고쳤어요\n')
            return [KOREAN]
        if kind == 'many':
            for i in range(MANY):
                self.write(app, f'many/f{i:02}.txt', f'{i} changed\n')
            return [f'many/f{i:02}.txt' for i in range(MANY)]
        raise ValueError(kind)


KINDS = ('unstaged', 'staged', 'both', 'deleted', 'renamed', 'crlf_binary', 'korean', 'many')


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / 'gitconfig').write_text('')
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=str(self.root / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1',
                   GIT_TERMINAL_PROMPT='0', GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                   GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid',
                   PHOS_TEST_DEPS_LOG=str(self.root / 'deps.log'))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.c = Checkout(self.root / 'one')

    def assertRefused(self, outcome, *fragments):
        phase, message, _ = outcome
        self.assertEqual(phase, 'error', outcome)
        for fragment in fragments:
            self.assertIn(fragment, message)

    def clean_tree_journal(self):
        """The journal a clean-tree update of the same history ends with."""
        with mock.patch.dict(os.environ, PHOS_TEST_DEPS_LOG=str(self.root / 'twin-deps.log')):
            twin = Checkout(self.root / 'twin')
            twin.publish('release 2')
            self.assertEqual(twin.update()[0], 'awaiting_restart')
            return twin.journal(), twin.head()


def comparable(rec):
    return {k: v for k, v in rec.items() if k not in ('id', 'updated', 'previous_ref', 'set_aside')}


class NoChoice(Base):
    """Without an answer, local edits stop Update exactly as before."""

    def test_refusal_is_unchanged_for_every_kind(self):
        self.c.publish('release 2')
        self.c.add_bystanders()
        previous = self.c.head()
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.c.make_edits(kind)
                before = self.c.files()
                for _ in range(2):
                    phase, message, exc = self.c.update()
                    self.assertEqual((phase, message), ('error', EDITS))
                    self.assertIsInstance(exc, ut.UpdateError)
                    self.assertEqual(self.c.files(), before)
                    self.assertEqual(self.c.head(), previous)
                    self.assertEqual(self.c.journal()['phase'], 'fetching')
                    self.assertEqual(self.c.stashes(), [])
                self.c.git(self.c.app, 'reset', '-q', '--hard')
        self.assertEqual(self.c.deps_runs(), 0)

    def test_refusal_after_a_succeeded_update(self):
        self.c.publish('release 2')
        self.assertEqual(self.c.update()[0], 'awaiting_restart')
        tx = self.c.tx()
        tx.request_restart()
        tx.acknowledge(self.c.head())
        self.c.make_edits('unstaged')
        self.assertEqual(self.c.update()[:2], ('error', EDITS))
        self.assertEqual(self.c.journal()['phase'], 'succeeded')

    def test_main_prints_the_same_refusal(self):
        self.c.publish('release 2')
        self.c.make_edits('unstaged')
        out = []
        with mock.patch.object(ut, '__file__', str(self.c.app / 'update_transaction.py')), \
                mock.patch('builtins.print', lambda *a, **k: out.append(' '.join(map(str, a)))):
            code = ut.main()
        self.assertEqual((code, out), (1, ['FATAL error: ' + EDITS]))


class Refusal(Base):
    """The refusal names the files when it may be answered."""

    def test_names_the_edited_files(self):
        self.c.publish('release 2')
        for kind in KINDS:
            with self.subTest(kind=kind):
                expected = self.c.make_edits(kind)
                exc = self.c.update()[2]
                self.assertIsInstance(exc, ut.LocalEdits)
                self.assertEqual(sorted(exc.files), sorted(expected))
                self.c.git(self.c.app, 'reset', '-q', '--hard')

    def test_untracked_and_ignored_are_not_edits(self):
        self.c.publish('release 2')
        self.c.add_bystanders()
        self.assertEqual(self.c.update()[0], 'awaiting_restart')

    def test_in_flight_refusal_is_not_offered(self):
        release = self.c.publish('release 2')
        self.c.git(self.c.app, 'fetch', '-q')
        ref = 'refs/phosphene/updates/' + '0' * 32 + '/previous'
        self.c.git(self.c.app, 'update-ref', ref, self.c.head())
        self.c.tx().save(dict(schema=1, id='0' * 32, previous=self.c.head(), previous_ref=ref,
                              candidate=release, upstream='origin/main', phase='source_pending',
                              dependencies_started=False))
        self.c.make_edits('unstaged')
        phase, message, exc = self.c.update()
        self.assertEqual((phase, message), ('error', EDITS))
        self.assertNotIsInstance(exc, ut.LocalEdits)


class Stash(Base):
    def setUp(self):
        super().setUp()
        self.release = self.c.publish('release 2')
        self.c.add_bystanders()

    def test_every_kind_is_set_aside_and_comes_back(self):
        want_journal, want_head = self.clean_tree_journal()
        for kind in KINDS:
            with self.subTest(kind=kind):
                if kind != KINDS[0]:
                    self.doCleanups()   # a fresh checkout per kind
                    self.setUp()
                expected = self.c.make_edits(kind)
                dirty = self.c.files()
                self.assertEqual(self.c.update('stash'), ('awaiting_restart', '', None))
                # The same end state as a clean-tree update.
                self.assertEqual(self.c.head(), want_head)
                self.assertEqual(comparable(self.c.journal()), comparable(want_journal))
                self.assertEqual(self.c.status(), '')
                self.assertTrue(self.c.tracked_match(self.release))
                self.assertEqual(self.c.deps_runs(), 1)
                # Bystanders untouched.
                after = self.c.files()
                for name in ('notes.txt', 'scratch/idea.txt', 'debug.log'):
                    self.assertEqual(after[name], dirty[name])
                # One stash entry, recognisable, named in the record.
                stashes = self.c.stashes()
                self.assertEqual(len(stashes), 1)
                self.assertRegex(stashes[0], r' On main: Phosphene update \d{4}-\d\d-\d\d \d\d:\d\d UTC$')
                aside = self.c.journal()['set_aside']
                self.assertEqual((aside['how'], sorted(aside['files'])), ('stash', sorted(expected)))
                self.assertEqual(aside['commit'], stashes[0].split()[0])
                self.assertIn(aside['message'], stashes[0])
                # Not popped by Update; pop brings every edit back.
                self.c.git(self.c.app, 'stash', 'pop')
                restored = self.c.files()
                restored.pop('VERSION'), dirty.pop('VERSION')
                self.assertEqual(restored, dirty)

    def test_answer_after_the_refusal(self):
        # The page's flow: the refusal leaves the record at 'fetching'; the
        # answer then resumes that same record.
        for answer in ('stash', 'discard'):
            with self.subTest(answer=answer):
                if answer != 'stash':
                    self.doCleanups()
                    self.setUp()
                self.c.make_edits('both')
                for _ in range(2):
                    self.assertEqual(self.c.update()[:2], ('error', EDITS))
                    self.assertEqual(self.c.journal()['phase'], 'fetching')
                self.assertEqual(self.c.update(answer), ('awaiting_restart', '', None))
                self.assertEqual(self.c.head(), self.release)
                self.assertEqual(len(self.c.stashes()), 1 if answer == 'stash' else 0)

    def test_stash_keeps_earlier_stashes(self):
        self.c.write(self.c.app, 'ENVIRONMENT', 'OLDER=1\n')
        self.c.git(self.c.app, 'stash', 'push', '-q', '-m', 'my own stash')
        self.c.make_edits('staged')
        self.assertEqual(self.c.update('stash')[0], 'awaiting_restart')
        stashes = self.c.stashes()
        self.assertEqual(len(stashes), 2)
        self.assertIn('my own stash', stashes[1])

    def test_status_and_retry_messages_name_the_stash(self):
        self.c.make_edits('unstaged')
        with mock.patch.dict(os.environ, PHOS_TEST_DEPS_EXIT='3'):
            phase, message, _ = self.c.update('stash')
        self.assertEqual(phase, 'error')
        self.assertIn('Dependency repair stopped (exit 3)', message)
        self.assertIn('Your edits are in the Git stash as "Phosphene update', message)
        self.assertIn('git stash pop', self.c.tx().status()['error'])
        # Retry needs no answer: the tree is clean and the candidate is pinned.
        self.assertEqual(self.c.update(), ('awaiting_restart', '', None))
        self.assertEqual(len(self.c.stashes()), 1)
        self.assertEqual(self.c.deps_runs(), 2)

    def test_note_ends_with_the_update(self):
        self.c.make_edits('unstaged')
        self.assertEqual(self.c.update('stash')[0], 'awaiting_restart')
        tx = self.c.tx()
        tx.request_restart()
        tx.acknowledge(self.c.head())
        self.c.git(self.c.app, 'stash', 'pop', '-q')
        self.c.git(self.c.app, 'commit', '-q', '-am', 'keep it')
        self.c.publish('release 3')
        self.c.bare.rename(self.c.tmp / 'away.git')
        phase, message, _ = self.c.update()
        self.assertEqual(phase, 'error')
        self.assertNotIn('stash', message)


class Discard(Base):
    def setUp(self):
        super().setUp()
        self.release = self.c.publish('release 2')
        self.c.add_bystanders()

    def test_every_kind_is_discarded(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                if kind != KINDS[0]:
                    self.doCleanups()   # a fresh checkout per kind
                    self.setUp()
                expected = self.c.make_edits(kind)
                dirty = self.c.files()
                self.assertEqual(self.c.update('discard'), ('awaiting_restart', '', None))
                self.assertEqual(self.c.head(), self.release)
                self.assertEqual(self.c.status(), '')
                self.assertTrue(self.c.tracked_match(self.release))
                after = self.c.files()
                for name in ('notes.txt', 'scratch/idea.txt', 'debug.log'):
                    self.assertEqual(after[name], dirty[name])
                if kind == 'renamed':
                    self.assertNotIn('start_panel.sh', after)
                # Not a stash in disguise: no stash entry, no ref keeps the snapshot.
                self.assertEqual(self.c.stashes(), [])
                aside = self.c.journal()['set_aside']
                self.assertEqual((aside['how'], sorted(aside['files'])), ('discard', sorted(expected)))
                refs = self.c.git(self.c.app, 'for-each-ref', '--contains', aside['commit'])
                self.assertEqual(refs, '')
                self.assertEqual(self.c.git(self.c.app, 'cat-file', '-t', aside['commit']), 'commit')

    def test_support_can_still_apply_the_snapshot(self):
        self.c.make_edits('unstaged')
        self.assertEqual(self.c.update('discard')[0], 'awaiting_restart')
        snapshot = self.c.journal()['set_aside']['commit']
        self.c.git(self.c.app, 'stash', 'apply', snapshot)
        self.assertEqual((self.c.app / 'ENVIRONMENT').read_text(), 'MODEL_DIR=/Volumes/AI/models\n')


class Answers(Base):
    """Answers that must change nothing, and answers outside the safe window."""

    def test_unknown_answers_change_nothing(self):
        self.c.publish('release 2')
        self.c.make_edits('unstaged')
        before = self.c.files()
        for answer in ('cancel', '', 'STASH', 'reset', 'stash; rm -rf /'):
            with self.subTest(answer=answer):
                phase, message, _ = self.c.update(answer)
                self.assertEqual(phase, 'error')
                self.assertIn('Unknown choice for local edits', message)
                self.assertEqual(self.c.journal(), {})
                self.assertEqual(self.c.files(), before)
                self.assertEqual(self.c.stashes(), [])

    def test_answer_with_a_clean_tree_is_an_ordinary_update(self):
        release = self.c.publish('release 2')
        for answer in ('stash', 'discard'):
            with self.subTest(answer=answer):
                self.assertEqual(self.c.update(answer)[0], 'awaiting_restart')
                self.assertNotIn('set_aside', self.c.journal())
                self.assertEqual((self.c.head(), self.c.stashes()), (release, []))
                tx = self.c.tx()
                tx.request_restart()
                tx.acknowledge(self.c.head())
                release = self.c.publish(f'release after {answer}')

    def test_answer_when_already_up_to_date_keeps_the_edits(self):
        self.c.make_edits('unstaged')
        before = self.c.files()
        for answer in ('stash', 'discard'):
            self.assertEqual(self.c.update(answer)[0], 'current')
            self.assertEqual(self.c.files(), before)
            self.assertEqual(self.c.stashes(), [])

    def test_answer_on_a_diverged_copy_keeps_the_edits(self):
        self.c.write(self.c.app, 'local.txt', 'local commit\n')
        self.c.commit(self.c.app, 'local work')
        self.c.publish('release 2')
        self.c.make_edits('unstaged')
        before = self.c.files()
        for answer in ('stash', 'discard'):
            self.assertRefused(self.c.update(answer), 'cannot fast-forward', 'Nothing was changed')
            self.assertEqual(self.c.files(), before)
            self.assertEqual(self.c.stashes(), [])

    def test_answer_while_an_update_is_in_flight_is_not_honoured(self):
        release = self.c.publish('release 2')
        self.c.git(self.c.app, 'fetch', '-q')
        previous = self.c.head()
        ref = 'refs/phosphene/updates/' + '0' * 32 + '/previous'
        self.c.git(self.c.app, 'update-ref', ref, previous)
        for phase in ('source_pending', 'source_ready', 'dependencies_failed'):
            with self.subTest(phase=phase):
                if phase != 'source_pending':
                    self.c.git(self.c.app, 'reset', '-q', '--hard')
                    self.c.git(self.c.app, 'merge', '-q', '--ff-only', release)
                self.c.tx().save(dict(schema=1, id='0' * 32, previous=previous, previous_ref=ref,
                                      candidate=release, upstream='origin/main', phase=phase,
                                      dependencies_started=phase == 'dependencies_failed'))
                self.c.make_edits('unstaged')
                before = self.c.files()
                for answer in ('stash', 'discard'):
                    phase_out, message, exc = self.c.update(answer)
                    self.assertEqual(phase_out, 'error')
                    self.assertIn(EDITS, message)
                    self.assertNotIsInstance(exc, ut.LocalEdits)
                    self.assertEqual(self.c.files(), before)
                    self.assertEqual(self.c.stashes(), [])
                    self.assertEqual(self.c.journal()['phase'], phase)


class Failures(Base):
    def setUp(self):
        super().setUp()
        self.release = self.c.publish('release 2')

    def test_stash_fails(self):
        self.c.make_edits('both')
        before = self.c.files()
        lock = self.c.app / '.git' / 'index.lock'
        lock.write_text('')
        phase, message, _ = self.c.update('stash')
        self.assertEqual(phase, 'error')
        self.assertIn('Could not set the local edits aside', message)
        self.assertIn('Nothing was set aside or discarded.', message)
        rec = self.c.journal()
        self.assertEqual(rec['phase'], 'fetching')
        self.assertNotIn('set_aside', rec)
        lock.unlink()
        self.assertEqual(self.c.files(), before)
        self.assertEqual(self.c.update('stash')[0], 'awaiting_restart')
        self.assertEqual(len(self.c.stashes()), 1)

    def test_discard_fails(self):
        self.c.make_edits('unstaged')
        before = self.c.files()
        lock = self.c.app / '.git' / 'index.lock'
        lock.write_text('')
        phase, message, _ = self.c.update('discard')
        self.assertEqual(phase, 'error')
        self.assertIn('Could not set the local edits aside', message)
        lock.unlink()
        if 'git stash apply' in message:
            # Interrupted after the snapshot: the message names it.
            self.assertRegex(message, r'git stash apply [0-9a-f]{40}')
        else:
            self.assertEqual(self.c.files(), before)
        self.assertEqual(self.c.journal()['phase'], 'fetching')
        self.assertEqual(self.c.update('discard')[0], 'awaiting_restart')

    def test_fetch_fails_before_anything_is_set_aside(self):
        # The answer is honoured after the fetch, so a failed fetch leaves
        # the edits where they are.
        self.c.make_edits('unstaged')
        before = self.c.files()
        moved = self.c.tmp / 'upstream-away.git'
        self.c.bare.rename(moved)
        phase, message, _ = self.c.update('stash')
        self.assertEqual(phase, 'error')
        self.assertNotIn('stash', message.lower())
        self.assertEqual((self.c.files(), self.c.stashes()), (before, []))
        self.assertEqual(self.c.journal()['phase'], 'fetching')
        moved.rename(self.c.bare)
        self.assertEqual(self.c.update('stash')[0], 'awaiting_restart')

    def test_source_move_fails_after_the_stash(self):
        # The release adds a file an untracked file stands in the way of.
        self.c.publish('new file', name='added.txt')
        self.c.write(self.c.app, 'added.txt', 'mine\n')
        self.c.make_edits('unstaged')
        phase, message, _ = self.c.update('stash')
        self.assertEqual(phase, 'error')
        self.assertIn('Your edits are in the Git stash as "Phosphene update', message)
        self.assertEqual(self.c.journal()['phase'], 'source_pending')
        self.assertEqual(len(self.c.stashes()), 1)
        again = self.c.update()
        self.assertIn('Your edits are in the Git stash', again[1])
        (self.c.app / 'added.txt').unlink()
        self.assertEqual(self.c.update()[0], 'awaiting_restart')
        self.assertEqual(len(self.c.stashes()), 1)

    def test_dependencies_fail_after_the_stash(self):
        self.c.make_edits('korean')
        with mock.patch.dict(os.environ, PHOS_TEST_DEPS_EXIT='5'):
            phase, message, _ = self.c.update('stash')
            self.assertEqual(phase, 'error')
            self.assertIn('Your edits are in the Git stash', message)
            self.assertEqual(self.c.journal()['phase'], 'dependencies_failed')
            self.assertIn('Your edits are in the Git stash', self.c.update()[1])
        self.assertEqual(self.c.update(), ('awaiting_restart', '', None))
        self.c.git(self.c.app, 'stash', 'pop')
        self.assertEqual((self.c.app / KOREAN).read_text(), '고쳤어요\n')


class FakeHandler:
    def __init__(self, body=''):
        self.body = body
        self.sent = []

    def _read_form_body(self):
        return self.body, parse_qs(self.body)

    def _json(self, payload, status=200):
        self.sent.append((status, payload))


class Route(Base):
    """POST /version/pull through panel.routes_meta with a stub panel module."""

    def setUp(self):
        super().setUp()
        import panel.routes_meta as routes_meta
        self.routes = routes_meta
        c = self.c
        self.started = []
        P = types.SimpleNamespace(
            update_transaction=ut, ROOT=c.app,
            _dist=types.SimpleNamespace(is_mac_app=lambda: False, MAC_APP_UPDATES='use the app'),
            _git_capture=lambda args: c.git(c.app, *args),
            _detect_local_install_state=lambda: None,
            get_version_state=lambda: {}, _VERSION_LOCK=threading.Lock(), _VERSION_STATE={},
            _read_local_version=lambda: (c.app / 'VERSION').read_text().strip(),
            _settings_set_internal=lambda **kw: None,
            post_update_start=lambda trigger: (self.started.append(trigger) or (202, {'ok': True})))
        self.P = P
        patcher = mock.patch.object(routes_meta, 'P', P)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pull(self, **form):
        h = FakeHandler(urlencode(form))
        self.routes.post_version_pull(h, '/version/pull', {}, 'application/x-www-form-urlencoded')
        self.assertEqual(len(h.sent), 1)
        return h.sent[0]

    def test_importing_the_routes_does_not_import_the_panel(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import panel.routes_meta; '
                'print("mlx_ltx_panel" in sys.modules)')
        out = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT)],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, 'False')

    def test_refusal_payload(self):
        self.c.publish('release 2')
        self.c.make_edits('many')
        self.c.make_edits('korean')
        status, payload = self.pull()
        self.assertEqual(status, 409)
        self.assertEqual((payload['ok'], payload['code'], payload['error']), (False, 'local_edits', EDITS))
        self.assertEqual(payload['files_total'], MANY + 1)
        self.assertEqual(len(payload['files']), self.routes.VERSION_PULL_FILES_MAX)
        self.assertIn('state', payload)
        self.assertEqual(self.started, [])
        json.dumps(payload)

    def test_stash_answer(self):
        release = self.c.publish('release 2')
        self.c.make_edits('both')
        # As the page does it: the refusal first, then the answer.
        self.assertEqual(self.pull()[0], 409)
        status, payload = self.pull(edits='stash')
        self.assertEqual(status, 200)
        self.assertTrue(payload['ok'] and payload['moved'])
        aside = payload['set_aside']
        self.assertEqual((aside['how'], aside['files_total']), ('stash', 2))
        self.assertTrue(aside['message'].startswith('Phosphene update '))
        self.assertEqual(self.started, ['update_now'])
        self.assertEqual(self.c.head(), release)

    def test_discard_answer(self):
        self.c.publish('release 2')
        self.c.make_edits('unstaged')
        self.assertEqual(self.pull()[0], 409)
        status, payload = self.pull(edits='discard')
        self.assertEqual((status, payload['set_aside']['how']), (200, 'discard'))
        self.assertEqual(self.c.stashes(), [])

    def test_bad_answers_change_nothing(self):
        self.c.publish('release 2')
        self.c.make_edits('unstaged')
        before = self.c.files()
        for answer in ('cancel', 'nope', 'Stash'):
            status, payload = self.pull(edits=answer)
            self.assertEqual((status, payload['code']), (400, 'bad_choice'))
        self.assertEqual((self.c.files(), self.c.journal(), self.c.stashes()), (before, {}, []))
        # An empty field is no answer at all: the ordinary refusal.
        status, payload = self.pull(edits='')
        self.assertEqual((status, payload['code']), (409, 'local_edits'))
        self.assertEqual((self.c.files(), self.c.stashes()), (before, []))

    def test_no_answer_and_nothing_to_do(self):
        status, payload = self.pull()
        self.assertEqual((status, payload['ok'], payload['moved'], payload['set_aside']), (200, True, False, None))
        self.c.make_edits('unstaged')
        status, payload = self.pull(edits='stash')
        self.assertEqual((status, payload['set_aside']), (200, None))
        self.assertEqual(self.c.stashes(), [])

    def test_in_flight_refusal_has_no_code(self):
        self.c.publish('release 2')
        self.c.make_edits('unstaged')
        self.c.git(self.c.app, 'stash', 'push', '-q')
        self.pull()                                   # pins release 2
        self.assertEqual(self.c.journal()['phase'], 'source_ready')
        self.c.git(self.c.app, 'stash', 'pop', '-q')
        self.c.tx().save(self.c.journal(), phase='source_pending')
        status, payload = self.pull(edits='stash')
        self.assertEqual(status, 409)
        self.assertNotIn('code', payload)
        self.assertEqual(payload['error'], EDITS)

    def test_mac_app_is_untouched(self):
        self.P._dist.is_mac_app = lambda: True
        for form in ({}, {'edits': 'stash'}, {'edits': 'bogus'}):
            status, payload = self.pull(**form)
            self.assertEqual((status, payload.get('app_updates')), (409, True))
        self.assertEqual(self.c.journal(), {})

    def test_not_on_main_is_untouched(self):
        self.c.git(self.c.app, 'checkout', '-q', '-b', 'mine')
        status, payload = self.pull(edits='stash')
        self.assertEqual(status, 409)
        self.assertIn("Update needs main; this copy is on 'mine'", payload['error'])


NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'node is not installed')
class Dialog(unittest.TestCase):
    """The page's decision function, run as written in webapp/js/health.js."""

    @staticmethod
    def function_source(path, name):
        src = (ROOT / path).read_text(encoding='utf-8')
        start = src.index(f'function {name}(')
        return src[start:src.index('\n}\n', start) + 2]

    def model(self, step, payload):
        js = '\n'.join([
            # The English path of the page's own T()/TP().
            'const _STR = {}; const _plural = new Intl.PluralRules("en");',
            self.function_source('webapp/js/i18n.js', '_fill'),
            self.function_source('webapp/js/i18n.js', 'T'),
            self.function_source('webapp/js/i18n.js', 'TP'),
            self.function_source('webapp/js/health.js', '_updEditsDialog'),
            'const [step, payload] = JSON.parse(process.argv[1]);',
            'process.stdout.write(JSON.stringify(_updEditsDialog(step, payload)));',
        ])
        out = subprocess.run([NODE, '-e', js, json.dumps([step, payload])],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_first_question(self):
        m = self.model('ask', {'code': 'local_edits', 'files': ['ENVIRONMENT', 'run_panel.sh'],
                               'files_total': 2})
        self.assertEqual(m['title'], 'You changed 2 files in this copy')
        self.assertTrue(m['body'].startswith('\u2022 ENVIRONMENT\n\u2022 run_panel.sh\n\n'))
        self.assertIn('git stash pop', m['body'])
        self.assertEqual((m['primaryLabel'], m['secondaryLabel'], m['tertiaryLabel']),
                         ('Set aside and update', 'Cancel', 'Discard my changes\u2026'))
        self.assertTrue(m['focusPrimary'])
        self.assertNotIn('tone', m)

    def test_one_file(self):
        m = self.model('ask', {'files': [KOREAN], 'files_total': 1})
        self.assertEqual(m['title'], 'You changed 1 file in this copy')
        self.assertIn(KOREAN, m['body'])

    def test_list_cap(self):
        files = [f'many/f{i:02}.txt' for i in range(20)]
        m = self.model('ask', {'files': files, 'files_total': 31})
        self.assertEqual(m['title'], 'You changed 31 files in this copy')
        lines = m['body'].split('\n\n')[0].split('\n')
        self.assertEqual(len(lines), 21)
        self.assertEqual(lines[-1], '\u2026and 11 more files')
        m = self.model('discard', {'files': files, 'files_total': 21})
        self.assertIn('\u2026and 1 more file\n', m['body'])

    def test_names_stay_text(self):
        name = '<img src=x onerror=alert(1)>{count}&amp;.txt'
        m = self.model('ask', {'files': [name], 'files_total': 1})
        self.assertIn('\u2022 ' + name + '\n', m['body'])
        # The shared dialog writes title and body with textContent, never HTML.
        show = self.function_source('webapp/js/health.js', '_phModalShow')
        self.assertIn("getElementById('phModalBody').textContent", show)
        self.assertIn("getElementById('phModalTitle').textContent", show)
        self.assertNotIn('innerHTML', show)

    def test_discard_confirmation(self):
        m = self.model('discard', {'files': ['ENVIRONMENT', 'run_panel.sh'], 'files_total': 2})
        self.assertEqual(m['tone'], 'danger')
        self.assertEqual(m['title'], 'Your changes to 2 files will be lost')
        self.assertIn('\u2022 ENVIRONMENT', m['body'])
        self.assertIn('cannot be undone', m['body'])
        self.assertEqual((m['primaryLabel'], m['secondaryLabel']), ('Discard and update', 'Keep my changes'))
        self.assertNotIn('tertiaryLabel', m)
        self.assertFalse(m.get('focusPrimary'))

    def test_done_notices(self):
        m = self.model('done', {'how': 'stash', 'message': 'Phosphene update 2026-11-17 09:00 UTC',
                                'files': ['ENVIRONMENT'], 'files_total': 1})
        self.assertEqual(m['title'], 'Your change to 1 file is set aside')
        self.assertIn('"Phosphene update 2026-11-17 09:00 UTC"', m['body'])
        self.assertIn('git stash pop', m['body'])
        self.assertIn('does not do this for you', m['body'])
        m = self.model('done', {'how': 'discard', 'files': ['a', 'b'], 'files_total': 2})
        self.assertEqual(m['title'], 'Your changes to 2 files were discarded')
        self.assertNotIn('stash', m['body'])

    def test_garbage_payload(self):
        m = self.model('ask', {'files': 'not a list', 'files_total': 'x'})
        self.assertEqual(m['title'], 'You changed 0 files in this copy')

    def test_page_wiring(self):
        src = (ROOT / 'webapp/js/health.js').read_text(encoding='utf-8')
        pull = self.function_source('webapp/js/health.js', 'versionDoPull')
        # Only this refusal opens the dialog; everything else keeps the alert.
        self.assertIn("if (data && data.code === 'local_edits') {", pull)
        self.assertIn('if (_updEditsAsk(data)) return;', pull)
        self.assertIn('alert(`Pull failed', pull)
        self.assertIn('new URLSearchParams({edits: opts.edits})', pull)
        ask = self.function_source('webapp/js/health.js', '_updEditsAsk')
        self.assertIn("edits: 'stash'", ask)
        self.assertIn("edits: 'discard'", ask)
        self.assertRegex(ask, re.compile(r"onTertiary: \(\) => _phModalShow\(Object\.assign\(_updEditsDialog\('discard'"))
        self.assertIn('id="phModalTertiary" hidden', (ROOT / 'webapp/index.html').read_text(encoding='utf-8'))
        self.assertIn('#phModalTertiary', (ROOT / 'webapp/style/panel.css').read_text(encoding='utf-8'))
        self.assertIn('_updEditsDialog', src)


if __name__ == '__main__':
    unittest.main()
