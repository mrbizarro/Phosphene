"""Image generation engines for the Agentic Flows planner.

The director-collaboration loop:

  1. Agent plans a multi-shot piece in plain text.
  2. Agent calls `generate_shot_images(shot_label, prompt, n=4)` per shot.
     Each call returns a list of candidate PNGs saved on disk.
  3. UI renders the candidates as a thumbnail grid in the tool-result
     card. User clicks the best one — that POSTs to
     `/agent/sessions/<id>/anchors/select` which records the choice in
     `session.tool_state["selected_anchors"]`.
  4. User types "render". Agent reads the selected anchors and submits
     i2v shots with each anchor as `ref_image_path`. The video model
     fills the motion between known frames; the look is locked.

This module is the dispatch layer. Four backends ship today:

  - **mock** — flat-colored PNGs drawn with PIL. Zero deps beyond what
    LTX already needs. Used for testing the UX without spending API
    credits and as a fallback when no API key is set.
  - **bfl**  — Black Forest Labs (api.bfl.ml). Async submit + poll.
    Requires `bfl_api_key`. Models: flux-dev (cheap, 25 steps),
    flux-pro (better, slower), flux-schnell (4 steps, fastest).
  - **mflux** — fully-local Mac generation via filipstrand/mflux. Now
    multi-family: mflux 0.17.x ships per-family CLI commands
    (`mflux-generate-flux2`, `mflux-generate-z-image-turbo`,
    `mflux-generate-fibo`, `mflux-generate-qwen`, `mflux-generate-kontext`)
    in addition to the legacy `mflux-generate` (flux1-only). We auto-detect
    the family from the model id and call the right binary, with
    per-family step / guidance defaults so users who just pick a model
    don't have to know it needs 4 steps vs 25.
  - **hidream** — fully-local HiDream-O1-Image-Dev (8B Qwen3-VL-based unified
    pixel-patch transformer, MIT). Lives in its own venv outside Phosphene
    at ``$HIDREAM_LAB_DIR`` (defaults to ``~/HIDREAM-O1-MLX-LAB-active``).
    Subprocess pattern, mirrors
    mflux. Default **BF16** (~16 GB working set, 1024×1024 in ~67s) —
    matches upstream's master-weight precision and is the only precision
    that doesn't show patch-grid artifacts in flat regions. Q8/Q6 work
    at square 2048×2048 but exhibit visible 32-px grid artifacts at
    non-square trained dims. Q4 ships dark (brightness collapse).
    **Supports HiDream's native edit + multi-ref**: K=1 instruction edits
    ("change the white jacket to red") preserve scene+pose+identity;
    K=2-3 subject-driven personalization composes multiple references
    into a new scene. Edit requires BF16 (quant breaks ref attention).

  Recommended defaults (May 2026):
    - **Comfortable+ (32 GB+)**  → `Runpod/FLUX.2-klein-4B-mflux-4bit`
      via `mflux-generate-flux2`, 4 steps, guidance 1.0. Apache 2.0,
      ~4.3 GB on disk, 4 candidates per shot in 50-75 s.
    - **Compact (16-32 GB)**     → `filipstrand/Z-Image-Turbo-mflux-4bit`
      via `mflux-generate-z-image-turbo`, 9 steps, guidance 0.0.
      Apache 2.0, ~5.9 GB on disk.

See `docs/IMAGE_GEN_RESEARCH_2026-05.md` for the full landscape and
tier-aware default table.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

import image_diagnostics

_DIAGNOSTIC_BEGIN = None
_MFLUX_STALL_SECONDS = 1800.0
_MFLUX_WATCHDOG_POLL = 15.0

import distribution as _dist   # Pinokio vs the Mac app wording


# Active-subprocess registry — every image engine that spawns a Popen
# registers it here so the panel's /stop endpoint can terminate the
# whole tree. Without this the Stop button only killed the LTX video
# HELPER and image generations ran to completion. Engines must:
#   1. Spawn with `start_new_session=True` so kill cascades to the
#      child's process group (mflux + Metal spawn helpers).
#   2. Call `_register_active_proc(proc)` immediately after Popen.
#   3. Wrap the wait in try/finally and `_unregister_active_proc(proc)`
#      in the finally — so a normal exit doesn't leave a stale entry.
_ACTIVE_PROC_LOCK = threading.Lock()
_ACTIVE_PROCS: set = set()  # set[subprocess.Popen]


def _clean_subprocess_env() -> dict:
    """os.environ.copy() with macOS Malloc* debug vars stripped.

    Pinokio's launcher (and some macOS dev tools) export Malloc* env
    vars — most commonly an empty/zero MallocStackLogging or
    MallocStackLoggingNoCompact. Subprocesses inherit those, and each
    child prints "MallocStackLogging: can't turn off malloc stack
    logging because it was not enabled" to stderr on exit. With the
    HiDream / mflux lab spawning 50+ Python subprocesses per request
    (HF download workers + the generation process + helpers), that
    flooded the terminal. Stripping the Malloc* vars makes the
    warning go away cleanly — those vars are only useful if you're
    actively profiling with Instruments, and we never are.
    """
    import os as _os
    env = _os.environ.copy()
    for key in list(env.keys()):
        if key.startswith("Malloc"):
            del env[key]
    return env


def _hf_hub_root(env: "dict | None" = None) -> Path:
    """The Hub cache root the mflux subprocess will resolve, from ITS env —
    HF_HUB_CACHE, else HF_HOME/hub, else ~/.cache/huggingface/hub, the
    order huggingface_hub itself uses."""
    import os as _os
    src = env if env is not None else _os.environ
    explicit = src.get("HF_HUB_CACHE") or src.get("HUGGINGFACE_HUB_CACHE")
    if explicit:
        return Path(explicit).expanduser()
    home = src.get("HF_HOME")
    if home:
        return Path(home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _is_hf_repo_id(value) -> bool:
    # mflux's own test for "this is a Hub id, not a path": exactly one slash
    # and no path prefix.
    s = str(value or "")
    return ("/" in s and s.count("/") == 1
            and not s.startswith(("./", "../", "~/", "/")))


def hf_repo_partial_download(repo_id: str, env: "dict | None" = None) -> "dict | None":
    """Report an interrupted download of `repo_id` in the Hub cache, or None.

    huggingface_hub streams each file to `blobs/<sha>.incomplete` and renames
    it on completion, so a leftover `.incomplete` is the one unambiguous sign
    that a download stopped partway. #73: 38 of 54 GB had landed, 14 files
    were still `.incomplete`, the panel reported the engine ready and every
    image generation died in mflux's weight loader.
    """
    repo_dir = _hf_hub_root(env) / ("models--" + repo_id.replace("/", "--"))
    blobs = repo_dir / "blobs"
    if not blobs.is_dir():
        return None
    try:
        entries = list(blobs.iterdir())
    except OSError:
        return None
    partial = [p for p in entries if p.name.endswith(".incomplete")]
    # An `.incomplete` whose finished blob is ALREADY here is litter: the file
    # was fetched again and completed under its real name, and huggingface_hub
    # never goes back for the leftover. Counting it kept a COMPLETE download
    # "partial" forever — the repair found nothing to fetch, the size never
    # moved, and the panel said "stuck at 4.6 GB" about a 4.6 GB model (fleet,
    # FLUX.2 klein, 14 renders on 3 installs).
    names = {p.name for p in entries}
    live = []
    for p in partial:
        if p.name[: -len(".incomplete")] in names:
            try:
                p.unlink()
                continue
            except OSError:
                pass
        live.append(p)
    partial = live
    if not partial:
        return None
    total = 0
    for p in entries:
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return {"incomplete": len(partial), "on_disk_gb": round(total / 1e9, 1),
            "repo_dir": repo_dir}


def clear_download_litter(repo_id: str, env: "dict | None", before: float) -> int:
    """Delete `.incomplete` blobs untouched since `before`; returns how many.

    Call ONLY after an engine run on this repo exited 0: the model loaded, so
    every file this revision needs is complete, and a leftover the run never
    touched belongs to no file it wants (an older revision, a renamed file).
    Left alone, it keeps hf_repo_partial_download() reporting the model as
    unfinished forever (fleet: "stuck at 4.6 GB" about a 4.6 GB model). The
    mtime guard keeps a download another process is running right now."""
    blobs = _hf_hub_root(env) / ("models--" + str(repo_id).replace("/", "--")) / "blobs"
    n = 0
    try:
        entries = list(blobs.iterdir())
    except OSError:
        return 0
    for p in entries:
        if not p.name.endswith(".incomplete"):
            continue
        try:
            if p.stat().st_mtime < before:
                p.unlink()
                n += 1
        except OSError:
            pass
    if n:
        _REPAIR_MEMO.pop(str(repo_id), None)
    return n


def download_progress_step(prev_gb: float, now_gb: float) -> "tuple[bool, str | None]":
    """One 15-second sample of a model download folder: (moved, log line).

    `moved` is any change of more than 1 MB either way - a download that
    restarts an unfinished file is alive. huggingface_hub's Xet downloader
    (hf_xet, which every repo uploaded since 2025 uses) cannot resume: it
    truncates the file's `.incomplete` to 0 and fetches it again, so the
    folder SHRINKS when a retry starts. -1 means the folder does not exist."""
    if now_gb < 0.0:
        return False, None
    if prev_gb < 0.0:
        return now_gb > 0.001, None             # the folder appeared: alive, nothing to compare
    if now_gb > prev_gb + 0.001:
        return True, f"[download] model weights: {now_gb:.1f} GB"
    if now_gb < prev_gb - 0.001:
        return True, (f"[download] an unfinished file is being fetched again from "
                      f"its start (the Hugging Face downloader cannot resume it), "
                      f"so the model folder dropped to {now_gb:.1f} GB")
    return False, None


def _download_stopped_note(repo_id, env, cause: str = "") -> "str | None":
    """The first line of an engine failure whose model download is unfinished,
    or None. Names the model, how far it got and the engine's own last line,
    and says the next Generate continues it (it does - the repair is never
    refused, see repair_partial_hf_download)."""
    if not _is_hf_repo_id(repo_id):
        return None
    try:
        info = hf_repo_partial_download(str(repo_id), env)
    except Exception:  # noqa: BLE001
        info = None
    if not info:
        return None
    why = f" The engine's last line: {cause[:220]}." if cause else ""
    return (f"The model download for {repo_id} stopped at "
            f"{float(info.get('on_disk_gb') or 0.0):.1f} GB before it finished "
            f"({info.get('incomplete')} file(s) unfinished).{why} Generate again to "
            f"continue it; if it stops again, check the network and free disk space.")


_REPAIR_MEMO: dict = {}   # repo_id -> on_disk_gb at the last repair attempt


def repair_partial_hf_download(repo_id: str, env: "dict | None" = None) -> "dict | None":
    """Make an interrupted download resume instead of crash.

    mflux resolves a Hub id cached-first, and its completeness test only
    asks whether every file pattern has SOME match — a snapshot missing 14
    of its transformer shards passes, mflux loads the shards it finds and
    dies in WeightLoader, and snapshot_download (the only thing that would
    resume the `.incomplete` blobs) is never called. Removing `snapshots/`
    — a tree of symlinks; every finished blob stays where it is — makes the
    cached rule fail, so mflux's own download rule runs: it re-links each
    blob that is already complete and resumes the rest. Returns the partial
    report when a repair was made, None when nothing was needed.
    """
    import shutil as _shutil
    info = hf_repo_partial_download(repo_id, env)
    if not info:
        _REPAIR_MEMO.pop(str(repo_id), None)
        return None
    # 4.19.18 (fleet, incident fw-20261102-1838): this used to REFUSE the
    # render when the size had not moved since the previous repair ("The
    # download for X is stuck at N GB - it did not grow on the last try") and
    # it never forgot that verdict, so every later Generate in the same panel
    # session failed in under a second without trying the download at all,
    # while the message told the user to "Generate again to resume it". One
    # M2 Max hit it 16 times on Ideogram 4 (16.7 -> 23.3 -> 30.1 GB): the
    # download only moved after a panel restart cleared the memo. Two facts
    # made the verdict wrong in the first place:
    #   - a pass that was Stopped (or killed) before the downloader wrote a
    #     byte is not a stalled download; cancelled jobs are not even reported;
    #   - the Hub's Xet downloader (hf_xet, every repo uploaded since 2025)
    #     cannot resume: it truncates an unfinished `.incomplete` to 0 and
    #     fetches that file again, so the folder's size is not a progress
    #     meter (measured on cocktailpeanut/ideogram-4-fp8: 58.7 MB -> 0 ->
    #     the whole file).
    # So a repair is never refused before it runs. The run itself says what
    # went wrong: a network or disk error from the engine, and, when it
    # exits with the download unfinished, `_download_stopped_note` names the
    # download on the error's first line.
    # Leftover `.incomplete` files that belong to no file this revision
    # needs used to be deleted here when the previous pass had re-linked
    # `snapshots/`. That proves only that the pass reached the Hub and
    # finished ONE file: a Stop after the first small file, or a blocked Xet
    # server, deleted partials the model still needed, the cache then read
    # complete, and mflux crashed in its weight loader on every render (#73).
    # Only a render that SUCCEEDED proves the download is whole; the litter
    # is cleared there (clear_download_litter, called by _generate_mflux).
    on_disk = round(float(info.get("on_disk_gb") or 0.0), 2)
    import time as _time
    _REPAIR_MEMO[str(repo_id)] = (on_disk, _time.time())
    snaps = info["repo_dir"] / "snapshots"
    if snaps.is_dir():
        _shutil.rmtree(snaps, ignore_errors=True)
    return info


class ImageJobCancelled(RuntimeError):
    """Raised when an image-generation subprocess is killed by /stop.

    Distinguishable from a real engine failure so the queue worker can
    mark the job 'cancelled' instead of 'failed'.
    """


# Set by the panel: "has Stop been pressed for the queue job this thread is
# running?" None outside the panel (tests, scripts) = never cancelled.
_CANCEL_CHECK = None


def _register_active_proc(proc) -> None:
    """Publish a fresh Popen where /stop looks, then honour a Stop that came
    first.

    THE ORDER IS THE FIX (same shape as the panel's _register_job_pgid). Stop
    sets the job's cancel flag and then reads this registry; this adds to the
    registry and then reads the flag. Whichever runs first, the other sees it:
    Stop finds the process and kills it, or this finds the flag and kills the
    process itself. Before, a Stop during the image job's preflight found no
    process to kill, and the engine started anyway and published its image."""
    with _ACTIVE_PROC_LOCK:
        _ACTIVE_PROCS.add(proc)
    check = _CANCEL_CHECK
    try:
        cancelled = bool(check and check())
    except Exception:        # noqa: BLE001
        cancelled = False
    if cancelled:
        import os
        import signal
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:        # noqa: BLE001
                pass
        try:
            proc.wait(timeout=5)
        except Exception:        # noqa: BLE001
            pass
        _unregister_active_proc(proc)
        raise ImageJobCancelled("image engine stopped before it started (Stop)")


def _unregister_active_proc(proc) -> None:
    with _ACTIVE_PROC_LOCK:
        _ACTIVE_PROCS.discard(proc)


def kill_active_image_procs() -> int:
    """Terminate every in-flight image subprocess.

    Returns the number of processes signaled. Called from the panel's
    `stop_current_job` so /stop covers both video (HELPER) and image
    (these) generation paths uniformly.

    Sends SIGTERM to each proc's whole process group (which is why the
    Popens use `start_new_session=True`). Caller is responsible for
    handling the resulting RC/cancellation in the engine wrapper.
    """
    import os
    import signal
    with _ACTIVE_PROC_LOCK:
        procs = list(_ACTIVE_PROCS)
    killed = 0
    for p in procs:
        try:
            pgid = os.getpgid(p.pid)
            os.killpg(pgid, signal.SIGTERM)
            killed += 1
        except (ProcessLookupError, PermissionError, OSError):
            # Already gone or permission denied — try a direct kill as
            # a fallback before giving up.
            try:
                p.terminate()
                killed += 1
            except Exception:        # noqa: BLE001
                pass
    return killed


@dataclass
class ImageEngineConfig:
    """How the agent generates anchor stills.

    Persisted in `state/agent_image_config.json`. The HTTP `/agent/image/config`
    GET masks `bfl_api_key` (returns `has_bfl_api_key` bool only).
    """

    # `mock` was the historical default but it produces flat colored
    # rectangles — every fresh install hit this and got confused output
    # before realizing they had to pick a real engine in Settings. New
    # default is `mflux` with FLUX.2 [klein] (Apache 2.0, ~4.3 GB,
    # 4-step generation). Existing configs with kind=mock get auto-
    # promoted at panel load time IF mflux is installed (see
    # _load_agent_image_config in mlx_ltx_panel.py).
    kind: str = "mflux"                             # "mock" | "mflux" | "bfl" | "hidream"

    # FLUX 3 always runs in its own worker environment.
    flux3_variant: str = "distilled"

    # BFL (cloud)
    bfl_api_key: str = ""
    bfl_model: str = "flux-dev"                     # flux-dev | flux-pro | flux-pro-1.1 | flux-schnell
    bfl_base_url: str = "https://api.bfl.ml/v1"

    # mflux (local, MLX-native — `pip install mflux`).
    # Default updated 2026-05 to FLUX.2 [klein] 4B 4-bit — Apache 2.0,
    # 4-step inference, ~4.3 GB on disk, ~12-18 s/image on Comfortable
    # (M-series 32 GB+). Picks superseded the previous `krea-dev`
    # default which is non-commercial and 12 GB.
    #
    # `mflux_model` accepts either a named shorthand the CLI understands
    # ("krea-dev", "dev", "schnell", "dev-fill", "flux2-klein-4b") OR an
    # HF repo id / local path (e.g. "Runpod/FLUX.2-klein-4B-mflux-4bit").
    # `mflux_family` is "auto" by default — `_infer_mflux_family` reads
    # the model string and dispatches to the right CLI:
    #   flux1            → mflux-generate           (krea-dev, dev, schnell)
    #   flux2            → mflux-generate-flux2     (klein-4B/9B, dev)
    #   z_image_turbo    → mflux-generate-z-image-turbo
    #   z_image          → mflux-generate-z-image
    #   fibo             → mflux-generate-fibo
    #   qwen             → mflux-generate-qwen
    #   kontext          → mflux-generate-kontext
    # Override only when the inference fails for a custom model name.
    mflux_model: str = "Runpod/FLUX.2-klein-4B-mflux-4bit"
    mflux_family: str = "auto"                      # "auto" or one of the family ids above
    mflux_base_model: str = ""                      # only needed when mflux_model is a path/HF id and family inference can't tell the architecture
    mflux_steps: int = 4                            # Lightning 4-step default (was 0 = family default). The default Lightning LoRA below is distilled for 4 steps; the auto-promote in mlx_ltx_panel.py picks this up when promoting mock/legacy configs onto qwen_edit. Non-Lightning families ignore this and use the family default in MFLUX_FAMILY_DEFAULTS via the `eff_steps = config.mflux_steps if config.mflux_steps > 0 else ...` path in _generate_mflux — set this back to 0 to force the family default.
    mflux_quantize: int = 6                         # 3 | 4 | 5 | 6 | 8 — Q6 is the Apple-Silicon community sweet spot (~4-6% quality loss vs full precision; Q4 is 8-12%). Draw Things uses Q6/Q5 by default. Q4 only matters on ≤16 GB Macs; on 64 GB M4 Max the speed gap is negligible and the quality jump is visible.
    mflux_guidance: float | None = None             # None = use family default (1.0 / 0.0 / 4.5 / 5.0 per family)
    # Ideogram 4 sampler preset. Only consumed by the `ideogram` family
    # (passed straight to `--preset`); ignored by every other mflux CLI.
    # None → the ideogram argv branch falls back to "V4_DEFAULT_20" (20
    # steps). Valid values: "V4_TURBO_12" | "V4_DEFAULT_20" | "V4_QUALITY_48".
    mflux_preset: str | None = None
    # Ideogram 4 "reference bridge". Ideogram is TEXT-TO-IMAGE only (no
    # --image-paths), so when this is on AND refs are present we vision-
    # caption the first ref(s) with local Gemma 3 and splice the caption
    # into the Ideogram prompt — a re-interpretation of the look, not a
    # pixel copy. Ignored by every non-ideogram family (they consume refs
    # directly via --image-paths). See the bridge block in _generate_mflux.
    ideogram_reference_bridge: bool = False
    mflux_python_path: str = ""                     # optional override for the mflux CLI location
    # Optional Lightning / acceleration LoRAs. With qwen_edit + a 4-step
    # Lightning LoRA, generation drops from ~5 min to ~10-15 sec per
    # image. Passed straight through to mflux's `--lora-paths` and
    # `--lora-scales` flags (lengths must match if both set; mflux
    # rejects the mismatch). Each path is a HuggingFace repo id, a
    # collection-format string (`repo:filename.safetensors`), or a
    # local file path.
    #
    # DEFAULT shipped 2026-05-26 (E2): bake the Lightning 4-step LoRA in
    # so a fresh ImageEngineConfig() — and any v2.x upgrade promoted by
    # _auto_promote_image_engine_kind — lands on the ~1:20-per-image tier
    # out of the box instead of the legacy ~2:35 8-step path. Lightning
    # was distilled for scale 1.0 — don't change it. Only applies when
    # the resolved family is qwen_edit; other families silently skip the
    # LoRA flags because mflux's non-qwen CLIs reject Lightning entries
    # on unrelated architectures. (No-op for the FLUX.2 / Z-Image / FIBO
    # paths because _resolve_mflux_family returns flux2/etc. and the
    # auto-promote rewrites the whole config — model + family + steps +
    # loras — when a real engine binary is detected on PATH.)
    mflux_lora_paths: list[str] = field(default_factory=lambda: [
        "lightx2v/Qwen-Image-Edit-2511-Lightning:Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"
    ])
    mflux_lora_scales: list[float] = field(default_factory=lambda: [1.0])

    # HiDream-O1-Image-Dev (BF16 MLX) — runs out of a separate lab venv outside
    # Phosphene's tree. Subprocess pattern; we never import mlx-vlm into
    # Phosphene's interpreter. Defaults below point at the canonical lab
    # location; override when the lab moves.
    hidream_python_path: str = ""                   # default = HIDREAM_LAB_DIR/.venv/bin/python
    hidream_model_path: str = ""                    # default = HIDREAM_LAB_DIR/mlx_models/hidream-o1-dev-bf16
    hidream_steps: int = 6                          # 6 + FBCache (threshold 0.15) at HD is ~80s denoise with quality matching the 20-step baseline. Lower step counts (3-4) drop detail; 6+FBCache is the empirical sweet spot before quality goes "creamy". Full overrides to 50.
    hidream_noise_scale: float = 7.5                # FlashFlowMatch tuned default; lowering collapses the image
    hidream_noise_clip_std: float = 2.5
    # New for the Full / undistilled variant: pick the lab script's recipe
    # via --model-type. "dev" = 28-step distilled (existing behaviour);
    # "full" = 50-step undistilled with CFG + FlowEulerScheduler. The lab
    # script auto-overrides steps + guidance + shift when model-type=full.
    hidream_recipe: str = "dev"                     # "dev" | "full"
    hidream_guidance_scale: float = 0.0             # 0 = no CFG (Dev). 5.0 typical for Full.
    # Dev-edit scheduler. Upstream switched from "flash" -> "flow_match" on
    # 2026-05-12 — flow_match (FlowMatchEulerDiscreteScheduler from diffusers
    # via torch CPU bridge) cures the milky/over-textured Dev-edit output we
    # saw in the morning gallery. Ignored for Full or T2I.
    hidream_editing_scheduler: str = "flow_match"   # "flow_match" | "flash"
    # First-Block Cache. At 6 steps the conservative 0.15 threshold gives
    # ~1 cache hit per edit, knocking ~30s off the wall-time without the
    # "creamy" detail loss that happens at higher thresholds. keep_last=8
    # always runs the final 8 of 36 layers so fine texture work survives.
    hidream_fb_cache: bool = True
    hidream_fb_threshold: float = 0.15
    hidream_fb_keep_last: int = 8

    # P-H3-Image (Pruna, on MiniMax H3), run by the H3 engine's own runner as a subprocess.
    # The panel fills the paths (it owns the H3 install); `ph3image_mp` is the quality tier:
    # Fast 1.5 / Draft 2 / Standard 4 / High 8 megapixels.
    ph3image_python_path: str = ""
    ph3image_script_path: str = ""
    ph3image_root: str = ""          # folder made by fetch_ph3_image.py
    ph3image_dit: str = ""           # MiniMax-H3 FL2VA transformer (our Q8 pack)
    ph3image_mp: float = 1.5
    ph3image_warm_service: bool = False  # Studio enables on qualified 64 GB hosts
    ph3image_pro_profile: bool = False  # private runner, admitted by the shared Pro gate
    ph3image_warm_batch: bool = False  # experimental until real-weight speed/parity validation

    def to_public_dict(self) -> dict:
        d = asdict(self)
        if d.get("bfl_api_key"):
            d["bfl_api_key"] = ""
            d["has_bfl_api_key"] = True
        else:
            d["has_bfl_api_key"] = False
        return d


# Aspect ratios → (width, height). Picks favor larger dimensions because
# Qwen-Image-Edit-2509 is documented to be best at 1024² and 1280×720;
# the previous 1024×576 default for 16:9 was a Flux-era artifact and
# visibly degrades qwen_edit output. Flux2 / Z-Image fine here too — they
# scale gracefully past 1024.
ASPECT_DIMS = {
    "16:9":  (1280, 720),
    "4:3":   (1024, 768),
    "1:1":   (1024, 1024),
    "9:16":  (720, 1280),
    "3:4":   (768, 1024),
    "21:9":  (1280, 544),
    # Small variants (value suffixed "s") — ~1/3–1/4 the pixels so weaker
    # Apple GPUs render fast and stay under the Metal watchdog. The client
    # CSS ratio uses parseFloat, which reads "9s" as 9, so the stage still
    # shows the right shape. Requested by cocktailpeanut.
    "16:9s": (768, 432),
    "1:1s":  (512, 512),
    "9:16s": (432, 768),
}


def generate(*, prompt: str, n: int, output_dir: Path,
             aspect: str = "16:9",
             base_seed: int | None = None,
             refs: list[str] | None = None,
             config: ImageEngineConfig,
             on_log: "callable | None" = None,
             diagnostic=None, install_env: dict | None = None) -> list[dict]:
    """Generate `n` candidate images for one shot. Saves under `output_dir`.

    Args:
        prompt: text prompt
        n: number of candidates
        output_dir: where PNGs land
        aspect: aspect-ratio key from ASPECT_DIMS
        base_seed: when given, candidate i uses base_seed + i (reproducible)
        refs: optional list of reference image paths (1-3 supported by
              Qwen-Image-Edit-2509 via mflux-generate-qwen-edit). Refs are
              the way to lock character + place / character + product
              composition without training a LoRA. The agent's path:
              user uploads or library-picks 1-3 reference images, the
              engine composes them per the prompt, the resulting still
              becomes an LTX keyframe. Backends without multi-ref support
              (mock, plain qwen, flux1/2, z_image, fibo, BFL) currently
              ignore refs — they fall back to text-only generation. The
              caller is responsible for picking a config.kind/family
              that respects refs (today: qwen_edit). If refs are passed
              to a non-supporting family, a warning shows in the result
              dict but the call still succeeds with text-only output.
        config: backend selection + per-backend params
        on_log: optional callback `(line: str) -> None` invoked once per
            stdout/stderr line emitted by the underlying engine. Lets the
            panel surface live mflux progress (e.g., tqdm `[12/30]` step
            lines) instead of waiting silently. None = discard log lines.

    Returns a list of `{png_path, seed, engine, width, height, ...}`
    dicts in submission order.
    """
    if config.kind != "ph3image":
        import ph3_image_service
        ph3_image_service.evict()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    width, height = ASPECT_DIMS.get(aspect, ASPECT_DIMS["16:9"])

    refs = list(refs or [])
    # Validate ref paths up-front so we fail fast — Qwen-Edit-2509 supports
    # 1-3 input images; more than 3 is unsupported by the model.
    if refs:
        if len(refs) > (10 if config.kind == "flux3" else 3):
            raise ValueError(
                f"refs: Qwen-Image-Edit-2509 supports 1-3 input images, got {len(refs)}"
            )
        for r in refs:
            if not Path(r).is_file():
                raise FileNotFoundError(f"ref image not found: {r}")

    if config.kind == "flux3":
        import flux3_engine
        return flux3_engine.generate(prompt, n, width, height, output_dir, base_seed,
                                     config, refs, on_log, install_env)
    if config.kind == "mock":
        return _generate_mock(prompt, n, width, height, output_dir, base_seed, refs=refs)
    if config.kind == "mflux":
        if diagnostic is None and _DIAGNOSTIC_BEGIN is not None:
            try:
                diagnostic = _DIAGNOSTIC_BEGIN()
            except Exception:
                pass
        image_diagnostics.attempt(diagnostic, "prepare", prompt, refs, width, height)
        try:
            return _generate_mflux(prompt, n, width, height, output_dir, base_seed, config, refs=refs, on_log=on_log, diagnostic=diagnostic)
        except ImageJobCancelled:
            image_diagnostics.attempt(diagnostic, "finish", "cancelled")
            raise
        except Exception:
            image_diagnostics.attempt(diagnostic, "finish", "failed")
            raise
    if config.kind == "bfl":
        return _generate_bfl(prompt, n, width, height, output_dir, base_seed, config)
    if config.kind == "hidream":
        return _generate_hidream(prompt, n, width, height, output_dir, base_seed, config, refs=refs, on_log=on_log)
    if config.kind == "ph3image":
        return _generate_ph3image(prompt, n, aspect, output_dir, base_seed, config, refs=refs, on_log=on_log)
    raise ValueError(f"unknown image engine kind: {config.kind!r}")


def health_check(config: ImageEngineConfig) -> tuple[bool, str]:
    """Light readiness probe for the configured backend."""
    if config.kind == "flux3":
        import flux3_engine
        return True, ("FLUX 3 ready" if flux3_engine.worker_ready()
                      else "FLUX 3 installs on first use")
    if config.kind == "mock":
        try:
            from PIL import Image  # noqa: F401
            return True, "mock engine ready (PIL available)"
        except ImportError:
            return False, "PIL not installed — pip install Pillow"
    if config.kind == "mflux":
        bin_path = _resolve_mflux_bin(config)
        if not bin_path:
            fam = _resolve_mflux_family(config)
            return False, mflux_missing_message(
                MFLUX_FAMILY_BIN.get(fam, "mflux-generate"), fam)
        return True, f"mflux ready at {bin_path}; model={config.mflux_model}"
    if config.kind == "bfl":
        if not config.bfl_api_key:
            return False, "BFL API key not configured"
        return True, f"BFL configured for {config.bfl_model}"
    if config.kind == "hidream":
        py = _resolve_hidream_python(config)
        model = _resolve_hidream_model(config)
        if not py:
            return False, ("HiDream lab venv not found. Install at "
                           f"{HIDREAM_DEFAULT_PY} or override hidream_python_path")
        if not model:
            return False, (f"HiDream model not found at {config.hidream_model_path or HIDREAM_DEFAULT_MODEL}. "
                           "Run the lab's converter or override hidream_model_path.")
        return True, f"HiDream ready: model={Path(model).name}, steps={config.hidream_steps}"
    if config.kind == "ph3image":
        missing = ph3image_missing(config)
        if missing:
            return False, "P-H3-Image is not installed: " + "; ".join(missing)
        return True, f"P-H3-Image ready ({config.ph3image_mp:g} MP, {5 if config.ph3image_pro_profile else 8} steps)"
    return False, f"unknown engine kind: {config.kind!r}"


# Per-family CLI command. mflux 0.17.x splits FLUX.1, FLUX.2, Z-Image,
# FIBO, Qwen, and Kontext into separate `mflux-generate-*` binaries —
# each one knows its architecture, expected step count, guidance scale,
# etc. We dispatch by family. The legacy `mflux-generate` remains for
# FLUX.1 (krea-dev / dev / schnell / kontext) backward-compat.
MFLUX_FAMILY_BIN = {
    "flux1":          "mflux-generate",
    "flux2":          "mflux-generate-flux2",
    # FLUX.2 Klein Edit — image-conditioned variant of FLUX.2. Like qwen_edit
    # it takes --image-paths (REQUIRED) and composes the prompt onto the
    # reference image(s). Distilled at 4 steps for the klein-4b/9b base
    # models (guidance MUST be 1.0); the klein-base-4b/9b non-distilled
    # variants run at 25+ steps with real guidance (4.0 typical) and
    # produce photorealistic output instead of the distilled illustrative
    # look. See engine_override "flux2_edit" (distilled) vs
    # "flux2_edit_high" (base, photoreal) in agent/tools.py.
    "flux2_edit":     "mflux-generate-flux2-edit",
    "z_image":        "mflux-generate-z-image",
    "z_image_turbo":  "mflux-generate-z-image-turbo",
    "fibo":           "mflux-generate-fibo",
    "qwen":           "mflux-generate-qwen",
    # Qwen-Image-Edit-2509 (multi-reference, 1-3 input images via --image-paths).
    # Apache 2.0. Default model: Qwen/Qwen-Image-Edit-2509.
    # Trained for "person + person", "person + product", "person + scene"
    # combinations — exactly the place + character composition the agent
    # needs for keyframe stills. mflux 0.11.1+.
    "qwen_edit":      "mflux-generate-qwen-edit",
    "kontext":        "mflux-generate-kontext",
    # Ideogram 4 (text-to-image, Qwen3 text encoder + Flux2 VAE). Presets
    # define step count + guidance schedule + noise schedule — the CLI
    # IGNORES --steps / --guidance and reads the caption via --prompt-file
    # (the prompt is a JSON document, so it can't go on the shell as
    # --prompt). fp8 weights at ideogram-ai/ideogram-4-fp8. mflux 0.18+.
    "ideogram":       "mflux-generate-ideogram4",
}

# Sensible per-family defaults so a user who picks a model from the
# dropdown gets the right step count + guidance without having to
# read the model card. `steps` and `guidance` apply to `_generate_mflux`
# when `config.mflux_steps == 0` or `mflux_guidance` is unset (the
# server only overrides when the user explicitly picks a custom value).
MFLUX_FAMILY_DEFAULTS = {
    "flux1":         {"steps": 25, "guidance": 4.5,  "base_model": "dev"},
    "flux2":         {"steps": 4,  "guidance": 1.0,  "base_model": "flux2-klein-4b"},
    # flux2_edit default: 4 steps + guidance 1.0 (the distilled klein-4b
    # path, fastest). Switch to klein-base-4b/9b + steps 25 + guidance 4.0
    # via engine_override="flux2_edit_high" for photorealistic output —
    # see agent/tools.py engine_override branches. The distilled path
    # FORCES guidance == 1.0 (mflux's flux2_edit_generate.py rejects any
    # other value); the base path REQUIRES guidance > 1.0 to do real CFG.
    "flux2_edit":    {"steps": 4,  "guidance": 1.0,  "base_model": "flux2-klein-4b"},
    "z_image":       {"steps": 25, "guidance": 5.0,  "base_model": ""},
    "z_image_turbo": {"steps": 9,  "guidance": 0.0,  "base_model": ""},
    "fibo":          {"steps": 30, "guidance": 5.0,  "base_model": ""},
    "qwen":          {"steps": 30, "guidance": 5.0,  "base_model": ""},
    # qwen_edit default: 8 steps. The Qwen card recommends 30-40 for
    # final-quality, but the agent + Image Studio are iteration tools —
    # ~1 min/image at Q4-8steps, then bump to 30 steps once the user
    # picks a composition they like. Pair with a Lightning 4-step LoRA
    # (mflux_lora_paths) to drop further to ~10-15 s. Guidance 4.0 from
    # the model card.
    "qwen_edit":     {"steps": 8,  "guidance": 4.0,  "base_model": ""},
    "kontext":       {"steps": 30, "guidance": 4.5,  "base_model": ""},
    # Ideogram 4: steps + guidance are INERT — the preset (V4_TURBO_12 /
    # V4_DEFAULT_20 / V4_QUALITY_48) defines the step count and guidance
    # schedule, and the CLI warns-and-ignores --steps / --guidance. The
    # values below are placeholders only (the ideogram argv branch in
    # _generate_mflux never reads them). base_model points the loader at
    # the fp8 weights when --model is an HF id / path.
    # base_model feeds the mflux `--base-model` ARCHITECTURE enum (dev/…/ideogram4),
    # NOT the HF repo path (that's config.mflux_model → `--model`). Must be the enum
    # or argparse exits 2. steps/guidance are inert (Ideogram presets define them).
    "ideogram":      {"steps": 20, "guidance": 0.0,  "base_model": "ideogram4"},
}


def _infer_mflux_family(model: str) -> str:
    """Map a model id / shorthand to its mflux family.

    Examples:
      "Runpod/FLUX.2-klein-4B-mflux-4bit"             → "flux2"
      "filipstrand/Z-Image-Turbo-mflux-4bit"          → "z_image_turbo"
      "Tongyi-MAI/Z-Image"                            → "z_image"
      "filipstrand/FLUX.1-Krea-dev-mflux-4bit"        → "flux1"
      "krea-dev" / "dev" / "schnell"                  → "flux1"
      "flux2-klein-4b" / "flux2-klein-9b"             → "flux2"
      "briaai/FIBO" / "fibo"                          → "fibo"
      "filipstrand/Qwen-Image-mflux-6bit" / "qwen-*"  → "qwen"
      "*kontext*"                                     → "kontext"

    Falls back to "flux1" so legacy configs keep working.
    """
    s = (model or "").lower()
    if "z-image-turbo" in s or "z_image_turbo" in s or "zimage-turbo" in s:
        return "z_image_turbo"
    if "z-image" in s or "z_image" in s or "tongyi-mai/z-image" in s:
        return "z_image"
    if "flux2" in s or "flux.2" in s or "flux-2" in s:
        return "flux2"
    if "kontext" in s:
        return "kontext"
    if "fibo" in s or "briaai/fibo" in s:
        return "fibo"
    # qwen_edit must be matched BEFORE plain qwen — "qwen-image-edit-2509"
    # contains "qwen" + "image" so the plain qwen branch would steal it.
    if "qwen" in s and ("image-edit" in s or "image_edit" in s or "qwen-edit" in s):
        return "qwen_edit"
    if "qwen" in s and "image" in s:
        return "qwen"
    if "ideogram" in s:
        return "ideogram"
    # Default: flux1 (krea-dev / dev / schnell, plus filipstrand/FLUX.1-*)
    return "flux1"


def _resolve_mflux_family(config: ImageEngineConfig) -> str:
    """Resolve config.mflux_family, falling back to inference."""
    fam = (config.mflux_family or "auto").strip().lower()
    if fam == "auto" or fam not in MFLUX_FAMILY_BIN:
        return _infer_mflux_family(config.mflux_model)
    return fam


#: The Mac app only (set by the studio's __main__, never on import, so a test
#: or a gate never pip-installs into a real venv). Called as
#: ``MFLUX_AUTOINSTALL(on_log)`` when an image job finds no mflux CLI; returns
#: None once the engine is in place, else a short reason it is not.
#: Fleet 8 Nov (fw-20261107-1904): the app's setup never installed mflux (only
#: Pinokio's install.js did), so every FLUX.2 / Qwen-Edit / Ideogram image on a
#: Mac-app install that had not copied a Pinokio engine failed, and the message
#: said "quit and reopen so setup can put it back", which did nothing.
MFLUX_AUTOINSTALL = None


def mflux_missing_message(bin_name: str, fam: str, install_problem: str | None = None) -> str:
    """What an image job says when the mflux CLI is not there.

    The image-engine pack (scripts/pinokio/mflux_pack.sh) is optional at
    install, so a failed or skipped pack leaves no mflux CLI. This used to say
    "<bin> not found ... `pip install -U mflux>=0.17` (see
    docs/AGENTIC_FLOWS.md)": a terminal command, an eager upgrade this repo
    forbids (it re-resolves transformers and breaks video), and a deleted doc
    (fleet fw-20261107-0907: 11 failures on 7 Macs). It names the one-click
    way back; on the Mac app, when the studio tried to add the engine itself,
    it says that and why it did not work. The CLI name stays at the end for
    logs and the fleet's grouping."""
    if install_problem:
        head = ("The image engine is not installed and Phosphene could not add it "
                f"({install_problem.rstrip('.')}). ")
        way = _dist.say(
            "In Pinokio, click \"Install/repair image engines (Ideogram 4 + "
            "Qwen-Edit)\" in Phosphene's menu; it keeps every model. ",
            "Check that this Mac is online and has a few GB free, then make the "
            "image again; if it keeps happening, report it with "
            f"{_dist.MAC_REPORT_MENU} ")
    else:
        head = "The image engine is not installed, so images cannot start. "
        way = _dist.say(
            "In Pinokio, click \"Install/repair image engines (Ideogram 4 + "
            "Qwen-Edit)\" in Phosphene's menu; it keeps every model. ",
            "Quit Phosphene and open it again so setup can put it back; if "
            "it keeps happening, report it with "
            f"{_dist.MAC_REPORT_MENU} ")
    return head + way + f"Video renders are not affected. ({bin_name} missing, family {fam})"


def _resolve_mflux_bin(config: ImageEngineConfig) -> str | None:
    """Find the `mflux-generate-*` executable for the configured family.

    Order:
      1. config.mflux_python_path (explicit override — useful when mflux
         is installed in a venv outside the panel's standard location).
         The override is taken as-is; user is responsible for pointing
         at the right per-family binary.
      2. The Phosphene panel's bundled venv (ltx-2-mlx/env/bin/<bin>).
      3. The sibling image-gen venv (image-gen/env/bin/<bin>) — reserved
         for a future split where mflux's transformers/mlx pins differ
         from LTX's. Doesn't exist today; safe to probe.
      4. shutil.which(<bin>) — system PATH fallback.

    Returns the absolute path to the binary, or None if not installed.
    """
    import os
    import shutil

    fam = _resolve_mflux_family(config)
    bin_name = MFLUX_FAMILY_BIN.get(fam, "mflux-generate")

    if config.mflux_python_path:
        # Defensive re-validation at exec time. Save-time
        # (_validate_mflux_python_path in mlx_ltx_panel.py) already
        # gates this field, but the file may have been deleted between
        # save and use, or an older config may pre-date the gate. The
        # exec call site below treats this as the mflux CLI binary
        # (not a Python interpreter), so we only accept mflux-generate*
        # names. Anything else (including legacy python3 entries from
        # before the validator was tightened): fall back silently to
        # the panel's bundled venv.
        cand = Path(config.mflux_python_path)
        if (cand.is_absolute()
                and cand.is_file()
                and os.access(cand, os.X_OK)
                and cand.name.startswith("mflux-generate")):
            return str(cand)

    # image_engine.py lives AT the repo root (panel.git/image_engine.py).
    # Regression in d7a7caa: a `.parent.parent` accidentally walked one
    # directory too high to Pinokio's api/ dir, where neither the
    # ltx-2-mlx env nor the image-gen env exist — every probe fell
    # through to shutil.which() which (with no global mflux on PATH)
    # returned None, so health_check() reported "mflux not installed"
    # and image generations never wrote anything to disk. Fixed
    # 2026-05-18: walk just one level so the bundled venv resolves.
    repo_root = Path(__file__).resolve().parent
    candidates = [
        repo_root / "ltx-2-mlx" / "env" / "bin" / bin_name,
        repo_root / "image-gen" / "env" / "bin" / bin_name,
    ]
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)

    return shutil.which(bin_name)


# ---- The engine venv's Pillow (4.19.6 fleet fix) ----------------------------
# Pillow 13.0.0 shipped on 2026-10-16. Its PIL/_typing.py now imports
# `from numbers import _IntegralLike` at module level and relies on Python
# 3.15's lazy imports (`__lazy_modules__`) to never execute it. The engine venv
# is Python 3.11, where the import runs and fails. mflux 0.18.0 imports
# `from PIL._typing import StrOrBytesPath` (mflux/utils/image_util.py), so every
# FLUX.2 / Qwen-Edit render on a venv that resolved Pillow 13 died with
#     ImportError: cannot import name '_IntegralLike' from 'numbers'
# Fleet 2026-10-17..19: 9 fresh 4.19.5 installs, every one installed after
# Pillow 13 was released; video was unaffected (nothing else imports _typing).
# mflux itself only asks for pillow>=12.1.1, so no resolver could know.
# scripts/pinokio/engine_constraints.txt now caps Pillow below 13 for every
# install into the engine venv and post_update.sh moves an existing 13 back.
# This check is the in-panel half: an image job on a venv outside that range
# stops before the subprocess, naming the cause and the way out.
MFLUX_JUDGED_PACKAGES = ("pillow",)
_REPO_ROOT = Path(__file__).resolve().parent     # tests point this at a fake tree


def _engine_constraint_specs(names=MFLUX_JUDGED_PACKAGES) -> dict:
    """{name: specifier string} for `names` from engine_constraints.txt."""
    out: dict = {}
    path = _REPO_ROOT / "scripts" / "pinokio" / "engine_constraints.txt"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$", line)
        if not m:
            continue
        name = re.sub(r"[-_.]+", "-", m.group(1)).lower()
        if name in names and m.group(2):
            out[name] = m.group(2).strip()
    return out


def mflux_env_fault(bin_path) -> str:
    """"" when the venv that holds `bin_path` can import mflux's image code,
    else the reason and the way out. Judges only the panel's own venvs (the
    bundled ltx-2-mlx/env and image-gen/env); a custom mflux binary somewhere
    else is not ours to judge. Fails open: anything unreadable is ""."""
    try:
        repo_root = _REPO_ROOT
        venv = Path(bin_path).parent.parent
        if venv.resolve().parent.name not in ("ltx-2-mlx", "image-gen"):
            return ""
        if venv.resolve().parent.parent != repo_root.resolve():
            return ""
        if not (venv / "pyvenv.cfg").is_file():
            return ""
        specs = _engine_constraint_specs()
        if not specs:
            return ""
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
        have: dict = {}
        for site in venv.glob("lib/python*/site-packages"):
            for d in site.iterdir():
                if not d.name.endswith(".dist-info"):
                    continue
                name, _, ver = d.name[:-len(".dist-info")].rpartition("-")
                key = re.sub(r"[-_.]+", "-", name).lower()
                if key in specs and ver:
                    have[key] = ver
        for key, spec in specs.items():
            ver = have.get(key)
            if ver and not SpecifierSet(spec).contains(Version(ver), prereleases=True):
                return (f"The image engine cannot start: its venv has {key.capitalize()} "
                        f"{ver}, and mflux works only with {key.capitalize()} {spec} "
                        f"(it stops with \"cannot import name '_IntegralLike'\"). "
                        + _dist.say(
                            "Press Update in Pinokio, then restart - Update puts it back "
                            "in about a minute and keeps every model. Video renders are "
                            "not affected.",
                            "Report it with Help \u2192 Report a Bug\u2026 Video renders "
                            "are not affected."))
    except Exception:                                          # noqa: BLE001
        return ""
    return ""


# ---- LoRAs: what reaches mflux, and what must stop before it ----------------
def mflux_lora_stack(config: ImageEngineConfig, fam: str | None = None,
                     on_log=None) -> tuple[list[str], list[float]]:
    """The LoRA paths + scales the mflux CLI will really be handed.

    One function for the command builder AND the pre-launch check, so the
    two can never disagree about which adapters ride along.

    2026-05-31 review fix (C2): the default config ships the Qwen-Edit
    Lightning LoRA (qwen_edit is the UI default). If the engine is switched
    to a FLUX family (e.g. via engine_override="auto") while that LoRA is
    still in the config, mflux would load a Qwen-architecture LoRA onto a
    FLUX transformer — a ~50s model load followed by a no-op or crash. A
    Qwen LoRA cannot apply to a non-Qwen model, so drop cross-family LoRAs
    (and their paired scales, kept index-aligned) up front and log it."""
    fam = fam or _resolve_mflux_family(config)
    paths = list(config.mflux_lora_paths or [])
    scales = list(config.mflux_lora_scales or [])

    def _log(msg: str) -> None:
        if on_log is not None:
            try: on_log(msg)
            except Exception:  # noqa: BLE001
                pass

    if fam == "ideogram" and paths:
        # Ideogram 4 CANNOT consume external LoRAs: its transformer key
        # names share zero overlap with Qwen/FLUX LoRA keys, so mflux's
        # loader matches 0/2304 keys yet prints "applied successfully" — a
        # silent no-LoRA render that makes users blame Ideogram for ignoring
        # their character. Drop ALL user LoRAs (not just Qwen ones) and log
        # honestly. This one chokepoint covers every entry path: the picker,
        # a selection carried over from an engine switch, and /image/agent.
        for lp in paths:
            _log(f"[lora] Ideogram cannot use external LoRAs — ignoring: {lp}")
        return [], []
    if fam != "qwen_edit" and paths:
        kept_p: list = []
        kept_s: list = []
        for i, lp in enumerate(paths):
            if "qwen" in str(lp).lower():
                _log(f"[lora] skipping Qwen LoRA on {fam} family (incompatible): {lp}")
                continue
            kept_p.append(lp)
            if i < len(scales):
                kept_s.append(scales[i])
        paths, scales = kept_p, kept_s
    return paths, scales


def _mflux_lora_resolvable(path: str) -> bool:
    """Would mflux's own LoRA resolver find `path`? Mirrors the rule table of
    mflux 0.18 (models/common/resolution/lora_resolution.py) minus the
    in-process registry, which no Phosphene caller fills: a file on disk; a
    `repo:file` / `hf:` collection; an `org/repo` id. Anything else — above
    all an absolute path whose file has gone — is
    `error: LoRA file not found`, raised by the CLI's argparse (exit 2)."""
    s = str(path or "").strip()
    if not s:
        return False
    try:
        if Path(s).expanduser().exists():
            return True
    except (OSError, ValueError):
        return False
    if s.startswith(("/", "~", "./", "../")):
        return False                    # a filesystem path, and it is not there
    if s.startswith("hf:"):
        rest = s[3:]
        return (":" in rest and "/" in rest) or rest.count("/") == 2
    if ":" in s and "/" in s:
        return True                     # repo:filename collection
    return s.count("/") == 1            # org/repo


#: How wide the FLUX.2 transformers are (attention heads x 128), from mflux
#: 0.18 model_config.py `transformer_overrides`: [klein] 4B 24 heads, [klein]
#: 9B 32 heads; FLUX.2 [dev] 48 heads.
FLUX2_WIDTH_NAMES = {3072: "FLUX.2 [klein] 4B", 4096: "FLUX.2 [klein] 9B",
                     6144: "FLUX.2 [dev]"}


def flux2_transformer_width(config: ImageEngineConfig) -> int | None:
    """The width of the FLUX.2 transformer this config runs, or None when it
    is not a FLUX.2 config or the variant cannot be told from its names."""
    fam = _resolve_mflux_family(config)
    if fam not in ("flux2", "flux2_edit"):
        return None
    base = (config.mflux_base_model
            or MFLUX_FAMILY_DEFAULTS.get(fam, {}).get("base_model") or "")
    for name in (str(config.mflux_model or ""), str(base)):
        s = name.lower()
        if "9b" in s:
            return 4096
        if "4b" in s or "klein" in s:
            return 3072
        if "flux.2-dev" in s or "flux2-dev" in s:
            return 6144
    return None


def lora_input_width(path) -> int | None:
    """The width a LoRA was trained on: the most common input size of its
    down/A matrices (attention q/k/v/out and the MLP input all read the
    model's hidden width, so it dominates any full or attention-only
    adapter). Reads the safetensors header only. None when the file has no
    plain lora_A/lora_down pairs (LoKr, LoHa) or no width clearly leads."""
    import lora_compat
    header = lora_compat.read_tensor_header(path)
    counts: dict[int, int] = {}
    for key, meta in header.items():
        if not key.endswith((".lora_A.weight", ".lora_down.weight")):
            continue
        shape = meta.get("shape") if isinstance(meta, dict) else None
        if isinstance(shape, list) and len(shape) == 2:
            try:
                w = int(shape[1])
            except (TypeError, ValueError):
                continue
            counts[w] = counts.get(w, 0) + 1
    if not counts:
        return None
    width, hits = max(counts.items(), key=lambda kv: kv[1])
    return width if hits * 5 >= sum(counts.values()) * 2 else None   # >= 40 %


_SHAPE_ERROR_MARKS = ("[broadcast_shapes]", "[matmul]", "[addmm]", "Shapes (")


def _lora_shape_note(config: ImageEngineConfig, lora_paths, cause: str) -> "str | None":
    """The first line of an engine failure that a picked LoRA caused, or None.

    mflux attaches LoRAs by key name and never compares shapes, so a LoRA made
    for another model dies mid-denoise with an MLX shape error. The pre-launch
    check (mflux_lora_problem) reads the width of plain lora_A/lora_down
    adapters; LoKr/LoHa and full-delta files have none and pass it. Fleet
    4.19.16: "ValueError: [broadcast_shapes] Shapes (1,1024,18432) ..." five
    times on one Mac, every time with a style LoRA picked on Auto (18432 is
    [klein] 4B's feed-forward width, 3072 x 3 x 2). The card said only
    "mflux-generate-flux2 failed (exit 1)"; it now names the LoRA."""
    # The engine presets' own step-distillation adapters (Lightning) are made
    # for their model and are never the user's pick: they are not blamed.
    picked = [lp for lp in (lora_paths or [])
              if not any(k in str(lp).lower() for k in ("lightning", "lightx2v"))]
    if not picked or not cause or not any(m in cause for m in _SHAPE_ERROR_MARKS):
        return None
    names = [Path(str(lp)).name or str(lp) for lp in picked]
    want = flux2_transformer_width(config)
    model = FLUX2_WIDTH_NAMES.get(want) if want else None
    model = model or str(getattr(config, "mflux_model", "") or "this model")
    which = (f"the LoRA {names[0]}" if len(names) == 1 else
             "one of the LoRAs you picked (" + ", ".join(names) + ")")
    return (f"The render stopped on a layer-size mismatch, most likely because {which} "
            f"does not fit {model} (the engine said: {cause[:160]}). Un-pick it"
            f"{'' if len(names) == 1 else ', one at a time to find which'}, or pick a "
            f"LoRA made for {model}.")


def mflux_lora_problem(config: ImageEngineConfig) -> tuple[str, str] | None:
    """Why the LoRAs on this config would make mflux fail, or None.

    Returns (kind, message); kind is "missing", "damaged" or "mismatch".
    Checked BEFORE the engine starts, because each of these failed after it
    had (fleet 4.19.16):

    - "missing": an absolute LoRA path whose file has gone (a chip restored
      from a draft or Load Params after the LoRA was deleted or renamed).
      mflux-generate-qwen-edit / run_mflux_with_teacache.py exited 2 with
      "error: LoRA file not found" — 13 renders on one Mac in two days.
    - "damaged": a download cut short (the header says more bytes follow).
    - "mismatch": a FLUX.2 LoRA trained on a different FLUX.2 size. mflux
      attaches it by key name without comparing shapes, then the first
      denoise step dies with "ValueError: [matmul] Last dimension of first
      input ..." (a [klein] 9B or [dev] adapter, 4096/6144 wide, on the
      3072-wide [klein] 4B that Auto and Klein-Edit run).
    """
    if config.kind != "mflux":
        return None
    paths, _ = mflux_lora_stack(config)
    want = flux2_transformer_width(config)
    for lp in paths:
        s = str(lp or "").strip()
        name = Path(s).name or s
        if not _mflux_lora_resolvable(s):
            return ("missing",
                    f"The LoRA {name} is no longer on disk ({s}). It was moved, "
                    "renamed or deleted after it was picked - un-pick it in the "
                    "LoRA list (or pick it again), then generate again.")
        p = Path(s).expanduser()
        if not (p.is_file() and p.suffix.lower() == ".safetensors"):
            continue
        try:
            have = lora_input_width(p)
        except Exception as exc:                               # noqa: BLE001
            if type(exc).__name__ == "LoraCompatibilityError":
                return ("damaged", str(exc))
            continue
        if want and have and have != want:
            made_for = FLUX2_WIDTH_NAMES.get(have)
            runs = FLUX2_WIDTH_NAMES[want]
            made = (f"was made for {made_for}" if made_for else
                    f"was made for a different model (its layers are {have} "
                    f"wide, {runs}'s are {want})")
            return ("mismatch",
                    f"The LoRA {name} {made} and cannot load on {runs}, the "
                    f"model this image engine runs. Un-pick it in the LoRA "
                    f"list, or pick a LoRA made for {runs}.")
    return None


# ---- Backends ---------------------------------------------------------------
def _generate_mock(prompt: str, n: int, width: int, height: int,
                   output_dir: Path, base_seed: int | None,
                   refs: list[str] | None = None) -> list[dict]:
    """Draw `n` distinguishable colored PNGs locally. Each carries a label
    with the candidate index + first ~60 chars of the prompt so the user
    can verify the UX flow without confusing identical images.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise RuntimeError(
            "PIL not available — install Pillow to use the mock engine, "
            "or configure a real backend in Settings → Image generation."
        ) from e

    palette = [
        (220, 100, 80),  (80, 150, 220),  (220, 200, 80),  (130, 220, 130),
        (180, 100, 200), (240, 160, 100), (100, 200, 200), (180, 180, 180),
    ]
    font = None
    for path in [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        try:
            font = ImageFont.truetype(path, max(18, height // 28))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    results = []
    for i in range(n):
        color = palette[i % len(palette)]
        img = Image.new("RGB", (width, height), color)
        draw = ImageDraw.Draw(img)

        # Subtle gradient stripe on top so candidates aren't perfectly flat
        for y in range(0, height // 4):
            tint = 1 - (y / (height // 4)) * 0.4
            stripe = tuple(min(255, int(c * tint + 20)) for c in color)
            draw.line([(0, y), (width, y)], fill=stripe)

        title = f"Mock candidate {i + 1}"
        body = (prompt or "")[:120]
        draw.text((24, 24), title, fill=(255, 255, 255), font=font)
        # Word-wrap the body
        words = body.split()
        line = ""
        y = 24 + (font.size if hasattr(font, "size") else 18) + 12
        for w in words:
            test = (line + " " + w).strip()
            if len(test) > 36:
                draw.text((24, y), line, fill=(255, 255, 255), font=font)
                y += (font.size if hasattr(font, "size") else 18) + 6
                line = w
            else:
                line = test
        if line:
            draw.text((24, y), line, fill=(255, 255, 255), font=font)

        seed = (base_seed if base_seed is not None else 1000) + i
        out_path = output_dir / f"cand_{i:02d}_mock.png"
        img.save(out_path, "PNG")
        results.append({
            "png_path": str(out_path), "seed": seed, "engine": "mock",
            "width": width, "height": height,
            # Mock doesn't compose refs into the output. Surface this
            # explicitly so callers (panel /image/generate, agent
            # generate_shot_images) can warn the user that picking a
            # ref didn't influence the result.
            "refs_ignored": bool(refs),
        })
    return results


_EXC_LINE_RX = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Interrupt|Exit)\s*:\s*\S")
_NOISE_PREFIXES = ("Traceback", 'File "', "During handling", "The above exception",
                   "^^^", "~~~", "[", "step")


def _subprocess_cause_line(lines) -> str:
    """The line in a dead engine subprocess's tail that names the cause: the
    last `SomethingError: ...` line, else the last line that is not traceback
    scaffolding or a progress bar, else "". Same rule as the panel's
    failure_line() (mlx_ltx_panel.py), kept here because this module is
    imported by the panel, not the other way round."""
    clean = [str(t).strip() for t in (lines or ()) if str(t or "").strip()]
    for t in reversed(clean):
        if _EXC_LINE_RX.match(t):
            return t
    for t in reversed(clean):
        if (not t.startswith(_NOISE_PREFIXES) and not t[0].isdigit()
                and "%|" not in t and "it/s]" not in t and "s/it]" not in t):
            return t
    return ""


def _generate_mflux(prompt: str, n: int, width: int, height: int,
                    output_dir: Path, base_seed: int | None,
                    config: ImageEngineConfig,
                    refs: list[str] | None = None,
                    on_log: "callable | None" = None, diagnostic=None) -> list[dict]:
    """Subprocess the right `mflux-generate-*` binary ONCE for all `n` seeds.

    Pre-2026-05-08 this spawned one subprocess per candidate, paying the
    ~30-60 s model-load cost N times. mflux's per-family CLIs already
    loop internally over `args.seed`, loading the model exactly once
    and looping over `for seed in args.seed: image = qwen.generate_image(...)`.
    By passing every seed in a single invocation and using mflux's
    `{seed}` template in `--output`, we drop wall-time on n=4 batches
    from "4 × (load + gen)" to "1 × load + 4 × gen", which is a
    3-4× speedup on Lightning configs and a 1.3-1.6× speedup on
    raw 8-step Q4. Bigger when generation itself is fast (load dominates).

    Family is inferred from `config.mflux_model` (or taken from
    `config.mflux_family` when set). FLUX.1 / FLUX.2 / Z-Image /
    Z-Image-Turbo / FIBO / Qwen / Qwen-Edit / Kontext each have their
    dedicated CLI from mflux 0.17.x.

    `config.mflux_lora_paths` + `config.mflux_lora_scales` plumb through
    to `--lora-paths` and `--lora-scales`. The intended use is a
    Lightning LoRA on qwen_edit:
      mflux_lora_paths = ["lightx2v/Qwen-Image-Edit-2511-Lightning"]
      mflux_lora_scales = [1.0]
      mflux_steps = 4
    which lands at ~10-15 s/image vs ~5 min for raw 30-step gen.

    Recommended defaults (May 2026):
      - **Qwen-Image-Edit-2509** — Apache 2.0, multi-ref. Iteration:
        Q4 + 8 steps, ~1 min/image. Final: Q8 + 30 steps, ~5 min.
      - **FLUX.2 [klein] 4B 4-bit** — Apache 2.0, 4 steps, ~4.3 GB.
      - **Z-Image-Turbo 4-bit** — Apache 2.0, 9 steps, ~5.9 GB.

    First run downloads weights (4-34 GB depending on family) to
    ~/.cache/huggingface. Subsequent calls use the cached copy.
    """
    import os
    import random
    import subprocess
    import sys  # sys.executable used by the TeaCache wrap fallback below

    bin_path = _resolve_mflux_bin(config)
    fam = _resolve_mflux_family(config)
    bin_name = MFLUX_FAMILY_BIN.get(fam, "mflux-generate")
    fam_defaults = MFLUX_FAMILY_DEFAULTS.get(fam, MFLUX_FAMILY_DEFAULTS["flux1"])

    install_problem = None
    if not bin_path and MFLUX_AUTOINSTALL is not None:
        # The Mac app's studio adds the missing engine itself, once, inside
        # this job (fw-20261107-1904), then carries on with the image.
        try:
            install_problem = MFLUX_AUTOINSTALL(on_log)
        except Exception as exc:                                  # noqa: BLE001
            install_problem = f"the install could not run: {exc}"
        bin_path = _resolve_mflux_bin(config)

    if not bin_path:
        raise RuntimeError(mflux_missing_message(bin_name, fam, install_problem))

    # 4.19.6: a Pillow the engine cannot import stops HERE, in a second, with
    # the way out - not 30 s later as an ImportError from inside mflux.
    _env_fault = mflux_env_fault(bin_path)
    if _env_fault:
        raise RuntimeError(_env_fault)

    # qwen_edit and flux2_edit BOTH refuse to start without --image-paths
    # (mflux argparse marks it required on both edit-flavored CLIs).
    # Catch here with a clear validation error instead of letting the
    # user wait minutes for a confusing argparse failure.
    if fam in ("qwen_edit", "flux2_edit") and not refs:
        raise ValueError(
            f"{fam} needs at least 1 reference image — drop "
            "a photo into one of the 3 reference slots above, or switch "
            "the Engine dropdown to FLUX.2 [klein] / Z-Image-Turbo for "
            "text-only generation."
        )

    # Effective steps + guidance: prefer user-set values if non-zero,
    # otherwise fall back to the family default.
    eff_steps = config.mflux_steps if config.mflux_steps > 0 else fam_defaults["steps"]
    eff_guidance = (config.mflux_guidance
                    if config.mflux_guidance is not None
                    else fam_defaults["guidance"])
    # A LIGHTNING ADAPTER RUNS AT GUIDANCE 1.0, whatever the family default.
    # Lightning is a step distillation trained without CFG; the qwen_edit
    # family default (4.0) is the 40-step Quality recipe's true-CFG. Reference
    # Edit Fast, the dataclass default and the auto-promotion all carry the
    # Lightning LoRA with guidance unset, so they sent `--guidance 4.0`
    # (Codex UI-03, 2026-09-24). An explicit mflux_guidance still wins.
    if (config.mflux_guidance is None
            and any("lightning" in str(lp).lower() for lp in (config.mflux_lora_paths or []))):
        eff_guidance = 1.0

    # Build the seed list. mflux's CLI loops `for seed in args.seed`
    # AFTER loading the model exactly once, so passing all seeds at
    # once amortizes the cold-start cost across N images.
    if base_seed is not None:
        seeds = [base_seed + i for i in range(n)]
    else:
        # Equivalent to mflux's --auto-seeds N but explicit — gives us
        # the seed values up-front so we can map them to output paths.
        seeds = [random.randint(0, 2**31 - 1) for _ in range(n)]

    # Output path uses mflux's `{seed}` template — `--output cand_{seed}_mflux.png`
    # writes one file per seed value (see qwen_image_edit_generate.py:61
    # `Path(args.output.format(seed=seed))`). Including the seed in the
    # filename also guarantees no collisions if the user resubmits the
    # same shot label without `append: true`.
    output_template = str(output_dir / "cand_{seed}_mflux.png")

    # Ideogram 4 takes a DIFFERENT argv shape than every other mflux CLI:
    #   - the prompt is a JSON document (braces break shell --prompt
    #     quoting), so mflux reads it via --prompt-file <path>. We write
    #     the prompt string VERBATIM to a temp .json and pass that path.
    #   - --steps / --guidance are warned-and-ignored (the preset defines
    #     them) so we omit them; the preset goes on --preset.
    #   - --image-paths is unsupported (text-to-image).
    #   - -q defaults to 6 (the config builder passes mflux_quantize=6 for
    #     normal Ideogram, 4 for fast mode). Quantizing the fp8 weights on
    #     load gives smaller/faster GPU kernels — the only thing that helps
    #     slower Apple GPUs (M1/M2) clear the macOS Metal command-buffer
    #     watchdog (ml-explore/mlx#3267, wontfix). Raw fp8 (no -q) renders
    #     but is far slower and trips the watchdog on M1; q6 is the M1-safe
    #     default cocktailpeanut validated, q4 halves RAM again for ≤16 GB.
    # The temp file is deleted in the `finally` after the subprocess ends
    # (it must outlive the child, which reads it during model setup).
    ideogram_prompt_file: str | None = None
    if fam == "ideogram":
        import tempfile
        # Reference bridge: Ideogram has no image input, so when the user
        # enabled it AND dropped reference image(s), vision-caption the
        # first ref(s) with local Gemma 3 in a SEPARATE subprocess and
        # splice the caption into the prompt. The subprocess (not an
        # in-process load) matters here: Gemma's ~6 GB RSS is reclaimed by
        # the OS the moment it exits — BEFORE we launch the ~24 GB Ideogram
        # render below — so the two never coexist. The whole block is
        # best-effort: any failure/timeout falls back to plain text-to-
        # image with a warning and NEVER breaks the render.
        eff_prompt = prompt
        if config.ideogram_reference_bridge and refs and prompt.lstrip().startswith("{"):
            # Layout-mode prompts are a serialized Ideogram JSON caption (the
            # on-canvas editor). Appending a plain-text reference description
            # would corrupt the JSON and mflux would silently drop the whole
            # structured layout. Skip the bridge — the UI also hides the toggle
            # in Layout mode, so this is a belt-and-suspenders guard.
            if on_log is not None:
                try: on_log("reference bridge skipped — Layout caption uses the canvas, not a reference")
                except Exception:  # noqa: BLE001
                    pass
        elif config.ideogram_reference_bridge and refs:
            try:
                # Same interpreter that runs mflux (the venv python sits
                # next to bin_path), mirroring the TeaCache wrap fallback.
                bin_dir = Path(bin_path).parent
                cap_py = bin_dir / "python3"
                if not cap_py.is_file():
                    cap_py = bin_dir / "python"
                if not cap_py.is_file():
                    cap_py = Path(sys.executable)
                cap_helper = (Path(__file__).resolve().parent
                              / "caption_reference_vision.py")
                cap_cmd = [str(cap_py), str(cap_helper)]
                for r in refs[:3]:                       # first 3 refs only
                    cap_cmd += ["--image", str(Path(r).resolve())]
                if on_log is not None:
                    try: on_log("captioning reference with Gemma 3 vision…")
                    except Exception:  # noqa: BLE001
                        pass
                cap_res = subprocess.run(
                    cap_cmd, capture_output=True, text=True, errors="replace",
                    env=_clean_subprocess_env(), timeout=180,
                )
                if cap_res.returncode != 0:
                    raise RuntimeError(
                        (cap_res.stderr or "captioner exited non-zero").strip())
                # Prefer the CAPTION_JSON marker line; fall back to the
                # last non-empty stdout line.
                caption = ""
                for ln in cap_res.stdout.splitlines():
                    if ln.startswith("CAPTION_JSON:"):
                        try:
                            caption = json.loads(
                                ln.split("CAPTION_JSON:", 1)[1].strip()
                            ).get("caption", "")
                        except Exception:  # noqa: BLE001
                            caption = ""
                if not caption:
                    tail = [ln for ln in cap_res.stdout.splitlines() if ln.strip()]
                    caption = tail[-1].strip() if tail else ""
                if not caption:
                    raise RuntimeError("captioner returned no caption")
                eff_prompt = (
                    f"{prompt}\n\nVisual reference to echo (subject, "
                    f"composition, palette, lighting, style): {caption}")
                if on_log is not None:
                    try: on_log(f"reference caption: {caption[:120]}…")
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as e:        # noqa: BLE001 — never break the render
                if on_log is not None:
                    try: on_log(f"reference caption failed ({e}); rendering text-only")
                    except Exception:  # noqa: BLE001
                        pass
                eff_prompt = prompt
        _fd, ideogram_prompt_file = tempfile.mkstemp(
            suffix=".json", prefix="ideogram_prompt_",
        )
        with os.fdopen(_fd, "w", encoding="utf-8") as _pf:
            _pf.write(eff_prompt)
        cmd = [
            bin_path,
            "--model", config.mflux_model,
            "--prompt-file", ideogram_prompt_file,
            "--output", output_template,
            "--width", str(width),
            "--height", str(height),
            "--preset", (config.mflux_preset or "V4_DEFAULT_20"),
        ]
        if config.mflux_quantize:                       # None/0 → raw fp8; 4 → fast mode
            cmd += ["-q", str(config.mflux_quantize)]
        cmd += ["--seed", *[str(s) for s in seeds]]
    else:
        cmd = [
            bin_path,
            "--model", config.mflux_model,
            "--prompt", prompt,
            "--output", output_template,
            "--steps", str(eff_steps),
            "--width", str(width),
            "--height", str(height),
            "-q", str(config.mflux_quantize),
            "--guidance", str(eff_guidance),
            "--seed", *[str(s) for s in seeds],
        ]
    # Reference image input — three different argument shapes across
    # mflux's edit-flavored CLIs:
    #   qwen_edit  / flux2_edit  : --image-paths (plural, 1+ images)
    #   kontext                  : --image-path  (singular, 1 image only)
    # Other families silently drop refs and tag refs_ignored=True.
    # mflux v0.17+. Adding more edit families? Update this dispatch.
    refs_used: list[str] = []
    if refs and fam in ("qwen_edit", "flux2_edit"):
        refs_used = [str(Path(r).resolve()) for r in refs]
        cmd.extend(["--image-paths", *refs_used])
    elif refs and fam == "kontext":
        # kontext only consumes a single image. Use the first ref;
        # the agent's tools.py refs validation already caps at 3 so
        # we silently take refs[0] without warning here.
        refs_used = [str(Path(refs[0]).resolve())]
        cmd.extend(["--image-path", refs_used[0]])
    # Optional Lightning / acceleration LoRAs.
    # Translate any `repo_id:filename.safetensors` entries to real local
    # paths via hf_hub_download — the mflux CLI's --lora-paths accepts
    # ABSOLUTE files only; its repo-shorthand was removed and now errors
    # with "Could not find LoRA file ... in downloaded files". Paths that
    # look like real files or that don't contain a colon are passed
    # through untouched.
    # Which adapters ride along (cross-family drops) is mflux_lora_stack's
    # call, shared with the pre-launch check below so the two cannot drift.
    _lora_paths_in, _lora_scales_in = mflux_lora_stack(config, fam, on_log)
    # 4.19.17 (fleet 4.19.16): a LoRA that cannot load stops HERE, before the
    # engine spends a model load on it - see mflux_lora_problem. The panel's
    # queue worker asks the same question first and answers as a refusal; this
    # is the backstop for every other caller (/image/generate, agents).
    _lora_problem = mflux_lora_problem(config)
    if _lora_problem:
        raise ValueError(_lora_problem[1])
    if _lora_paths_in:
        resolved_paths: list[str] = []
        for lp in _lora_paths_in:
            lp_str = str(lp)
            # Path with a colon → repo:filename collection syntax. Skip
            # if the file already exists locally (e.g. a Civitai LoRA
            # under panel_uploads or an absolute /Users/... path).
            if ":" in lp_str and not Path(lp_str).exists():
                try:
                    repo_id, filename = lp_str.split(":", 1)
                except ValueError:
                    resolved_paths.append(lp_str)
                    continue
                try:
                    from huggingface_hub import hf_hub_download
                    cached = hf_hub_download(repo_id=repo_id, filename=filename)
                    if on_log is not None:
                        try: on_log(f"[lora] resolved {lp_str} -> {cached}")
                        except Exception:  # noqa: BLE001
                            pass
                    resolved_paths.append(cached)
                except Exception as e:        # noqa: BLE001
                    if on_log is not None:
                        try: on_log(f"[lora] WARN: could not resolve {lp_str}: {e}")
                        except Exception:  # noqa: BLE001
                            pass
                    resolved_paths.append(lp_str)
            else:
                resolved_paths.append(lp_str)
        cmd.append("--lora-paths")
        cmd.extend(resolved_paths)
        if _lora_scales_in:
            # 2026-05-31 review fix (C3): catch a scales/paths length mismatch
            # HERE rather than letting mflux discover it after a ~50s model
            # load and crash with an opaque error.
            if len(_lora_scales_in) != len(resolved_paths):
                raise ValueError(
                    f"LoRA scales/paths length mismatch: {len(_lora_scales_in)} "
                    f"scale(s) vs {len(resolved_paths)} path(s) — check "
                    f"mflux_lora_scales / mflux_lora_paths in the engine config")
            cmd.append("--lora-scales")
            cmd.extend(str(s) for s in _lora_scales_in)
    # When `--model` is a HuggingFace id or local path (contains a
    # slash or starts with `~`), mflux needs `--base-model` to know
    # which architecture to instantiate. Fall through to the per-family
    # default if the user hasn't set one.
    looks_like_path = ("/" in config.mflux_model
                       or config.mflux_model.startswith("~"))
    if looks_like_path:
        base = (config.mflux_base_model
                or fam_defaults.get("base_model") or "")
        if base:
            cmd.extend(["--base-model", base])

    # Inherit env so HF_HOME / HF_TOKEN are honored for the one-time
    # weight download. Family-aware total timeout — first run can pull
    # 22-34 GB for qwen_edit which makes the cold-start much longer
    # than steady-state. Per-family steady-state per-image is roughly:
    #   flux2 / z_image_turbo: ~15 s @ 4-9 steps Q4
    #   qwen_edit:             ~30 s @ 8 steps Q4 / ~5 min @ 30 steps Q8
    # Plus the one-time load, plus first-run download.
    env = _clean_subprocess_env()
    # Ideogram (raw fp8 DiT) overruns the macOS Metal command-buffer
    # watchdog on slower Apple GPUs — the subprocess aborts with
    # kIOGPUCommandBufferCallbackErrorImpactingInteractivity / SIGABRT
    # (exit -6). Disabling MLX's kernel compile and forcing fast Metal
    # sync make MLX submit shorter command buffers that stay under the
    # watchdog. Found by cocktailpeanut getting Ideogram to run on an
    # M1 Max 64 GB (ml-explore/mlx#3267, upstream wontfix). Ideogram-only
    # so the other mflux families keep mx.compile.
    if fam == "ideogram":
        env.setdefault("MLX_DISABLE_COMPILE", "1")
        env.setdefault("MLX_METAL_FAST_SYNCH", "1")
    # FBCache toggle for the Qwen-Edit transformer (skips middle blocks
    # when the layer-0 residual is stable step-to-step). The patch is
    # injected by patch_mflux_fbcache.py at install/update time and is
    # a no-op unless MFLUX_FB_CACHE=1 is set in the subprocess env.
    # Enabled for the SHORT Qwen tiers only. At 4-step Lightning the
    # per-step residual deltas are usually large enough that no cache
    # hits land (no gain, no harm); at 8 steps we measured ~28% speedup
    # with identical visual output. At 40 steps it was NEVER measured,
    # and there the inter-step deltas shrink enough that the skip path
    # (a direct replay of a stale pre-last-layer state, no consecutive-
    # skip cap) can serve dozens of middle steps from one cache — the
    # second contributor to the 2026-08-30 Quality-tier noise. Long
    # schedules run honest until a long-schedule measurement exists.
    if fam == "qwen_edit" and int(config.mflux_steps or 0) <= 12:
        env["MFLUX_FB_CACHE"] = "1"
        env.setdefault("MFLUX_FB_THRESHOLD", "0.15")
        env.setdefault("MFLUX_FB_KEEP_LAST", "8")
    # TeaCache wrap for FLUX.2 (mlx-teacache 0.4.1, github.com/IonDen/
    # mlx-teacache, MIT). Skip-decodes ~3/25 timesteps on the base
    # 25-step Klein at SSIM>0.99 and ~1.25x on the distilled 4-step path
    # (mostly mx.compile avoid). Default on — set MFLUX_TC_FLUX=0 to
    # bypass. We export the env var unconditionally so the wrapper
    # subprocess sees the threshold; the wrapper itself decides whether
    # to wrap based on MFLUX_TC_FLUX. The bin_path swap below is what
    # actually launches the wrapper instead of bare mflux-generate-flux2.
    if fam == "flux2" and os.environ.get("MFLUX_TC_FLUX", "1").strip().lower() not in ("0", "false", "no"):
        env["MFLUX_TC_FLUX"] = "1"
        env.setdefault("MFLUX_TC_FLUX_THRESH", "0.20")
    per_image_budget = 60 if fam in ("flux2", "z_image_turbo") else 360
    cold_start_budget = 240 if fam in ("flux2", "z_image_turbo") else 1800
    timeout_s = cold_start_budget + per_image_budget * n

    # TeaCache bin-path swap: when wrapping is on, the subprocess command
    # becomes `<venv-python> run_mflux_with_teacache.py <args...>` instead
    # of the bare CLI. The wrapper imports mlx_teacache + monkey-patches
    # Flux2Klein.generate_image to enter apply_teacache, then defers to
    # mflux's own flux2_generate.main() with sys.argv intact — so the
    # argv contract (--model / --prompt / --output / --steps / --lora-
    # paths / ...) is exactly what bare mflux expects. If the import or
    # patch fails the wrapper itself shells back out to the bare CLI, so
    # there's no scenario where this swap loses a render — only the
    # speedup.
    if fam == "flux2" and env.get("MFLUX_TC_FLUX") == "1":
        # bin_path is .../ltx-2-mlx/env/bin/mflux-generate-flux2; the
        # corresponding python is the sibling python3 in the same bin/.
        # Use sys.executable as a sanity fallback if the inferred path
        # doesn't exist (e.g. the user has a non-standard mflux install
        # via mflux_python_path that lives outside our venv layout).
        bin_dir = Path(bin_path).parent
        venv_py = bin_dir / "python3"
        if not venv_py.is_file():
            venv_py = bin_dir / "python"
        if not venv_py.is_file():
            venv_py = Path(sys.executable)
        wrapper = Path(__file__).resolve().parent / "run_mflux_with_teacache.py"
        if wrapper.is_file():
            cmd[:1] = [str(venv_py), str(wrapper)]
            if on_log is not None:
                try: on_log(f"[mflux-teacache] wrap on (thresh={env.get('MFLUX_TC_FLUX_THRESH')})")
                except Exception:  # noqa: BLE001
                    pass

    # Switched to Popen + line-streaming so the panel can surface mflux's
    # tqdm progress bars (`[12/30]`-style step lines) and weight-download
    # status to the user while the subprocess runs. Capturing both
    # stdout and stderr to one stream so the line ordering matches what
    # mflux actually printed; tail of the stream is kept for inclusion
    # in any error message.
    #
    # Use select() with a poll interval so the deadline check fires even
    # when the child goes silent (e.g. model-load hang, Metal driver
    # deadlock, stuck weight download). Previous design used blocking
    # `for raw in iter(readline, "")` which could block forever if mflux
    # stopped flushing — the deadline check after each line never tripped
    # and the queue worker hung indefinitely.
    # A download that stopped partway must resume, not crash (#73).
    if _is_hf_repo_id(getattr(config, "mflux_model", None)):
        _partial = repair_partial_hf_download(config.mflux_model, env)
        if _partial and on_log is not None:
            try:
                on_log(f"[download] {config.mflux_model} was interrupted at "
                       f"{_partial['on_disk_gb']} GB ({_partial['incomplete']} files "
                       f"unfinished) — continuing it before this render. "
                       f"Finished files are kept; an unfinished one may start "
                       f"again from its beginning.")
            except Exception:  # noqa: BLE001
                pass

    import collections
    import select
    last_lines: collections.deque[str] = collections.deque(maxlen=64)
    stderr_tail = ""
    timed_out = False
    image_diagnostics.attempt(diagnostic, "spawn", cmd)
    _spawned_at = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True, errors="replace",
        bufsize=1,
        # Own process group so the Popen.terminate/kill below escalates
        # to the entire mflux+MLX subtree (mflux can spawn helper procs
        # for download / Metal init that survive a parent kill).
        start_new_session=True,
    )
    # Register before the wait loop so /stop can find this proc.
    try:
        _register_active_proc(proc)
        # Activity-based (stall) timeout, NOT a fixed total deadline: the model
        # download runs INSIDE this subprocess, and on a slow connection a
        # 20-34 GB first-run pull can exceed any fixed render budget. The old
        # fixed deadline killed a slow-but-progressing download mid-fetch — so a
        # brand-new user's first generation just "failed" with a partial model.
        # Reset the deadline on every output line (download "Fetching i/N" lines
        # stream per file; render emits step lines every few seconds), so we
        # only abort on a genuine STALL — no output for `stall_budget`. A
        # generous absolute cap is the runaway backstop.
        stall_budget = _MFLUX_STALL_SECONDS                  # 30 min of silence = stuck
        hard_cap = time.time() + max(timeout_s, 14400.0)   # 4 h absolute backstop
        # The download writes to disk for many minutes while its stdout is
        # silent OR floods \r-progress that readline() blocks on — either way
        # the read loop below can't tick a stall clock during the pull. So
        # liveness is owned by a DAEMON THREAD (a separate thread can't be
        # starved by readline blocking — the GIL releases during blocking I/O):
        # it polls the model's cache dir (growth = download alive) while the
        # read loop stamps `_alive` on each render line (render alive). It kills
        # the subprocess only on a GENUINE stall — neither disk nor stdout for
        # stall_budget — and emits a GB readout. (HiDream uses the same idea.)
        # Same hub root as the repair (HF_HUB_CACHE, then HF_HOME, then the
        # default): it used to read HF_HOME only.
        dl_dir = ((_hf_hub_root(env)
                   / ("models--" + config.mflux_model.replace("/", "--")))
                  if getattr(config, "mflux_model", None) else None)
        def _dl_gb() -> float:
            try:
                if not dl_dir or not dl_dir.exists():
                    return -1.0
                total = 0
                for _root, _dirs, _files in os.walk(dl_dir):
                    for _f in _files:
                        _fp = os.path.join(_root, _f)
                        try:
                            if not os.path.islink(_fp):   # skip snapshot symlinks → no double count
                                total += os.path.getsize(_fp)
                        except OSError:
                            pass
                return total / 1e9
            except Exception:  # noqa: BLE001
                return -1.0
        _alive = [time.time()]
        _wd_stop = threading.Event()
        _wd_killed = [False]
        def _watchdog() -> None:
            _last = _dl_gb()
            while not _wd_stop.wait(_MFLUX_WATCHDOG_POLL):
                _sz = _dl_gb()
                # Compared with the LAST SAMPLE, not the largest one seen
                # (4.19.18): the Xet downloader restarts an unfinished file
                # from 0, the folder shrinks by gigabytes, and a max-so-far
                # test showed no progress (and stamped no liveness) until
                # the file had grown back past its old size.
                _moved, _line = download_progress_step(_last, _sz)
                if _moved:
                    _alive[0] = time.time()
                    if _line and on_log is not None:
                        try: on_log(_line)
                        except Exception:  # noqa: BLE001
                            pass
                _last = _sz
                if (time.time() - _alive[0] > stall_budget) or (time.time() > hard_cap):
                    _wd_killed[0] = True
                    try: os.killpg(os.getpgid(proc.pid), 15)   # SIGTERM tree → readline EOFs
                    except (OSError, ProcessLookupError):
                        pass
                    return
        threading.Thread(target=_watchdog, name="mflux-dl-watchdog", daemon=True).start()
        poll_interval = 0.5
        if proc.stdout is not None:
            while True:
                # Wait up to poll_interval for a readable line; fall
                # through to the deadline + exit-status check otherwise.
                ready, _, _ = select.select(
                    [proc.stdout], [], [], poll_interval,
                )
                if ready:
                    try:
                        raw = proc.stdout.readline()
                    except (OSError, ValueError):
                        # Pipe closed — child likely exited; let the
                        # poll() / wait() branch below handle it.
                        raw = ""
                    if raw == "":
                        # EOF — readline returns "" only at end-of-stream
                        # for text-mode pipes. Drop into the post-loop
                        # cleanup (waits for rc).
                        break
                    line = raw.rstrip("\n")
                    if line:
                        _alive[0] = time.time()                 # render/output activity → alive
                        image_diagnostics.attempt(diagnostic, "line", line)
                        last_lines.append(line)
                        if on_log is not None:
                            try:
                                on_log(line)
                            except Exception:        # noqa: BLE001
                                # A buggy logger callback must not break the gen.
                                pass
                # Stall timeout is owned by the watchdog thread (it sees disk
                # growth even while readline() above is blocked on \r-output).
                # Child exited but we haven't seen EOF yet — flush any
                # remaining buffered output on the next select cycle.
                # Don't break here: select+readline above will drain
                # the pipe to "" and end the loop cleanly.
                if proc.poll() is not None and not ready:
                    # No data in this slice + child gone → drain done.
                    break
        if _wd_killed[0]:                  # watchdog SIGTERM'd a genuine stall
            timed_out = True
        _wd_stop.set()                     # stop the watchdog promptly
        if timed_out:
            # SIGTERM the whole process group, give it 2s to clean up,
            # then SIGKILL if still alive. The except subprocess.TimeoutExpired
            # branch below would also fire but only after proc.wait() blocks
            # for the remaining timeout window — we skip that by killing now.
            try:
                os.killpg(os.getpgid(proc.pid), 15)        # SIGTERM
            except (OSError, ProcessLookupError):
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), 9)     # SIGKILL
                except (OSError, ProcessLookupError):
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            raise RuntimeError(
                f"{bin_name} stalled: no output for {int(stall_budget)}s "
                f"(a hung download or a Metal deadlock). First-run weight pulls "
                f"stream progress and keep this alive — a genuine stall tripped it."
            )
        rc = proc.wait(timeout=30)
    except subprocess.TimeoutExpired as e:
        timed_out = True
        try:
            proc.kill()
        except OSError:
            pass
        raise RuntimeError(
            f"{bin_name} did not exit after its output ended "
            f"(possible zombie / stuck cleanup)."
        ) from e
    finally:
        try: _wd_stop.set()                # belt: stop watchdog even on early error
        except NameError: pass
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
        try:
            cancelled = bool(_CANCEL_CHECK and _CANCEL_CHECK())
        except Exception:
            cancelled = False
        image_diagnostics.attempt(diagnostic, "finish",
            "cancelled" if cancelled else "timeout" if timed_out else
            "completed" if getattr(proc, "returncode", None) == 0 else "failed",
            getattr(proc, "returncode", None))
        _unregister_active_proc(proc)
        # Ideogram's temp --prompt-file outlives the child (read during
        # model setup); remove it now that the subprocess has exited.
        if ideogram_prompt_file is not None:
            try:
                os.unlink(ideogram_prompt_file)
            except OSError:
                pass

    stderr_tail = "\n".join(last_lines)
    # 4.19.4: the line that NAMES the cause goes on the error's FIRST line.
    # The fleet keeps only that first line (scrubbed), and it used to read
    # "mflux-generate-flux2 failed (exit 1) on batch of 1 seeds. Tail of
    # stdout/stderr:" - two Macs, 21 FLUX.2 failures since 4.16, and not one
    # word of why. The tail still follows in full for the user and the Logs.
    cause = _subprocess_cause_line(last_lines)
    cause_note = f"; last line: {cause[:220]}" if cause else ""
    # SIGTERM/SIGKILL → user hit /stop. Surface as a typed cancellation
    # so the queue worker marks the job 'cancelled' rather than 'failed'.
    if rc in (-15, 143, -9, 137):
        raise ImageJobCancelled(f"{bin_name} cancelled by /stop (rc={rc})")
    if rc != 0:
        # Detect MLX/Metal `abort()` from the GPU completion thread (issue #2:
        # @Akossimon's three crashes in 2 minutes were all this exact
        # signature). When MLX submits a Metal command buffer and the GPU
        # returns an error, mlx::core::gpu::check_error throws a C++
        # exception on the Metal completion queue — Python never gets a
        # clean traceback, the whole process gets SIGABRT and dumps
        # ~/Library/Logs/DiagnosticReports/python3.11-*.ips. From the
        # subprocess parent's view, `rc` is -6 (POSIX signal -SIGABRT)
        # or 134 (shell-style 128+6). Without this branch the user sees
        # `mflux failed (exit -6)` which tells them nothing.
        # rc == -6 is Popen's "killed by SIGABRT" convention (signal-numbered
        # negative). rc == 134 is the shell convention (128 + signal 6).
        # Cover both because `subprocess` is inconsistent across versions.
        looks_like_abort = (
            rc in (-6, 134)
            or "abort()" in stderr_tail
            or "MTLCommandBuffer" in stderr_tail
            or ("Metal" in stderr_tail and "error" in stderr_tail.lower())
        )
        # Distinguish OOM from other Metal errors when we can — mflux's
        # tqdm + MLX print specific markers for each. OOM is the common
        # one for first-time users with too-big a model.
        looks_like_oom = (
            "out of memory" in stderr_tail.lower()
            or "MetalAlloc" in stderr_tail
            or "insufficient memory" in stderr_tail.lower()
            or "kIOReturnNoSpace" in stderr_tail
        )
        if looks_like_abort or looks_like_oom:
            hint = (
                "The engine reported that it ran out of memory."
                if looks_like_oom
                else "The image engine aborted in native code. Memory pressure "
                "or a Metal error may be involved, but this log does not confirm the cause."
            )
            signal_note = "SIGABRT; " if rc in (-6, 134) else ""
            raise RuntimeError(
                f"{bin_name} aborted on batch of {n} seeds ({signal_note}exit {rc}){cause_note}. {hint}\n\n"
                f"Try one of:\n"
                f"  - Smaller engine (FLUX.2 [klein] 4B / Z-Image-Turbo) "
                f"if you picked a 9B/edit model.\n"
                f"  - Choose a small canvas: 768×432 for wide images or 512×512 for square images.\n"
                f"  - Lower quantization (Q4 instead of Q6/Q8) for tight RAM.\n"
                f"  - Quit other big apps (browsers + IDEs eat 8-15 GB).\n"
                f"If it happens again, check ~/Library/Logs/DiagnosticReports/"
                f"for a Python .ips crash report and attach it to your bug report.\n\n"
                f"Tail of stdout/stderr:\n{stderr_tail[-800:]}"
            )
        # The engine exited with its model download unfinished (4.19.18):
        # say THAT on the first line - the line the user reads and the
        # fleet keeps - not only whatever mflux printed last.
        _dl_note = _download_stopped_note(getattr(config, "mflux_model", None),
                                          env, cause)
        if _dl_note:
            raise RuntimeError(
                f"{_dl_note}\n\nTail of stdout/stderr:\n{stderr_tail[-1200:]}")
        _shape_note = _lora_shape_note(config, _lora_paths_in, cause)
        if _shape_note:
            raise RuntimeError(
                f"{_shape_note}\n\nTail of stdout/stderr:\n{stderr_tail[-1200:]}")
        # Generic non-zero exit — pass through the tail so the user sees
        # whatever mflux printed last. Keep the END of the tail: a Python
        # traceback puts the exception on its last line, and the head-slice
        # this used to take cut it off right before the message (#73).
        raise RuntimeError(
            f"{bin_name} failed (exit {rc}) on batch of {n} seeds{cause_note}. "
            f"Tail of stdout/stderr:\n{stderr_tail[-1200:]}"
        )

    # The run loaded the model, so its download is whole: clear leftovers.
    if _is_hf_repo_id(getattr(config, "mflux_model", None)):
        try:
            clear_download_litter(config.mflux_model, env, _spawned_at)
        except Exception:  # noqa: BLE001
            pass

    # Build results from whatever files actually landed. If the
    # subprocess errored partway through (e.g. one seed's generation
    # raised), some seeds may not have produced an output — return the
    # ones that did rather than failing the whole batch. Order by seed
    # so candidate numbering is deterministic.
    #
    # IMPORTANT (2026-05-31 review H7 — do NOT delete this as "future compat"):
    # mflux is pinned ==0.17.5 (<0.18). The PINNED 0.17.5 appends _seed_<seed>
    # to the filename whenever n>1 seeds are passed (the default agent batch is
    # n=4) — even when the template already substitutes {seed}. So our
    # `cand_{seed}_mflux.png` template lands on disk as
    # `cand_<seed>_mflux_seed_<seed>.png`. This fallback is LOAD-BEARING on the
    # current pin, not dead >=0.18 compensation. Without it every multi-seed
    # image-gen run returned an empty candidates list,
    # which meant: no sidecar JSON written (the for-loop ran zero times),
    # users saw thumbnails in the gallery via the library scan but had
    # no per-image metadata to show / reproduce / debug. The candidates
    # below are still ordered by `seeds` so n=4 batches map to consistent
    # indices in the UI.
    def _is_safety_placeholder(png: Path) -> bool:
        # Ideogram's safety head returns a flat gray "Image blocked by safety
        # filter" placeholder on some benign prompts (no mflux signal — it's
        # baked into the model weights). A real render has high colour variance;
        # the placeholder is uniform mid-gray. Detect it so the job fails
        # honestly instead of reporting a gray box as a successful render.
        try:
            from PIL import Image, ImageStat
            st = ImageStat.Stat(Image.open(png).convert("RGB"))
            mean_std = sum(st.stddev) / 3.0
            spread = max(st.mean) - min(st.mean)   # gray = channels ~equal
            return mean_std < 15.0 and spread < 25.0
        except Exception:  # noqa: BLE001 — detection must never break a render
            return False

    results: list[dict] = []
    for i, seed in enumerate(seeds):
        # Try the templated path first (old mflux behavior + the n=1 case).
        path = Path(output_template.format(seed=seed))
        if not path.is_file():
            # Fall back to scanning the output dir for the EXACT filename
            # pinned mflux 0.17.5 writes on n>1 (template + auto-`_seed_<seed>`).
            # Substring matching by `seed_str in p.name` was wrong: seed
            # 123 would also match `cand_9123_mflux.png` from a previous
            # run in the same agent shot folder, returning the wrong PNG.
            # Pin to the two known templates and mtime-sort defensively
            # in case both happen to coexist. (Code-review P2-2.)
            exact_patterns = (
                f"cand_{seed}_mflux.png",                  # old mflux
                f"cand_{seed}_mflux_seed_{seed}.png",      # mflux >=0.18
            )
            candidates = sorted(
                (p for p in output_dir.iterdir()
                 if p.is_file() and p.name in exact_patterns),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                # No file → mflux really didn't produce one for this seed.
                # Partial success: return whatever we got from other seeds.
                continue
            path = candidates[0]
        results.append({
            "png_path": str(path),
            "seed": seed,
            "engine": "mflux",
            "family": fam,
            "model": config.mflux_model,
            "width": width, "height": height,
            "refs": refs_used,
            # refs_ignored derives from refs_used so kontext (which DOES
            # consume refs[0]) no longer reports refs_ignored=True. Was
            # checking `fam not in ("qwen_edit", "flux2_edit")`, which
            # excluded kontext incorrectly — the panel's "this engine
            # ignored your refs" warning lit up on every kontext sidecar.
            # (Code-review P3, 2026-05-09.)
            "refs_ignored": (bool(refs) and not refs_used),
            "lora_paths": list(config.mflux_lora_paths or []),
            "safety_blocked": (fam == "ideogram" and _is_safety_placeholder(path)),
        })

    if fam == "ideogram" and results and all(r.get("safety_blocked") for r in results):
        raise RuntimeError(
            "Ideogram's safety filter blocked this prompt — it returned a gray "
            "placeholder instead of a render. This is a known false-positive in "
            "the Ideogram model itself (baked into the weights, not Phosphene) on "
            "some perfectly benign prompts. Try rephrasing the prompt, or switch "
            "to a Reference Edit engine."
        )

    if not results:
        raise RuntimeError(
            f"{bin_name} exited 0 but produced no files for any of {n} seeds "
            f"({seeds!r}). Check the panel log for mflux warnings."
        )
    return results


def _generate_bfl(prompt: str, n: int, width: int, height: int,
                  output_dir: Path, base_seed: int | None,
                  config: ImageEngineConfig) -> list[dict]:
    """Submit + poll Black Forest Labs API for each candidate.

    BFL's API is async-poll. POST `/{model}` returns `{id}`; GET
    `/get_result?id=...` returns `{status, result}` where status flips
    to "Ready" with `result.sample` (a download URL).
    """
    if not config.bfl_api_key:
        raise RuntimeError(
            "BFL API key not configured. Add one under Settings → Image "
            "generation, or pick the mock engine to test the workflow."
        )
    headers = {"Content-Type": "application/json", "X-Key": config.bfl_api_key}
    poll_headers = {"X-Key": config.bfl_api_key}
    submit_url = f"{config.bfl_base_url.rstrip('/')}/{config.bfl_model}"
    poll_url_base = f"{config.bfl_base_url.rstrip('/')}/get_result"

    results = []
    for i in range(n):
        body = {
            "prompt": prompt,
            "width": width, "height": height,
            "prompt_upsampling": False,
            "safety_tolerance": 2,
        }
        if base_seed is not None:
            body["seed"] = base_seed + i

        # Submit
        req = urllib.request.Request(
            submit_url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:                       # noqa: BLE001
                pass
            raise RuntimeError(
                f"BFL submit failed (HTTP {e.code} {e.reason}): {detail[:400]}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"BFL unreachable: {e.reason}") from e

        task_id = resp.get("id") or resp.get("task_id")
        if not task_id:
            raise RuntimeError(f"BFL submit returned no task id: {resp!r}")

        # Poll
        deadline = time.time() + 180
        sample_url = None
        seed_used = base_seed + i if base_seed is not None else 0
        while time.time() < deadline:
            time.sleep(1.5)
            poll_url = f"{poll_url_base}?id={task_id}"
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(poll_url, headers=poll_headers),
                    timeout=20,
                ) as r:
                    rs = json.loads(r.read())
            except urllib.error.URLError:
                continue
            status = rs.get("status", "")
            if status == "Ready":
                inner = rs.get("result") or {}
                sample_url = inner.get("sample")
                if "seed" in inner:
                    seed_used = inner["seed"]
                break
            if status in ("Error", "Failed", "Content Moderated"):
                raise RuntimeError(f"BFL task {task_id} {status}: {rs!r}")
        if sample_url is None:
            raise RuntimeError(f"BFL task {task_id} timed out after 180s")

        # Download. Validate the URL before fetching: BFL hands us back a
        # sample URL from a JSON response, and a compromised/MITM'd response
        # could substitute file:///... (local file read) or an attacker host
        # (exfil of poll_headers via Referer / credential reuse). Pin to
        # https on bfl.ai / bfl.ml only.
        parsed = urllib.parse.urlparse(sample_url)
        host = (parsed.hostname or "").lower()
        allowed = ("bfl.ai", "bfl.ml")
        host_ok = any(host == d or host.endswith("." + d) for d in allowed)
        if parsed.scheme != "https" or not host_ok:
            raise RuntimeError(
                f"BFL returned unexpected URL: {parsed.scheme}://{parsed.hostname}"
            )
        try:
            with urllib.request.urlopen(sample_url, timeout=60) as r:
                data = r.read()
        except urllib.error.URLError as e:
            raise RuntimeError(f"BFL sample download failed: {e.reason}") from e

        out_path = output_dir / f"cand_{i:02d}_bfl.png"
        out_path.write_bytes(data)
        results.append({
            "png_path": str(out_path), "seed": seed_used, "engine": "bfl",
            "width": width, "height": height,
        })
    return results


# ---------------------------------------------------------------------------
# HiDream-O1-Image-Dev (BF16 MLX)
#
# Lives in its own venv outside Phosphene so that mlx-vlm + Phosphene's
# ltx-2-mlx env stay decoupled (different MLX-related dep trees historically
# fight each other on install). Subprocess pattern, mirrors mflux.
#
# Lab layout (resolved via $HIDREAM_LAB_DIR; default ~/HIDREAM-O1-MLX-LAB-active):
#   <HIDREAM_LAB_DIR>/
#     .venv/bin/python            <- the interpreter we shell out to
#     mlx_models/hidream-o1-dev-bf16/
#       model.safetensors         <- 17 GB BF16 backbone (mlx-vlm-loadable)
#       extras/custom_heads.safetensors
#       config.json (with quantization field)
#       tokenizer.json, processor configs
#     scripts/hidream_o1/generate_hidream_o1_mlx.py
#
# That whole tree is intentionally outside ~/pinokio/ so Pinokio cleanup
# never touches it; see the lab's README.md.
#
# Health check is honest about both the venv and the model dir, so the
# settings UI can surface a precise error if either is missing.
# ---------------------------------------------------------------------------

# HiDream-O1 MLX lab — the standalone repo that ships the BF16 generator
# script + custom_heads.safetensors. Public installs that want to enable
# the HiDream engine point this at their checkout via
# ``HIDREAM_LAB_DIR`` (env). Default lives just outside the home dir so
# Pinokio cleanup never touches it; if the env var isn't set, falls back
# to ``~/HIDREAM-O1-MLX-LAB-active`` and ``~/AI/HIDREAM-O1-MLX-LAB-active``
# in that order. The health check below reports clearly when neither
# exists so users know to clone the lab.
def _resolve_hidream_lab_dir() -> Path:
    env_p = os.environ.get("HIDREAM_LAB_DIR")
    if env_p:
        return Path(env_p).expanduser()
    home = Path.home()
    for cand in (home / "HIDREAM-O1-MLX-LAB-active",
                 home / "AI" / "HIDREAM-O1-MLX-LAB-active",
                 home / "AI" / "projects" / "HIDREAM-O1-MLX-LAB-active"):
        if cand.exists():
            return cand
    # Return the most conventional location even if it doesn't exist —
    # the health check surfaces a clean error from here.
    return home / "HIDREAM-O1-MLX-LAB-active"

HIDREAM_LAB_DIR = _resolve_hidream_lab_dir()
HIDREAM_DEFAULT_PY = HIDREAM_LAB_DIR / ".venv" / "bin" / "python"
# BF16 (no quantization) is the only precision that doesn't accumulate visible
# patch-grid artifacts. Upstream loads weights in fp32 + autocasts to bf16;
# any per-group quantization (Q4/Q6/Q8) introduces dequant rounding that
# compounds across 36 layers and shows as a 32-pixel grid in flat regions
# (skies, walls, water), especially at non-square trained dimensions.
# BF16 is 17.55 GB on disk + ~16 GB peak RAM at 1440×2560 (Comfortable+ tier).
# Q4 corrupts brightness (ships dark) — never use.
# Q6/Q8 work at square 2048×2048 but show grid at non-square trained dims.
HIDREAM_DEFAULT_MODEL = HIDREAM_LAB_DIR / "mlx_models" / "hidream-o1-dev-bf16"
HIDREAM_GENERATE_SCRIPT = HIDREAM_LAB_DIR / "scripts" / "hidream_o1" / "generate_hidream_o1_mlx.py"
HIDREAM_PATCH_SIZE = 32   # HiDream operates on patch-aligned multiples; we round if asked for non-aligned

# HiDream-O1 was trained on a fixed list of resolutions. Generating at off-spec
# dimensions produces visible 32-pixel patch grid artifacts because the model
# never saw mrope position codes outside this distribution. Snap to the closest
# trained ratio before generation. List is from upstream pipeline.py utils.py.
HIDREAM_TRAINED_RESOLUTIONS = [
    (2048, 2048),
    (2304, 1728), (1728, 2304),
    (2560, 1440), (1440, 2560),
    (2496, 1664), (1664, 2496),
    (3104, 1312), (1312, 3104),
    (2304, 1792), (1792, 2304),
]


def _snap_to_trained_resolution(width: int, height: int) -> tuple[int, int]:
    """Snap (w, h) to the closest aspect ratio HiDream was trained on."""
    img_ratio = width / height
    best, min_diff = (2048, 2048), float("inf")
    for w, h in HIDREAM_TRAINED_RESOLUTIONS:
        diff = abs(w / h - img_ratio)
        if diff < min_diff:
            min_diff, best = diff, (w, h)
    return best


def _resolve_hidream_python(config: ImageEngineConfig) -> str | None:
    """Return the absolute path of the HiDream venv python, or None."""
    import os
    p = Path(config.hidream_python_path) if config.hidream_python_path else HIDREAM_DEFAULT_PY
    return str(p) if p.is_file() and os.access(p, os.X_OK) else None


def _resolve_hidream_model(config: ImageEngineConfig) -> str | None:
    """Return the absolute path of the converted HiDream model dir, or None."""
    p = Path(config.hidream_model_path) if config.hidream_model_path else HIDREAM_DEFAULT_MODEL
    return str(p) if (p / "model.safetensors").exists() and (p / "extras" / "custom_heads.safetensors").exists() else None


def _patch_align(value: int, patch: int = HIDREAM_PATCH_SIZE) -> int:
    """HiDream requires width/height to be a multiple of PATCH_SIZE=32.

    Aspect-ratio entries like 1280×720 don't satisfy that (720 % 32 != 0).
    We round DOWN to the nearest multiple. Rounding up risks blowing
    memory at the upper resolutions.
    """
    return max(patch, (value // patch) * patch)


def _generate_hidream(prompt: str, n: int, width: int, height: int,
                      output_dir: Path, base_seed: int | None,
                      config: ImageEngineConfig,
                      refs: list[str] | None = None,
                      on_log: "callable | None" = None) -> list[dict]:
    """Subprocess pattern: one call per candidate.

    HiDream's generator script writes a single PNG and exits. We invoke n
    times with seed = base_seed + i (or random if base_seed is None) so
    candidate order is reproducible.

    Edit / multi-ref support is enabled — when `refs` is non-empty the
    generator runs HiDream's native edit/multi-reference path at BF16
    precision. K=1 → instruction-based edit ("change the white jacket
    to red"); K=2-3 → subject-driven personalization (compose multiple
    subjects in a new scene). Q6/Q8 quantization breaks edit because
    per-group dequant noise compounds in the attention against ref
    features, so edit ALWAYS uses BF16 even if the user picked a smaller
    quant for text-to-image.
    """
    import os
    import random
    import subprocess

    py = _resolve_hidream_python(config)
    if not py:
        raise FileNotFoundError(
            f"HiDream venv python not found at {config.hidream_python_path or HIDREAM_DEFAULT_PY}"
        )
    model = _resolve_hidream_model(config)
    if not model:
        raise FileNotFoundError(
            f"HiDream model not found at {config.hidream_model_path or HIDREAM_DEFAULT_MODEL}"
        )
    script = str(HIDREAM_GENERATE_SCRIPT)
    if not Path(script).is_file():
        raise FileNotFoundError(f"HiDream generator script missing at {script}")

    # Snap to trained-aspect-ratio resolution first, then patch-align.
    # Generating off-spec produces visible 32-pixel patch grid artifacts
    # because the model never saw those mrope position codes during training.
    snap_w, snap_h = _snap_to_trained_resolution(width, height)
    if (snap_w, snap_h) != (width, height) and on_log:
        on_log(f"[hidream] snapping {width}x{height} -> {snap_w}x{snap_h} (trained dim)")
    aligned_w = _patch_align(snap_w)
    aligned_h = _patch_align(snap_h)

    # Single subprocess invocation for all n candidates so the BF16 model
    # (~17 GB into RAM, ~45s cold load) is loaded ONCE per batch instead of n
    # times. The lab's generator accepts multiple --seed and --output values.
    seeds: list[int] = []
    pngs: list[Path] = []
    ts = int(time.time() * 1000)
    for i in range(n):
        seed = (base_seed + i) if base_seed is not None else random.randint(0, 2**31 - 1)
        seeds.append(seed)
        pngs.append(output_dir / f"cand_{i:02d}_hidream_{ts}.png")

    cmd = [
        py, script,
        "--model-path", model,
        "--model-type", config.hidream_recipe,
        "--prompt", prompt,
        "--width", str(aligned_w),
        "--height", str(aligned_h),
        "--output", *map(str, pngs),
        "--seed", *map(str, seeds),
        "--num-inference-steps", str(config.hidream_steps),
        "--noise-scale-start", str(config.hidream_noise_scale),
        "--noise-scale-end", str(config.hidream_noise_scale),
        "--noise-clip-std", str(config.hidream_noise_clip_std),
    ]
    # CFG only fires when guidance > 0 AND no refs (Full edit not supported yet).
    if config.hidream_guidance_scale and config.hidream_guidance_scale > 0:
        cmd.extend(["--guidance-scale", str(config.hidream_guidance_scale)])
    if refs:
        cmd.extend(["--ref-images", *map(str, refs)])
        # Dev-edit scheduler choice — upstream's 2026-05-12 flow_match is the
        # default and the recipe we ship. Only meaningful when model_type=dev
        # AND refs are present; ignored otherwise by the generator.
        cmd.extend(["--editing-scheduler", config.hidream_editing_scheduler])
        # First-Block Cache for the Dev-edit path. Only worth enabling when
        # there are refs (the edit path is where the per-step cost matters).
        if config.hidream_fb_cache and config.hidream_recipe == "dev":
            cmd.extend([
                "--fb-cache",
                "--fb-threshold", str(config.hidream_fb_threshold),
                "--fb-keep-last", str(config.hidream_fb_keep_last),
            ])
    if on_log:
        on_log(f"[hidream] launching {n} candidate(s) in one process, seeds={seeds}"
               + (f" with {len(refs)} ref(s)" if refs else ""))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, errors="replace",
        env=_clean_subprocess_env(),
        # Own process group so /stop's killpg cascades to any
        # child procs MLX/Metal might spawn under this generation.
        start_new_session=True,
    )
    _register_active_proc(proc)
    # 2026-05-31 review fix (C1): the read loop below blocks on proc.stdout
    # with no deadline. A hung HiDream render (stalled MLX/Metal, no output,
    # never exits) would pin the queue worker forever — only a manual /stop
    # recovered. A watchdog timer SIGKILLs the process group after a generous
    # deadline so a hang self-heals. mflux already has a select()-based
    # deadline (≈:866); HiDream is dropdown-hidden (#15) so this lighter
    # watchdog is sufficient until the two streamers are unified.
    _hd_timed_out = {"v": False}
    _hd_deadline = float(os.environ.get("PHOSPHENE_HIDREAM_TIMEOUT_S", "1500"))

    def _hd_kill_on_timeout():
        _hd_timed_out["v"] = True
        try:
            os.killpg(os.getpgid(proc.pid), 9)   # SIGKILL the whole group
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    _hd_wd = threading.Timer(_hd_deadline, _hd_kill_on_timeout)
    _hd_wd.daemon = True
    _hd_wd.start()
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                if on_log:
                    on_log(f"[hidream] {line}")
        rc = proc.wait()
    finally:
        _hd_wd.cancel()
        _unregister_active_proc(proc)
    if _hd_timed_out["v"]:
        raise RuntimeError(
            f"HiDream gen exceeded its {_hd_deadline:.0f}s deadline and was "
            f"killed (likely a hung render). Raise PHOSPHENE_HIDREAM_TIMEOUT_S "
            f"if your machine legitimately needs longer.")
    if rc != 0:
        # SIGTERM → rc = -15 on POSIX (or 143 if the shell wrapped it).
        # Treat that as cancellation, not failure, so the worker can mark
        # the job 'cancelled' and the queue advances cleanly.
        if rc in (-15, 143, -9, 137):
            raise ImageJobCancelled(f"HiDream gen cancelled by /stop (rc={rc})")
        raise RuntimeError(f"HiDream gen failed with rc={rc}")

    results: list[dict] = []
    for seed, png in zip(seeds, pngs):
        if not png.exists():
            raise RuntimeError(f"HiDream gen finished but no PNG at {png}")
        results.append({
            "png_path": str(png),
            "seed": seed,
            "engine": "hidream-o1-dev-bf16",
            "width": aligned_w,
            "height": aligned_h,
        })

    return results


# ---------------------------------------------------------------------------------------------
# P-H3-Image (Pruna), on MiniMax H3. One subprocess per image: the H3 engine's generate_image.py
# runs staged (text encoder, then the DiT with the adapter, then the decoder) so a 64 GB Mac
# never holds all three. Text-to-image only for now; refs fall back to a warning, like the other
# backends without multi-ref support.
# ---------------------------------------------------------------------------------------------

# Aspect keys of the Image Studio -> the seven ratios the model was trained at.
PH3IMAGE_RATIOS = {"1:1": "1:1", "4:3": "4:3", "3:4": "3:4", "3:2": "3:2", "2:3": "2:3",
                   "16:9": "16:9", "9:16": "9:16"}
PH3IMAGE_ADAPTER = "loras/p-h3-image_lora_bf16.safetensors"
# Megapixel tiers: Draft (2), Standard (4), High (8). Below 8 the author warns of more artifacts.
PH3IMAGE_TIERS = (2.0, 4.0, 8.0)


def ph3image_missing(config: ImageEngineConfig) -> list[str]:
    """What is not on disk yet, in plain words ([] = ready)."""
    out: list[str] = []
    py, script = config.ph3image_python_path, config.ph3image_script_path
    if not py or not Path(py).exists():
        out.append("the H3 engine is not installed")
    if not script or not Path(script).is_file():
        out.append("this H3 engine build has no image runner")
    root = Path(config.ph3image_root) if config.ph3image_root else None
    if root is None or not (root / PH3IMAGE_ADAPTER).is_file():
        out.append("the P-H3-Image adapter is not downloaded")
    if root is None or not (root / "vae" / "decoder.safetensors").is_file():
        out.append("the image decoder is not downloaded")
    if root is None or not (root / "text_encoder").is_dir():
        out.append("the text encoder is not downloaded")
    if not config.ph3image_dit or not Path(config.ph3image_dit).exists():
        out.append("the H3 video engine weights are missing")
    return out


def _ph3image_aspect(aspect: str) -> str:
    return PH3IMAGE_RATIOS.get(aspect, "16:9")


# Studio installs its existing signed-token authority. Standalone callers fail closed.
_ph3image_pro_status = lambda: {"active": False}


def ph3image_resident_headroom_gb(config):
    # Weights are already resident: reserve additional working space by canvas size.
    return max(6.0, min(12.0, float(config.ph3image_mp) * 2.0))


# What a private profile must not leave beside a picture: the schedule that makes it faster.
# The profile name stays (it is how a render proves which path ran); the method does not.
_PH3IMAGE_PRIVATE_METRIC_KEYS = ("sigmas", "forwards", "steps", "schedule", "timesteps")


def _ph3image_scrub_private_metrics(path) -> None:
    try:
        p = Path(path)
        data = json.loads(p.read_text())
        if isinstance(data, dict) and any(k in data for k in _PH3IMAGE_PRIVATE_METRIC_KEYS):
            for k in _PH3IMAGE_PRIVATE_METRIC_KEYS:
                data.pop(k, None)
            p.write_text(json.dumps(data, indent=2))
    except (OSError, ValueError):
        pass


def ph3image_pro_active():
    try:
        return bool(_ph3image_pro_status().get("active"))
    except Exception:
        return False


def _generate_ph3image(prompt: str, n: int, aspect: str, output_dir: Path, base_seed: int | None,
                       config: ImageEngineConfig, refs: list[str] | None = None,
                       on_log: "callable | None" = None) -> list[dict]:
    import json as _json
    import os
    import random
    import subprocess

    missing = ph3image_missing(config)
    if missing:
        raise FileNotFoundError("P-H3-Image is not ready: " + "; ".join(missing))
    if refs and on_log:
        on_log("[p-h3-image] reference images are not used yet (text-to-image only); rendering from the prompt")
    if (config.ph3image_pro_profile or Path(config.ph3image_script_path).name == "generate_image_pro.py") and not ph3image_pro_active():
        import ph3_image_service
        ph3_image_service.evict()
        raise RuntimeError("Pro is no longer active. Select Fast again to use the Free profile.")
    mp = float(config.ph3image_mp or 1.5)
    ratio = _ph3image_aspect(aspect)
    results: list[dict] = []
    ts = int(time.time() * 1000)
    requests = []
    for i in range(n):
        seed = (base_seed + i) if base_seed is not None else random.randint(0, 2**31 - 1)
        png = output_dir / f"cand_{i:02d}_ph3image_{ts}.png"
        metrics = output_dir / f"cand_{i:02d}_ph3image_{ts}.json"
        requests.append(dict(prompt=prompt, output=str(png), metrics=str(metrics), seed=seed,
                             aspect=ratio, mp=mp))
    service_script = Path(config.ph3image_script_path).with_name("serve_image_pro.py" if config.ph3image_pro_profile else "serve_image.py")
    if config.ph3image_warm_service and service_script.is_file():
        import ph3_image_service
        import hostinfo
        command = [config.ph3image_python_path, str(service_script),
                   "--root", config.ph3image_root, "--dit", config.ph3image_dit]
        def memory_preflight(reusing):
            # Selection and admission share the worker mutex, so idle eviction
            # cannot turn a warm budget check into an unchecked cold load.
            if hostinfo.total_ram_bytes() < 64 * 1024**3:
                raise ph3_image_service.ResidencyUnavailable("Warm image retention needs 64 GB")
            required = (ph3image_resident_headroom_gb(config) if reusing else 40) * 1024**3
            if hostinfo.available_bytes() < required:
                raise ph3_image_service.ResidencyUnavailable("Not enough free memory for image retention")
        for index, request in enumerate(requests):
            try:
                info = ph3_image_service.WORKER.request(command, request, _clean_subprocess_env(),
                    timeout=float(os.environ.get("PHOSPHENE_PH3IMAGE_TIMEOUT_S", "3600")),
                    on_log=on_log, register=_register_active_proc, unregister=_unregister_active_proc,
                    preflight=memory_preflight)
            except ph3_image_service.ResidencyUnavailable:
                # request() reaps the resident process before propagating admission
                # failure. A staged image is still allowed if its smaller budget fits.
                if hostinfo.available_bytes() < 26 * 1024**3:
                    raise RuntimeError("P-H3-Image needs more free memory. Close other apps and retry.")
                if on_log:
                    on_log("[p-h3-image] releasing warm models; rendering with staged memory")
                requests = requests[index:]
                break
            if config.ph3image_pro_profile:
                _ph3image_scrub_private_metrics(request["metrics"])
            results.append({"png_path": request["output"], "seed": request["seed"], "engine": "p-h3-image",
                            "width": int(info.get("width") or 0), "height": int(info.get("height") or 0),
                            "seconds": float(info.get("request_seconds") or 0.0)})
        else:
            return results
    # A staged request must evict a worker left by an earlier warm request,
    # including when the service was disabled or the installed script changed.
    import ph3_image_service
    ph3_image_service.evict()
    # One subprocess per batch also bounds residency to this caller's GPU-lock lifetime.
    warm = config.ph3image_warm_batch and len(requests) > 1
    groups = [requests[i:i + 16] for i in range(0, len(requests), 16)] if warm else [[r] for r in requests]
    for i, group in enumerate(groups):
        request = group[0]
        seed = request["seed"]
        png, metrics = Path(request["output"]), Path(request["metrics"])
        cmd = [config.ph3image_python_path, config.ph3image_script_path]
        batch_file = None
        if warm:
            batch_file = output_dir / f"ph3image_batch_{ts}.json"
            batch_file.write_text(_json.dumps(group))
            cmd += ["--batch-json", str(batch_file)]
        else:
            cmd += [prompt, "-o", str(png), "--aspect", ratio, "--mp", f"{mp:g}",
                    "--seed", str(seed), "--metrics", str(metrics)]
        cmd += ["--root", config.ph3image_root, "--dit", config.ph3image_dit]
        if on_log:
            on_log(f"[p-h3-image] image {i + 1}/{n}: {ratio} at {mp:g} MP, seed {seed}")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    errors="replace", env=_clean_subprocess_env(), start_new_session=True)
        except OSError:
            if batch_file is not None:
                batch_file.unlink(missing_ok=True)
            raise
        _register_active_proc(proc)
        deadline = float(os.environ.get("PHOSPHENE_PH3IMAGE_TIMEOUT_S", "3600")) * len(group)
        timed_out = {"v": False}

        def _kill():
            timed_out["v"] = True
            # The image CLI creates no children; stop only the process we started.
            try:
                proc.kill()
            except ProcessLookupError:
                pass

        wd = threading.Timer(deadline, _kill)
        wd.daemon = True
        wd.start()
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    line = line.rstrip()
                    if line and on_log:
                        on_log(f"[p-h3-image] {line}")
            rc = proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            wd.cancel()
            _unregister_active_proc(proc)
            if batch_file is not None:
                batch_file.unlink(missing_ok=True)
            if proc.stdout is not None:
                proc.stdout.close()
        if timed_out["v"]:
            raise RuntimeError(f"P-H3-Image exceeded its {deadline:.0f}s deadline and was stopped")
        if rc in (-15, 143, -9, 137):
            raise ImageJobCancelled(f"P-H3-Image cancelled by /stop (rc={rc})")
        if rc != 0:
            raise RuntimeError(f"P-H3-Image failed with rc={rc}")
        for request in group:
            png, metrics = Path(request["output"]), Path(request["metrics"])
            if not png.exists():
                raise RuntimeError(f"P-H3-Image finished but no PNG at {png}")
            info: dict = {}
            try:
                info = _json.loads(metrics.read_text())
            except Exception:
                pass
            if config.ph3image_pro_profile:
                _ph3image_scrub_private_metrics(metrics)
            results.append({"png_path": str(png), "seed": request["seed"], "engine": "p-h3-image",
                            "width": int(info.get("width") or 0), "height": int(info.get("height") or 0),
                            "seconds": float(info.get("total_seconds") or 0.0)})
    return results
