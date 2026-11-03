"""Why a training run stopped, as one word from a closed list (4.19.1).

The fleet only ever saw "training exited with code 1 — see Logs for stack
trace": 4.19.0 had nine of those on two Macs and nothing to tell a memory
failure from a missing file from a full disk. The trainer knows — it holds the
exception — so it names the class here and sends it with its `error` event;
the panel checks it against the same list and sends only the word.

Classified from the exception's TYPE first and only then from MLX's own
fixed message fragments (MLX raises every Metal allocation failure as a plain
RuntimeError). Never from a path, a caption, a trigger or anything else the
user typed: the class is all that may leave the machine.
"""
from __future__ import annotations

import errno
import math
from pathlib import Path

#: The closed vocabulary. docs/ANALYTICS.md, `render_failed.train_error`.
TRAIN_ERROR_CLASSES = ("oom", "missing_file", "caption_check", "disk_full",
                       "import_error", "nan_loss", "other")

# MLX / Metal allocation failures. All of these arrive as RuntimeError text.
_OOM_FRAGMENTS = (
    "[metal::malloc]",
    "insufficient memory",
    "outofmemory",            # kIOGPUCommandBufferCallbackErrorOutOfMemory
    "out of memory",
    "resource limit",         # "[metal::malloc] Resource limit (499000) exceeded"
    "maximum allowed buffer size",
    "unable to allocate",
)
_DISK_FRAGMENTS = ("no space left on device", "disk quota exceeded")

#: Consecutive non-finite losses before a run is stopped as `nan_loss`. One
#: NaN step poisons every LoRA parameter through the optimizer, so every step
#: after it is NaN too; a short streak is enough to be sure and saves hours.
NAN_LOSS_ABORT_STEPS = 10


class NonFiniteLoss(RuntimeError):
    """The loss stopped being a number; the adapter is no longer trainable."""


class CaptionCheckError(RuntimeError):
    """The captions do not carry the trigger the job was started with."""


def _chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def classify_exception(exc: BaseException | None) -> str:
    """One of TRAIN_ERROR_CLASSES for the exception that stopped a run.

    The whole cause chain is read (the dataset loader wraps a missing file in
    a RuntimeError), most specific signal first."""
    if exc is None:
        return "other"
    links = list(_chain(exc))
    for e in links:
        if isinstance(e, NonFiniteLoss):
            return "nan_loss"
        if isinstance(e, CaptionCheckError):
            return "caption_check"
    for e in links:
        if isinstance(e, MemoryError):
            return "oom"
        if isinstance(e, OSError) and e.errno in (errno.ENOSPC, errno.EDQUOT):
            return "disk_full"
    for e in links:
        text = str(e).lower()
        if any(f in text for f in _DISK_FRAGMENTS):
            return "disk_full"
        if any(f in text for f in _OOM_FRAGMENTS):
            return "oom"
    for e in links:
        if isinstance(e, ImportError):
            return "import_error"
        if isinstance(e, FileNotFoundError):
            return "missing_file"
        # huggingface_hub reads a local model folder that does not exist as a
        # malformed repo id: the shape of a missing LTX-2.3 pack before 4.19.1.
        if (type(e).__name__ == "HFValidationError"
                and "repo id must be in the form" in str(e).lower()):
            return "missing_file"
    return "other"


def is_finite_loss(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


#: Minimum size of the full-precision LTX-2.3 dev transformer. Anything smaller
#: is the Q4 repo's quantized copy, which trains a near-zero LoRA (#35).
FULL_DEV_MIN_BYTES = 15 * 1000**3

#: The stages the trainers report an `error` event from (train_character,
#: train_audio). Closed, like TRAIN_ERROR_CLASSES: the panel forwards the
#: stage word to analytics, never anything outside this list.
TRAIN_STAGES = ("models", "caption", "config", "ingest", "crop", "preprocess",
                "train", "publish", "sidecar", "validate", "verify", "unknown")


def dev_transformer_candidates(model_dir: str | Path) -> tuple[Path, Path]:
    """The two places the trainer loads the dev transformer from, in order.
    The trainer reads nothing else (lora_lab.train's loader override), so a
    check that accepts any other place (a Hugging Face cache) says "ready"
    for a run that cannot start."""
    model_dir = Path(model_dir)
    return (model_dir / "transformer-dev.safetensors",
            model_dir.parent / "ltx-2.3-mlx-q8" / "transformer-dev.safetensors")


def training_weights_problem(model_dir: str | Path) -> str | None:
    """What is missing for a training run, in one sentence, or None (4.19.1).

    Checked before the crop and the caption encode. Without it a missing
    LTX-2.3 base pack died ~10 s in inside the preprocessor's Hugging Face
    lookup ("Repo id must be in the form 'repo_name'..."), and a missing
    full-precision dev transformer died only after minutes of caption and
    image encoding - both as a bare "exited with code 1". The Train tab shows
    a download banner for these, but Start was never blocked by it.
    Same locations and size floor as lora_lab.train's loader override; the
    panel asks this before it queues a run (train_support.train_weights_refusal)."""
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        return (f"the LTX-2.3 base pack is not downloaded (no {model_dir.name}/ in the models "
                "folder). Open the Train tab and click Download all, then start again.")
    for cand in dev_transformer_candidates(model_dir):
        try:
            if cand.is_file() and cand.stat().st_size >= FULL_DEV_MIN_BYTES:
                return None
        except OSError:
            continue
    return ("the full-precision LTX-2.3 dev transformer (~21 GB) is not downloaded, or "
            "only partly. Open the Train tab and click Download all, then start again.")
