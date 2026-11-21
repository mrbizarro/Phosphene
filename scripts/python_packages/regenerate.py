#!/usr/bin/env python3
"""Regenerate trusted hashes from PyPI: python3 scripts/python_packages/regenerate.py.
Use --check in gates; optional set names regenerate only those install sets.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from install import HERE, ROOT, digest, input_digest

# Pin changes anywhere at an install entry point require regeneration/review.
SOURCES = ['scripts/pinokio/ltx_engine_env.sh', 'scripts/pinokio/mflux_pack.sh',
           'scripts/post_update.sh', 'install_qwen.js', 'install_sharp.js',
           'scripts/pinokio/h3_deps.sh', 'scripts/pinokio/h3_checkout.sh',
           'scripts/pinokio/a2v_stems_deps.sh', 'scripts/pinokio/flux3_env.py',
           'scripts/speech/speech_setup.sh', 'mlx_ltx_panel.py']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('sets', nargs='*')
    args = ap.parse_args()
    sets = json.loads((HERE / 'sets.json').read_text())
    path = HERE / 'generated.json'
    data = json.loads(path.read_text()) if path.exists() else {'sets': {}}
    sources = {p: digest(ROOT / p) for p in SOURCES}
    if args.check:
        assert data.get('sources') == sources, 'Install sources changed; regenerate package locks'
        for name, entry in sets.items():
            if entry.get('pypi_only'):
                continue
            record = data['sets'][name]
            assert record['input_sha256'] == input_digest(entry), name + ': pins changed'
            assert record['lock_sha256'] == digest(HERE / (name + '.txt')), name + ': lock changed'
        print('Python package locks match their install sources and pins.')
        return
    uv = shutil.which('uv')
    if not uv:
        raise SystemExit('Put uv on PATH to regenerate trusted package locks.')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('UV_', 'PIP_'))}
    env.update(UV_HTTP_TIMEOUT='60', UV_CACHE_DIR=str(ROOT / '.package-lock-cache'),
               UV_PYTHON_DOWNLOADS='never', MACOSX_DEPLOYMENT_TARGET='14.0')
    for name, entry in sets.items():
        if entry.get('pypi_only') or (args.sets and name not in args.sets):
            continue
        with tempfile.TemporaryDirectory(prefix='phos-lock-') as temp:
            src = Path(temp) / 'requirements.in'
            src.write_text('\n'.join(entry['requirements']) + '\n')
            out = Path(temp) / 'requirements.txt'
            cmd = [uv, '--no-config', 'pip', 'compile', str(src), '--generate-hashes',
                   '--python-version', entry['python'], '--python-platform', 'aarch64-apple-darwin',
                   '--index-url', 'https://pypi.org/simple', '--no-header', '--no-annotate',
                   '--only-binary', ':all:', '--output-file', str(out)]
            if entry.get('lock_constraints'):
                extra = Path(temp) / 'constraints.txt'
                extra.write_text('\n'.join(entry['lock_constraints']) + '\n')
                cmd += ['-c', str(extra)]
            for p in entry.get('files', []):
                cmd += ['-c', str(ROOT / p)]
            if entry.get('no_deps'):
                cmd += ['--no-deps']
            subprocess.run(cmd, env=env, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            (HERE / (name + '.txt')).write_bytes(out.read_bytes())
        data['sets'][name] = {'input_sha256': input_digest(entry),
                              'lock_sha256': digest(HERE / (name + '.txt'))}
        path.write_text(json.dumps(data, indent=2) + '\n')
        print(name + ': locked from PyPI', flush=True)
    data['sources'] = sources
    path.write_text(json.dumps(data, indent=2) + '\n')


if __name__ == '__main__':
    main()
