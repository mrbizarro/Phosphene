"""LoRA training support: dataset and caption-map helpers, completed-run listing, model preflight, failure classification.

The helpers around a training job (the job itself, run_train_job_inner, stays in the panel): dataset folders and caption maps, the dev-transformer install, time estimates, advice, and turning a runner event or exit code into a plain-English error.

Moved verbatim out of mlx_ltx_panel.py. The body of each function is unchanged
except that panel globals and this module's own functions are spelled
`P.<name>` (the same mechanical rename panel/routes_*.py uses). `P` is the
RUNNING panel module, assigned by mlx_ltx_panel where it imports this module;
it is never imported here because the panel usually runs as __main__ and
importing it by name would execute the whole file a second time. Routing every
cross-reference through `P` keeps test patches effective: a test that patches
`P.USAGE_LOG_FILE` or `P._usage_fleet_query_one` still reaches the code that
uses it. Constants and locks stay defined in the panel.

The names below stay importable from mlx_ltx_panel, which re-exports them, so
panel/routes_*.py and the tests are unchanged.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import threading
import time

P = None  # the running mlx_ltx_panel module; assigned where the panel imports this


def _train_dataset_dir(job_id: str) -> Path:
    """Per-job dataset folder. Holds images/, captions/, spec.json."""
    p = P.TRAIN_DIR / job_id
    return p


def _train_caption_map_path(dataset_dir: Path) -> Path:
    return dataset_dir / P._TRAIN_CAPTION_MAP_NAME


def _train_read_caption_map(dataset_dir: Path) -> dict[str, str]:
    p = P._train_caption_map_path(dataset_dir)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()
                    if isinstance(k, str) and isinstance(v, str)}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _train_write_caption_map(dataset_dir: Path, mapping: dict[str, str]) -> None:
    p = P._train_caption_map_path(dataset_dir)
    try:
        p.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    except OSError:
        pass


def _train_set_caption_map_entry(dataset_dir: Path,
                                 original_stem: str,
                                 saved_stem: str) -> None:
    m = P._train_read_caption_map(dataset_dir)
    m[original_stem] = saved_stem
    P._train_write_caption_map(dataset_dir, m)


def _train_reconcile_one(dataset_dir: Path,
                         original_stem: str,
                         saved_stem: str) -> bool:
    """If a caption file exists under `original_stem.txt`, rename it to
    `saved_stem.txt`. Used when the user uploads `image_001.txt` BEFORE its
    matching `image_001.png` arrives. Returns True if a paired caption now
    sits under `<captions>/<saved_stem>.txt`."""
    captions_dir = dataset_dir / "captions"
    saved_path = captions_dir / f"{saved_stem}.txt"
    if saved_path.is_file():
        return True
    if original_stem == saved_stem:
        return False
    parked = captions_dir / f"{original_stem}.txt"
    if parked.is_file():
        try:
            parked.rename(saved_path)
            return True
        except OSError:
            return False
    return False


def _train_reconcile_captions(dataset_dir: Path) -> tuple[int, int, list[str]]:
    """Called on training start. Walks images/ + captions/, ensures every
    caption under an *original* stem in caption_map.json is moved to its
    saved stem so the trainer's per-image lookup hits. Returns
    (paired_count, total_images, warnings)."""
    images_dir = dataset_dir / "images"
    captions_dir = dataset_dir / "captions"
    cmap = P._train_read_caption_map(dataset_dir)
    if not images_dir.is_dir():
        return 0, 0, ["images dir missing"]
    images = [p for p in sorted(images_dir.iterdir())
              if p.is_file() and p.suffix.lower() in P.TRAIN_IMAGE_EXTS]
    total = len(images)
    warnings: list[str] = []
    if not captions_dir.is_dir():
        return 0, total, warnings
    for original, saved in cmap.items():
        if original == saved:
            continue
        parked = captions_dir / f"{original}.txt"
        target = captions_dir / f"{saved}.txt"
        if parked.is_file() and not target.is_file():
            try:
                parked.rename(target)
            except OSError as e:
                warnings.append(f"could not move {parked.name} → {target.name}: {e}")
    paired = sum(1 for img in images
                 if (captions_dir / f"{img.stem}.txt").is_file())
    return paired, total, warnings


def _train_estimate_seconds(preset: str, image_count: int, *,
                            steps_override: int | None = None,
                            is_style: bool = False) -> int:
    """Wall-time estimate in seconds for a training run. The image count
    has a small overhead for the preprocess pass (image → VAE latent
    cache, ~3 s/image on M4 Max), then `steps * seconds_per_step` for
    the train loop itself.

    `is_style` switches the preset lookup table — style training pulls
    its seconds_per_step from TRAIN_STYLE_PRESETS, not TRAIN_PRESETS."""
    table = P.TRAIN_STYLE_PRESETS if is_style else P.TRAIN_PRESETS
    cfg = table.get(preset) or table["quick"]
    # Steps derive from epochs × image_count; advanced override wins.
    steps = steps_override if steps_override else P._preset_steps_for(cfg, image_count)
    preprocess = 3.0 * max(0, image_count)
    train = float(steps) * float(cfg["seconds_per_step"])
    return int(preprocess + train + 30)  # +30 s for setup + checkpoint write


def _train_list_completed() -> list[dict]:
    """Scan mlx_models/loras/ for .safetensors with a Phosphene-trained
    sidecar (`kind=train_character` OR `kind=train_style`) and return one
    record per discovered run for the Trained-LoRAs list in the Train tab.
    Surfacing both kinds in a single list lets the user see their full
    training inventory in one place; the UI filters by `train_type` to show
    the kind that matches the active train-type toggle (and lists everything
    when "All" is selected)."""
    out: list[dict] = []
    if P.LORAS_DIR.exists():
        for p in sorted(P.LORAS_DIR.iterdir(),
                        key=lambda x: x.stat().st_mtime if x.exists() else 0,
                        reverse=True):
            if p.suffix.lower() != ".safetensors":
                continue
            # Style LoRAs use a `.style.safetensors` suffix, so the sidecar
            # is `.style.json` not `.json`. Probe both paths.
            sidecar_candidates = [p.with_suffix(".json")]
            if p.name.endswith(".style.safetensors"):
                sidecar_candidates.insert(
                    0, p.with_name(p.name[: -len(".safetensors")] + ".json")
                )
            sidecar = next((s for s in sidecar_candidates if s.is_file()), None)
            if sidecar is None:
                continue
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                continue
            kind = meta.get("kind")
            if kind not in ("train_character", "train_style"):
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            verdict = P._adapter_verdict(meta)
            out.append({
                "id": meta.get("job_id") or p.stem,
                "name": meta.get("name") or p.stem,
                "trigger": meta.get("trigger") or "",
                "preset": meta.get("preset") or "",
                "kind": kind,
                "train_type": meta.get("train_type")
                                or ("style" if kind == "train_style"
                                    else "character"),
                "path": str(p),
                "filename": p.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 1),
                "created_at": meta.get("created_at") or stat.st_mtime,
                "image_count": meta.get("image_count") or 0,
                # WHETHER THIS FILE CAN DO ANYTHING, carried out of the
                # sidecar so the library can warn before somebody spends a
                # render finding out. "unknown" for anything trained by a
                # build that did not measure — silence is not weakness, and a
                # chip on every older LoRA would be noise nobody could act on.
                "adapter_verdict": verdict,
                # AND WHAT TO DO ABOUT IT. A verdict the user cannot act on
                # sends them to a GitHub thread to find out what their own
                # number meant (#62); the remedy travels with the verdict, and
                # is hardware-aware because on a sub-64 GB Mac "use High" is
                # not a thing that machine can do.
                "adapter_advice": (
                    P._train_weak_advice(
                        verdict,
                        str(meta.get("preset") or ""),
                        kind == "train_style",
                    )
                    if verdict not in ("ok", "unknown") else ""
                ),
            })
    return out


def _train_weak_advice(verdict: str, preset: str = "", is_style: bool = False) -> str:
    """One sentence a user can ACT on when their adapter measures weak/inert.

    The measurement shipped in v4.6.0 and the number was correct; what it did
    not do was tell anyone what to do about it. A first-time user on the Quick
    preset read "1.98e-04", found nothing in the panel that explained it, and
    ended up reading a 21-comment GitHub thread to learn that rank 8 has never
    carried a face (#62). The verdict without the remedy is the actual product
    failure in that issue.

    The remedy is hardware-dependent, which is why this is a function and not
    a constant: on a Mac under 64 GB `_select_train_profile` rewrites the whole
    table and *High is rank 8 at 500 steps on two projections*, so "train again
    on High" would be advice that machine cannot honour.
    """
    verdict = (verdict or "").lower()
    if verdict == "inert":
        return (
            "This adapter carries no weight delta at all — every low-rank "
            "product is exactly zero, so it cannot change a single pixel. "
            "Treat the run as failed rather than as a weak result: nothing "
            "was learned. Check the Logs for a trainer error, then train "
            "again."
        )
    compact = bool(P.TRAIN_PROFILE.get("compact"))
    if is_style:
        head = ("This style adapter came out weak: the deltas it learned may "
                "be too small to visibly change a render.")
    else:
        head = ("This character came out weak: the deltas it learned are "
                "below the level any adapter that has carried a face here "
                "has ever measured, so renders will most likely look "
                "LoRA-free.")
    if compact:
        ram = P.TRAIN_PROFILE.get("ram_gb")
        where = f"{ram} GB" if ram else "under 64 GB"
        return (
            f"{head} This Mac has {where}, so training runs the compact "
            "profile — even High is rank 8 / 500 steps / 448px on two of the "
            "four attention projections, not the rank-32 recipe that was "
            "graded on a face. No preset on this machine reaches that recipe, "
            "so this is the hardware and not your photos. More images and "
            "sharper, more varied ones still help; a 64 GB-class Mac is what "
            "unlocks the graded recipe."
        )
    if (preset or "").lower() != "high":
        return (
            f"{head} Train again on the High preset — rank 8 (Quick) and "
            "rank 16 (Medium) have never carried an identity here, and High "
            "is the only recipe that has. Raising the character strength "
            "toward 2.0 helps a weak adapter a little; it cannot rescue one "
            "this far under the floor."
        )
    return (
        f"{head} This was already the High preset, so the next levers are the "
        "dataset rather than the recipe: more images (20-40), sharper and more "
        "varied in pose and lighting, and captions that name the trigger plainly. "
        "Please attach the training log to https://github.com/mrbizarro/phosphene/issues/62 "
        "— a weak High run is the case that issue is still open on."
    )


def _train_required_models() -> list[dict]:
    """Return the list of model files Train Character needs and whether each
    is present locally. Surfaces missing models to the UI so the user can
    one-click download them via the existing hf flow.

    Train Character requires the FULL-PRECISION `transformer-dev.safetensors`
    from the LTX-2.3 Q8 repo (~21 GB). The dev base is required because
    distilled is structurally wrong for training (different sigma schedule
    than the trainer's flow-matching objective) — and it must be the Q8
    repo's full-precision copy because the Q4 repo's transformer-dev is
    QUANTIZED (~11 GB): the trainer refuses it (train.py's
    FULL_DEV_MIN_BYTES) since it silently trains a near-zero LoRA (#35).
    The default install ships neither, to save disk for inference-only
    users.

    Gemma + the rest of the Q4 stack (vae, audio, etc.) are already required
    for inference so they should already be present, but we check anyway.
    """
    q4_local_dir = P.ROOT / "mlx_models" / "ltx-2.3-mlx-q4"
    q8_local_dir = P.ROOT / "mlx_models" / "ltx-2.3-mlx-q8"
    gemma_local_dir = P.ROOT / "mlx_models" / "gemma-3-12b-it-4bit"
    gemma_repo_id = "mlx-community/gemma-3-12b-it-4bit"

    def _file_present(local_dir: Path, repo_id: str, filename: str,
                      *, extra_dirs: list[Path] | None = None,
                      min_bytes: int = P._MIN_FILE_BYTES) -> bool:
        """Check known locations for a model file in this order:

          1. The primary local_dir (where the helper expects it by default).
          2. Any `extra_dirs` callers list explicitly — used for files that
             can legitimately live in more than one place. The dev
             transformer is the canonical example: original downloads
             land in mlx_models/ltx-2.3-mlx-q4/ but Phosphene also ships
             a Q8-friendly copy under mlx_models/ltx-2.3-mlx-q8/, and the
             trainer happily loads either. Without this fallback the
             preflight reports "missing" on installs that only have the
             Q8-side copy and prompts an unnecessary 11 GB redownload.
          3. The configured HF_HOME hub for repo_id.
          4. The user-default HF cache (~/.cache/huggingface/hub) — covers
             installs where HF_HOME wasn't set at first download time and
             weights landed in the default location.
        """
        candidates: list[Path] = [local_dir / filename]
        for d in (extra_dirs or []):
            candidates.append(d / filename)
        cache_dir = P._repo_hf_cache_dir(repo_id)
        if cache_dir is not None:
            candidates.append(cache_dir / filename)
        # Last-resort: default HF cache, regardless of HF_HOME. Matches what
        # huggingface_hub does when neither env var is set. Old downloads
        # often live here even when HF_HOME is now pointing elsewhere.
        safe = repo_id.replace("/", "--")
        default_root = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{safe}" / "snapshots"
        try:
            if default_root.is_dir():
                snaps = sorted(default_root.iterdir(),
                               key=lambda d: d.stat().st_mtime, reverse=True)
                if snaps:
                    candidates.append(snaps[0] / filename)
        except OSError:
            pass
        for c in candidates:
            try:
                if c.exists() and c.stat().st_size >= min_bytes:
                    return True
            except OSError:
                continue
        return False

    # Mirror of lora_lab/train.py FULL_DEV_MIN_BYTES: anything smaller is a
    # quantized transformer-dev, which the trainer refuses. The preflight
    # must not show green on a file the trainer will reject (#35).
    _FULL_DEV_MIN_BYTES = 15 * 1000**3

    items = [
        {
            # THE ASSUMPTION THAT EXPIRED. This function's own docstring says
            # "the rest of the Q4 stack (vae, audio, etc.) are already required
            # for inference so they should already be present" — true right up
            # until v4.0, when a fresh install stopped fetching LTX-2.3 at all.
            # The trainer runs against 2.3 and needs its VAE, audio VAE and
            # vocoder; without this row the Train tab would have looked ready
            # and failed at load time on a pack nobody told the user about.
            "key": "ltx23_base",
            "label": "LTX-2.3 base pack (training runs against 2.3)",
            "blurb": "The trainer runs against LTX-2.3, not 2.5 — it needs 2.3's "
                     "VAE, audio VAE and vocoder. Your clips still render on "
                     "LTX-2.5; this is training-only. A fresh v4.0 install does "
                     "not download it, so it is fetched when you want to train.",
            "repo_id": "dgrauet/ltx-2.3-mlx-q4",
            "filename": "vae_decoder.safetensors",
            "local_dir": str(q4_local_dir),
            "size_gb": 20.48,
            "ready": _file_present(q4_local_dir, "dgrauet/ltx-2.3-mlx-q4",
                                   "vae_decoder.safetensors"),
        },
        {
            "key": "ltx_dev_transformer",
            "label": "LTX-2.3 dev transformer (training-only, full precision)",
            # SYS-15/SYS-42: "the dev transformer has the right flow-matching
            # schedule for LoRA-from-images training" was lab jargon a
            # creator can't act on. Outcome language: what it's for, and
            # what goes wrong if the wrong copy is used.
            "blurb": "Required for training — a different file from the one "
                     "your renders already use. Must be the full-precision "
                     "copy (~21 GB): a smaller, compressed copy trains a "
                     "LoRA that looks fine in the log and does nothing when "
                     "you use it.",
            "repo_id": "dgrauet/ltx-2.3-mlx-q8",
            "filename": "transformer-dev.safetensors",
            "local_dir": str(q8_local_dir),
            "size_gb": 20.6,
            # Size-gated: an 11 GB quantized dev (the old preflight's Q4
            # download, still on many disks) must show NOT-ready, because
            # the trainer refuses it and the LoRA comes out dead (#35).
            "ready": _file_present(q8_local_dir, "dgrauet/ltx-2.3-mlx-q8",
                                   "transformer-dev.safetensors",
                                   extra_dirs=[q4_local_dir],
                                   min_bytes=_FULL_DEV_MIN_BYTES),
        },
        {
            "key": "gemma_text_encoder",
            "label": "Gemma 3 12B (text encoder)",
            "blurb": "Encodes prompts for both inference and training. Already "
                     "required by Phosphene's renderer, so this is usually green.",
            "repo_id": gemma_repo_id,
            "filename": "config.json",
            "local_dir": str(gemma_local_dir),
            "size_gb": 6.0,
            "ready": _file_present(gemma_local_dir, gemma_repo_id, "config.json"),
        },
    ]
    return items


def _train_install_dev_transformer(push_log) -> dict:
    """Trigger an hf download for just `transformer-dev.safetensors` from
    the LTX-2.3 Q8 repo — the FULL-PRECISION copy the trainer requires.
    (Until v3.4.1 this pulled the Q4 repo's transformer-dev, which is
    quantized: the trainer refuses it and the LoRA trains dead — #35.)
    Reuses the same `hf download` binary the existing
    HF download flow uses; runs synchronously in a worker thread so the
    caller can stream progress (the panel currently invokes this from a
    /train/install endpoint that returns quickly + then polls /status).

    Returns {"ok": True, "started": True, "repo_id": ...} or {"ok": False,
    "error": "..."} if the download couldn't be started (already running,
    binary missing, etc.).
    """
    import subprocess
    if P.DOWNLOAD["active"]:
        return {"ok": False,
                "error": f"another download is already active "
                         f"({P.DOWNLOAD.get('repo_id', '?')})."}

    repo_id = "dgrauet/ltx-2.3-mlx-q8"
    local_dir = P.ROOT / "mlx_models" / "ltx-2.3-mlx-q8"
    local_dir.mkdir(parents=True, exist_ok=True)

    hf_bin = P.HF_BIN if P.HF_BIN is not None else P._resolve_hf()
    if hf_bin is None:
        return {"ok": False, "error": "hf CLI not found on PATH."}

    cmd = [str(hf_bin), "download", repo_id,
           "--include", "transformer-dev.safetensors",
           "--local-dir", str(local_dir)]

    env = dict(os.environ)
    hf_token = P._active_hf_token()
    if hf_token:
        env["HF_TOKEN"] = hf_token
        env["HUGGING_FACE_HUB_TOKEN"] = hf_token

    with P.DOWNLOAD_LOCK:
        P.DOWNLOAD["active"] = True
        P.DOWNLOAD["key"] = "ltx_dev_transformer"
        P.DOWNLOAD["repo_id"] = repo_id
        P.DOWNLOAD["started_ts"] = time.time()
        P.DOWNLOAD["last_line"] = ""

    def _runner():
        try:
            push_log(f"[hf] dev transformer download started "
                     f"({repo_id} / transformer-dev.safetensors, ~21 GB — "
                     f"the full-precision copy training requires).")
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1, env=env, start_new_session=True)
            if not P._download_register(proc):
                push_log("[hf] dev transformer download cancelled.")
                return
            buf = ""
            assert proc.stdout is not None
            while True:
                ch = proc.stdout.read(1)
                if not ch:
                    break
                if ch in ("\n", "\r"):
                    line = buf.strip()
                    buf = ""
                    if line:
                        with P.DOWNLOAD_LOCK:
                            P.DOWNLOAD["last_line"] = line[:200]
                        push_log(f"[hf:dev] {line[:300]}")
                else:
                    buf += ch
            rc = proc.wait()
            if rc == 0:
                push_log("[hf] dev transformer downloaded successfully.")
            else:
                push_log(f"[hf] dev transformer download exited with code {rc}.")
        except Exception as exc:
            push_log(f"[hf] dev transformer crashed: {exc}")
        finally:
            with P.DOWNLOAD_LOCK:
                P.DOWNLOAD["active"] = False
                P.DOWNLOAD["key"] = None
                P.DOWNLOAD["repo_id"] = None
                P.DOWNLOAD["started_ts"] = None
                P.DOWNLOAD["last_line"] = ""
                P.DOWNLOAD["proc"] = None
                P.DOWNLOAD["pgid"] = None
                P.DOWNLOAD["cancelled"] = False

    threading.Thread(target=_runner, daemon=True, name="hf-dev-download").start()
    return {"ok": True, "started": True, "repo_id": repo_id,
            "filename": "transformer-dev.safetensors"}


def _train_phase_from_line(line: str, payload: "dict | None", current: str) -> str:
    """Which phase the trainer is in, from the lines it prints.

    The preprocessor's phase banners are plain text (they reach the panel
    on the merged stdout/stderr pipe); a training step is a JSON event.
    Anything else keeps the current phase. Phases: start -> text_encode ->
    image_encode -> train.
    """
    if isinstance(payload, dict):
        if (payload.get("event") or "step") in ("step", "train_progress"):
            return "train"
        return current
    s = line or ""
    if "Phase 1: encoding text captions" in s:
        return "text_encode"
    if "Phase 2: encoding image latents" in s:
        return "image_encode"
    return current


def _train_encode_retry_wanted(rc: int, watchdog_seen: bool, phase: str,
                               already_retried: bool) -> bool:
    """One automatic relaunch, and only for the kill this can fix (#61):
    SIGABRT with a Metal-watchdog signature in the log, during the caption
    encode, not already retried. A kill during image encode or training
    is the canvas-sized problem the existing error names; a crash without
    the signature is not a GPU timeout at all."""
    return (rc == -6 and bool(watchdog_seen) and phase == "text_encode"
            and not already_retried)


def _train_gemma_fallback_max_length() -> int:
    """The shorter Gemma pad the relaunch uses — the same knob and default
    (256, floor 64) as the render helper's fallback, so one env var tunes
    both: LTX_GEMMA_FALLBACK_MAX_LENGTH."""
    try:
        return max(64, int(os.environ.get("LTX_GEMMA_FALLBACK_MAX_LENGTH", "256") or 256))
    except ValueError:
        return 256


def _train_wipe_caption_conditions(dataset_dir: Path) -> int:
    """Discard the caption encodings a killed run wrote, so the relaunch
    re-encodes every caption at one length — the preprocessor skips a
    condition file that exists, and a dataset half at 1024 tokens and half
    at 256 would not batch. Image latents live beside them and are left
    alone. Returns how many files went."""
    cond = Path(dataset_dir) / "training_data" / ".precomputed" / "conditions"
    if not cond.is_dir():
        return 0
    n = 0
    for f in cond.iterdir():
        if f.is_file() and f.name.startswith("condition_"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


def train_error_from_event(payload: dict) -> dict:
    """The trainer's `error` event, its class held to the closed list."""
    cls = str(payload.get("error_class") or "other")
    return {
        "stage": str(payload.get("stage") or "unknown")[:40],
        "message": str(payload.get("message") or "")[:2000],
        "error_class": cls if cls in P.TRAIN_ERROR_CLASSES else "other",
    }


def train_error_for_exit(rc: int, trainer_error: dict | None) -> str:
    """The class of a trainer that exited non-zero. The trainer's own word
    wins; a process killed by SIGKILL without one is macOS reclaiming memory
    (jetsam), the one signal death that has a single cause."""
    if trainer_error and trainer_error.get("error_class") in P.TRAIN_ERROR_CLASSES:
        return trainer_error["error_class"]
    if rc == -9:
        return "oom"
    return "other"


def _train_release_render_memory() -> None:
    """Stop the warm render helper before a training run (4.19.1)."""
    try:
        if P.HELPER.is_alive():
            P.push("[train] releasing the render engine's memory for training "
                 "(it reloads on the next render)")
            P.HELPER.kill()
    except Exception as e:  # never let housekeeping fail a training run
        P.push(f"[train] could not release the render engine: {e}")
