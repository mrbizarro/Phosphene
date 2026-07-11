"""Single-shot CLI: run the audio-side Character LoRA pipeline from a spec JSON.

This is the audio-side companion to :mod:`lora_lab.train_character`. Where the
character module owns face/image training, this module owns voice training:
slice a clip into VAE audio latents, build a small LoRA on the audio attention
+ feed-forward paths, and drop the final ``.safetensors`` at the path the
Phosphene panel requested.

The panel writes a spec JSON and invokes this module as a subprocess:

    ./scripts/run.sh python -m lora_lab.train_audio \\
        --spec /abs/path/to/spec.json --job-id <id>

The CLI streams JSON-lines progress to stdout (one JSON object per line) so
the panel can tail it.

------------------------------------------------------------------------
Stdout protocol (one JSON object per line; ordered, append-only):

    {"event":"start","job_id":"..."}
    {"event":"phase","phase":"audio","label":"Training voice LoRA"}
    {"event":"plan","rank":16,"alpha":16,"steps":250,"lr":0.0001,
     "slice_seconds":4.0,"image_count":17}
    {"event":"preprocess_start"}
    {"event":"log","msg":"..."}
    ...
    {"event":"preprocess_done"}
    {"event":"train_start","total_steps":250}
    {"event":"step","phase":"audio","step":50,"total":250,"loss":0.41,"eta_s":...}
    ...
    {"event":"checkpoint","path":"/path/to/lora.safetensors"}
    {"event":"done","path":"/abs/path/to/<trigger>.audio.safetensors"}

On any failure:

    {"event":"error","stage":"preprocess","message":"..."}
    -> exit code 1 (or 2 for input-validation errors)

------------------------------------------------------------------------
Spec JSON schema (everything is required):

    {
      "schema": "phosphene/train_audio@1",
      "job_id": "char_20260515_120000_ab12",
      "trigger": "newchartrn",
      "audio_path": "/abs/path/to/voice.wav",
      "dataset_dir": "/abs/path/to/state/train_character/<job_id>",
      "image_count": 17,
      "audio_steps": 250,
      "audio_rank": 16,
      "audio_lr": 1.0e-4,
      "audio_slice_seconds": 4.0,
      "output_path": "/abs/path/to/mlx_models/loras/<trigger>.audio.safetensors"
    }

The face training pipeline (``train_character``) MUST have run first, so the
image latents at ``<dataset_dir>/.precomputed/latents/`` exist — the audio
preprocessor pairs each audio slice to an image latent by index.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Constants (kept in lockstep with the Phase B audio configs)
# ----------------------------------------------------------------------

# Resolved dynamically — see lora_lab/__init__.py. Vendored installs
# resolve to phosphene-dev.git/mlx_models/ltx-2.3-mlx-q4; the authoring
# tree falls back to the HF hub cache. ``LTX_MODELS_DIR`` env (set by
# the Phosphene panel) takes precedence.
from lora_lab import resolve_default_model_dir, resolve_default_text_encoder
DEFAULT_MODEL_PATH = resolve_default_model_dir()
DEFAULT_TEXT_ENCODER = resolve_default_text_encoder()

# The trainer's ``_find_lora_targets`` substring-matches against module paths
# in LTXModel. These three names cover the audio attention + audio FF paths,
# leaving the (face-LoRA-owned) video attention untouched.
AUDIO_TARGET_MODULES = ["audio_attn1", "audio_attn2", "audio_ff"]

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a"}

# Reasonable guardrails — fail loud if the panel hands us nonsense.
_MIN_AUDIO_STEPS = 50
_MAX_AUDIO_STEPS = 2000


# ----------------------------------------------------------------------
# Stdout JSON-lines emitter (mirrors train_character.emit so the panel
# parses both pipelines uniformly).
# ----------------------------------------------------------------------

# Capture the ORIGINAL stdout at import time. Training stages temporarily swap
# sys.stdout to silence the trainer's prints; emit() must keep writing to the
# real panel-facing stream regardless of those swaps.
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

_REQUIRED_SPEC_KEYS = (
    "job_id",
    "trigger",
    "audio_path",
    "dataset_dir",
    "image_count",
    "audio_steps",
    "audio_rank",
    "audio_lr",
    "audio_slice_seconds",
    "output_path",
)

_EXPECTED_SCHEMA = "phosphene/train_audio@1"


def load_spec(spec_path: Path) -> dict[str, Any]:
    if not spec_path.exists():
        raise FileNotFoundError(f"spec not found: {spec_path}")
    spec = json.loads(spec_path.read_text())

    # Schema check is soft — log a warning if missing/unexpected but don't
    # bail out, so an older panel build can still drive this CLI.
    schema = spec.get("schema")
    if schema and schema != _EXPECTED_SCHEMA:
        logger.warning(
            "spec.schema=%r does not match expected %r — continuing",
            schema, _EXPECTED_SCHEMA,
        )

    for k in _REQUIRED_SPEC_KEYS:
        if k not in spec:
            raise ValueError(f"spec missing required key: {k!r}")
    return spec


def validate_inputs(spec: dict[str, Any]) -> None:
    """Fail loud + early on bad inputs (exit code 2 for validation errors)."""
    audio_path = Path(spec["audio_path"])
    if not audio_path.exists():
        emit_error_and_exit(
            "validate",
            f"audio file not found: {audio_path}",
            code=2,
        )
    if audio_path.suffix.lower() not in AUDIO_EXTENSIONS:
        emit_error_and_exit(
            "validate",
            f"unsupported audio format {audio_path.suffix!r}; "
            f"expected one of {sorted(AUDIO_EXTENSIONS)}",
            code=2,
        )

    dataset_dir = Path(spec["dataset_dir"])
    # Face training (train_character.py) copies images into a `training_data/`
    # subdir before preprocessing, so the image latents land at
    # `<job_dir>/training_data/.precomputed/latents/`, not directly under the
    # job dir. Detect both layouts and re-point `spec["dataset_dir"]` at the
    # effective root so every downstream step (preprocess, config, train) sees
    # the right path.
    candidate_a = dataset_dir / ".precomputed" / "latents"
    candidate_b = dataset_dir / "training_data" / ".precomputed" / "latents"
    if candidate_b.exists():
        image_latents_dir = candidate_b
        spec["dataset_dir"] = str(dataset_dir / "training_data")
    elif candidate_a.exists():
        image_latents_dir = candidate_a
    else:
        emit_error_and_exit(
            "validate",
            f"image latents not found at {candidate_a} or {candidate_b}. "
            f"Face training (train_character) must run first to produce image "
            f"latents — audio preprocessing pairs each slice to an image latent.",
            code=2,
        )
    n_image_latents = sum(1 for _ in image_latents_dir.glob("latent_*.safetensors"))
    if n_image_latents == 0:
        emit_error_and_exit(
            "validate",
            f"no image latents found in {image_latents_dir} (directory empty). "
            f"Re-run face training before training the audio LoRA.",
            code=2,
        )

    try:
        steps = int(spec["audio_steps"])
    except (TypeError, ValueError):
        emit_error_and_exit("validate", f"audio_steps must be int, got {spec['audio_steps']!r}", code=2)
    if not (_MIN_AUDIO_STEPS <= steps <= _MAX_AUDIO_STEPS):
        emit_error_and_exit(
            "validate",
            f"audio_steps={steps} outside allowed range [{_MIN_AUDIO_STEPS}, {_MAX_AUDIO_STEPS}]",
            code=2,
        )


# ----------------------------------------------------------------------
# Phase 2: preprocess (call the existing module — same env, same audio VAE)
# ----------------------------------------------------------------------

def run_preprocess(
    *,
    audio_path: Path,
    dataset_dir: Path,
    slice_seconds: float,
) -> int:
    """Slice the WAV and encode VAE audio latents into ``<dataset_dir>/.precomputed/audio_latents/``.

    Always uses ``match_image_count=True`` so the audio latent count equals the
    image latent count (the trainer iterates paired image + audio latents by
    index). Returns the number of audio latents written.
    """
    from lora_lab import preprocess_audio as pa  # local — heavy deps

    emit("preprocess_start")

    # Pipe the inner module's bare ``print()`` calls into stderr so our
    # JSON-lines stream on stdout stays clean.
    orig_stdout = sys.stdout
    try:
        sys.stdout = sys.stderr
        pa.preprocess_audio(
            audio_path=str(audio_path),
            output_dir=str(dataset_dir),
            slice_seconds=float(slice_seconds),
            match_image_count=True,
        )
    finally:
        sys.stdout = orig_stdout

    audio_latents_dir = dataset_dir / ".precomputed" / "audio_latents"
    written = sorted(audio_latents_dir.glob("latent_*.safetensors"))
    if not written:
        raise RuntimeError(
            f"preprocess wrote no audio latents into {audio_latents_dir}"
        )
    emit("preprocess_done")
    emit("log", msg=f"wrote {len(written)} audio latents to {audio_latents_dir}")
    return len(written)


# ----------------------------------------------------------------------
# Phase 3: build the audio YAML config (template = char_audio_lora_bizarro.yaml)
# ----------------------------------------------------------------------

def build_audio_config(
    *,
    rank: int,
    steps: int,
    lr: float,
    dataset_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Compose the audio LoRA YAML config dict.

    Mirrors ``configs/char_audio_lora_bizarro.yaml``:
      - target_modules = audio_attn1/2 + audio_ff (NO video targets)
      - rank == alpha (per Codex's tiny-adapter recipe)
      - generate_audio: true (enables audio loss + reads audio_latents/)
      - validation.generate_audio: false (don't vocode val samples — saves mem)
    """
    return {
        "model": {
            "model_path": DEFAULT_MODEL_PATH,
            "text_encoder_path": DEFAULT_TEXT_ENCODER,
            "training_mode": "lora",
        },
        "lora": {
            "rank": int(rank),
            "alpha": int(rank),
            "dropout": 0.0,
            "target_modules": list(AUDIO_TARGET_MODULES),
        },
        "optimization": {
            "learning_rate": float(lr),
            "steps": int(steps),
            "batch_size": 1,
            "gradient_accumulation_steps": 1,
            "max_grad_norm": 1.0,
            "weight_decay": 0.0,
            "scheduler_type": "constant",
        },
        "data": {
            "preprocessed_data_root": str(dataset_dir),
        },
        "training_strategy": {
            "name": "text_to_video",
            "generate_audio": True,   # the knob: enables audio loss + audio_latents/
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
            # Save every ~10% of total steps so the user has fallbacks if a
            # long run is interrupted, but keep at least one mid-run checkpoint.
            "interval": max(1, int(steps) // 5),
            "keep_last_n": 2,
        },
        "seed": 42,
        "output_dir": str(output_dir),
    }


def write_audio_yaml(config_dict: dict[str, Any], yaml_path: Path) -> None:
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(yaml.safe_dump(config_dict, sort_keys=False))


# ----------------------------------------------------------------------
# Phase 4: train (in-process, same patch set as train.py / train_character.py)
# ----------------------------------------------------------------------

def run_training(
    *,
    config_dict: dict[str, Any],
    output_dir: Path,
) -> tuple[Path, float]:
    """Run the trainer in-process. Returns (final_checkpoint_path, wall_seconds).

    Forwards per-step events to stdout under ``event=step`` with
    ``phase=audio`` so the panel can distinguish face-phase from audio-phase
    progress in the same log stream.
    """
    # Late imports — keep CLI start cheap and let validation/preprocess fail
    # before we pay the trainer's import cost.
    from ltx_trainer_mlx.config import LtxTrainerConfig
    from ltx_trainer_mlx.trainer import LtxvTrainer
    from lora_lab import train as lab_train

    config = LtxTrainerConfig.model_validate(config_dict)

    # Same patch set train.py / train_character.py apply: dev transformer +
    # image-only safety + fps→frame_rate kwarg shim + audio-attn exclusion.
    # The audio-attn exclusion filter inside train.py drops audio_* matches —
    # but here we WANT those targets, so we deliberately re-monkey-patch the
    # filter AFTER lab_train's patch to restore the audio paths.
    lab_train._patch_loader_prefer_dev_transformer()
    lab_train._patch_strategy_for_image_only()
    lab_train._patch_compute_video_positions_fps_kwarg()
    _restore_audio_lora_targets()

    total_steps = int(config_dict["optimization"]["steps"])
    emit("train_start", total_steps=total_steps)

    train_start = time.time()
    last_emit_step = 0
    emit_every = max(5, min(50, total_steps // 25 or 5))
    last_loss: float | None = None

    # Sniff per-step loss the same way train_character does — patch
    # TrainingProgress.update_training to record the most recent loss.
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
        eta = 0
        if step > 0:
            per_step = elapsed / step
            eta = max(0, int(per_step * (total - step)))
        loss_val = round(last_loss, 4) if last_loss is not None else None
        # Panel parses {"event":"step"} with {step, total, loss}. Tagging
        # phase=audio lets the panel render an "audio phase" progress bar
        # distinct from the face phase.
        emit("step", phase="audio", step=step, total=total, loss=loss_val, eta_s=eta)

    # Silence the trainer's own logging to stdout — only JSON-lines reach it.
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
    emit("checkpoint", path=str(saved_path))
    return Path(saved_path), train_wall


def _restore_audio_lora_targets() -> None:
    """Undo ``train._patch_lora_target_exclude_audio``.

    The face-side patch drops any LoRA target whose path contains
    ``audio_attn`` or ``audio_ff`` — that's correct for face training (random
    gradients corrupt the audio path). For AUDIO training those are exactly
    the targets we want to keep, so we force-reimport the trainer module
    and restore the original ``_find_lora_targets``.

    A plain ``importlib.reload`` does not always suffice because patched
    references may persist elsewhere in the import graph. We delete every
    cached reference under ``ltx_trainer_mlx`` first, then do a fresh
    ``importlib.reload`` on the root package to cascade into sub-modules.
    """
    import sys
    import importlib

    # Nuke all cached modules under the ltx_trainer_mlx namespace so a
    # subsequent ``reload`` actually re-executes ``trainer.py`` and
    # restores ``_find_lora_targets`` to its original implementation.
    stale = [k for k in sys.modules if k.startswith("ltx_trainer_mlx")]
    for k in stale:
        del sys.modules[k]
    importlib.invalidate_caches()

    import ltx_trainer_mlx.trainer as _trainer
    importlib.reload(_trainer)
    logger.info("audio LoRA: restored upstream _find_lora_targets (audio paths preserved)")


# ----------------------------------------------------------------------
# Phase 5: discover the final checkpoint + copy to output_path
# ----------------------------------------------------------------------

def find_final_checkpoint(output_dir: Path, expected_step: int) -> Path:
    """Locate ``lora_weights_step_<N>.safetensors`` matching the final step.

    The trainer writes checkpoints under ``<output_dir>/checkpoints/``, named
    ``lora_weights_step_NNNNN.safetensors`` (5-digit zero-padded step).
    """
    ckpt_root = output_dir / "checkpoints"
    if not ckpt_root.exists():
        # Some trainer paths nest checkpoints one level deeper; recurse.
        candidates = list(output_dir.rglob("lora_weights_step_*.safetensors"))
    else:
        candidates = list(ckpt_root.glob("lora_weights_step_*.safetensors"))
        if not candidates:
            candidates = list(output_dir.rglob("lora_weights_step_*.safetensors"))

    if not candidates:
        raise FileNotFoundError(
            f"no lora_weights_step_*.safetensors under {output_dir}"
        )

    def _step_of(p: Path) -> int:
        try:
            return int(p.stem.split("step_")[1])
        except (IndexError, ValueError):
            return -1

    # Prefer the checkpoint matching the final step. If it's missing (e.g. a
    # rounding mismatch between checkpoints.interval and total steps), fall
    # back to the maximum step number we found.
    by_step = {_step_of(p): p for p in candidates}
    if expected_step in by_step:
        return by_step[expected_step]
    max_step = max(by_step)
    logger.warning(
        "exact step %d checkpoint not found; using max step %d (%s)",
        expected_step, max_step, by_step[max_step].name,
    )
    return by_step[max_step]


# ----------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------

def run_pipeline(spec_path: Path) -> int:
    spec = load_spec(spec_path)
    job_id: str = spec["job_id"]
    trigger: str = spec["trigger"]

    emit("start", job_id=job_id)
    emit("phase", phase="audio", label="Training voice LoRA")

    # ---- phase 1: validate ----
    validate_inputs(spec)

    audio_path = Path(spec["audio_path"]).resolve()
    dataset_dir = Path(spec["dataset_dir"]).resolve()
    output_path = Path(spec["output_path"]).resolve()
    image_count = int(spec["image_count"])
    audio_steps = int(spec["audio_steps"])
    audio_rank = int(spec["audio_rank"])
    audio_lr = float(spec["audio_lr"])
    slice_seconds = float(spec["audio_slice_seconds"])

    emit(
        "plan",
        trigger=trigger,
        rank=audio_rank,
        alpha=audio_rank,
        steps=audio_steps,
        lr=audio_lr,
        slice_seconds=slice_seconds,
        image_count=image_count,
        target_modules=list(AUDIO_TARGET_MODULES),
    )

    # ---- phase 2: preprocess ----
    try:
        run_preprocess(
            audio_path=audio_path,
            dataset_dir=dataset_dir,
            slice_seconds=slice_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        emit_error_and_exit("preprocess", str(exc))

    # ---- phase 3: generate audio YAML ----
    audio_train_out_dir = dataset_dir / "audio_train_output"
    audio_yaml_path = dataset_dir / "audio_config.yaml"
    try:
        config_dict = build_audio_config(
            rank=audio_rank,
            steps=audio_steps,
            lr=audio_lr,
            dataset_dir=dataset_dir,
            output_dir=audio_train_out_dir,
        )
        write_audio_yaml(config_dict, audio_yaml_path)
        emit("log", msg=f"wrote audio config: {audio_yaml_path}")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        emit_error_and_exit("config", str(exc))

    # ---- phase 4: train ----
    try:
        checkpoint_path, _train_wall = run_training(
            config_dict=config_dict,
            output_dir=audio_train_out_dir,
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        emit_error_and_exit("train", str(exc))

    # ---- phase 5: publish final checkpoint ----
    # trainer.train() already returns the last-saved checkpoint path; double-
    # check by walking the output dir for the exact step number, falling back
    # gracefully if the interval rounding didn't hit the final step exactly.
    try:
        if not checkpoint_path.exists():
            checkpoint_path = find_final_checkpoint(audio_train_out_dir, audio_steps)
    except Exception as exc:  # noqa: BLE001
        emit_error_and_exit("publish", f"failed to locate checkpoint: {exc}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(str(checkpoint_path), str(output_path))
    except Exception as exc:  # noqa: BLE001
        emit_error_and_exit("publish", f"failed to copy checkpoint: {exc}")

    emit("done", path=str(output_path))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LoRA-lab Train Audio pipeline (Phase B)")
    p.add_argument("spec", nargs="?", help="path to job-spec JSON (positional)")
    p.add_argument(
        "--spec", dest="spec_flag",
        help="path to job-spec JSON (flag form, used by Phosphene panel)",
    )
    p.add_argument(
        "--job-id", dest="job_id", default=None,
        help="optional job id label (the spec JSON's own job_id is authoritative)",
    )
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
