"""PyPI first; one hash-checked, wheel-only China retry for connection failures.

Every caller keeps its original command. Locks are release inputs, never obtained
from the mirror. Local/build-dependent sets deliberately have no mirror retry.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MIRROR = 'https://pypi.tuna.tsinghua.edu.cn/simple/'
NOTICE = 'Python package server unreachable, using the China mirror'


def network_failure(text):
    # Never turn a resolver, HTTP, certificate, disk or hash failure into a retry.
    low = text.lower()
    if re.search(r'\b(?:40[0-9]|50[0-9])\b|no solution found|resolutionimpossible|'
                 r'conflicting dependencies|failed to build|build backend|'
                 r'hash mismatch|hashes.*(?:match|differ)|certificate|no space left|permission denied', low):
        return False
    # pip also prints "no matching distribution" after exhausting connection
    # retries; that alone is not a network failure, the transport cause is.
    return bool(re.search(r'timed? out|(?:read|connect)timeout|connection (?:refused|reset|aborted)|'
                         r'failed to (?:connect|lookup)|cannot connect|could not connect|'
                         r'network is unreachable|network unreachable|name or service not known|'
                         r'temporary failure in name resolution|nodename nor servname|dns error', low))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def requirements(path):
    return sorted(line.split('#', 1)[0].strip().lower().replace('_', '-')
                  for line in Path(path).read_text().splitlines()
                  if line.split('#', 1)[0].strip())


def matches_request(entry, command):
    """A stale or different caller must never receive this lock's packages."""
    args = iter(command[command.index('install') + 1:])
    specs, constraints = [], []
    target = None
    no_deps = False
    try:
        for arg in args:
            if arg == '--python':
                target = next(args)
            elif arg in ('-c', '--constraint', '--constraints'):
                constraints.append(requirements(next(args)))
            elif arg in ('-r', '--requirement', '--requirements'):
                specs.extend(requirements(next(args)))
            elif arg in ('--retries', '--timeout', '--target', '--prefix'):
                next(args)
            elif arg == '--no-deps':
                no_deps = True
            elif arg in ('--reinstall', '--force-reinstall', '--upgrade',
                         '--no-cache', '--no-cache-dir'):
                pass
            elif arg.startswith('-'):
                return False
            else:
                specs.append(arg.lower().replace('_', '-'))
        expected = entry.get('request', entry['requirements'])
        if sorted(specs) != sorted(x.lower().replace('_', '-') for x in expected):
            return False
        if no_deps != bool(entry.get('no_deps')):
            return False
        if sorted(constraints) != sorted(requirements(ROOT / p) for p in entry.get('request_constraints', entry.get('files', []))):
            return False
        if target is None:
            target = (str(Path(command[0]).with_name('python'))
                      if Path(command[0]).name.startswith('pip') else command[0])
        result = subprocess.run([target, '-c',
                                 'import sys; print("%s.%s" % sys.version_info[:2])'],
                                capture_output=True, text=True, timeout=10)
        return result.returncode == 0 and result.stdout.strip() == entry['python']
    except (OSError, ValueError, StopIteration, subprocess.TimeoutExpired):
        return False


def mirror_command(command, lock):
    # Preserve the installer and target interpreter, plus install semantics.
    at = command.index('install')
    result = command[:at + 1]
    args = command[at + 1:]
    for i, arg in enumerate(args):
        if arg in ('--python', '--target', '--prefix'):
            result += [arg, args[i + 1]]
        elif arg in ('--reinstall', '--force-reinstall', '--no-cache', '--no-cache-dir'):
            result.append(arg)
    result += ['--index-url', MIRROR, '--require-hashes', '--only-binary', ':all:',
               '--no-deps', '-r', str(lock)]
    return result


def install(name, command):
    env = os.environ.copy()
    env.setdefault('UV_HTTP_TIMEOUT', '60')
    # Buffer the first attempt: Pinokio treats an intermediate "error:" as final.
    # Children stay in the caller's process group so Stop still cancels them.
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as log:
        proc = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        log.seek(0)
        output = log.read()
    if proc.returncode == 0:
        print(output, end='', flush=True)
        return 0
    entry = json.loads((HERE / 'sets.json').read_text()).get(name, {})
    lock = HERE / (name + '.txt')
    meta_path = HERE / 'generated.json'
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    record = meta.get('sets', {}).get(name, {})
    eligible = (platform.system() == 'Darwin' and platform.machine() == 'arm64'
                and entry and not entry.get('pypi_only') and lock.is_file()
                and record.get('lock_sha256') == digest(lock)
                and record.get('input_sha256') == input_digest(entry)
                and network_failure(output) and matches_request(entry, command))
    if not eligible:
        print(output, end='', flush=True)
        return proc.returncode
    # Configuration must not add another index, trusted-host, find-links, or
    # requirements to the verified fallback. Never disable TLS verification.
    retry_env = {k: v for k, v in env.items() if not k.startswith(('PIP_', 'UV_'))}
    for key in ('UV_CACHE_DIR', 'UV_PYTHON_INSTALL_DIR', 'UV_PYTHON_PREFERENCE',
                'UV_PYTHON_BIN_DIR', 'UV_TOOL_BIN_DIR', 'UV_HTTP_TIMEOUT'):
        if key in env:
            retry_env[key] = env[key]
    retry_env.update(UV_NO_CONFIG='1', PIP_CONFIG_FILE=os.devnull)
    print(NOTICE, flush=True)
    # uv splits requirements paths with spaces; a private short temporary path
    # works under Application Support as well as Pinokio's spaced home paths.
    with tempfile.TemporaryDirectory(prefix='phos-packages-', dir='/tmp') as temp:
        copy = Path(temp) / 'requirements.txt'
        copy.write_bytes(lock.read_bytes())
        retry = mirror_command(command, copy)
        if Path(retry[0]).name == 'uv':
            retry.insert(1, '--no-config')
        return subprocess.run(retry, env=retry_env).returncode


def input_digest(entry):
    payload = dict(entry)
    payload['files'] = {p: digest(ROOT / p) for p in entry.get('files', [])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


if __name__ == '__main__':
    sys.exit(install(sys.argv[1], sys.argv[3:]))
