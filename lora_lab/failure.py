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
