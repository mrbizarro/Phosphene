"""Image-only preprocessor for LTX-2 MLX trainer.

Encodes still images into 1-frame VAE latents and captions into Gemma
embeddings, written in the layout `PrecomputedDataset` expects:

    output_dir/
      .precomputed/
        latents/
          latent_0000.safetensors   {latents:[128,1,h,w], num_frames=1, height, width, fps}
          latent_0001.safetensors
        conditions/
          condition_0000.safetensors {video_prompt_embeds, audio_prompt_embeds, prompt_attention_mask}
          ...

Caption encoding is delegated to the existing video preprocessor's helper —
the path is identical and we don't fork that code.

Usage::

    ./scripts/run.sh python -m lora_lab.preprocess_images \
        --images dataset/images \
        --captions dataset/captions \
        --output dataset \
        --width 512 --height 320

The model dir defaults to the same LTX-2.3 Q4 snapshot Phosphene uses.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import mlx.core as mx
import numpy as np
from PIL import Image
from safetensors.numpy import save_file as save_safetensors

from ltx_core_mlx.components.patchifiers import compute_video_latent_shape
from ltx_core_mlx.utils.memory import aggressive_cleanup
from ltx_trainer_mlx.preprocess import _encode_all_captions, _resolve_model_dir

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Resolved dynamically — see lora_lab/__init__.py for the resolution
# order. Public Pinokio installs use the vendored mlx_models dir; the
# authoring tree falls back to the HF hub cache.
from lora_lab import resolve_default_model_dir
DEFAULT_MODEL_DIR = resolve_default_model_dir()


def _force_eval(*arrays: mx.array) -> None:
    mx.eval(*arrays)


def _apply_gemma_max_length_override() -> int | None:
    """Honour ``LTX2_GEMMA_MAX_LENGTH`` for the caption encode, the way the
    render pipelines do.

    The vendored ``_encode_all_captions`` calls ``encode_all_layers(caption)``
    with the default 1024-token pad, and on some chips the macOS GPU watchdog
    kills that command buffer partway through the caption list (#61: SIGABRT
    on the 1st, 7th or 11th caption of different runs — a race on accumulated
    GPU pressure, not a bad caption). The render helper survives the same
    kill by retrying once at a shorter encode; the panel relaunches the
    trainer the same way and passes the length through this variable. A
    256-token pad holds every caption the trainer writes (50-80 words) with
    room to spare, and the transformer already trains and renders against
    that length on the render side. Returns the applied length, or None when
    the variable is unset (the default 1024 stays).
    """
    raw = (os.environ.get("LTX2_GEMMA_MAX_LENGTH") or "").strip()
    if not raw:
        return None
    try:
        n = max(64, int(raw))
    except ValueError:
        return None
    from ltx_core_mlx.text_encoders.gemma.encoders import base_encoder as _be
    cls = _be.GemmaLanguageModel
    orig = cls.encode_all_layers
    if getattr(orig, "_ltx_max_length_override", None) == n:
        return n

    def encode_all_layers(self, text: str, max_length: int = 1024):
        return orig(self, text, min(int(max_length), n))

    encode_all_layers._ltx_max_length_override = n  # type: ignore[attr-defined]
    cls.encode_all_layers = encode_all_layers
    print(f"caption encode: LTX2_GEMMA_MAX_LENGTH={n} (default 1024)")
    return n


def _reconcile_precomputed(precomputed: Path, image_files: list, captions: list,
                           canvas: tuple) -> str:
    """Drop cached tensors that no longer match the inputs, and say what was done.

    The vendored encoders skip work by INDEX FILENAME — "condition_0007 exists,
    skipping" — with no idea what produced it. Re-training from the same folder
    with a new trigger therefore trained on the OLD captions; dropping a photo
    shifted every later index so latents paired with the wrong captions; a new
    canvas kept the old latents. Issue #62: three triggers, one folder, one
    result. A manifest of (image name/size/mtime, caption hash, canvas, encode
    length) is written BEFORE encoding, so a crash mid-way resumes correctly
    and any change to the inputs invalidates exactly what it must.
    """
    import hashlib
    import json
    import shutil

    def _h(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    cur = {
        "images": [[f.name, f.stat().st_size, int(f.stat().st_mtime)] for f in image_files],
        "captions": [_h(c) for c in captions],
        "canvas": list(canvas),
        "gemma_max_length": os.environ.get("LTX2_GEMMA_MAX_LENGTH", ""),
    }
    latents_dir = precomputed / "latents"
    conditions_dir = precomputed / "conditions"
    manifest = precomputed / "manifest.json"
    try:
        prev = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = None

    def _has_files(d: Path) -> bool:
        return d.is_dir() and any(d.iterdir())

    if prev is None:
        if _has_files(latents_dir) or _has_files(conditions_dir):
            shutil.rmtree(latents_dir, ignore_errors=True)
            shutil.rmtree(conditions_dir, ignore_errors=True)
            action = "cache predates manifests, cannot prove it matches these inputs - re-encoding everything"
        else:
            action = "fresh"
    elif prev.get("images") != cur["images"] or prev.get("canvas") != cur["canvas"]:
        shutil.rmtree(latents_dir, ignore_errors=True)
        shutil.rmtree(conditions_dir, ignore_errors=True)
        action = "images or canvas changed - re-encoding everything"
    elif (prev.get("gemma_max_length") != cur["gemma_max_length"]
          or len(prev.get("captions") or []) != len(cur["captions"])):
        shutil.rmtree(conditions_dir, ignore_errors=True)
        action = "caption encode settings changed - re-encoding captions"
    elif prev.get("captions") != cur["captions"]:
        n = 0
        for i, (a, b) in enumerate(zip(prev["captions"], cur["captions"])):
            if a != b:
                (conditions_dir / f"condition_{i:04d}.safetensors").unlink(missing_ok=True)
                n += 1
        action = f"{n} caption(s) changed - re-encoding those"
    else:
        action = "inputs unchanged - reusing cached tensors"

    precomputed.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(cur), encoding="utf-8")
    return action


def _resolve_captions(image_files: list[Path], captions_dir: Path | None, caption_ext: str) -> list[str]:
    captions: list[str] = []
    if captions_dir is None:
        for img in image_files:
            captions.append(img.stem.replace("_", " ").replace("-", " "))
        print("  no captions dir provided; using filenames as captions")
        return captions

    for img in image_files:
        cap_file = captions_dir / f"{img.stem}{caption_ext}"
        if cap_file.exists():
            captions.append(cap_file.read_text().strip())
        else:
            fallback = img.stem.replace("_", " ").replace("-", " ")
            logger.warning("no caption file for %s, using filename: '%s'", img.name, fallback)
            captions.append(fallback)
    return captions


def _load_image_as_1frame_tensor(image_path: Path, target_h: int, target_w: int, crop_anchor: str = "center") -> mx.array:
    """Load `image_path`, scale-and-crop to (target_h, target_w), return shape (1, C, 1, H, W) in [-1, 1].

    Critical: this is a CROP (preserves face aspect ratio), not a stretch. Earlier
    versions used Image.resize() directly, which non-uniformly scaled portrait
    selfies into squares — squashed the face geometry, and the LoRA only worked
    at inference aspect ratios that undid the same distortion. Center-crop is
    the right fix.

    `crop_anchor` controls vertical positioning when source is taller than target
    after scale-fit (i.e. portrait sources cropped to landscape target):
      - "center" (default): symmetric crop top+bottom — backward-compatible
      - "top": anchor at top — keeps upper portion (face for portrait sources)
      - "bottom": anchor at bottom — keeps lower portion

    For character LoRAs trained at landscape from portrait sources, "top" is
    usually right: faces sit in the upper third of portraits, legs in the lower
    third. Center-crop on a 3:4 portrait → 16:9 landscape can clip the top of
    the head AND the chin simultaneously.
    """
    img = Image.open(image_path).convert("RGB")
    src_w, src_h = img.size
    # Resize so the SHORTER side fits the target, preserving aspect ratio.
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(round(src_w * scale))
    new_h = int(round(src_h * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    # Crop the LONGER side down to target. Horizontal always centers;
    # vertical position is controlled by crop_anchor.
    left = (new_w - target_w) // 2
    if crop_anchor == "top":
        top = 0
    elif crop_anchor == "bottom":
        top = new_h - target_h
    else:  # "center"
        top = (new_h - target_h) // 2
    img = img.crop((left, top, left + target_w, top + target_h))
    arr = np.array(img, dtype=np.float32) / 255.0    # (H, W, 3) in [0, 1]
    arr = arr.transpose(2, 0, 1)                     # (3, H, W)
    arr = arr[:, None, :, :]                         # (3, 1, H, W) — F=1
    arr = arr[None]                                  # (1, 3, 1, H, W)
    arr = arr * 2.0 - 1.0                            # to [-1, 1] for VAE
    return mx.array(arr).astype(mx.bfloat16)


def _encode_all_images(
    image_files: list[Path],
    latents_dir: Path,
    model_dir: str,
    target_height: int,
    target_width: int,
    crop_anchor: str = "center",
) -> None:
    from ltx_trainer_mlx.model_loader import load_video_vae_encoder

    if target_height % 32 != 0 or target_width % 32 != 0:
        raise ValueError(f"width ({target_width}) and height ({target_height}) must be divisible by 32")

    needed: list[int] = []
    for i in range(len(image_files)):
        out_path = latents_dir / f"latent_{i:04d}.safetensors"
        if out_path.exists():
            print(f"  [{i + 1}/{len(image_files)}] skipping (exists): {out_path.name}")
        else:
            needed.append(i)
    if not needed:
        print("  all image latents already encoded")
        return

    vae_encoder = load_video_vae_encoder(model_dir=model_dir)
    vae_encoder.freeze()

    F_lat, H_lat, W_lat = compute_video_latent_shape(1, target_height, target_width)
    assert F_lat == 1, f"expected F_lat=1 for F=1, got {F_lat}"
    print(f"  target latent shape per image: [128, {F_lat}, {H_lat}, {W_lat}]")

    for i in needed:
        img_path = image_files[i]
        out_path = latents_dir / f"latent_{i:04d}.safetensors"
        print(f"  [{i + 1}/{len(image_files)}] encoding: {img_path.name}")
        try:
            video = _load_image_as_1frame_tensor(img_path, target_height, target_width, crop_anchor=crop_anchor)
            latent = vae_encoder.encode(video)
            _force_eval(latent)
            save_safetensors(
                {
                    "latents": np.array(latent[0].astype(mx.float32)),  # [128, 1, H_lat, W_lat]
                    "num_frames": np.array([F_lat], dtype=np.int32),
                    "height": np.array([H_lat], dtype=np.int32),
                    "width": np.array([W_lat], dtype=np.int32),
                    "fps": np.array([24.0], dtype=np.float32),
                },
                str(out_path),
            )
        except Exception as exc:
            logger.error("failed to encode %s: %s", img_path.name, exc)
            continue
        if i % 5 == 0:
            aggressive_cleanup()

    del vae_encoder
    aggressive_cleanup()
    print("  image encoding complete")


def preprocess_images(
    images_dir: str,
    output_dir: str,
    model_dir: str = DEFAULT_MODEL_DIR,
    gemma_model_id: str | None = None,
    target_height: int = 320,
    target_width: int = 512,
    captions_dir: str | None = None,
    caption_ext: str = ".txt",
    crop_anchor: str = "center",
) -> None:
    # Default to the locally-resolved Gemma path (vendored install ships
    # mlx_models/gemma-3-12b-it-4bit; the panel sets LTX_MODELS_DIR so
    # the resolver lands there). Falling back to the HF repo id, the way
    # the previous hard-coded default did, would silently duplicate-
    # download ~6 GB to HF_HOME on a clean Pinokio install AND fail on
    # offline rigs that have the local Gemma already cached.
    if gemma_model_id is None:
        from lora_lab import resolve_default_text_encoder
        gemma_model_id = resolve_default_text_encoder()
    _apply_gemma_max_length_override()
    mx.set_cache_limit(mx.device_info()["memory_size"])
    model_dir = _resolve_model_dir(model_dir)

    images_path = Path(images_dir)
    if not images_path.exists():
        raise FileNotFoundError(f"images dir not found: {images_path}")

    image_files = sorted(
        f for f in images_path.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS and f.is_file()
    )
    if not image_files:
        raise ValueError(f"no images found in {images_path}")
    print(f"found {len(image_files)} images in {images_path}")

    precomputed = Path(output_dir) / ".precomputed"
    latents_dir = precomputed / "latents"
    conditions_dir = precomputed / "conditions"
    latents_dir.mkdir(parents=True, exist_ok=True)
    conditions_dir.mkdir(parents=True, exist_ok=True)

    captions = _resolve_captions(
        image_files,
        Path(captions_dir) if captions_dir else None,
        caption_ext,
    )

    print("cache: " + _reconcile_precomputed(
        precomputed, image_files, captions,
        (int(target_width), int(target_height), str(crop_anchor))))
    latents_dir.mkdir(parents=True, exist_ok=True)
    conditions_dir.mkdir(parents=True, exist_ok=True)

    print("Phase 1: encoding text captions (Gemma)...")
    _encode_all_captions(
        captions=captions,
        conditions_dir=conditions_dir,
        model_dir=model_dir,
        gemma_model_id=gemma_model_id,
    )

    print(f"Phase 2: encoding image latents at {target_width}x{target_height} (1 frame each, crop_anchor={crop_anchor})...")
    _encode_all_images(
        image_files=image_files,
        latents_dir=latents_dir,
        model_dir=model_dir,
        target_height=target_height,
        target_width=target_width,
        crop_anchor=crop_anchor,
    )

    print(f"\npreprocessing complete. {len(image_files)} samples written to {precomputed}")
    print(f"  latents:    {latents_dir}")
    print(f"  conditions: {conditions_dir}")


def main() -> int:
    p = argparse.ArgumentParser(description="LTX-2 image-only LoRA preprocessor")
    p.add_argument("--images", required=True, help="directory of source images")
    p.add_argument("--captions", default=None, help="directory of caption .txt files (one per image stem)")
    p.add_argument("--caption-ext", default=".txt")
    p.add_argument("--output", required=True, help="output root — preprocessed data goes to <output>/.precomputed/")
    p.add_argument("--width", type=int, default=512, help="target width (divisible by 32)")
    p.add_argument("--height", type=int, default=320, help="target height (divisible by 32)")
    p.add_argument("--crop-anchor", default="center", choices=["center", "top", "bottom"],
                   help="vertical crop position when source is taller than target after scale-fit "
                        "(center=default+backward-compatible; top=preserves face on portrait sources "
                        "going to landscape target; bottom=preserves lower body)")
    p.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    # Default left as None so the preprocess_images() body's local
    # resolver (resolve_default_text_encoder) picks the right path.
    # Hardcoding the HF repo id here would duplicate-download Gemma to
    # HF_HOME on a vendored install that already has it under
    # mlx_models/gemma-3-12b-it-4bit/.
    p.add_argument("--gemma", default=None,
                   help="Gemma model dir or repo id. Default: resolved "
                        "via LTX_MODELS_DIR or vendored mlx_models/.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    preprocess_images(
        images_dir=args.images,
        captions_dir=args.captions,
        caption_ext=args.caption_ext,
        output_dir=args.output,
        target_height=args.height,
        target_width=args.width,
        model_dir=args.model_dir,
        gemma_model_id=args.gemma,
        crop_anchor=args.crop_anchor,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
