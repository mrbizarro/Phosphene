"""Auto-caption a Train Character dataset with the local Gemma 3 12B
multimodal model.

Standalone subprocess called by the panel's `/train/auto-caption` endpoint.
Reads images from `<dataset>/images/`, generates one `[VISUAL]: <trigger>, …`
caption per image describing what VARIES (pose / framing / clothing /
lighting / setting / mood) while explicitly skipping identity features
(face, hair, age, ethnicity) so the LoRA learns identity from pixels and
not from prose. Mirrors the caption recipe Bizarro v2 was trained on.

Why a subprocess + not the warm helper:
    Lightricks' Gemma wrapper (used by the helper for prompt enhancement)
    is text-only — it explicitly does not pass images through the vision
    tower. We need `mlx_vlm`, which uses the same Gemma 3 weights but
    invokes the `Gemma3ForConditionalGeneration` path. Running it in a
    short-lived subprocess means Gemma's ~6 GB RSS frees the moment
    captioning finishes; nothing accumulates on top of the dev
    transformer the trainer needs next.

Protocol (mirrors lora_lab.train_character):
    Emits one JSON object per line on stdout. The panel tails stdout and
    pushes each line into STATE['log']. Events:
        {"event":"loading"}                          model load start
        {"event":"loaded","elapsed_sec":3.2}         model ready
        {"event":"progress","i":1,"n":37,"file":"…","caption":"…"}
        {"event":"done","elapsed_sec":85.0,"count":37}
        {"event":"error","message":"…"}              fatal — exit non-zero

CLI:
    python -m caption_with_gemma \
        --dataset <state/train_character/donaldtrn> \
        --trigger donaldtrn \
        [--gemma-path <mlx_models/gemma-3-12b-it-4bit>] \
        [--max-tokens 200] [--temperature 0.3]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import traceback


def _emit(event: str, **kwargs) -> None:
    """Write one JSON event to stdout + flush. The panel reads these
    line-by-line and surfaces them in the log pane."""
    payload = {"event": event, **kwargs}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# v2 caption recipe — describe what VARIES, skip identity. The "ONE
# sentence" + "50-80 words" constraints are loose targets (Gemma comes in
# slightly shorter in practice, ~30-50 words); that's fine, still well
# above the 3-word trigger_simple fallback the panel uses without us.
_SYSTEM_TMPL = (
    "You are captioning images for a video character LoRA. The trigger "
    "token is {trigger}. "
    "Write ONE single sentence, 50-80 words, in this exact format:\n"
    "[VISUAL]: {trigger}, <description>\n"
    "Describe ONLY what VARIES across shots: pose, framing, clothing, "
    "setting, lighting, camera angle, mood. "
    "DO NOT describe facial features, hair color, age, ethnicity, or "
    "identity — the LoRA absorbs those from pixels. "
    "DO NOT mention the trigger word inside the description. "
    "Plain prose, no bullet points, no markdown, no quotes."
)
_USER_TMPL = "Caption this image following the spec."

# Images extensions the panel accepts under <dataset>/images/. Mirrors
# TRAIN_IMAGE_EXTS in mlx_ltx_panel.py — keep in sync.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _normalise_caption(text: str, trigger: str) -> str:
    """Clean up Gemma's output to the canonical `[VISUAL]: <trigger>, body`
    format. Handles every drift mode we've seen in practice:
      * leading/trailing quotes the model occasionally wraps
      * missing `[VISUAL]:` prefix (model jumps straight to `tok, body`)
      * doubled trigger in body (model re-emits the trigger after the
        prefix it was told to start with — pre-fix this produced
        `[VISUAL]: tok, tok, body` on disk for a couple of images)
      * extra `[VISUAL]:` segments deeper in the text
    """
    text = (text or "").strip()
    text = re.sub(r'^["\']|["\']$', '', text).strip()
    # Step 1: extract the body by stripping ALL leading occurrences of
    # `[VISUAL]:` and `<trigger>,` (with optional whitespace) — handles
    # `[VISUAL]: tok, tok, body`, `tok, body`, and the bare body case in
    # one pass.
    pattern = re.compile(
        r"^\s*(?:\[VISUAL\]\s*:\s*)?"           # optional [VISUAL]:
        r"(?:" + re.escape(trigger) + r"\s*,\s*)*",  # zero+ trigger,
        flags=re.IGNORECASE,
    )
    body = pattern.sub("", text, count=1).strip().lstrip(",").strip()
    # Step 2: re-attach the canonical prefix exactly once.
    return f"[VISUAL]: {trigger}, {body}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True,
                    help="Path to the Train Character dataset dir "
                         "(contains images/ and captions/).")
    ap.add_argument("--trigger", required=True,
                    help="Trigger token to embed in every caption.")
    ap.add_argument("--gemma-path", default=None,
                    help="Override Gemma 3 model dir (default: "
                         "mlx_models/gemma-3-12b-it-4bit next to this script).")
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--temperature", type=float, default=0.3,
                    help="Low temp keeps the format stable; higher = "
                         "more varied prose but more drift off-format.")
    args = ap.parse_args()

    dataset = pathlib.Path(args.dataset).resolve()
    images_dir = dataset / "images"
    captions_dir = dataset / "captions"
    if not images_dir.is_dir():
        _emit("error", message=f"images dir not found: {images_dir}")
        return 1
    captions_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(p for p in images_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in _IMAGE_EXTS)
    if not images:
        _emit("error", message=f"no images in {images_dir}")
        return 1

    # Default Gemma path is the same one the panel's preflight already
    # downloads — no extra disk required. Resolve via lora_lab's shared
    # helper so this honors `LTX_MODELS_DIR` the same way every other
    # lora_lab module does; anchoring to `__file__.parent` previously broke
    # when this script ran from a Pinokio install where the panel had set
    # LTX_MODELS_DIR to a different root than the script's directory.
    if args.gemma_path:
        gemma_path = pathlib.Path(args.gemma_path).resolve()
    else:
        try:
            from lora_lab import resolve_default_text_encoder
            resolved = resolve_default_text_encoder()
        except ImportError:
            # lora_lab not on PYTHONPATH (shouldn't happen via the panel's
            # spawn path, but keep a sane fallback so the standalone CLI
            # still works in a clean checkout).
            resolved = str(pathlib.Path(__file__).resolve().parent
                           / "mlx_models" / "gemma-3-12b-it-4bit")
        gemma_path = pathlib.Path(resolved).resolve()
    if not gemma_path.is_dir():
        _emit("error",
              message=f"Gemma model dir not found: {gemma_path} — "
                      f"run /train/preflight via the panel, or set "
                      f"LTX_MODELS_DIR to a root that contains "
                      f"gemma-3-12b-it-4bit/.")
        return 1

    # mlx_vlm + Gemma 3. Imports here (not module top) so the JSON-event
    # protocol still works if the package is missing: we can emit a clean
    # "error" event before crashing.
    t_load = time.time()
    _emit("loading")
    try:
        from mlx_vlm import load, generate
        from mlx_vlm.prompt_utils import apply_chat_template
    except ImportError as e:
        _emit("error", message=f"mlx_vlm not installed: {e}. "
                                f"Run: pip install --no-deps 'mlx-vlm==0.4.4'")
        return 1
    # WHICH GEMMA IS THIS. Two folders live side by side in mlx_models and
    # only one of them can see an image:
    #
    #   gemma-3-12b-it-4bit    model_type "gemma3"          <- the VLM
    #   gemma4-12b-ltx25-q4    model_type "gemma4_unified"  <- LTX-2.5's text
    #                                                          encoder, no
    #                                                          vision tower
    #
    # Handing the second one to mlx_vlm produces "Model type gemma4_unified
    # not supported" — 19 captioning failures across 4 installs and five
    # releases in the fleet, and nothing in that sentence says which folder
    # was wrong or which one is right. One read of config.json does.
    try:
        _cfg = json.loads((gemma_path / "config.json").read_text())
        _mtype = str(_cfg.get("model_type") or "")
    except Exception:                                          # noqa: BLE001
        _mtype = ""
    if _mtype and not _mtype.startswith("gemma3"):
        _emit("error",
              message=f"{gemma_path.name} is not the captioning model — its "
                      f"config says model_type \"{_mtype}\", which has no "
                      f"vision tower (gemma4-12b-ltx25-q4 is LTX-2.5's text "
                      f"encoder). Captioning needs gemma-3-12b-it-4bit. "
                      f"Run /train/preflight from the panel to fetch it, or "
                      f"point LTX_GEMMA_PATH at that folder.")
        return 1
    try:
        model, processor = load(str(gemma_path))
    except Exception as e:
        _emit("error", message=f"failed to load Gemma: "
                                f"{type(e).__name__}: {e}")
        return 1
    _emit("loaded", elapsed_sec=round(time.time() - t_load, 2))

    system = _SYSTEM_TMPL.format(trigger=args.trigger)
    n = len(images)
    t_start = time.time()
    # Counted, not assumed. A per-image failure `continue`s below, so the
    # number of images is NOT the number of captions on disk. Reporting the
    # former made the panel print "done - 24 captions" over a dataset that
    # had 20, and the four images with no caption then trained on the
    # 3-word `<trigger> man` fallback without anyone being told. Same class
    # as the zip filter that dropped 6 of 18 images in #61: a silent loss
    # under a green summary.
    written = 0
    for i, img_path in enumerate(images, 1):
        t_step = time.time()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _USER_TMPL},
        ]
        try:
            formatted = apply_chat_template(
                processor, model.config, messages, num_images=1)
            out = generate(
                model, processor, formatted, image=[str(img_path)],
                max_tokens=args.max_tokens, verbose=False,
                temperature=args.temperature,
            )
        except Exception as e:
            # Don't abort the whole run on a single bad image — log and
            # keep going. The image will fall back to trigger_simple at
            # train time (panel writes "<trigger> man" for missing caps).
            _emit("error", message=f"caption failed for {img_path.name}: "
                                    f"{type(e).__name__}: {e}",
                  i=i, n=n, file=img_path.name)
            continue
        text = (getattr(out, "text", out) or "")
        caption = _normalise_caption(text, args.trigger)
        cap_path = captions_dir / f"{img_path.stem}.txt"
        try:
            cap_path.write_text(caption + "\n", encoding="utf-8")
        except OSError as e:
            _emit("error", message=f"could not write {cap_path}: {e}",
                  i=i, n=n, file=img_path.name)
            continue
        written += 1
        _emit("progress",
              i=i, n=n,
              file=img_path.name,
              elapsed_sec=round(time.time() - t_step, 2),
              caption=caption)

    # Nothing on disk is not a run that finished, whatever the exit code
    # said. Emitting `done` here left the panel green while the whole
    # dataset fell through to the fallback caption at train time.
    if written == 0:
        _emit("error",
              message=f"no captions were written for any of the {n} images "
                      f"— every image failed to caption. The dataset is "
                      f"unchanged; see the errors above for the cause.",
              count=0, failed=n, total=n)
        return 1

    _emit("done",
          elapsed_sec=round(time.time() - t_start, 2),
          count=written,
          failed=n - written,
          total=n)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        _emit("error", message="cancelled (SIGINT)")
        raise SystemExit(130)
    except Exception as exc:
        _emit("error", message=f"{type(exc).__name__}: {exc}",
              traceback=traceback.format_exc())
        raise SystemExit(1)
