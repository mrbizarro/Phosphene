# Python package fallback

`install.py <set> -- <original command>` runs the original PyPI command first,
with `UV_HTTP_TIMEOUT=60` only when unset. Success never reads a lock or contacts
a mirror. Only a transport failure permits one retry. HTTP errors (including
404), resolver conflicts, build errors, TLS verification failures, disk errors
and hash failures do not permit a retry.

The retry uses Tsinghua, the checked-in requirements, `--require-hashes`,
`--only-binary :all:` and `--no-deps` (the lock already contains the complete
closure). It ignores index/config overrides and preserves the target Python and
reinstall flags. A changed request, constraints file, Python minor version or
lock digest disables fallback. The primary command and its pins are unchanged.

## Regeneration

With uv on PATH:

```
python3 scripts/python_packages/regenerate.py
python3 scripts/python_packages/regenerate.py --check
```

The generator uses only https://pypi.org/simple, ignores local uv configuration,
and runs `uv pip compile --generate-hashes` for macOS 14 arm64 and each install's
Python (3.11 for the video/H3 environment, 3.12 for FLUX 3/speech). See
[uv's target-platform documentation](https://docs.astral.sh/uv/reference/cli/).
Update `sets.json` with any changed install request; regenerate in the same
change. `generated.json` records the input, lock and install-source fingerprints.
`test_china_package_fallback.py` fails when these drift. `h3.in` records the
requirements of H3 commit 26010372e81af2224da97e6ea27e6be3bcb6d957 from its
GitHub raw requirements.txt; update it and the set when that engine pin moves.

The fallback alone holds transitive `platformdirs` at 4.13.5, already used by the
pinned FLUX 3 environment, because both candidate mirrors lacked the newly
resolved 4.14.8 file at the 2026-11-20 check. This satisfies the resolved package
requirements. Tsinghua also served the locked litellm wheel Aliyun lacked.
`verify_mirror.py --output /path/to/audit.json` checks every target wheel against
PyPI's filename/hash and performs a HEAD request to both mirror candidates.
It requires `packaging`. It does not replace hash verification at install time.

## Deliberately PyPI-only

- The local LTX package dependency and reinstall passes: vendored source builds
  and their build isolation are not a wheel-only hashed install.
- The engine extras group and webpush: `http-ece` (under `pywebpush`) is source-only.
  The original grouped primary command is preserved.
- Demucs: source distributions and installed-machine torch/numpy constraints.
- Music's frozen `uv sync`: it builds the local project and uses upstream locked
  sources. It is not a `uv pip install` requirement set.
- Linux installers and Python/venv seed downloads: these macOS arm64 install
  locks do not apply to them.

Never obtain release hashes from a mirror or remove hash/wheel enforcement to
make a failing retry pass. The fixture tests use a private local server and real
installers to prove that altered wheel bytes cannot be installed.
