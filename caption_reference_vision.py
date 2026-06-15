"""Vision-caption a reference image with the local Gemma 3 12B multimodal
model, for the Ideogram 4 "reference bridge".

Why this exists:
    Ideogram 4 (mflux-generate-ideogram4) is TEXT-TO-IMAGE only — it has
    no image input. To give it pseudo image-to-image, we describe the
    user's reference image in words with the local Gemma 3 vision tower,
    then splice that description into Ideogram's text prompt so Ideogram
    redraws the look. It is a re-interpretation of the reference, never a
    pixel copy.

Why a standalone subprocess (and not the warm helper / caption_with_gemma):
    1. RAM. The Ideogram fp8 DiT render needs ~24 GB. Gemma loaded for
       captioning is ~6 GB RSS. Running the caption in a short-lived
       subprocess means Gemma's memory is fully reclaimed by the OS the
       moment this process exits — BEFORE the heavy Ideogram render
       starts. image_engine._generate_mflux spawns us, reads our caption
       off stdout, and only then launches the Ideogram subprocess.
    2. Lightricks' Gemma wrapper (used by the LTX helper for prompt
       enhancement) and `caption_with_gemma.py` are TEXT-ONLY — they do
       not pass images through the vision tower. We need `mlx_vlm`, which
       drives the `Gemma3ForConditionalGeneration` multimodal path on the
       same Gemma 3 weights.

Output contract (read by image_engine._generate_mflux):
    On success we print a single-line machine-readable marker plus the
    bare caption as the LAST line, e.g.:
        CAPTION_JSON: {"caption": "A weathered ..."}
        A weathered brass diving helmet on a dark studio backdrop, ...
    The caller prefers the CAPTION_JSON line; if that is missing it falls
    back to the last non-empty stdout line. On any failure we write a
    one-line message to stderr and exit non-zero — the caller treats that
    as "caption unavailable" and renders text-only (never crashes).

CLI:
    python caption_reference_vision.py \
        --image <path> [--image <path> ...]   # 1-3 images; first is described
        [--model <mlx_models/gemma-3-12b-it-4bit>]
        [--max-tokens 160]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import sys


# The instruction Gemma sees. We want a tight, plain-prose description an
# artist (here: Ideogram) can use to recreate the LOOK — subject,
# composition, palette, lighting, style — with no chat preamble.
_PROMPT = (
    "Describe this image so an artist can recreate its look. In 2-3 "
    "plain-prose sentences, cover: the main subject, the composition and "
    "framing, the color palette, the lighting, and the overall visual "
    "style (e.g. photographic, painterly, 3D render, illustration). "
    "Do NOT add any preamble, list, markdown, or quotes — just the "
    "description."
)


def _resolve_model(override: str | None) -> pathlib.Path | None:
    """Resolve the Gemma 3 model dir the same way the panel does.

    Order, mirroring mlx_ltx_panel.py's GEMMA resolution:
      1. explicit --model arg,
      2. env LTX_GEMMA_PATH,
      3. <script-dir>/mlx_models/gemma-3-12b-it-4bit,
      4. the HuggingFace cache snapshot glob under HF_HOME (or the
         default cache root) — `cache/HF_HOME` is the in-repo location
         the panel's preflight downloads into.
    Returns the first directory that exists, else None.
    """
    if override:
        p = pathlib.Path(override).expanduser()
        return p if p.is_dir() else None

    env_path = os.environ.get("LTX_GEMMA_PATH")
    if env_path:
        p = pathlib.Path(env_path).expanduser()
        if p.is_dir():
            return p

    here = pathlib.Path(__file__).resolve().parent
    local = here / "mlx_models" / "gemma-3-12b-it-4bit"
    if local.is_dir():
        return local

    # HF cache snapshot fallback. HF_HOME may point at the in-repo
    # cache/HF_HOME dir; otherwise fall back to the standard ~/.cache root.
    hf_home = os.environ.get("HF_HOME") or str(here / "cache" / "HF_HOME")
    candidates: list[str] = []
    for root in (hf_home, str(pathlib.Path.home() / ".cache" / "huggingface")):
        candidates.extend(glob.glob(os.path.join(
            root, "hub",
            "models--mlx-community--gemma-3-12b-it-4bit",
            "snapshots", "*")))
    for snap in candidates:
        p = pathlib.Path(snap)
        # A usable snapshot has the config + at least one weight shard.
        if p.is_dir() and (p / "config.json").is_file():
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Vision-caption a reference image with local Gemma 3 "
                    "for the Ideogram 4 reference bridge. Prints the "
                    "caption as the last stdout line plus a "
                    "CAPTION_JSON: {...} marker line; exits non-zero on "
                    "failure.")
    ap.add_argument("--image", action="append", required=True, metavar="PATH",
                    help="Reference image path. Repeat for up to 3; the "
                         "first image is described, the rest are noted.")
    ap.add_argument("--model", default=None,
                    help="Override Gemma 3 model dir (default: resolve via "
                         "LTX_GEMMA_PATH, then mlx_models/gemma-3-12b-it-4bit, "
                         "then the HF_HOME cache snapshot).")
    ap.add_argument("--max-tokens", type=int, default=160,
                    help="Caption length cap (default 160).")
    args = ap.parse_args()

    # Accept 1-3 images; the bridge only ever sends up to 3. Take the
    # first as the one to describe and keep the resolved existing paths.
    raw = [pathlib.Path(p).expanduser() for p in (args.image or [])]
    images = [p for p in raw if p.is_file()]
    if not images:
        sys.stderr.write(
            "caption_reference_vision: no readable image among "
            f"{[str(p) for p in raw]}\n")
        return 1
    images = images[:3]

    gemma_path = _resolve_model(args.model)
    if gemma_path is None:
        sys.stderr.write(
            "caption_reference_vision: Gemma model dir not found — set "
            "LTX_GEMMA_PATH or pass --model (looked for "
            "mlx_models/gemma-3-12b-it-4bit and the HF cache snapshot).\n")
        return 1

    # Import here (not module top) so --help and the arg parsing above
    # never pay the import cost, and a missing package yields a clean
    # stderr message instead of a traceback at import time.
    try:
        from mlx_vlm import load, generate
        from mlx_vlm.prompt_utils import apply_chat_template
    except ImportError as e:
        sys.stderr.write(
            f"caption_reference_vision: mlx_vlm not installed: {e}. "
            "Run: pip install --no-deps 'mlx-vlm==0.4.4'\n")
        return 1

    try:
        model, processor = load(str(gemma_path))
    except Exception as e:                                # noqa: BLE001
        sys.stderr.write(
            f"caption_reference_vision: failed to load Gemma: "
            f"{type(e).__name__}: {e}\n")
        return 1

    # Single-image caption is the robust path. If the user dropped more
    # than one reference, describe the first and mention how many others
    # accompany it so Ideogram knows it's part of a set — but we do NOT
    # try to fuse multiple images (Gemma's multi-image grounding is weak
    # and the extra tokens/RAM aren't worth it for a prompt hint).
    described = images[0]
    user_text = _PROMPT
    if len(images) > 1:
        user_text += (f" (This is the primary of {len(images)} reference "
                      "images provided together; describe this one.)")
    messages = [{"role": "user", "content": user_text}]

    try:
        formatted = apply_chat_template(
            processor, model.config, messages, num_images=1)
        out = generate(
            model, processor, formatted, image=[str(described)],
            max_tokens=args.max_tokens, verbose=False,
        )
    except Exception as e:                                # noqa: BLE001
        sys.stderr.write(
            f"caption_reference_vision: caption failed for "
            f"{described.name}: {type(e).__name__}: {e}\n")
        return 1

    text = (getattr(out, "text", out) or "")
    # Collapse whitespace/newlines into a single clean line — the caller
    # splices this into a one-line prompt fragment and also relies on the
    # caption being the LAST stdout line.
    caption = " ".join(str(text).split()).strip()
    if not caption:
        sys.stderr.write(
            "caption_reference_vision: model returned an empty caption.\n")
        return 1

    # Machine-readable marker first (preferred by the caller), then the
    # bare caption as the final line (fallback parse).
    sys.stdout.write(
        "CAPTION_JSON: "
        + json.dumps({"caption": caption}, ensure_ascii=False) + "\n")
    sys.stdout.write(caption + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
