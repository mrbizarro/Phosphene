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
`mlx_outputs/` — i.e. newest `*.mp4` that has a `*.mp4.json` sidecar
CARRYING AN `output_codec` BLOCK — skipping `engine: "h3"` jobs (the H3 mux
is not the patched LTX encoder and is legitimately yuv420p). When the
sidecar names a `native_output` distinct from the delivered file, the NATIVE
file is checked: the panel-side upscale export re-encodes with the settings
codec + faststart and would mask a broken patch underneath.

THE `output_codec` BLOCK IS THE ATTRIBUTION, NOT THE SIDECAR. Sidecar
presence alone was the first cut and it false-positived immediately: the lab
and bench harnesses (`mlx_outputs/hq704_*`, perf sweeps, chained-window
probes) write their own sidecars through plain ffmpeg, with no
`output_codec` block, because they never went through the panel's encode
path. Resolution then fell all the way through to `PATCHED_DEFAULT_PIX_FMT`
and asserted a lab render against a request nobody ever made — a red
"Renders are not being encoded as requested" banner and a boot warning on a
perfectly healthy install. A sidecar with no `output_codec` block records no
codec request, so there is nothing to assert: SKIP it and keep walking back
to older candidates. If nothing in the directory carries one, that is
"nothing to check", not a failure.

With an explicit path: that file is checked as-is (no `output_codec`
requirement — an explicit path is an explicit instruction). If its sidecar
says `engine: "h3"`, the probe is reported but only asserted when `--expect`
was given explicitly — H3's native encode never went through the patch.

Exit codes: 0 = pass, 1 = assertion failed, 2 = nothing to check /
environment / usage (no ffprobe, no candidate file, unreadable target, or an
INCONCLUSIVE probe — an mp4 whose box structure does not parse, which used
to pass the faststart half of the gate silently). A release gate treats any
non-zero as DO NOT SHIP; 2 means "go find out", 1 means "it is broken".

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
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "mlx_outputs"
PATCHED_DEFAULT_PIX_FMT = "yuv444p"  # the patched encoder's env-absent default


class CheckError(RuntimeError):
    """Environment/usage failure — exit 2, distinct from an assertion FAIL."""


def find_ffprobe() -> Path | None:
    """Same resolution ladder as the panel's ffmpeg: explicit env first, then
    PATH, then the Pinokio ffmpeg-env, then the usual Homebrew locations."""
    # Read the env var ONCE and branch on that value. The previous form built
    # the path from os.environ["LTX_FFMPEG"] while guarding on a separate
    # os.environ.get("LTX_FFMPEG") — two independent reads of mutable state
    # inside one comprehension element, i.e. a KeyError waiting for the first
    # caller that clears the var between them (the panel re-syncs env at job
    # time from another thread).
    ffmpeg = os.environ.get("LTX_FFMPEG")
    candidates = [
        os.environ.get("LTX_FFPROBE"),
        # Sibling of an explicitly-pinned ffmpeg (the panel's LTX_FFMPEG knob).
        str(Path(ffmpeg).parent / "ffprobe") if ffmpeg else None,
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


def requested_pix_fmt(sidecar: dict | None) -> str | None:
    """The pix_fmt this render's sidecar says was REQUESTED, or None when the
    sidecar carries no `output_codec` block at all. None means "not produced
    by the panel's encode path" — nothing was requested, so nothing can be
    asserted. The panel's output_codec_settings() always writes pix_fmt, so a
    block without one is as unattributable as no block."""
    block = (sidecar or {}).get("output_codec")
    if not isinstance(block, dict):
        return None
    value = block.get("pix_fmt")
    return str(value) if value else None


def panel_output_candidates(output_dir: Path) -> Iterator[tuple[Path, dict]]:
    """Newest-first LTX-lane clips with a sidecar, H3 excluded. Lazy on
    purpose: a real mlx_outputs/ holds thousands of files and both callers
    stop at the first hit."""
    try:
        mp4s = sorted(output_dir.glob("*.mp4"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for mp4 in mp4s:
        sidecar = sidecar_for(mp4)
        if sidecar is None:
            continue
        if sidecar_engine(sidecar) == "h3":
            continue
        native = sidecar.get("native_output") or sidecar.get("raw_output")
        if native and Path(native).is_file() and Path(native) != mp4:
            yield Path(native), sidecar
        else:
            yield mp4, sidecar


def newest_panel_output(output_dir: Path, *,
                        require_output_codec: bool = True
                        ) -> tuple[Path, dict] | None:
    """Newest panel-rendered LTX-lane clip: newest mp4 WITH a sidecar that
    records an `output_codec` request and whose engine isn't h3. That block —
    not mere sidecar presence — is the attribution: lab and bench harnesses
    write sidecars too, and asserting one of those against the patched
    default is a false alarm, not a finding. Candidates without it are
    SKIPPED and the walk continues to older files.
    Returns (file_to_check, sidecar); the file is the sidecar's
    native_output when that exists (see module docstring)."""
    for target, sidecar in panel_output_candidates(output_dir):
        if require_output_codec and requested_pix_fmt(sidecar) is None:
            continue
        return target, sidecar
    return None


def resolve_expected(sidecar: dict | None, expect_flag: str | None) -> tuple[str, str]:
    """→ (expected_pix_fmt, source-of-truth label)."""
    if expect_flag:
        return expect_flag, "--expect"
    requested = requested_pix_fmt(sidecar)
    if requested:
        return requested, "sidecar output_codec"
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
    inconclusive: list[str] = []
    if require_faststart and engine != "h3":
        if faststart is False:
            problems.append(
                "no +faststart (moov after mdat) - the unpatched upstream "
                "encoder's fingerprint")
        elif faststart is None:
            # has_faststart() returns None on ANY parse failure - truncated
            # file, exotic box layout, unreadable. The old code only tested
            # `faststart is False`, so an unparseable mp4 sailed through half
            # the gate reporting PASS. Half a gate that says PASS is the
            # v3.8.1 shape all over again, so say so instead.
            inconclusive.append(
                "faststart is UNKNOWN - the mp4 box structure did not parse "
                "(truncated or still being written?). The +faststart half of "
                "this gate did NOT run on this file.")
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
        "inconclusive": inconclusive,
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
    for note in report.get("inconclusive", ()):
        print(f"[codec-gate] INCONCLUSIVE: {note}")
    if report["ok"]:
        if report.get("inconclusive"):
            print(f"[codec-gate] INCONCLUSIVE - {name} passed every check that "
                  "could be run, but not all of them ran. Exit 2, not 0.")
            return
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


# =============================================================================
# --self-test — the gate's own gate
# =============================================================================
# The false-positive this file shipped with was a SELECTION bug, and selection
# had no test: every validation run pointed the gate at a real directory whose
# newest render happened to be a panel render. Hermetic fixtures (crafted mp4
# boxes + a stub ffprobe, no real encode, no network) so the skip path is
# proven on every run, on any machine, in well under a second.
_MOOV = b"\x00\x00\x00\x10moov" + b"\x00" * 8
_MDAT = b"\x00\x00\x00\x10mdat" + b"\x00" * 8
_UNPARSEABLE = b"\x00\x00\x00\x02junk"   # box_size 2 < 8 -> structure unknown


def _self_test() -> int:
    import contextlib
    import io
    import tempfile

    failures: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ok    {label}")
        else:
            failures.append(label)
            print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")

    def write_clip(d: Path, name: str, body: bytes,
                   sidecar: dict | None, mtime: float) -> Path:
        mp4 = d / f"{name}.mp4"
        mp4.write_bytes(body)
        if sidecar is not None:
            (d / f"{name}.mp4.json").write_text(json.dumps(sidecar),
                                                encoding="utf-8")
        os.utime(mp4, (mtime, mtime))
        return mp4

    def run_main(*argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue() + err.getvalue()

    PANEL = {"engine": "ltx",
             "output_codec": {"preset": "standard", "pix_fmt": "yuv420p",
                              "crf": "18"}}
    LAB = {"steps": 8, "seed": 720720}          # a bench sidecar: no codec block
    H3 = {"engine": "h3",
          "output_codec": {"preset": "standard", "pix_fmt": "yuv420p"}}

    print("[codec-gate] self-test")

    # --- requested_pix_fmt: what counts as "the panel asked for something" ---
    check("no output_codec block -> None",
          requested_pix_fmt(LAB) is None)
    check("output_codec: null -> None",
          requested_pix_fmt({"output_codec": None}) is None)
    check("output_codec without pix_fmt -> None",
          requested_pix_fmt({"output_codec": {"crf": "18"}}) is None)
    check("output_codec with pix_fmt -> the value",
          requested_pix_fmt(PANEL) == "yuv420p")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # A stub ffprobe pinned through LTX_FFPROBE (first rung of the
        # resolution ladder) makes the whole self-test hermetic: it needs no
        # real ffmpeg, no encode, and it cannot be perturbed by whatever the
        # ambient LTX_* env happens to say on this machine.
        stub = root / "ffprobe"
        stub.write_text(f"#!{sys.executable}\nprint('yuv420p')\n",
                        encoding="utf-8")
        stub.chmod(0o755)
        saved_env = {k: os.environ.get(k)
                     for k in ("LTX_FFPROBE", "LTX_FFMPEG",
                               "LTX_OUTPUT_PIX_FMT")}
        os.environ["LTX_FFPROBE"] = str(stub)
        os.environ.pop("LTX_FFMPEG", None)
        os.environ.pop("LTX_OUTPUT_PIX_FMT", None)
        try:
            # --- THE REGRESSION: a lab render must not shadow a panel one ---
            mixed = root / "mixed"
            mixed.mkdir()
            write_clip(mixed, "old_panel", _MOOV + _MDAT, PANEL, 1000)
            write_clip(mixed, "h3_clip", _MOOV + _MDAT, H3, 2000)
            write_clip(mixed, "lab_bench", _MDAT + _MOOV, LAB, 3000)
            write_clip(mixed, "orphan", _MDAT + _MOOV, None, 4000)
            picked = newest_panel_output(mixed)
            check("newest lab/H3/orphan clips are skipped for an older panel one",
                  picked is not None and picked[0].name == "old_panel.mp4",
                  f"picked {picked and picked[0].name}")

            # --- native_output redirection survives the new filter ----------
            native = root / "native"
            native.mkdir()
            raw = write_clip(native, "job_native", _MOOV + _MDAT, None, 1000)
            write_clip(native, "job_720p", _MOOV + _MDAT,
                       dict(PANEL, native_output=str(raw)), 2000)
            picked = newest_panel_output(native)
            check("a sidecar's native_output is still what gets checked",
                  picked is not None and picked[0].name == "job_native.mp4",
                  f"picked {picked and picked[0].name}")

            # --- lab-only directory: skip, not failure ----------------------
            labs = root / "labs"
            labs.mkdir()
            write_clip(labs, "sweep_a", _MDAT + _MOOV, LAB, 1000)
            write_clip(labs, "sweep_b", _MDAT + _MOOV, LAB, 2000)
            check("a lab-only directory yields no candidate",
                  newest_panel_output(labs) is None)
            check("...but they ARE candidates without the output_codec filter",
                  newest_panel_output(labs,
                                      require_output_codec=False) is not None)
            code, text = run_main(str(labs))
            check("lab-only directory exits 2 (nothing to check), never 1",
                  code == 2, f"exit {code}")
            check("...and says WHY, naming the missing output_codec block",
                  "NOTHING TO CHECK" in text and "output_codec" in text
                  and "sweep_b.mp4" in text, text.strip()[-160:])
            check("...and does NOT print the codec-failure banner",
                  "OUTPUT CODEC GATE FAILED" not in text)

            empty = root / "empty"
            empty.mkdir()
            code, text = run_main(str(empty))
            check("an empty directory still exits 2 with its own reason",
                  code == 2 and "no panel-rendered" in text, f"exit {code}")

            # --- faststart box walk -----------------------------------------
            check("moov before mdat -> True",
                  has_faststart(write_clip(root, "fs_yes", _MOOV + _MDAT,
                                           None, 1000)) is True)
            check("mdat before moov -> False",
                  has_faststart(write_clip(root, "fs_no", _MDAT + _MOOV,
                                           None, 1000)) is False)
            check("unparseable boxes -> None",
                  has_faststart(write_clip(root, "fs_junk", _UNPARSEABLE,
                                           None, 1000)) is None)

            # --- the real assertions are untouched for real panel renders ---
            good = root / "good"
            good.mkdir()
            write_clip(good, "render", _MOOV + _MDAT, PANEL, 1000)
            code, text = run_main(str(good))
            check("a real panel render still PASSES against its own sidecar",
                  code == 0 and "PASS" in text,
                  f"exit {code}: {text.strip()[-160:]}")

            bad = root / "bad"
            bad.mkdir()
            write_clip(bad, "render", _MDAT + _MOOV, PANEL, 1000)
            code, text = run_main(str(bad))
            check("a panel render with no +faststart still FAILS (exit 1)",
                  code == 1 and "OUTPUT CODEC GATE FAILED" in text,
                  f"exit {code}")

            wrong = root / "wrong"
            wrong.mkdir()
            write_clip(wrong, "render", _MOOV + _MDAT,
                       {"engine": "ltx",
                        "output_codec": {"pix_fmt": "yuv444p"}}, 1000)
            code, text = run_main(str(wrong))
            check("a pix_fmt mismatch still FAILS against the SIDECAR's request",
                  code == 1 and "expected yuv444p" in text, f"exit {code}")

            torn = root / "torn"
            torn.mkdir()
            write_clip(torn, "render", _UNPARSEABLE, PANEL, 1000)
            code, text = run_main(str(torn))
            check("an unparseable mp4 is INCONCLUSIVE (exit 2), not a "
                  "silent pass", code == 2 and "INCONCLUSIVE" in text,
                  f"exit {code}")
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    print("")
    print(f"RESULT: {'FAIL (%d)' % len(failures) if failures else 'PASS'}")
    return 1 if failures else 0


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
    ap.add_argument("--self-test", action="store_true",
                    help="run this gate's own hermetic fixtures (no ffmpeg, "
                         "no renders) and exit; proves the lab-sidecar skip")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

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
                # Distinguish "nothing here at all" from "things are here but
                # none of them recorded a codec request". The second is the
                # lab/bench case and reads as an alarm if it isn't named.
                loose = newest_panel_output(scan_dir, require_output_codec=False)
                if loose is None:
                    print(f"[codec-gate] NOTHING TO CHECK: no panel-rendered "
                          f"(sidecar'd, non-H3) mp4 found under {scan_dir}. "
                          "Render the checklist smoke first, or pass an "
                          "explicit path.", file=sys.stderr)
                else:
                    print(f"[codec-gate] NOTHING TO CHECK: every sidecar'd "
                          f"non-H3 mp4 under {scan_dir} was written WITHOUT an "
                          "output_codec block (newest: "
                          f"{Path(loose[0]).name}), i.e. by a lab/bench "
                          "harness rather than the panel's encode path. No "
                          "codec was requested, so there is nothing to "
                          "assert - this is NOT a codec failure. Render "
                          "through the panel, or pass an explicit path.",
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
    if not report["ok"]:
        return 1
    return 2 if report.get("inconclusive") else 0


if __name__ == "__main__":
    sys.exit(main())
