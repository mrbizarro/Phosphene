"""Durable source update and dependency repair, shared by both Update buttons.

The previous Git tree is retained, not the Python environment. After dependency
work starts, recovery means retrying the pinned candidate's repair script. No
package rollback is promised. Journals and locks are local to this checkout.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import threading
import uuid


class UpdateError(RuntimeError):
    pass


class Busy(UpdateError):
    pass


class Transaction:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.fd = None
        self.directory = Path(self.git('rev-parse', '--absolute-git-dir'))
        self.path = self.directory / 'phosphene-update.json'

    def command(self, args, *, env=None):
        p = subprocess.run(args, cwd=self.root, env=env, capture_output=True,
                           text=True, errors='replace', timeout=180,
                           pass_fds=(() if self.fd is None else (self.fd,)))
        if p.returncode:
            raise UpdateError((p.stdout + p.stderr).strip() or f'Command stopped (exit {p.returncode}).')
        return p.stdout.strip()

    def git(self, *args):
        return self.command(['git', *args])

    def read(self):
        try:
            rec = json.loads(self.path.read_text())
            if (not isinstance(rec, dict) or rec.get('schema') != 1
                    or rec.get('phase') not in ('fetching', 'source_pending', 'source_ready',
                        'dependencies', 'dependencies_failed', 'awaiting_restart',
                        'restart_requested', 'succeeded', 'current')):
                raise ValueError('unknown journal format')
            return rec
        except FileNotFoundError:
            return {}
        except (ValueError, OSError) as exc:
            raise UpdateError(f'Cannot read {self.path}. Keep this file for recovery: {exc}') from exc

    def save(self, rec, **changes):
        rec.update(changes, updated=time.time())
        tmp = self.path.with_suffix('.tmp')
        with tmp.open('w') as f:
            json.dump(rec, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return rec

    @contextlib.contextmanager
    def locked(self):
        with (self.directory / 'phosphene-update.lock').open('a') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Busy('Another update or dependency repair is still running. Wait for it to finish.') from exc
            self.fd = f.fileno()
            try:
                yield
            finally:
                self.fd = None
                # Do not explicitly unlock: a surviving child inherits the lock.

    def recovery(self, rec):
        ref = rec.get('previous_ref', '')
        cmd = 'cd ' + shlex.quote(str(self.root)) + ' && bash scripts/pinokio/update_transaction.sh'
        note = ('Python packages may have changed; restoring source alone does not restore them.'
                if rec.get('dependencies_started') else 'Dependency repair has not started.')
        return f'Retry this same update: {cmd}. Previous source: {ref or rec.get("previous", "unknown")}. {note}'

    def clean(self):
        if self.git('status', '--porcelain', '-uno'):
            raise UpdateError('Local source edits are present. Save or commit them before retrying Update. Nothing was discarded.')

    def prepare(self):
        with self.locked():
            rec = self.read()
            if rec and rec['phase'] not in ('succeeded', 'current'):
                return self._resume_source(rec)
            if rec.get('phase') == 'succeeded':
                self.clean()
                self.git('fetch', rec['upstream'].split('/')[0])
                if (self.git('rev-parse', 'HEAD') == rec['candidate']
                        and self.git('rev-parse', rec['upstream'] + '^{commit}') == rec['candidate']):
                    return rec
            old = self.git('rev-parse', 'HEAD')
            upstream = self.git('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
            rec = dict(schema=1, id=uuid.uuid4().hex, previous=old,
                       previous_ref='', candidate='', upstream=upstream,
                       phase='fetching', dependencies_started=False)
            self.save(rec)
            return self._resume_source(rec)

    def _resume_source(self, rec):
        phase = rec['phase']
        if phase in ('awaiting_restart', 'restart_requested', 'succeeded', 'dependencies', 'dependencies_failed', 'source_ready'):
            if self.git('rev-parse', 'HEAD') != rec['candidate']:
                raise UpdateError('Source moved outside this update. ' + self.recovery(rec))
            return rec
        # Fetch does not change source. A retry may fetch again, but once a
        # candidate is recorded every retry uses that exact commit.
        if phase == 'fetching':
            self.clean()
            head = self.git('rev-parse', 'HEAD')
            if head != rec['previous']:
                # No source/dependency mutation has begun. A user may have
                # committed the edits that blocked the first attempt.
                self.save(rec, previous=head)
            self.git('fetch', rec['upstream'].split('/')[0])
            target = self.git('rev-parse', rec['upstream'] + '^{commit}')
            if target == rec['previous']:
                return self.save(rec, phase='current', candidate=target)
            ref = 'refs/phosphene/updates/' + rec['id'] + '/previous'
            self.git('update-ref', ref, rec['previous'])
            self.save(rec, phase='source_pending', candidate=target, previous_ref=ref)
        self.clean()
        head = self.git('rev-parse', 'HEAD')
        if head not in (rec['previous'], rec['candidate']):
            raise UpdateError('Source changed outside this update. ' + self.recovery(rec))
        if head != rec['candidate']:
            # No reset fallback: local commits and edits are never disposable.
            # --no-overwrite-ignore also protects files hidden by .gitignore.
            self.git('merge', '--ff-only', '--no-overwrite-ignore', rec['candidate'])
        self.clean()
        return self.save(rec, phase='source_ready')

    def dependencies(self, env=None, output=print, timeout=3 * 3600):
        with self.locked():
            rec = self.read()
            if not rec:
                raise UpdateError('No update is ready. Run Update first.')
            if rec['phase'] in ('awaiting_restart', 'restart_requested', 'succeeded', 'current'):
                return rec
            if rec['phase'] not in ('source_ready', 'dependencies', 'dependencies_failed'):
                raise UpdateError('Source update is incomplete. Run Update again before repairing dependencies.')
            self.clean()
            if self.git('rev-parse', 'HEAD') != rec['candidate']:
                raise UpdateError('Source changed outside this update. ' + self.recovery(rec))
            self.save(rec, phase='dependencies', dependencies_started=True)
            script = self.root / 'scripts/post_update.sh'
            try:
                if not script.is_file():
                    raise UpdateError('scripts/post_update.sh is missing.')
                # Child holds the lock if the updater dies. Incomplete repair
                # reruns on retry; completed repair is never run twice.
                with subprocess.Popen(['bash', str(script), str(self.root)],
                                      cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, errors='replace', pass_fds=(self.fd,)) as proc:
                    expired = threading.Event()
                    def expire():
                        expired.set()
                        # Only this owned process. Any surviving child keeps
                        # the inherited lock until it exits.
                        proc.kill()
                    timer = threading.Timer(timeout, expire)
                    timer.daemon = True
                    timer.start()
                    tail = []
                    try:
                        for line in proc.stdout:
                            tail = (tail + [line.rstrip()])[-12:]
                            # Pinokio aborts on these words before checking status.
                            # Publish the fatal line only AFTER the durable receipt.
                            output(re.sub(r'error:|errno ', 'problem: ', line.rstrip(), flags=re.I))
                        rc = proc.wait()
                    finally:
                        timer.cancel()
                if expired.is_set():
                    raise UpdateError('Dependency repair exceeded its time limit.')
                if rc:
                    detail = next((s.split('PHOSPHENE UPDATE FAILED:', 1)[1].strip()
                                   for s in tail if 'PHOSPHENE UPDATE FAILED:' in s), '')
                    raise UpdateError(f'Dependency repair stopped (exit {rc})' + (f': {detail}' if detail else '.'))
            except Exception as exc:
                message = str(exc) + ' ' + self.recovery(rec)
                self.save(rec, phase='dependencies_failed', error=message)
                raise UpdateError(message) from exc
            return self.save(rec, phase='awaiting_restart', error='')

    def request_restart(self):
        with self.locked():
            rec = self.read()
            if rec.get('phase') in ('awaiting_restart', 'restart_requested'):
                self.clean()
                if self.git('rev-parse', 'HEAD') != rec['candidate']:
                    raise UpdateError('Source changed before restart. ' + self.recovery(rec))
                self.save(rec, phase='restart_requested')
            elif rec and rec['phase'] not in ('current', 'succeeded'):
                raise UpdateError(self.recovery(rec))

    def acknowledge(self, boot_sha):
        """Called only by a candidate that bound its HTTP listener."""
        with self.locked():
            rec = self.read()
            if (rec.get('phase') in ('awaiting_restart', 'restart_requested')
                    and boot_sha == rec.get('candidate')
                    and self.git('rev-parse', 'HEAD') == boot_sha):
                self.clean()
                return self.save(rec, phase='succeeded', error='')
            return rec

    def status(self):
        rec = self.read()
        if not rec or rec['phase'] == 'current':
            return {}
        phase = rec['phase']
        done = phase in ('awaiting_restart', 'restart_requested', 'succeeded')
        active = False
        try:
            with self.locked():
                pass
        except Busy:
            active = True
        return dict(state='done' if done else ('running' if active else 'failed'),
                    active=active, pending=active, phase=phase,
                    error='' if done else rec.get('error') or self.recovery(rec),
                    candidate=rec.get('candidate'), previous_ref=rec.get('previous_ref'),
                    outcome='ok' if phase == 'succeeded' else 'pending')


def main():
    try:
        tx = Transaction(Path(__file__).resolve().parent)
        rec = tx.prepare()
        if rec['phase'] == 'current':
            print('Already up to date. Nothing to restart.')
        else:
            tx.dependencies()
            print('Update prepared. Restart Phosphene to check that the new version starts.')
        return 0
    except Exception as exc:
        print(f'FATAL error: {exc}', flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
