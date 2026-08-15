#!/usr/bin/env python3
"""Idempotent patch against ltx-core-mlx — the ONLY edit Phosphene still needs.

History: through ltx-2-mlx **v0.14.0** this script carried SEVEN patches,
working around upstream gaps. As of the **v0.14.8** pin (2026-06-01 catch-up)
dgrauet has absorbed all but one of them upstream, so they were DROPPED
(and the **v0.14.19** pin, 2026-08-11, keeps exactly this shape — the
`decode_and_stream` ffmpeg line moved from 482 to 487 but its text is
character-identical, so the patch applies unchanged):

  - I2V OOM / pre-denoise / base-load memory frees (old Patches 2,3,4)
        → native `low_memory` path. v0.14.8 frees generation components via
        composition-block `.free()` (`prompt_encoder`/`image_conditioner`) in
        `ti2vid_one_stage.py` + `_base.py` before loading decoders.
  - VAE decode streaming (old Patch 5)
        → native budget-aware tiled `decode_and_stream` in `video_vae.py`
        (`LTX2_VAE_DECODE_BUDGET_GB`, default 8 GB; falls through to a single
        pass for short clips). Strictly better than our hand-rolled version.
  - Metal-watchdog DiT-eval-cadence fix (old Patch 7)
        → native `_pre_denoise_flush` (an `mx.eval` barrier before every
        denoise loop). `_DIT_EVAL_EVERY` no longer exists. The flush is wired
        into the Q4 one-stage path (`ti2vid_one_stage.py:239`) — the exact path
        that produced the I2V "mosaic" on memory-pressured Macs (#17) — plus
        every other pipeline. This is dgrauet's fix for the same
        MTLCommandBufferErrorInternal code 14 we were fighting from our side.
  - one-stage frame_rate / 12→24 fps long clips (old Patch 6)
        → native, first-class, keyword-only `frame_rate` threaded end-to-end
        (generate_one_stage_dev / generate_and_save / compute_*_positions /
        combined_image_conditionings / decode_and_stream).

The helper already calls the new API defensively (`hasattr` guard on
`generate`/`generate_from_image`, `inspect.signature` probing, and
`_filter_unsupported_kwargs`), so nothing depends on the dropped patches.
Re-implementing them against the pinned tag would only re-introduce divergence — the
whole point of the catch-up was to let pinned-upstream own this behaviour.

What REMAINS is the one preference upstream doesn't share:

1. Output codec. Upstream emits `yuv420p crf 18` — 4:2:0 chroma subsampling
   produces visible JPEG-style block artifacts on faces / skin. We patch to
   `yuv444p crf 0` (lossless, no chroma subsampling) plus `+faststart` (moov
   atom at the front of the file so gallery thumbnails decode the first frame
   without downloading the whole clip). Override via `LTX_OUTPUT_PIX_FMT` /
   `LTX_OUTPUT_CRF`.

If a future pin restructures `decode_and_stream`'s ffmpeg line, this fails
LOUD (exit non-zero) rather than silently shipping a 4:2:0 install.

Safe to re-run — checks for its marker before touching anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

# 3.8.2: the fallback list. It is now genuinely a FALLBACK — see _find.
#
# The old code used ONLY this list, resolved against the process CWD, and that
# was two bugs in one. It guessed at a layout (hardcoded `python3.11`, hardcoded
# venv directory names) instead of asking the interpreter that will actually do
# the importing, and because the paths were relative to CWD the same install
# reported "MISSING — target file not found" when the script was run from
# anywhere but the app root — which is exactly what happened when the owner ran
# it by hand on a machine whose file was sitting right there.
#
# Anchored on the script's own directory now, not the CWD, so even the fallback
# works from any working directory.
_HERE = Path(__file__).resolve().parent

VENV_ROOTS = [
    "ltx-2-mlx/env/lib/python3.11/site-packages",      # Pinokio
    "ltx-2-mlx/.venv/lib/python3.11/site-packages",    # manual
    "ltx-2-mlx/packages/ltx-core-mlx/src",             # editable (ltx-core)
    "ltx-2-mlx/packages/ltx-pipelines-mlx/src",        # editable (ltx-pipelines)
]

# Which packages own which sub-paths, for the import-based resolution below.
_PKG_OF = {
    "ltx_core_mlx": "ltx_core_mlx",
    "ltx_pipelines_mlx": "ltx_pipelines_mlx",
}


def _find_by_import(rel: str) -> Path | None:
    """Resolve `rel` through the RUNNING interpreter's own import machinery.

    This is the authoritative answer to "which file will the panel import?",
    which is the only file worth patching. Run under the venv's python
    (install.js / update.js both invoke `ltx-2-mlx/env/bin/python3.11`) it
    lands on that venv's copy — site-packages when the packages are installed
    normally, the vendored source when they are installed editable. Either way
    it is the file the runtime loads, with no assumptions about directory
    names, python minor version, or working directory.

    `find_spec` on a TOP-LEVEL package does not execute it, so this stays
    cheap and cannot be broken by an import-time failure deeper in the tree.
    """
    import importlib.util

    top = rel.split("/", 1)[0]
    pkg = _PKG_OF.get(top)
    if pkg is None:
        return None
    try:
        spec = importlib.util.find_spec(pkg)
    except Exception:
        return None
    if spec is None or not spec.origin:
        return None
    # spec.origin is <pkg>/__init__.py; its parent is the package root, which
    # is what `rel` is relative to (minus the leading package name).
    root = Path(spec.origin).resolve().parent.parent
    p = root / rel
    return p if p.exists() else None


def _find(rel: str) -> Path | None:
    """Resolve a package-relative path: ask the interpreter first, then guess.

    Order matters. The import-based answer is the file the runtime actually
    loads; the path list is a last resort for the case where the packages are
    not importable at all (a half-finished install), kept so the script still
    has something to say instead of silently doing nothing.
    """
    p = _find_by_import(rel)
    if p is not None:
        print(f"  [resolve] via import: {p}")
        return p
    for root in VENV_ROOTS:
        cand = _HERE / root / rel
        if cand.exists():
            print(f"  [resolve] via path fallback: {cand}")
            return cand
    return None


# ---- Patch: lossless h264 codec ----------------------------------------------
PATCH_CODEC_OLD = 'cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", output_path])'
PATCH_CODEC_NEW = (
    '# PATCHED (LTX23MLX): default to lossless yuv444p crf 0 (no chroma\n'
    '        # subsampling, no JPEG-style block artifacts on faces). Override via env.\n'
    '        # `+faststart` moves the moov atom to the front of the file so the\n'
    '        # gallery thumbnails (preload="metadata") can decode the first\n'
    '        # frame without downloading the full clip — without it the thumbs\n'
    '        # render black until clicked.\n'
    '        import os as _os\n'
    '        _pix = _os.environ.get("LTX_OUTPUT_PIX_FMT", "yuv444p")\n'
    '        _crf = _os.environ.get("LTX_OUTPUT_CRF", "0")\n'
    '        cmd.extend(["-c:v", "libx264", "-pix_fmt", _pix, "-crf", _crf,\n'
    '                    "-movflags", "+faststart", output_path])'
)


# Outcome codes for apply_patch — three-valued (vs the old True/False) so
# main() can distinguish a genuinely missing target / drifted upstream from
# the no-op "already patched" case. Without this distinction the install
# used to exit 0 on a corrupt patch attempt and ship a broken pipeline.
OUTCOME_APPLIED = "applied"
OUTCOME_ALREADY = "already"
OUTCOME_MISSING = "missing"          # target file not on disk
OUTCOME_DRIFT   = "drift"            # target found but expected text isn't there


def _atomic_write(target: Path, text: str) -> None:
    """Write to a temp file in the same directory, fsync, then os.replace.
    Avoids the failure mode where Pinokio kills the install mid-write and
    leaves a half-written .py that imports as a SyntaxError forever."""
    import os, tempfile
    target_dir = target.parent
    fd, tmp_path = tempfile.mkstemp(prefix=target.name + ".", dir=str(target_dir))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except Exception:
        # Clean up the temp file if we never made it to the replace.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def apply_patch(target: Path, old: str, new: str, marker: str, label: str,
                upgrade_marker: str | None = None) -> str:
    """Idempotently apply old→new replacement on `target`. Returns one of
    OUTCOME_APPLIED / OUTCOME_ALREADY / OUTCOME_MISSING / OUTCOME_DRIFT —
    deep-review fix to surface upstream drift loudly instead of silently
    no-op'ing the patch and shipping a broken install.

    `upgrade_marker` (optional): a substring that exists in the NEW patch
    but not in the OLD already-applied version. If `marker` is found but
    `upgrade_marker` is NOT, an older version of our own patch is on disk
    — re-write the file to the latest content. Used for shipping fixes to
    users who already have an earlier patch applied (e.g. adding +faststart
    to the codec patch without forcing a venv rebuild)."""
    if target is None or not target.exists():
        print(f"  [{label}] MISSING — target file not found", file=sys.stderr)
        return OUTCOME_MISSING
    text = target.read_text()
    if marker in text:
        # Marker present → some version of our patch is on disk. If the
        # caller didn't supply an upgrade_marker we treat it as ALREADY.
        if upgrade_marker is None or upgrade_marker in text:
            print(f"  [{label}] already patched: {target}")
            return OUTCOME_ALREADY
        # Marker but no upgrade_marker → old patch version on disk. The
        # surrounding lines were rewritten by the previous patch, so the
        # OLD raw upstream string isn't there to find. Find the old codec
        # line (without faststart) and replace with the new one.
        print(f"  [{label}] upgrading older patch: {target}")
        old_one_liner = ('cmd.extend(["-c:v", "libx264", "-pix_fmt", _pix, '
                         '"-crf", _crf, output_path])')
        new_one_liner = ('cmd.extend(["-c:v", "libx264", "-pix_fmt", _pix, '
                         '"-crf", _crf,\n                    "-movflags", '
                         '"+faststart", output_path])')
        if old_one_liner in text:
            _atomic_write(target, text.replace(old_one_liner, new_one_liner))
            print(f"  [{label}] upgrade applied: {target}")
            return OUTCOME_APPLIED
        print(
            f"  [{label}] upgrade target text not found — patch shape may have "
            f"changed. Manual inspection needed.", file=sys.stderr,
        )
        return OUTCOME_DRIFT
    if old not in text:
        print(
            f"  [{label}] DRIFT — expected text not found in {target}. "
            f"Upstream likely restructured this file. The patch needs to be "
            f"updated (see patch_ltx_codec.py); the install will fail loud "
            f"rather than ship an unpatched copy.",
            file=sys.stderr,
        )
        return OUTCOME_DRIFT
    _atomic_write(target, text.replace(old, new))
    print(f"  [{label}] patched {target}")
    return OUTCOME_APPLIED


def _shout(*lines: str) -> None:
    """Print a failure the Pinokio console cannot be skim-read past.

    The old single-line stderr message scrolled off inside a wall of pip
    output. A patch that did not apply means every clip is encoded 4:2:0;
    that deserves a banner.
    """
    bar = "!" * 72
    print(f"\n{bar}", file=sys.stderr)
    print("!! PHOSPHENE: CODEC PATCH FAILURE", file=sys.stderr)
    for line in lines:
        print(f"!! {line}", file=sys.stderr)
    print(f"{bar}\n", file=sys.stderr)


def main() -> int:
    print("Applying LTX23MLX codec patch (ltx-2-mlx v0.14.19+ltx25.6 — only the codec edit remains):")

    # `upgrade_marker="+faststart"` lets us upgrade installs where the
    # earlier version of this patch was applied (LTX_OUTPUT_PIX_FMT marker
    # present, but the +faststart movflag missing). Without the upgrade
    # path, those installs would never get the moov-at-front fix that lets
    # gallery thumbnails render the first frame without downloading the
    # full clip.
    codec_target = _find("ltx_core_mlx/model/video_vae/video_vae.py")
    outcome = apply_patch(
        codec_target, PATCH_CODEC_OLD, PATCH_CODEC_NEW,
        marker="LTX_OUTPUT_PIX_FMT",
        upgrade_marker="+faststart",
        label="codec (yuv444p crf 0 + faststart)",
    )

    if outcome in (OUTCOME_MISSING, OUTCOME_DRIFT):
        _shout(
            f"codec patch failed to apply ({outcome}).",
            "Without it every render is encoded 4:2:0 yuv420p — visible blocky",
            "artifacts on faces and skin. This exits non-zero so install.js /",
            "update.js fail loud instead of shipping it silently.",
            "If a pin-bump restructured decode_and_stream's ffmpeg line, update",
            "PATCH_CODEC_OLD in patch_ltx_codec.py to match, then re-run.",
        )
        return 2

    # 3.8.2: VERIFY, do not trust the outcome code.
    #
    # v3.8.1 shipped an entire fleet of unpatched installs while every log line
    # anyone looked at said the update had finished. The patch step never ran at
    # all (the Pinokio run truncated several steps earlier), and nothing
    # downstream ever asked the only question that matters: does the file the
    # runtime imports actually carry the patch? Ask it here, out of the same
    # interpreter that resolved the target, and fail loud when the answer is no.
    verify = _find("ltx_core_mlx/model/video_vae/video_vae.py")
    if verify is None or not verify.exists():
        _shout("codec patch verification could not find the target after patching.")
        return 3
    vtext = verify.read_text()
    missing = [m for m in ("LTX_OUTPUT_PIX_FMT", "+faststart") if m not in vtext]
    if missing:
        _shout(
            f"codec patch VERIFICATION FAILED — missing {', '.join(missing)}",
            f"in {verify}",
            "The runtime would encode 4:2:0 yuv420p (blocky faces). Not shipping",
            "this silently.",
        )
        return 4

    if outcome == OUTCOME_APPLIED:
        print(f"Done — codec patch applied and verified in {verify}")
    else:
        print(f"Codec patch already applied and verified in {verify}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
