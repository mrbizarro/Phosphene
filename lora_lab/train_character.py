"""Single-shot CLI: run the full Character LoRA pipeline from a job-spec JSON.

This is the orchestration backend for Phosphene's "Train Character" panel.
The panel writes a spec JSON and invokes this module as a subprocess:

    ./scripts/run.sh python -m lora_lab.train_character path/to/spec.json

The CLI streams JSON-lines progress to stdout (one JSON object per line) so
the panel can tail it. The final `.safetensors` is written to the path in
the spec, with a `<lora>.safetensors.json` sidecar of training metadata.

------------------------------------------------------------------------
Stdout protocol (one JSON object per line; ordered, append-only):

    {"event":"start","job_id":"..."}
    {"event":"plan","preset":"medium","rank":32,"alpha":32,"steps":3000,
     "lr":0.0001,"resolution":576,"caption_strategy":"class_word",
     "estimated_wall_s":7234,"image_count":24}
    {"event":"crop_start","total":24}
    {"event":"crop_progress","done":1,"total":24,"path":"..."}
    ...
    {"event":"crop_done"}
    {"event":"preprocess_start","total":24}
    {"event":"preprocess_progress","done":1,"total":24}
    ...
    {"event":"preprocess_done"}
    {"event":"train_start","total_steps":3000,"estimated_wall_s":7200}
    {"event":"train_progress","step":250,"loss":0.512,"eta_s":6300}
    ...
    {"event":"train_done","checkpoint":"/path/to/lora.safetensors"}
    {"event":"sidecar_written","path":"/path/to/lora.safetensors.json"}
    {"event":"done"}

On any failure:

    {"event":"error","stage":"preprocess","message":"..."}
    -> exit code 1

------------------------------------------------------------------------
Spec JSON schema (everything except `advanced` is required):

    {
      "job_id": "char_20260511_134552",
      "trigger": "mrbz07",
      "preset": "quick" | "medium" | "high",
      "images_dir": ".../state/train_character/<job_id>/images",
      "output_lora_path": ".../mlx_models/loras_local/<job_id>/<job_id>.safetensors",
      "advanced": {
        "rank": null | int,
        "alpha": null | int,
        "steps": null | int,
        "lr": null | float,
        "resolution": null | int,
        "caption_strategy": null | "class_word" | "trigger_only" | "auto_caption",
        "crop_strategy": null | "center"   // "face_centered" not yet implemented
      }
    }

Null fields fall back to the preset defaults below.

------------------------------------------------------------------------
Presets (validated recipes — do not invent new ones):

    | Field             | quick      | medium     | high       |
    |-------------------|------------|------------|------------|
    | rank              | 16         | 32         | 32         |
    | alpha             | 16         | 32         | 32         |
    | steps             | 1500       | 3000       | 5000       |
    | lr                | 1.0e-4     | 1.0e-4     | 1.0e-4     |
    | resolution        | 576        | 576        | 576        |
    | caption_strategy  | class_word | class_word | class_word |
    | target_modules    | to_q/k/v/out (all)                  |

------------------------------------------------------------------------
Wall-time estimator (panel must mirror this byte-for-byte):

    base_step_s   = 2.4                  # M4 Max 64 GB, 576x576 rank 32
    res_scale     = (res / 576.0) ** 2   # per-step time ~ tokens ~ res^2
    rank_scale    = 0.95 if rank <= 16 else 1.0
    per_step_s    = base_step_s * res_scale * rank_scale

    preprocess_s  = 90 + 2.0 * image_count   # model load + VAE encode + Gemma
    train_s       = steps * per_step_s
    total_s       = preprocess_s + train_s
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Presets + estimator (both must stay in lockstep with the panel JS mirror)
# ----------------------------------------------------------------------

PRESETS: dict[str, dict[str, Any]] = {
    "quick": {
        "rank": 16, "alpha": 16, "steps": 1500, "lr": 1.0e-4,
        "resolution": 576, "caption_strategy": "class_word",
    },
    "medium": {
        "rank": 32, "alpha": 32, "steps": 3000, "lr": 1.0e-4,
        "resolution": 576, "caption_strategy": "class_word",
    },
    "high": {
        "rank": 32, "alpha": 32, "steps": 5000, "lr": 1.0e-4,
        "resolution": 576, "caption_strategy": "class_word",
    },
}

TARGET_MODULES = ["to_q", "to_k", "to_v", "to_out"]

# Estimator constants — keep in sync with panel mirror.
_BASE_STEP_S = 2.4            # M4 Max 64 GB, 576x576 rank 32
_PREPROCESS_FIXED_S = 90.0    # model load + Gemma init
_PREPROCESS_PER_IMAGE_S = 2.0 # VAE encode per image


def _normalize_target_modules(value: Any) -> list[str]:
    """Accept panel-supplied LoRA target modules while keeping a safe allowlist."""
    if value is None:
        return list(TARGET_MODULES)
    if isinstance(value, str):
        raw = [x.strip() for x in value.split(",") if x.strip()]
    elif isinstance(value, (list, tuple)):
        raw = [str(x).strip() for x in value if str(x).strip()]
    else:
        raw = []
    allowed = set(TARGET_MODULES)
    mods = [m for m in raw if m in allowed]
    return mods or list(TARGET_MODULES)


def resolve_preset(preset: str, advanced: dict[str, Any] | None) -> dict[str, Any]:
    """Merge a preset with optional `advanced` overrides. None values fall through."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; expected one of {list(PRESETS)}")
    cfg = dict(PRESETS[preset])
    if advanced:
        for k, v in advanced.items():
            if v is not None and k in cfg:
                cfg[k] = v
        if advanced.get("target_modules") is not None:
            cfg["target_modules"] = _normalize_target_modules(advanced.get("target_modules"))
    if "target_modules" not in cfg:
        cfg["target_modules"] = list(TARGET_MODULES)
    return cfg


def _wh_from_advanced(cfg: dict[str, Any]) -> tuple[int, int]:
    """Resolve (width, height) from a cfg dict.

    Backwards-compat: if `width` and `height` aren't both present, falls
    back to a square `resolution × resolution` canvas (the legacy v2
    behavior). The 2026-05-20 widescreen-training change adds independent
    W/H so character LoRAs can train at the same aspect as inference
    (1024×576 widescreen) without baking square + letterbox-bar artifacts
    into the weights.
    """
    w = cfg.get("width")
    h = cfg.get("height")
    if w and h:
        return int(w), int(h)
    res = int(cfg.get("resolution") or 576)
    return res, res


def estimate_wall_seconds(image_count: int, preset: str, advanced: dict[str, Any] | None) -> float:
    """Estimate end-to-end wall seconds for a job.

    Formula (panel JS must mirror this exactly):
        per_step_s   = 2.4 * (W*H / 576^2) * (0.95 if rank <= 16 else 1.0)
        preprocess_s = 90 + 2.0 * image_count
        total_s      = preprocess_s + steps * per_step_s

    Pre-2026-05-20 this used (res/576)^2 assuming square training.
    Widescreen training (e.g. 1024×576) needs the token-budget scale
    expressed as the actual pixel ratio to match per-step compute.
    """
    cfg = resolve_preset(preset, advanced)
    w, h = _wh_from_advanced({**cfg, **(advanced or {})})
    rank = int(cfg["rank"])
    steps = int(cfg["steps"])
    pixel_scale = (w * h) / (576.0 * 576.0)
    rank_scale = 0.95 if rank <= 16 else 1.0
    per_step_s = _BASE_STEP_S * pixel_scale * rank_scale
    preprocess_s = _PREPROCESS_FIXED_S + _PREPROCESS_PER_IMAGE_S * image_count
    return preprocess_s + steps * per_step_s


# ----------------------------------------------------------------------
# Stdout JSON-lines emitter
# ----------------------------------------------------------------------

# Capture the ORIGINAL stdout at import time. The training stage temporarily
# swaps sys.stdout to silence the trainer's prints; emit() must keep writing
# to the real panel-facing stream regardless of those swaps.
_REAL_STDOUT = sys.stdout


def emit(event: str, **kwargs: Any) -> None:
    """Write one JSON event line to the real stdout, flushed."""
    obj = {"event": event, **kwargs}
    _REAL_STDOUT.write(json.dumps(obj, separators=(",", ":")) + "\n")
    _REAL_STDOUT.flush()


def emit_error_and_exit(stage: str, message: str, code: int = 1) -> None:
    emit("error", stage=stage, message=message)
    sys.exit(code)


# ----------------------------------------------------------------------
# Spec validation
# ----------------------------------------------------------------------

_REQUIRED_SPEC_KEYS = ("job_id", "trigger", "preset", "images_dir", "output_lora_path")


def load_spec(spec_path: Path) -> dict[str, Any]:
    if not spec_path.exists():
        raise FileNotFoundError(f"spec not found: {spec_path}")
    spec = json.loads(spec_path.read_text())

    # ---- compat shim: Phosphene's panel emits a flat-spec shape with
    # different key names than this CLI's canonical schema. Normalize
    # without losing data so both shapes work end-to-end.
    #
    # Panel writes:        CLI canonical:
    #   output_path          → output_lora_path
    #   rank/steps/lr/...    → advanced.{rank,steps,lr,...}  (flat → nested)
    #   caption_strategy:    → caption_strategy in {class_word, trigger_only,
    #     "trigger_simple"        auto_caption}; "trigger_simple" was the
    #                             panel agent's name for class_word.
    if "output_lora_path" not in spec and "output_path" in spec:
        spec["output_lora_path"] = spec["output_path"]
    if not isinstance(spec.get("advanced"), dict):
        spec["advanced"] = {}
    for k in ("rank", "alpha", "steps", "lr", "resolution",
              "width", "height",
              "target_modules", "caption_strategy", "crop_strategy"):
        if k in spec and k not in spec["advanced"]:
            spec["advanced"][k] = spec[k]
    cs = spec["advanced"].get("caption_strategy")
    if cs == "trigger_simple":
        # Panel-side alias; means "trigger word + a tiny framing hint",
        # which is what class_word does (e.g. "mychar man, close-up portrait").
        spec["advanced"]["caption_strategy"] = "class_word"

    for k in _REQUIRED_SPEC_KEYS:
        if k not in spec:
            raise ValueError(f"spec missing required key: {k!r}")
    return spec


# ----------------------------------------------------------------------
# Cropping + caption authoring
# ----------------------------------------------------------------------

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def list_source_images(images_dir: Path) -> list[Path]:
    """Sorted list of source image files. Sort by filename to be deterministic."""
    if not images_dir.exists():
        raise FileNotFoundError(f"images dir does not exist: {images_dir}")
    files = sorted(
        f for f in images_dir.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not files:
        raise ValueError(f"no images found in {images_dir}")
    return files


def _framing_for_image(width: int, height: int) -> str:
    return "close-up portrait" if height > width else "medium shot"


def _make_caption(trigger: str, strategy: str, width: int, height: int) -> str:
    # Emit LTX's canonical `[VISUAL]: ...\n[TEXT]: None\n` format so the
    # fallback rows match what caption_with_gemma.py writes for the auto-
    # caption path. Mixed-format datasets (some rows naked `<trigger> man`,
    # others `[VISUAL]:...`) caused identity drift on the fallback images
    # because the trainer's tokenizer saw two distinct prompt styles for
    # the same character. See ~/.claude/skills/ltx-lora/SKILL.md for the
    # validated v2 recipe.
    if strategy == "trigger_only":
        body = "a photo"
    else:
        # class_word (default) and auto_caption fallback
        body = _framing_for_image(width, height)
    return f"[VISUAL]: {trigger}, {body}\n[TEXT]: None\n"


def crop_and_caption(
    source_files: list[Path],
    cropped_dir: Path,
    captions_dir: Path,
    images_renamed_dir: Path,
    trigger: str,
    caption_strategy: str,
    resolution: int | None = None,
    crop_strategy: str = "center",
    *,
    width: int | None = None,
    height: int | None = None,
) -> tuple[list[Path], list[str]]:
    """Scale + (optionally) crop sources to a `(width × height)` canvas,
    rename sequentially, write captions.

    Backwards-compat: callers that pass `resolution=N` get a square N×N
    canvas (legacy v2 behavior). New callers pass `width` + `height`
    independently to produce non-square canvases that match the
    inference aspect (e.g. 1024×576 widescreen).

    `crop_strategy` ∈ {"center", "letterbox"} controls how non-square sources
    fit into the square training canvas:

      "center" (default, legacy behavior):
          Scale so the SHORTER source dim = target, then center-crop the
          longer dim to target. Image fills the square; content on the
          longer dim is clipped. Good for character close-ups where the
          face is centered. Bad for wide shots — long-shot proportions are
          never seen by the model, which is why generated long shots tend
          to look mushy / wrong-scale.

      "letterbox":
          Scale so the LONGER source dim = target, then pad the shorter
          dim with black to fill the square. Image content keeps its
          native aspect ratio inside the square. Model learns to ignore
          the consistent black bars (they're a constant factor across
          training samples). Trade-off: ~30% pixel-budget waste on bars
          for portraits/landscapes, but generated long shots / wide
          shots reproduce real proportions.

    Returns (renamed_image_paths, caption_strings) in matching order.

    Writes:
      images_renamed_dir/char_NNN.png          full-resolution training input (lossless)
      cropped_dir/char_NNN.jpg                 1:1 thumbnail (256px) for the UI gallery
      captions_dir/char_NNN.txt                caption matching each image stem
    """
    from PIL import Image

    cropped_dir.mkdir(parents=True, exist_ok=True)
    captions_dir.mkdir(parents=True, exist_ok=True)
    images_renamed_dir.mkdir(parents=True, exist_ok=True)

    if crop_strategy not in ("center", "letterbox"):
        raise ValueError(
            f"unknown crop_strategy: {crop_strategy!r} "
            f"(want 'center' or 'letterbox')"
        )

    # Resolve target canvas dims. New callers pass width + height
    # explicitly; legacy callers pass resolution (square fallback).
    if width and height:
        target_w, target_h = int(width), int(height)
    elif resolution:
        target_w = target_h = int(resolution)
    else:
        raise ValueError("crop_and_caption requires width+height or resolution")

    emit("crop_start", total=len(source_files), crop_strategy=crop_strategy,
         width=target_w, height=target_h)

    out_paths: list[Path] = []
    out_captions: list[str] = []

    for i, src in enumerate(source_files, start=1):
        try:
            img = Image.open(src).convert("RGB")
            src_w, src_h = img.size

            if crop_strategy == "letterbox":
                # Scale so the source FITS INSIDE (target_w × target_h)
                # without distortion. Pad with black to fill the canvas.
                # On non-square canvases this can produce smaller bars
                # (or even none, when source aspect matches target).
                scale = min(target_w / src_w, target_h / src_h)
                new_w = int(round(src_w * scale))
                new_h = int(round(src_h * scale))
                scaled = img.resize((new_w, new_h), Image.LANCZOS)
                cropped_full = Image.new("RGB", (target_w, target_h), (0, 0, 0))
                paste_x = (target_w - new_w) // 2
                paste_y = (target_h - new_h) // 2
                cropped_full.paste(scaled, (paste_x, paste_y))
            else:
                # "center" — scale-and-center-crop to fill (target_w ×
                # target_h). Mirrors preprocess_images._load_image_as_1frame_tensor:
                # scale so the SHORTER side hits its target, then crop
                # the longer side. On widescreen targets this is the
                # right call — for portrait sources it center-crops
                # vertically (head usually survives unless the source
                # is unusually tall).
                scale = max(target_w / src_w, target_h / src_h)
                new_w = int(round(src_w * scale))
                new_h = int(round(src_h * scale))
                scaled = img.resize((new_w, new_h), Image.LANCZOS)
                left = (new_w - target_w) // 2
                top = (new_h - target_h) // 2
                cropped_full = scaled.crop((left, top, left + target_w, top + target_h))

            stem = f"char_{i:03d}"
            png_path = images_renamed_dir / f"{stem}.png"
            cropped_full.save(png_path, format="PNG")

            # Thumbnail for the UI gallery (256x256, jpg, compact).
            thumb = cropped_full.copy()
            thumb.thumbnail((256, 256), Image.LANCZOS)
            thumb_path = cropped_dir / f"{stem}.jpg"
            thumb.save(thumb_path, format="JPEG", quality=88)

            # Honour user-provided captions written to captions_dir by the
            # panel (filename matches the source image stem OR the renamed
            # `char_NNN` stem the panel pre-mapped to). This lets the Train
            # tab ship industry-standard `image_001.png` + `image_001.txt`
            # pairs in LTX `[VISUAL]:` / `[TEXT]:` format without the
            # trainer stomping them with the dumbest-strongest fallback.
            user_cap_paths = [
                captions_dir / f"{stem}.txt",                  # post-rename match
                captions_dir / f"{src.stem}.txt",              # original-stem match
            ]
            user_cap = next((p for p in user_cap_paths if p.is_file()), None)
            if user_cap is not None:
                caption = user_cap.read_text(encoding="utf-8", errors="replace")
                # Re-write at the renamed stem so the preprocess step finds it.
                (captions_dir / f"{stem}.txt").write_text(caption, encoding="utf-8")
            else:
                caption = _make_caption(trigger, caption_strategy, src_w, src_h)
                (captions_dir / f"{stem}.txt").write_text(caption)

            out_paths.append(png_path)
            out_captions.append(caption)

            emit("crop_progress", done=i, total=len(source_files), path=str(png_path))
        except Exception as exc:  # noqa: BLE001
            # One unreadable / corrupt image must not kill the whole pass.
            # Mirrors preprocess_images.preprocess_images_to_latents which
            # logs+continues on per-image encode failure (preprocess_images.py
            # :174-176). Re-raising used to fail the entire crop stage, so
            # users with one bad JPEG in a 30-image dataset lost the whole run.
            logger.warning(
                "failed to crop %s: %s — skipping this image", src.name, exc
            )
            emit(
                "warning",
                stage="crop",
                message=f"skipped {src.name}: {exc}",
                file=src.name,
            )
            continue

    emit("crop_done")
    return out_paths, out_captions


# ----------------------------------------------------------------------
# Preprocess (call the existing module — same env, same VAE encode + Gemma)
# ----------------------------------------------------------------------

def run_preprocess(
    images_dir: Path,
    captions_dir: Path,
    output_root: Path,
    resolution: int | None = None,
    total: int = 0,
    *,
    width: int | None = None,
    height: int | None = None,
) -> None:
    """Encode images + captions into the layout PrecomputedDataset expects.

    Calls preprocess_images.preprocess_images() in-process so progress lines
    can be intermixed cleanly. We emit a coarse start/end pair; the inner
    preprocessor logs its own prints to stderr (we route them away from
    stdout to keep the JSON-lines stream pure).

    Resolution params: pass `width` and `height` for non-square training
    (1024×576 widescreen etc.), or pass `resolution=N` for the legacy
    square N×N canvas. The preprocessor already exposes target_height
    and target_width independently so this is a contract change only.
    """
    from lora_lab import preprocess_images as pp  # local import — heavy deps

    if width and height:
        target_w, target_h = int(width), int(height)
    elif resolution:
        target_w = target_h = int(resolution)
    else:
        raise ValueError("run_preprocess requires width+height or resolution")

    emit("preprocess_start", total=total, width=target_w, height=target_h)

    # Pipe inner module's stdout into stderr so JSON-lines on our stdout
    # stay clean. The preprocessor uses bare print(); duplicate fd2.
    orig_stdout = sys.stdout
    try:
        sys.stdout = sys.stderr  # redirect prints inside the call
        pp.preprocess_images(
            images_dir=str(images_dir),
            output_dir=str(output_root),
            # Pass the locally-resolved Gemma path explicitly. Without
            # this the preprocessor falls back to the HF repo id and
            # may duplicate-download ~6 GB to HF_HOME (or fail offline).
            gemma_model_id=DEFAULT_TEXT_ENCODER,
            target_height=target_h,
            target_width=target_w,
            captions_dir=str(captions_dir),
            caption_ext=".txt",
        )
    finally:
        sys.stdout = orig_stdout

    # The inner module already wrote one latent_NNNN.safetensors per image
    # into output_root/.precomputed/latents/. Verify count.
    latents_dir = output_root / ".precomputed" / "latents"
    written = sorted(latents_dir.glob("latent_*.safetensors"))
    if len(written) != total:
        raise RuntimeError(
            f"preprocess wrote {len(written)} latents; expected {total}"
        )
    emit("preprocess_progress", done=total, total=total)
    emit("preprocess_done")


# ----------------------------------------------------------------------
# Training (build a temp YAML, hand to the existing trainer wrapper logic)
# ----------------------------------------------------------------------

# Resolved dynamically — see lora_lab/__init__.py. The previous hard-
# coded HF cache snapshot path was correct for the maintainer's machine
# only; clean Pinokio installs have the model at mlx_models/ltx-2.3-mlx-q4/
# inside the panel install dir, and ``LTX_MODELS_DIR`` (set by the panel
# when it spawns this script) points the resolver there.
from lora_lab import resolve_default_model_dir, resolve_default_text_encoder
DEFAULT_MODEL_PATH = resolve_default_model_dir()
DEFAULT_TEXT_ENCODER = resolve_default_text_encoder()


def build_trainer_config(
    *,
    cfg: dict[str, Any],
    data_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Compose the LtxTrainerConfig dict from a resolved preset + paths."""
    return {
        "model": {
            "model_path": DEFAULT_MODEL_PATH,
            "text_encoder_path": DEFAULT_TEXT_ENCODER,
            "training_mode": "lora",
        },
        "lora": {
            "rank": int(cfg["rank"]),
            "alpha": int(cfg["alpha"]),
            "dropout": 0.0,
            "target_modules": _normalize_target_modules(cfg.get("target_modules")),
        },
        "optimization": {
            "learning_rate": float(cfg["lr"]),
            "steps": int(cfg["steps"]),
            "batch_size": 1,
            "gradient_accumulation_steps": 1,
            "max_grad_norm": 1.0,
            "weight_decay": 0.0,
            "scheduler_type": "constant",
        },
        "data": {
            "preprocessed_data_root": str(data_root),
        },
        "training_strategy": {
            "name": "text_to_video",
            "generate_audio": False,
        },
        "flow_matching": {
            "timestep_sampling_mode": "shifted_logit_normal",
        },
        "validation": {
            "prompts": [],
            "interval": None,
            "skip_initial_validation": True,
            "generate_audio": False,
        },
        "checkpoints": {
            # Save every ~10% of total steps so the user has fallbacks if a long
            # run is interrupted, but keep at least one mid-run checkpoint.
            "interval": max(1, int(cfg["steps"]) // 5),
            "keep_last_n": 2,
        },
        "seed": 42,
        "output_dir": str(output_dir),
    }


def run_training(
    *,
    cfg: dict[str, Any],
    data_root: Path,
    output_dir: Path,
    estimated_wall_s: float,
) -> tuple[Path, float]:
    """Run the trainer in-process. Returns (final_checkpoint_path, wall_seconds).

    We call the trainer with a step_callback so we can emit per-step progress
    without parsing tqdm output.
    """
    # Late imports — keep CLI start cheap.
    from ltx_trainer_mlx.config import LtxTrainerConfig
    from ltx_trainer_mlx.trainer import LtxvTrainer
    from lora_lab import train as lab_train

    config_dict = build_trainer_config(cfg=cfg, data_root=data_root, output_dir=output_dir)
    config = LtxTrainerConfig.model_validate(config_dict)

    # Apply the same patches train.py uses: dev transformer + image-only safety
    # + fps→frame_rate kwarg shim + audio-attn exclusion. These all run as
    # cheap monkey-patches before LtxvTrainer is instantiated.
    lab_train._patch_loader_prefer_dev_transformer()
    lab_train._patch_strategy_for_image_only()
    lab_train._patch_compute_video_positions_fps_kwarg()
    lab_train._patch_lora_target_exclude_audio()

    # Pin Python/numpy RNG before LtxvTrainer touches them. The upstream
    # trainer seeds `mx.random` with `cfg.seed=42` (see ltx_trainer_mlx/
    # trainer.py:114) but the dataset shuffle at trainer.py:978 uses
    # Python's stdlib `random.shuffle(indices)` which is NEVER seeded by
    # the trainer. Result: with batch=1 over 5000 steps, two identical-
    # recipe runs traverse the dataset in different orders and land at
    # different minima — exactly the variance Mr Bizarro saw between the
    # OLD chartest_v2 (good) and the 2026-05-18 retrain (worse). Pinning
    # stdlib + numpy RNG here closes the biggest reproducibility hole.
    # (MLX/Metal matmul nondeterminism still remains as a smaller second-
    # order source — documented, unfixable today.)
    import random as _stdlib_random
    import numpy as _np
    _seed = int(cfg.get("seed", 42))
    _stdlib_random.seed(_seed)
    _np.random.seed(_seed)

    total_steps = int(cfg["steps"])
    emit("train_start", total_steps=total_steps, estimated_wall_s=int(estimated_wall_s))

    train_start = time.time()
    last_emit_step = 0
    # Roughly every ~2% of total steps, with bounds [10, 250].
    emit_every = max(10, min(250, total_steps // 50 or 10))
    last_loss: float | None = None

    # Sniff per-step loss without forking the trainer: monkey-patch
    # TrainingProgress.update_training. It always gets called (even with
    # disable_progress_bars=True the method runs and just no-ops the UI).
    import ltx_trainer_mlx.progress as tp_mod  # type: ignore

    _orig_update = tp_mod.TrainingProgress.update_training

    def _sniff_update(self, *, loss, lr, step_time, advance=True):  # noqa: ANN001
        nonlocal last_loss
        try:
            last_loss = float(loss)
        except Exception:  # noqa: BLE001
            pass
        return _orig_update(self, loss=loss, lr=lr, step_time=step_time, advance=advance)

    tp_mod.TrainingProgress.update_training = _sniff_update

    def _cb(step: int, total: int, _videos: list[Path]) -> None:
        nonlocal last_emit_step
        if step - last_emit_step < emit_every and step != total:
            return
        last_emit_step = step
        elapsed = time.time() - train_start
        # Defensive: avoid div-by-zero on step 0.
        eta = 0
        if step > 0:
            per_step = elapsed / step
            eta = max(0, int(per_step * (total - step)))
        loss_val = round(last_loss, 4) if last_loss is not None else None
        emit(
            "train_progress",
            step=step,
            loss=loss_val,
            eta_s=eta,
        )
        # Panel-side alias: mlx_ltx_panel.py parses {"event":"step"} with
        # {step, total, loss} fields and renders the progress bar from it.
        emit("step", step=step, total=total, loss=loss_val, eta_s=eta)

    # Silence the trainer's own logging to stdout — only our JSON-lines should
    # reach stdout. Send everything else to stderr so it's still capturable.
    for noisy in (
        "ltx_trainer_mlx",
        "ltx_trainer_mlx.trainer",
        "ltx_core_mlx",
    ):
        logging.getLogger(noisy).propagate = False

    # Redirect generic prints from the trainer to stderr.
    orig_stdout = sys.stdout
    try:
        sys.stdout = sys.stderr
        trainer = LtxvTrainer(config)
        saved_path, _stats = trainer.train(disable_progress_bars=True, step_callback=_cb)
    finally:
        sys.stdout = orig_stdout

    train_wall = time.time() - train_start
    emit("train_done", checkpoint=str(saved_path))
    # Panel-side alias for the same milestone.
    emit("checkpoint", path=str(saved_path))
    return Path(saved_path), train_wall


# ----------------------------------------------------------------------
# Sidecar
# ----------------------------------------------------------------------

def write_sidecar(
    *,
    lora_path: Path,
    cfg: dict[str, Any],
    trigger: str,
    image_count: int,
    training_wall_s: float,
) -> Path:
    sidecar_path = lora_path.with_suffix(lora_path.suffix + ".json")
    payload = {
        "trigger": trigger,
        "preset": cfg.get("preset_name"),
        "rank": int(cfg["rank"]),
        "alpha": int(cfg["alpha"]),
        "steps": int(cfg["steps"]),
        "lr": float(cfg["lr"]),
        # Legacy `resolution` field — kept for backwards-compat with
        # consumers that still read it. Set to width when widescreen,
        # to the single square value when legacy.
        "resolution": int(cfg.get("width") or cfg.get("resolution") or 576),
        "width": int(cfg.get("width") or cfg.get("resolution") or 576),
        "height": int(cfg.get("height") or cfg.get("resolution") or 576),
        "image_count": image_count,
        "caption_strategy": cfg["caption_strategy"],
        "target_modules": _normalize_target_modules(cfg.get("target_modules")),
        "training_wall_seconds": round(float(training_wall_s), 1),
        "created_at": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_model": "Lightricks/LTX-2.3 (dgrauet/ltx-2.3-mlx-q4 dev transformer)",
        "training_resolution": [
            int(cfg.get("width") or cfg.get("resolution") or 576),
            int(cfg.get("height") or cfg.get("resolution") or 576),
        ],
        "lora_lab_version": "iter5",
        "loadable_via": "ltx_core_mlx.loader.fuse_loras.apply_loras",
    }
    sidecar_path.write_text(json.dumps(payload, indent=2))
    emit("sidecar_written", path=str(sidecar_path))
    return sidecar_path


# ----------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------

def run_pipeline(spec_path: Path) -> int:
    spec = load_spec(spec_path)
    job_id: str = spec["job_id"]
    trigger: str = spec["trigger"]
    preset: str = spec["preset"]
    images_dir = Path(spec["images_dir"]).resolve()
    output_lora_path = Path(spec["output_lora_path"]).resolve()
    advanced = spec.get("advanced") or {}

    emit("start", job_id=job_id)

    # Resolve preset + overrides.
    cfg = resolve_preset(preset, advanced)
    cfg["preset_name"] = preset
    caption_strategy = cfg["caption_strategy"]
    # The Phosphene panel speaks a slightly different caption_strategy
    # vocabulary than the trainer (historical reasons — panel evolved
    # around CivitAI conventions; trainer around dreambooth). Map the
    # panel names to ours:
    #   * 'trigger_simple' — panel's name for the fallback that writes
    #     `<trigger> man` for every image. Equivalent to our 'class_word'.
    #   * 'user_provided'  — panel asserts every image has a `.txt`
    #     sidecar (verified in /train/start). The crop step above already
    #     honors those files; `caption_strategy` here only governs the
    #     fallback path for images WITHOUT a sidecar, which by definition
    #     won't fire when 'user_provided' is set. Aliasing to 'class_word'
    #     keeps the fallback sensible if the contract is ever broken
    #     (e.g. a sidecar is deleted between /train/start validation and
    #     the crop loop reading it).
    _PANEL_CAPTION_STRATEGY_ALIASES = {
        "trigger_simple": "class_word",
        "user_provided": "class_word",
    }
    if caption_strategy in _PANEL_CAPTION_STRATEGY_ALIASES:
        original = caption_strategy
        caption_strategy = _PANEL_CAPTION_STRATEGY_ALIASES[caption_strategy]
        cfg["caption_strategy"] = caption_strategy
        emit(
            "log",
            line=f"caption_strategy: panel-alias {original!r} → {caption_strategy!r}",
        )
    if caption_strategy == "auto_caption":
        # Deferred — fall back to class_word with a warning.
        emit(
            "warning",
            stage="caption",
            message="auto_caption deferred; falling back to class_word",
        )
        cfg["caption_strategy"] = "class_word"
        caption_strategy = "class_word"
    if caption_strategy not in {"class_word", "trigger_only"}:
        emit_error_and_exit(
            "caption", f"unknown caption_strategy: {caption_strategy!r}"
        )

    crop_strategy = advanced.get("crop_strategy") or "center"
    if crop_strategy not in ("center", "letterbox"):
        emit(
            "warning",
            stage="crop",
            message=f"crop_strategy={crop_strategy!r} not implemented; using center",
        )
        crop_strategy = "center"

    # Resolve target dims (width × height). Spec may provide `width`
    # and `height` independently (widescreen training, 2026-05-20+) OR
    # a legacy `resolution` which is interpreted as a square canvas.
    target_w, target_h = _wh_from_advanced(cfg)
    if target_w % 32 != 0 or target_h % 32 != 0:
        emit_error_and_exit(
            "config",
            f"target dims {target_w}×{target_h} must both be divisible by 32",
        )
    # Stamp resolved dims back onto cfg so downstream sidecar + estimator
    # see consistent values even when only `resolution` was set.
    cfg["width"] = target_w
    cfg["height"] = target_h
    # Legacy single-int — keep for any consumer that still reads it.
    resolution = target_w  # NB: when widescreen, this is just the WIDTH

    # Source images.
    try:
        source_files = list_source_images(images_dir)
    except Exception as exc:  # noqa: BLE001
        emit_error_and_exit("ingest", str(exc))

    image_count = len(source_files)

    estimated_wall_s = estimate_wall_seconds(image_count, preset, advanced)
    emit(
        "plan",
        preset=preset,
        rank=int(cfg["rank"]),
        alpha=int(cfg["alpha"]),
        steps=int(cfg["steps"]),
        lr=float(cfg["lr"]),
        resolution=resolution,
        caption_strategy=caption_strategy,
        estimated_wall_s=int(estimated_wall_s),
        image_count=image_count,
    )

    # Lay out the job workspace alongside the source images. The panel writes
    # spec.json into state/train_character/<job_id>/, and `images_dir` lives
    # at .../<job_id>/images. We park our derived data under the same parent.
    job_root = images_dir.parent
    cropped_dir = job_root / "cropped"
    captions_dir = job_root / "captions"
    images_renamed_dir = job_root / "images_renamed"
    data_root = job_root / "training_data"
    train_out_dir = job_root / "train_output"

    try:
        renamed, _captions = crop_and_caption(
            source_files=source_files,
            cropped_dir=cropped_dir,
            captions_dir=captions_dir,
            images_renamed_dir=images_renamed_dir,
            trigger=trigger,
            caption_strategy=caption_strategy,
            width=target_w,
            height=target_h,
            crop_strategy=crop_strategy,
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        emit_error_and_exit("crop", str(exc))

    try:
        run_preprocess(
            images_dir=images_renamed_dir,
            captions_dir=captions_dir,
            output_root=data_root,
            width=target_w,
            height=target_h,
            total=len(renamed),
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        emit_error_and_exit("preprocess", str(exc))

    try:
        checkpoint_path, training_wall_s = run_training(
            cfg=cfg,
            data_root=data_root,
            output_dir=train_out_dir,
            estimated_wall_s=estimated_wall_s,
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        emit_error_and_exit("train", str(exc))

    # Move the checkpoint to the spec's output path.
    output_lora_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(str(checkpoint_path), str(output_lora_path))
    except Exception as exc:  # noqa: BLE001
        emit_error_and_exit("publish", f"failed to copy checkpoint: {exc}")

    try:
        write_sidecar(
            lora_path=output_lora_path,
            cfg=cfg,
            trigger=trigger,
            image_count=image_count,
            training_wall_s=training_wall_s,
        )
    except Exception as exc:  # noqa: BLE001
        emit_error_and_exit("sidecar", str(exc))

    emit("done")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LoRA-lab Train Character pipeline")
    p.add_argument("spec", nargs="?", help="path to job-spec JSON (positional)")
    p.add_argument("--spec", dest="spec_flag", help="path to job-spec JSON (flag form, used by Phosphene panel)")
    p.add_argument("--job-id", dest="job_id", default=None,
                   help="optional job id label (used by Phosphene panel; the spec JSON's own job_id field is authoritative)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    if args.spec_flag and not args.spec:
        args.spec = args.spec_flag
    if not args.spec:
        p.error("must provide a job-spec JSON path (positional or via --spec)")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        stream=sys.stderr,  # NEVER pollute stdout — JSON-lines protocol
    )

    try:
        return run_pipeline(Path(args.spec))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        emit("error", stage="unknown", message=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
