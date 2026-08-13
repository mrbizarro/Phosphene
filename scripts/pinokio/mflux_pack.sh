#!/usr/bin/env bash
# Install the mflux image-engine pack (Ideogram 4 + Qwen-Edit).
#
# Lifted out of install.js (529-char dispatch). See scripts/pinokio/README.md.
# update.js keeps its own slightly different copy inline — that one is 498 and
# under the ceiling; do not "tidy" it into this file without re-reading both.
#
#   cwd : the app root
#
# SEMANTICS: was a "\n"-joined string carrying its own `\` continuations, i.e.
# ONE compound command with a trailing `|| echo WARN`. BEST-EFFORT by design —
# a pip hiccup must not fail the core video install; the panel surfaces a
# one-click "Reinstall image engines" path if this didn't land. Preserved
# exactly: same subshell, same `||`, no `set -e`.
#
# uv, NOT plain pip: mlx-vlm is installed --no-deps (to dodge its PIL>=10 / av
# pins), which leaves its declared extras permanently unsatisfied. Plain
# `pip install` then dumps a scary "ERROR: pip's dependency resolver…" block
# about mlx-vlm on EVERY later install — verified, and it made cocktailpeanut's
# update look broken. Worse, on v3.8.1 that block ENDED the Pinokio run early
# and silently skipped every remaining step (the v3.8.2 truncation bug).
#
# Two-step (with-deps, then --reinstall --no-deps) mirrors install_qwen.js so
# transitive deps resolve first and the version is then locked, after which the
# FBCache patch is applied.

echo 'Installing the mflux image-engine pack (Ideogram 4 + Qwen-Edit)…' && \
( uv pip install --python ./ltx-2-mlx/env/bin/python 'mflux==0.18.0' && \
  uv pip install --python ./ltx-2-mlx/env/bin/python --reinstall --no-deps 'mflux==0.18.0' && \
  uv pip install --python ./ltx-2-mlx/env/bin/python 'mlx-teacache==0.4.1' && \
  ./ltx-2-mlx/env/bin/python3.11 patch_mflux_fbcache.py ) \
|| echo 'WARN: mflux image-engine install hit an error — video still works; use the Reinstall image engines action to retry image generation.'
