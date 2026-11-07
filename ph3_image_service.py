"""Bounded, evictable P-H3-Image worker. No model or MLX imports in Studio."""
from __future__ import annotations
import atexit
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid


class ResidencyUnavailable(RuntimeError):
    """The caller may retry staged after the resident process has been reaped."""


class ImageWorker:
    def __init__(self, idle_seconds=60):
        self.idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._proc = None
        self._key = None
        self._events = None
        self._timer = None
        self._serial = 0

    def _close_locked(self):
        self._serial += 1
        if self._timer:
            self._timer.cancel()
            self._timer = None
        proc, self._proc = self._proc, None
        self._key = None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        for stream in (proc.stdin, proc.stdout):
            if stream:
                stream.close()

    def close(self):
        with self._lock:
            self._close_locked()

    def _expire(self, serial):
        with self._lock:
            if self._serial == serial:
                self._close_locked()

    def request(self, command, request, env, *, timeout, on_log=None,
                register=lambda proc: None, unregister=lambda proc: None, preflight=None):
        with self._lock:
            self._serial += 1
            if self._timer:
                self._timer.cancel()
            # A changed interpreter, script, weights or environment must never reuse old state.
            key = (tuple(command), tuple(sorted(env.items())))
            reusable = self._proc is not None and self._proc.poll() is None and self._key == key
            if preflight is not None:
                try:
                    preflight(reusable)
                except BaseException:
                    self._close_locked()
                    raise
            if not reusable:
                self._close_locked()
                self._events = queue.Queue()
                self._proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, errors='replace', env=env, start_new_session=True)
                self._key = key
                proc, events = self._proc, self._events
                def read():
                    try:
                        for line in proc.stdout:
                            events.put(line.rstrip())
                    finally:
                        events.put(None)
                threading.Thread(target=read, daemon=True).start()
            proc = self._proc
            token = uuid.uuid4().hex
            started = time.monotonic()
            registered = False
            try:
                register(proc)
                registered = True
                proc.stdin.write(json.dumps(dict(request, id=token)) + '\n')
                proc.stdin.flush()
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise RuntimeError('P-H3-Image timed out')
                    try:
                        line = self._events.get(timeout=min(remaining, .25))
                    except queue.Empty:
                        continue
                    if line is None:
                        raise RuntimeError('P-H3-Image worker stopped before completing the image')
                    if line.startswith('PH3_RESULT '):
                        receipt = json.loads(line[len('PH3_RESULT '):])
                        if receipt.get('id') != token:
                            raise RuntimeError('P-H3-Image returned an unexpected request receipt')
                        if not receipt.get('ok'):
                            raise RuntimeError(receipt.get('error') or 'P-H3-Image failed')
                        if not Path(request['output']).is_file():
                            raise RuntimeError('P-H3-Image returned no image')
                        self._serial += 1
                        self._timer = threading.Timer(self.idle_seconds, self._expire, (self._serial,))
                        self._timer.daemon = True
                        self._timer.start()
                        return receipt['metrics']
                    if on_log:
                        on_log(line)
            except BaseException:
                self._close_locked()
                raise
            finally:
                if registered:
                    unregister(proc)


WORKER = ImageWorker()
atexit.register(WORKER.close)


def evict():
    """Call under Studio's GPU gate before another engine loads weights."""
    WORKER.close()
