#!/usr/bin/env python3
"""Render-level output-codec gate — asserts what a PRODUCED file actually is.

v3.8.1 shipped fleet-wide silent 4:2:0 because the codec patch never ran and
every gate that existed was static: `node --check`, shell-line ceilings,
patch-script exit codes. Not one of them ever looked at a rendered mp4. This
gate closes that class: it ffprobes a real output and fails loudly when the
bytes on disk don't match what was asked for. One run of this after the
release-checklist smoke render would have caught v3.8.1 in seconds.

What it asserts, per file:

1. **pix_fmt** — `ffprobe stream=pix_fmt` must equal the expected value.
   Expected resolves in this order:
     a. `--expect <fmt>` on the command line;
     b. the sidecar's `output_codec.pix_fmt` (what the panel actually
        requested at render time — survives later settings changes);
     c. the `LTX_OUTPUT_PIX_FMT` env var (the knob the patched encoder
        honours; set it when checking helper-CLI renders);
     d. `yuv444p` — the patched encoder's own default when the env var
        is absent.
2. **+faststart** (moov atom before mdat) — the unpatched upstream encode
   line writes neither `-movflags +faststart` nor reads the env vars, so a
   missing faststart on an LTX-lane file is THE universal fingerprint of an
   unpatched encoder — it detects the v3.8.1 class even when the requested
   pix_fmt happens to equal upstream's hardcoded yuv420p (the "standard"
   preset), where the pix_fmt check alone is blind. Skip with
   `--no-faststart` only when probing pre-faststart-era archives.

CRF is deliberately not asserted: x264 does not record it in the bitstream,
so there is nothing render-level to read back.

Target selection (no path argument): newest panel-rendered clip under
`mlx_outputs/` — i.e. newest `*.mp4` that has a `*.mp4.json` sidecar —
skipping `engine: "h3"` jobs (the H3 mux is not the patched LTX encoder and
is legitimately yuv420p). When the sidecar names a `native_output` distinct
from the delivered file, the NATIVE file is checked: the panel-side upscale
export re-encodes with the settings codec + faststart and would mask a
broken patch underneath.

With an explicit path: that file is checked as-is. If its sidecar says
`engine: "h3"`, the probe is reported but only asserted when `--expect` was
given explicitly — H3's native encode never went through the patch.

Exit codes: 0 = pass, 1 = assertion failed, 2 = environment/usage error
(no ffprobe, no candidate file, unreadable target). A release gate treats
any non-zero as DO NOT SHIP.

Import-safe (pure stdlib, no side effects): the panel imports
`newest_panel_output` / `check_file` for the `/status` model_integrity
self-report so users' own installs run the same check this gate runs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "mlx_outputs"
PATCHED_DEFAULT_PIX_FMT = "yuv444p"  # the patched encoder's env-absent default


class CheckError(RuntimeError):
    """Environment/usage failure — exit 2, distinct from an assertion FAIL."""


def find_ffprobe() -> Path | None:
    """Same resolution ladder as the panel's ffmpeg: explicit env first, then
    PATH, then the Pinokio ffmpeg-env, then the usual Homebrew locations."""
    candidates = [
        os.environ.get("LTX_FFPROBE"),
        # Sibling of an explicitly-pinned ffmpeg (the panel's LTX_FFMPEG knob).
        str(Path(os.environ["LTX_FFMPEG"]).parent / "ffprobe")
        if os.environ.get("LTX_FFMPEG") else None,
        shutil.which("ffprobe"),
        str(Path.home() / "pinokio/bin/ffmpeg-env/bin/ffprobe"),
        "/opt/homebrew/bin/ffprobe",
        "/usr/local/bin/ffprobe",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None


def probe_pix_fmt(path: Path, ffprobe: Path) -> str:
    proc = subprocess.run(
        [str(ffprobe), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        raise CheckError(
            f"ffprobe could not read {path.name}: "
            f"rc={proc.returncode} {(proc.stderr or '').strip()[:200]}")
    return out.splitlines()[0].strip()


def has_faststart(path: Path) -> bool | None:
    """Walk the top-level mp4 boxes; True iff moov appears before mdat.
    Pure-python so the check needs no second ffprobe invocation and cannot
    be fooled by a tool that 'fixes' the file while reading it. Returns
    None (unknown, never asserted) if the box structure doesn't parse."""
    try:
        size_total = path.stat().st_size
        with open(path, "rb") as fh:
            offset = 0
            while offset + 8 <= size_total:
                fh.seek(offset)
                header = fh.read(8)
                if len(header) < 8:
                    return None
                box_size = struct.unpack(">I", header[:4])[0]
                box_type = header[4:8]
                if box_size == 1:  # 64-bit largesize follows
                    large = fh.read(8)
                    if len(large) < 8:
                        return None
                    box_size = struct.unpack(">Q", large)[0]
                elif box_size == 0:  # box extends to EOF
                    box_size = size_total - offset
                if box_type == b"moov":
                    return True
                if box_type == b"mdat":
                    return False
                if box_size < 8:
                    return None
                offset += box_size
    except OSError:
        return None
    return None


def sidecar_for(mp4: Path) -> dict | None:
    sc = mp4.parent / (mp4.name + ".json")
    if not sc.is_file():
        return None
    try:
        data = json.loads(sc.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def sidecar_engine(sidecar: dict | None) -> str:
    if not sidecar:
        return ""
    return str(sidecar.get("engine")
               or (sidecar.get("params") or {}).get("engine") or "").lower()


def newest_panel_output(output_dir: Path) -> tuple[Path, dict] | None:
    """Newest panel-rendered LTX-lane clip: newest mp4 WITH a sidecar whose
    engine isn't h3. Sidecar presence is the attribution — files without one
    were not rendered by the panel and can't be tied to a codec request.
    Returns (file_to_check, sidecar); the file is the sidecar's
    native_output when that exists (see module docstring)."""
    try:
        mp4s = sorted(output_dir.glob("*.mp4"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for mp4 in mp4s:
        sidecar = sidecar_for(mp4)
        if sidecar is None:
            continue
        if sidecar_engine(sidecar) == "h3":
            continue
        native = sidecar.get("native_output") or sidecar.get("raw_output")
        if native and Path(native).is_file() and Path(native) != mp4:
            return Path(native), sidecar
        return mp4, sidecar
    return None


def resolve_expected(sidecar: dict | None, expect_flag: str | None) -> tuple[str, str]:
    """→ (expected_pix_fmt, source-of-truth label)."""
    if expect_flag:
        return expect_flag, "--expect"
    requested = ((sidecar or {}).get("output_codec") or {}).get("pix_fmt")
    if requested:
        return str(requested), "sidecar output_codec"
    env = os.environ.get("LTX_OUTPUT_PIX_FMT")
    if env:
        return env, "LTX_OUTPUT_PIX_FMT env"
    return PATCHED_DEFAULT_PIX_FMT, "patched-encoder default"


def check_file(path: Path, ffprobe: Path, *, sidecar: dict | None = None,
               expect_flag: str | None = None,
               require_faststart: bool = True) -> dict:
    """Run both assertions on one file. Returns a report dict; raises
    CheckError only for environment problems (unreadable file, dead probe)."""
    if sidecar is None:
        sidecar = sidecar_for(path)
    engine = sidecar_engine(sidecar)
    expected, source = resolve_expected(sidecar, expect_flag)
    actual = probe_pix_fmt(path, ffprobe)
    faststart = has_faststart(path)

    h3_informational = (engine == "h3" and not expect_flag)
    problems: list[str] = []
    if actual != expected and not h3_informational:
        problems.append(
            f"pix_fmt is {actual}, expected {expected} (from {source})")
    # Problem strings stay pure ASCII: they flow into panel stdout and the
    # /status log tail, where non-ASCII has broken the Pinokio handshake
    # before (2026-06-02).
    if require_faststart and faststart is False and engine != "h3":
        problems.append(
            "no +faststart (moov after mdat) - the unpatched upstream "
            "encoder's fingerprint")
    return {
        "ok": not problems,
        "file": str(path),
        "engine": engine or "ltx",
        "actual": actual,
        "expected": expected,
        "expected_source": source,
        "faststart": faststart,
        "informational": h3_informational,
        "problems": problems,
    }


def _print_report(report: dict) -> None:
    name = Path(report["file"]).name
    print(f"[codec-gate] file      : {report['file']}")
    print(f"[codec-gate] engine    : {report['engine']}")
    print(f"[codec-gate] pix_fmt   : {report['actual']} "
          f"(expected {report['expected']}, from {report['expected_source']})")
    fs = report["faststart"]
    print(f"[codec-gate] faststart : "
          f"{'yes' if fs else 'NO' if fs is False else 'unknown'}")
    if report["ok"]:
        note = " (H3 lane — informational only)" if report["informational"] else ""
        print(f"[codec-gate] PASS — {name} is what was asked for{note}")
        return
    bang = "!" * 72
    print(bang)
    print("!! PHOSPHENE: OUTPUT CODEC GATE FAILED")
    for p in report["problems"]:
        print(f"!! {p}")
    print(f"!! in {report['file']}")
    print("!! A produced render does not match its requested codec. This is")
    print("!! the v3.8.1 class (codec patch bypassed -> silent 4:2:0). Do not")
    print("!! promote. Re-run patch_ltx_codec.py, re-render, re-check.")
    print(bang)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Assert the actual pixel format (and +faststart) of a "
                    "produced render. See module docstring for semantics.")
    ap.add_argument("path", nargs="?",
                    help="mp4 to check, or a directory to scan (default: "
                         "newest panel-rendered LTX clip under mlx_outputs/)")
    ap.add_argument("--expect",
                    help="required pix_fmt; overrides sidecar/env resolution")
    ap.add_argument("--no-faststart", action="store_true",
                    help="skip the +faststart assertion")
    args = ap.parse_args(argv)

    ffprobe = find_ffprobe()
    if ffprobe is None:
        print("[codec-gate] ERROR: no ffprobe found (tried LTX_FFPROBE, "
              "LTX_FFMPEG sibling, PATH, the Pinokio ffmpeg-env, Homebrew).",
              file=sys.stderr)
        return 2

    try:
        if args.path and Path(args.path).is_file():
            target = Path(args.path)
            report = check_file(target, ffprobe, expect_flag=args.expect,
                                require_faststart=not args.no_faststart)
        else:
            scan_dir = Path(args.path) if args.path else DEFAULT_OUTPUT_DIR
            if not scan_dir.is_dir():
                print(f"[codec-gate] ERROR: {scan_dir} is neither a file nor "
                      "a directory.", file=sys.stderr)
                return 2
            picked = newest_panel_output(scan_dir)
            if picked is None:
                print(f"[codec-gate] ERROR: no panel-rendered (sidecar'd, "
                      f"non-H3) mp4 found under {scan_dir}. Render the "
                      "checklist smoke first, or pass an explicit path.",
                      file=sys.stderr)
                return 2
            target, sidecar = picked
            report = check_file(target, ffprobe, sidecar=sidecar,
                                expect_flag=args.expect,
                                require_faststart=not args.no_faststart)
    except CheckError as e:
        print(f"[codec-gate] ERROR: {e}", file=sys.stderr)
        return 2

    _print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
