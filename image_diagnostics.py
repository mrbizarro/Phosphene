"""Bounded local image evidence. No raw worker text, telemetry or network I/O.

Only literal recognized native phrases survive; this intentionally sacrifices
unknown exception prose rather than trying to blacklist every possible secret.
"""
import json
import math
import os
from pathlib import Path
import queue
import threading
import time

FILENAME = 'image-diagnostics.json'
MAX_RECORDS = 16
MAX_BYTES = 32768
MODELS = {'Qwen/Qwen-Image-Edit-2511', 'Qwen/Qwen-Image-Edit-2509',
          'Runpod/FLUX.2-klein-4B-mflux-4bit', 'qwen', 'qwen-edit',
          'cocktailpeanut/ideogram-4-fp8', 'filipstrand/Z-Image-Turbo-mflux-4bit'}
PRESETS = {'auto', 'qwen_edit_lightning_inline', 'qwen_edit_inline',
           'qwen_edit_high_inline', 'flux2_klein_inline', 'ideogram4_inline'}
PHASES = {'unknown', 'download', 'load', 'render'}
OUTCOMES = {'unknown', 'completed', 'failed', 'cancelled', 'timeout'}
# Exact excerpts, never surrounding text or captured groups.
NATIVE = ('std::bad_alloc', 'out of memory', 'failed to allocate',
          'insufficient memory', 'std::runtime_error', 'SIGABRT',
          'MTLCommandBufferErrorDomain', 'command buffer execution failed',
          'libc++abi: terminating', 'Abort trap')


def sanitize(record):
    """Allowlist again at persistence AND export, including tampered files."""
    if not isinstance(record, dict):
        return {}
    out = {}
    for key, allowed in [('model', MODELS), ('preset', PRESETS),
                         ('phase', PHASES), ('outcome', OUTCOMES)]:
        value = record.get(key)
        out[key] = value if isinstance(value, str) and value in allowed else 'unknown'
    for key, low, high in [('width', 1, 16384), ('height', 1, 16384),
                           ('references', 0, 3), ('quantization', 0, 16),
                           ('steps', 1, 1000), ('exit_status', -255, 255),
                           ('admission_available_gib', 0, 4096),
                           ('spawn_available_gib', 0, 4096), ('started_unix', 0, 4102444800)]:
        v = record.get(key)
        out[key] = (v if type(v) in (int, float) and math.isfinite(v)
                    and low <= v <= high else None)
    excerpts = record.get('native_excerpt')
    out['native_excerpt'] = [v for v in excerpts[:8] if type(v) is str and v in NATIVE] if isinstance(excerpts, list) else []
    return out


def read_records(directory):
    try:
        with (Path(directory) / FILENAME).open('rb') as f:
            raw = f.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return []
        data = json.loads(raw)
        return [sanitize(r) for r in data[-MAX_RECORDS:] if isinstance(r, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def memory_available():
    try:
        import hostinfo
        value = hostinfo.available_bytes()
        return round(value / 1024**3, 2) if value > 0 else None
    except Exception:
        return None


class Writer:
    """One daemon and a small queue. Full/broken storage drops evidence, never jobs."""
    def __init__(self):
        self.pending = queue.Queue(maxsize=8)
        self.started = False
        self.lock = threading.Lock()

    def submit(self, directory, record, enabled):
        try:
            if not self.lock.acquire(blocking=False):
                return
            try:
                if not self.started:
                    threading.Thread(target=self.run, daemon=True, name='image-diagnostics').start()
                    self.started = True
            finally:
                self.lock.release()
            self.pending.put_nowait((directory, sanitize(record), enabled))
        except Exception:
            pass

    def run(self):
        while True:
            directory, record, enabled = self.pending.get()
            try:
                if enabled():
                    self.persist(directory, record)
            except Exception:
                pass
            finally:
                self.pending.task_done()

    def persist(self, directory, record):
        directory = Path(directory)
        records = (read_records(directory) + [sanitize(record)])[-MAX_RECORDS:]
        data = json.dumps(records, separators=(',', ':')).encode()
        if len(data) > MAX_BYTES:
            return
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / FILENAME
        temp = directory / (FILENAME + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(data)
        os.replace(temp, target)


WRITER = Writer()


class Context:
    def __init__(self, directory, preset, enabled):
        self.directory, self.enabled = directory, enabled
        self.record = sanitize({'preset': preset, 'admission_available_gib': memory_available(), 'started_unix': int(time.time())})
        self.secrets = ()
        self.finished = False

    def prepare(self, prompt, refs, width, height):
        # Secrets live only during this job. Never passed to the writer.
        self.secrets = tuple(x for x in [prompt, *(refs or [])] if x)
        self.record.update(width=width, height=height, references=len(refs or []))

    def spawn(self, cmd):
        def arg(*keys):
            for key in keys:
                if key in cmd:
                    return cmd[cmd.index(key) + 1]
            return None
        self.record['model'] = arg('--model')
        for key, flags in [('quantization', ('-q', '--quantize')), ('steps', ('--steps',))]:
            try:
                self.record[key] = int(arg(*flags))
            except (ValueError, TypeError):
                self.record[key] = None
        self.record['spawn_available_gib'] = memory_available()

    def line(self, line):
        # Bound work even for a malicious single-line worker log.
        line = line[:4096]
        for secret in self.secrets:
            line = line.replace(secret, '')
        lower = line.lower()
        # Positive markers only. Starting a child is NOT evidence of model load.
        if lower.startswith(('fetching ', 'downloading ')):
            self.record['phase'] = 'download'
        elif lower.startswith(('loading model', 'loading weights', 'loading checkpoint')):
            self.record['phase'] = 'load'
        elif lower.startswith(('denoising', 'generating image', 'rendering image')):
            self.record['phase'] = 'render'
        for phrase in NATIVE:
            if phrase in line and phrase not in self.record['native_excerpt']:
                self.record['native_excerpt'] = (self.record['native_excerpt'] + [phrase])[-8:]

    def finish(self, outcome, exit_status=None):
        if self.finished:
            return
        self.finished = True
        self.record.update(outcome=outcome, exit_status=exit_status)
        self.secrets = ()
        WRITER.submit(self.directory, self.record, self.enabled)


def attempt(context, method, *args):
    """Diagnostic faults must never change the worker result."""
    try:
        if context is not None:
            getattr(context, method)(*args)
    except Exception:
        pass


def begin(directory, preset, enabled):
    try:
        return Context(directory, preset, enabled) if enabled() else None
    except Exception:
        return None


def bundle_lines(directory, enabled):
    try:
        if enabled():
            return ['[image-diagnostics] ' + json.dumps(r, separators=(',', ':')) for r in read_records(directory)]
    except Exception:
        pass
    return []
