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

VENV_ROOTS = [
    "ltx-2-mlx/env/lib/python3.11/site-packages",      # Pinokio
    "ltx-2-mlx/.venv/lib/python3.11/site-packages",    # manual
    "ltx-2-mlx/packages/ltx-core-mlx/src",             # editable (ltx-core)
    "ltx-2-mlx/packages/ltx-pipelines-mlx/src",        # editable (ltx-pipelines)
]


def _find(rel: str) -> Path | None:
    """Resolve a package-relative path under the first venv root that contains it."""
    for root in VENV_ROOTS:
        p = Path(root) / rel
        if p.exists():
            return p
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


def main() -> int:
    print("Applying LTX23MLX codec patch (ltx-2-mlx 871694d / 0.14.19+ltx25.1 — only the codec edit remains):")

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
        print(
            f"\nERROR: codec patch failed to apply ({outcome}).\n"
            "This exits non-zero so install.js / update.js fail loud rather than\n"
            "silently ship a 4:2:0 yuv420p install (visible block artifacts on\n"
            "faces/skin). If a pin-bump restructured decode_and_stream's ffmpeg\n"
            "line, update PATCH_CODEC_OLD in patch_ltx_codec.py to match the new\n"
            "upstream text, then re-run.",
            file=sys.stderr,
        )
        return 2

    if outcome == OUTCOME_APPLIED:
        print("Done — codec patch applied.")
    else:
        print("Codec patch already applied (no change).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
