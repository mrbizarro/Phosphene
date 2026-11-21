"""H3 "Write it for me": a small local model that turns a short idea into the
long, structured prompt Hailuo H3 was trained on.

WHAT IT IS
----------
LightX2V's `MiniMax-H3-Prompt-Rewriter-Qwen3.5-9B-FL2VA` (Apache 2.0), a Qwen3.5
9B fine-tuned to rewrite a user prompt into H3's three-field form
(`integrated_multimodal_description` / `overall_soundscape` /
`non_diegetic_music`, timed `[Shot N]` cuts, dialogue inside
`<d>[English] ...</d>`). It reads MiniMax's own writing guide
(`VIDEO_PROMPT_WRITING_GUIDE_base_en.md`, shipped beside the weights) as its
system prompt; the request format below is the one it was trained on. The code
is ours (mlx-lm), no ComfyUI, llama.cpp or PyTorch.

Measured (R&D L48, M4 Max 64 GB, MLX 8-bit): median 10.1 s per rewrite, 11.4 GB
peak, 20/20 rewrites in H3's format, spoken lines kept word for word. In both
finished same-seed H3 A/Bs the rewritten prompt fixed what the short one got
wrong (the action and the spoken line).

WHERE IT RUNS
-------------
In a short-lived child of the LTX engine venv (mlx-lm 0.31.1 has `qwen3_5`),
under the panel's GPU gate, BEFORE an H3 render and never beside one: the child
loads, writes, and exits, so nothing stays resident (same policy and reasons as
storyboard_planner.py). The LTX helper is parked first, like before any other
MLX runtime.

HOW IT GETS ON THE MAC
----------------------
Opt-in, on the first click: `scripts/pinokio/h3_rewriter_fetch.py` downloads the
publisher's checkpoint at a pinned revision (never re-hosted), converts it to
MLX on this Mac (`mlx_lm convert -q`, vision tower dropped, 8-bit; 4-bit on Macs
under 32 GB), copies the licence and the guide beside it, and deletes the
download. ~19 GB fetched, ~9 GB kept (~5 GB at 4-bit).

Free for everyone: no licence screen (Apache 2.0) and no Pro check.

Pure stdlib, Python 3.9-compatible: the panel imports this; only the child
imports mlx.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ROOT = Path(__file__).resolve().parent
MODELS_DIR = Path(os.environ.get("LTX_MODELS_DIR", str(ROOT / "mlx_models")))

REPO_ID = "lightx2v/MiniMax-H3-Prompt-Rewriter-Qwen3.5-9B-FL2VA"
# Pinned: the revision R&D L48 measured. Move it only with a re-run of the
# format check (scripts/h3_rewriter_worker.py --selftest) on the new revision.
REVISION = "ea2a677e0597831a992a400224d98dde4c405fe6"
LICENCE_NAME = "Apache License 2.0"
GUIDE_FILE = "VIDEO_PROMPT_WRITING_GUIDE_base_en.md"
# Upstream download (BF16, the four text+vision shards and the small files;
# the separate multi-token-prediction head is not needed and not fetched).
DOWNLOAD_BYTES = 18_820_000_000
KEPT_BYTES = {8: 9_520_000_000, 4: 5_300_000_000}
# Room needed while converting: the download and the converted copy coexist.
NEEDED_FREE_BYTES = DOWNLOAD_BYTES + KEPT_BYTES[8] + 2_000_000_000

DIR_NAME = {8: "h3-prompt-rewriter-9b-mlx-q8", 4: "h3-prompt-rewriter-9b-mlx-q4"}
MARKER = "phosphene_rewriter.json"     # written last by the fetcher = complete

DURATIONS = (3, 5, 10, 15)
WORKER = ROOT / "scripts" / "h3_rewriter_worker.py"
FETCHER = ROOT / "scripts" / "pinokio" / "h3_rewriter_fetch.py"
TIMEOUT_S = int(os.environ.get("LTX_H3_REWRITER_TIMEOUT", "300"))
SENTINEL = "@@REWRITER@@ "
MAX_PROMPT_CHARS = 4000


def _ram_gb() -> float:
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        return int(out) / (1 << 30)
    except Exception:                                           # noqa: BLE001
        return 64.0


def bits_for_this_mac(ram_gb: Optional[float] = None) -> int:
    """8-bit is what L48 measured (11.4 GB peak). Under 32 GB that peak sits
    on top of macOS and the panel, so those Macs get the 4-bit build (~5 GB
    weights). LTX_H3_REWRITER_BITS=4|8 overrides."""
    forced = os.environ.get("LTX_H3_REWRITER_BITS", "").strip()
    if forced in ("4", "8"):
        return int(forced)
    ram = _ram_gb() if ram_gb is None else ram_gb
    return 8 if ram >= 32 else 4


def model_dir(bits: Optional[int] = None) -> Path:
    override = os.environ.get("LTX_H3_REWRITER_PATH", "").strip()
    if override:
        return Path(override)
    return MODELS_DIR / DIR_NAME[bits or bits_for_this_mac()]


def installed(path: Optional[Path] = None) -> bool:
    p = path or model_dir()
    if os.environ.get("LTX_H3_REWRITER_PATH", "").strip():
        # A hand-made build (the lab's) has no marker; the weights are enough.
        return (p / "config.json").is_file() and (p / GUIDE_FILE).is_file()
    return ((p / MARKER).is_file() and (p / "config.json").is_file()
            and (p / GUIDE_FILE).is_file()
            and any(p.glob("model*.safetensors")))


def _resolve_worker_python() -> Path:
    cand = os.environ.get("LTX_HELPER_PYTHON")
    if cand and Path(cand).is_file():
        return Path(cand)
    for name in ("python3.11", "python"):
        p = ROOT / "ltx-2-mlx" / "env" / "bin" / name
        if p.is_file():
            return p
    return Path(sys.executable)


# ---------------------------------------------------------------------------
# Download + convert (background, opt-in)
# ---------------------------------------------------------------------------
_FETCH: Dict[str, Any] = {"running": False, "line": "", "error": "", "pct": None}
_FETCH_LOCK = threading.Lock()


def fetch_state() -> dict:
    return dict(_FETCH)


def status() -> dict:
    bits = bits_for_this_mac()
    path = model_dir(bits)
    return {
        "ready": installed(path),
        "bits": bits,
        "path": str(path),
        "download_gb": round(DOWNLOAD_BYTES / 1e9, 1),
        "kept_gb": round(KEPT_BYTES[bits] / 1e9, 1),
        "needed_free_gb": round(NEEDED_FREE_BYTES / 1e9, 1),
        "licence": LICENCE_NAME,
        "source": f"https://huggingface.co/{REPO_ID}",
        "fetch": fetch_state(),
    }


def _free_bytes(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(str(p)).free
    except OSError:
        return 0


def fetch_start(push: Callable[[str], None] = print,
                env: Optional[dict] = None,
                gpu: "Optional[tuple[Callable[[], None], Callable[[], None]]]" = None) -> "tuple[int, dict]":
    """Start the one-time download + conversion. 202 started, 200 already
    there, 409 already running, 507 not enough disk."""
    with _FETCH_LOCK:
        if _FETCH["running"]:
            return 409, {"error": "The prompt writer is already downloading."}
        bits = bits_for_this_mac()
        dest = model_dir(bits)
        if installed(dest):
            return 200, {"ok": True, "ready": True}
        free = _free_bytes(dest.parent)
        if free < NEEDED_FREE_BYTES:
            return 507, {"error": (
                f"Write it for me needs about {NEEDED_FREE_BYTES / 1e9:.0f} GB free "
                f"while it sets up (it keeps {KEPT_BYTES[bits] / 1e9:.0f} GB); this Mac "
                f"has {free / 1e9:.0f} GB free.")}
        _FETCH.update(running=True, line="starting", error="", pct=0)

    def run() -> None:
        held = False
        try:
            cmd = [str(_resolve_worker_python()), str(FETCHER), "--dest", str(dest),
                   "--repo", REPO_ID, "--revision", REVISION, "--bits", str(bits)]
            if gpu:
                cmd.append("--wait-gpu")
            proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env or os.environ.copy(),
                                    stdin=subprocess.PIPE if gpu else None,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors="replace", bufsize=1)
            for line in proc.stdout or ():
                line = line.strip()
                if not line:
                    continue
                if line == "WAITGPU" and gpu:
                    # The download is done; the MLX conversion waits for any render, then
                    # holds the Studio's GPU gate until the setup process ends.
                    _FETCH["line"] = "waiting for the current render to finish"
                    gpu[0]()
                    held = True
                    proc.stdin.write("go\n"); proc.stdin.flush()
                    continue
                if line.startswith("PCT "):
                    try:
                        _FETCH["pct"] = int(line.split()[1])
                    except (IndexError, ValueError):
                        pass
                    continue
                _FETCH["line"] = line[:200]
                push(f"[h3-writer] {line}")
            if proc.wait() != 0:
                # The fetcher's last line says what stopped; the "FATAL error:"
                # tag is for the log, not for the toast the user reads.
                last = (_FETCH.get("line") or "").replace("FATAL error: ", "", 1)
                _FETCH["error"] = last or f"the prompt writer setup failed (exit {proc.returncode})"
            elif not installed(dest):
                _FETCH["error"] = "the prompt writer setup finished without a usable model"
        except Exception as exc:                                # noqa: BLE001
            _FETCH["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if held and gpu:
                gpu[1]()
            _FETCH["running"] = False
            if not _FETCH["error"]:
                _FETCH.update(line="ready", pct=100)

    threading.Thread(target=run, name="h3-rewriter-fetch", daemon=True).start()
    return 202, {"ok": True, "started": True}


# ---------------------------------------------------------------------------
# The rewrite itself
# ---------------------------------------------------------------------------
def snap_duration(seconds: Any) -> int:
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return 5
    return min(DURATIONS, key=lambda d: abs(d - s))


def request(prompt: str, duration: int, one_shot: bool) -> dict:
    """The job the worker runs. Kept here (stdlib) so it is unit-tested
    without mlx; the worker turns it into chat messages."""
    return {"prompt": prompt.strip()[:MAX_PROMPT_CHARS], "duration": int(duration),
            "one_shot": bool(one_shot), "task": "T2VA"}


def looks_like_h3_prompt(text: str) -> bool:
    return ("integrated_multimodal_description:" in text
            and "overall_soundscape:" in text)


def rewrite(prompt: str, seconds: Any = 5, one_shot: bool = False,
            path: Optional[Path] = None, timeout: Optional[int] = None,
            runner: Optional[Callable[[list, str, int], "tuple[int, str, str]"]] = None) -> dict:
    """Run one rewrite in a fresh child process. Never raises for a model
    failure; returns {"ok": False, "error": ...} instead."""
    if not (prompt or "").strip():
        return {"ok": False, "error": "Type an idea first."}
    p = path or model_dir()
    if not installed(p):
        return {"ok": False, "error": "The prompt writer is not set up yet.",
                "needs_install": True}
    job = request(prompt, snap_duration(seconds), one_shot)
    cmd = [str(_resolve_worker_python()), str(WORKER), "--model", str(p)]
    t0 = time.time()
    run = runner or _run_child
    try:
        rc, out, err = run(cmd, json.dumps(job), timeout or TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The prompt writer took too long and was stopped."}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "error": f"The prompt writer could not start: {exc}"}
    reply = None
    for line in (out or "").splitlines():
        if line.startswith(SENTINEL):
            try:
                reply = json.loads(line[len(SENTINEL):])
            except ValueError:
                reply = None
    if not isinstance(reply, dict) or rc != 0:
        tail = (err or "").strip().splitlines()[-1:] or [f"exit {rc}"]
        detail = (reply or {}).get("error") if isinstance(reply, dict) else None
        return {"ok": False, "error": f"The prompt writer failed: {detail or tail[0]}"[:300]}
    text = str(reply.get("rewrite") or "").strip()
    if not text:
        return {"ok": False, "error": "The prompt writer returned nothing."}
    return {"ok": True, "original": job["prompt"], "rewrite": text,
            "format_ok": looks_like_h3_prompt(text),
            "duration": job["duration"], "one_shot": job["one_shot"],
            "seconds": round(time.time() - t0, 1),
            "gen_seconds": reply.get("gen_seconds"),
            "peak_gb": reply.get("peak_gb")}


def _run_child(cmd: list, stdin_text: str, timeout: int) -> "tuple[int, str, str]":
    proc = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                          errors="replace", timeout=timeout, cwd=str(ROOT))
    return proc.returncode, proc.stdout, proc.stderr
