#!/usr/bin/env python3
"""Several MiniMax-H3-Image pictures of one prompt in ONE engine process (15 Nov 2026).

Runs in the H3 pack's own interpreter against the INSTALLED engine checkout (``--engine-root``):
nothing here is new model code, only the engine's own staged picture flow with the models loaded
once for a list of seeds instead of once per picture, so the engine pin does not move.

    python3 scripts/h3_image_batch.py "a long English description" -o out.png \
        --engine-root <H3 checkout> --root <h3-image pack> --text-dir <ddalcu-q8> \
        --text-config <upstream text_encoder/config.json> --aspect 1:1 --mp 4 \
        --seeds 7,8,9 [--metrics out.json] [--reference p1.png ...] [--match-reference]

The flow is the engine's own, stage by stage, in the same order: the text encoder reads the prompt
and leaves; with reference pictures the latent encoder makes each seed's rows and leaves; the DiT
comes in once and draws every seed; the decoder comes in once and decodes every latent. The peak
stays that of one picture (a finished latent is a few MB), and the pictures are the ones the
per-picture runner makes for the same seeds. With ``--seeds`` each picture is saved as
``<output stem>_s<seed>.png`` as soon as it is decoded; one seed is saved at ``--output``.

Progress goes to stdout for the Studio's render log: the engine's own step lines, one
``{"saved": ..., "seed": ...}`` line per picture, and the metrics summary at the end. Exit 0 with
every PNG written (and the metrics JSON when asked); exit 4 when the engine checkout lacks a module
or name this runner stages (the Studio then runs one process per picture); any other exit is a
failure the Studio reports.
"""
from __future__ import annotations

import argparse
import gc
import importlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ENGINE_TOO_OLD = 4

# Every engine name this runner stages, by module: the contract with the pinned engine.
ENGINE_NAMES = {
    "minimax_h3_mlx.h3_image": ["DEFAULT_MIN_GENERATION_AREA", "MAX_REFERENCES", "EDIT_LABEL", "QWEN_SHORT_EDGE",
                                "generation_canvas", "resize_output", "size_for_megapixels", "bound_reference",
                                "load_reference", "vae_reference", "size_matching", "encode_reference_rows",
                                "load_pdd_heads", "denoise_h3_image"],
    "minimax_h3_mlx.adaln": ["load_modulation_cache"],
    "minimax_h3_mlx.h3_image_vae": ["decode_to_uint8", "load_h3_image_vae", "load_h3_image_vae_encoder"],
    "minimax_h3_mlx.load": ["load_dit"],
    "minimax_h3_mlx.text_encoder": ["MiniMaxH3TextEncoder"],
}


def engine_has_names(engine_root) -> list[str]:
    """What the checkout at ``engine_root`` is missing, by a plain read of its files (no import)."""
    import re
    missing = []
    for module, names in ENGINE_NAMES.items():
        path = Path(engine_root).joinpath(*module.split(".")).with_suffix(".py")
        if not path.is_file():
            missing.append(module)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in names:
            n = re.escape(name)
            # defined here, or re-exported by an import line (the engine's h3_image_vae does that)
            if not re.search(r"^(def|class)\s+%s\b|^%s\s*=|^(from\s+\S+\s+)?import\b.*\b%s\b" % (n, n, n), text, re.M):
                missing.append(f"{module}.{name}")
    return missing


def load_engine(engine_root) -> SimpleNamespace:
    """Import the engine's modules from ``engine_root`` and return their names on one namespace."""
    root = str(Path(engine_root).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    names = {}
    try:
        for module, wanted in ENGINE_NAMES.items():
            mod = importlib.import_module(module)
            for name in wanted:
                names[name] = getattr(mod, name)
    except (ImportError, AttributeError) as exc:
        raise EngineTooOld(f"the H3 engine at {root} lacks what this runner stages: {exc}") from exc
    import mlx.core as mx
    names["mx"] = mx
    return SimpleNamespace(**names)


class EngineTooOld(RuntimeError):
    pass


def release() -> None:
    import mlx.core as mx
    gc.collect()
    mx.clear_cache()


class BatchRunner:
    """The engine's staged picture flow for a list of seeds; every model is loaded once per call.

    Each stage is a small method so another driver can decide to hold a model between calls; this
    one lets each go as soon as the next stage needs the memory, exactly like the engine's own runner.
    The caller drops its own reference to a model BEFORE calling the release hook: the hook frees
    the allocator cache, and a buffer still referenced at that moment would stay allocated.
    """

    def __init__(self, engine: SimpleNamespace, root, text_dir, text_config=None):
        self.E = engine
        self.root, self.text_dir = Path(root), Path(text_dir)
        self.text_config = Path(text_config) if text_config else None
        self.metrics: dict = {}
        self._vae_json = None

    # -- stages: what is loaded, and when it goes ---------------------------------------------
    def starting(self, prompt: str, references: list) -> None:
        """Called first, before anything loads."""

    def text_encoder(self, with_vision: bool):
        return self.E.MiniMaxH3TextEncoder(self.text_dir, load_vision=with_vision, verbose=False,
                                           config_path=self.text_config)

    def release_text_encoder(self) -> None:
        release()

    def prompt_rows(self, prompt: str, references: list):
        """(cond, tags) for the prompt, read by the text encoder, which leaves afterwards."""
        encoder = self.text_encoder(bool(references))
        if references:
            qwen_images = [self.E.bound_reference(img, *self.E.QWEN_SHORT_EDGE) for img in references]
            cond, tags = encoder.encode(prompt, qwen_images, label_format=self.E.EDIT_LABEL)
        else:
            cond, tags = encoder.encode(prompt)
        self.E.mx.eval(cond)
        encoder = None
        self.release_text_encoder()
        return cond, tags

    def vae_config(self) -> dict:
        if self._vae_json is None:
            self._vae_json = json.loads((self.root / "vae" / "config.json").read_text())
        return self._vae_json

    def reference_encoder(self):
        return self.E.load_h3_image_vae_encoder(self.root / "vae" / "diffusion_pytorch_model.safetensors",
                                                self.vae_config())

    def release_reference_encoder(self) -> None:
        release()

    def before_dit(self) -> None:
        """Called right before the DiT is asked for."""

    def dit(self, verbose: bool):
        """(dit, modulation cache, PDD heads)."""
        dit = self.E.load_dit(self.root / "dit", verbose=verbose)
        cache = self.E.load_modulation_cache(self.root / "dit" / "adaln_cache.safetensors")
        heads = self.E.load_pdd_heads(self.root / "pdd_fused_heads.safetensors")
        dit.compile_image_blocks = False
        return dit, cache, heads

    def release_dit(self) -> None:
        release()

    def decoder(self):
        return self.E.load_h3_image_vae(self.root / "vae" / "diffusion_pytorch_model.safetensors", self.vae_config())

    def release_decoder(self) -> None:
        release()

    def close(self) -> None:
        release()

    # -- the pictures --------------------------------------------------------------------------
    def generate_many(self, prompt: str, width: int, height: int, seeds, verbose: bool = True,
                      min_generation_area: int | None = None, references: list | None = None,
                      on_image=None) -> list:
        """One picture per seed for the same prompt and size. ``on_image(index, seed, image, metrics)``
        is called as each picture is decoded, so the caller can save it before the next one."""
        E = self.E
        if min_generation_area is None:
            min_generation_area = E.DEFAULT_MIN_GENERATION_AREA
        seeds = [int(s) for s in seeds]
        if not seeds:
            raise ValueError("at least one seed")
        references = list(references or [])
        if len(references) > E.MAX_REFERENCES:
            raise ValueError(f"MiniMax-H3-Image takes at most {E.MAX_REFERENCES} reference pictures, got {len(references)}")
        m = self.metrics = {"references": len(references), "pictures": []}
        pics = [{"seed": s} for s in seeds]
        t0 = time.perf_counter()
        cw, ch = E.generation_canvas(width, height, min_generation_area)
        m["canvas"] = [cw, ch]
        self.starting(prompt, references)

        # 1. the prompt, once
        started = time.perf_counter()
        cond, tags = self.prompt_rows(prompt, references)
        m["text_seconds"], m["text_rows"] = time.perf_counter() - started, int(cond.shape[1])

        # 2. edit references: each seed's own rows (the posterior sample is keyed on the seed)
        reference_rows, grids = [None] * len(seeds), ()
        if references:
            started = time.perf_counter()
            enc = self.reference_encoder()
            prepared = [E.vae_reference(img) for img in references]
            grids = tuple(g for _, g in prepared)
            for k, seed in enumerate(seeds):
                parts = [E.encode_reference_rows(enc, pixels, (1, 2, 2), seed, index)
                         for index, (pixels, _) in enumerate(prepared)]
                rows = parts[0] if len(parts) == 1 else E.mx.concatenate(parts)
                E.mx.eval(rows)
                reference_rows[k] = rows
            m["reference_seconds"] = time.perf_counter() - started
            m["reference_grids"] = [list(g) for g in grids]
            if verbose:
                print(f"reference pictures: {len(prepared)} at {list(grids)} latent, "
                      f"{reference_rows[0].shape[0]} rows", flush=True)
            enc = None
            self.release_reference_encoder()

        # 3. the DiT, once for every seed
        self.before_dit()
        started = time.perf_counter()
        parts = self.dit(verbose)
        dit, cache, heads = parts
        m["dit_load_seconds"] = time.perf_counter() - started
        latents = []
        for k, seed in enumerate(seeds):
            started = time.perf_counter()
            lat = E.denoise_h3_image(dit, cond, tags, ch, cw, cache, heads, seed=seed, verbose=verbose,
                                     reference_rows=reference_rows[k], reference_grids=grids)
            E.mx.eval(lat)
            latents.append(lat)
            reference_rows[k] = None
            pics[k]["denoise_seconds"] = time.perf_counter() - started
            pics[k]["step_seconds"] = list(getattr(E.denoise_h3_image, "last_step_seconds", []))
            pics[k]["forwards"] = len(pics[k]["step_seconds"])
        dit = cache = heads = parts = reference_rows = None
        self.release_dit()

        # 4. the decoder, once for every latent
        started = time.perf_counter()
        vae = self.decoder()
        m["vae_load_seconds"] = time.perf_counter() - started
        images = []
        for k, seed in enumerate(seeds):
            started = time.perf_counter()
            image = E.resize_output(E.decode_to_uint8(vae, latents[k]), width, height)
            latents[k] = None
            pics[k]["decode_seconds"] = time.perf_counter() - started
            images.append(image)
            if on_image is not None:
                on_image(k, seed, image, pics[k])
        vae = None
        self.release_decoder()
        m["total_seconds"] = time.perf_counter() - t0
        m["peak_gb"] = E.mx.get_peak_memory() / 1e9
        m["pictures"] = pics
        return images


def output_for(out: Path, seed: int, many: bool) -> Path:
    return out.with_name(f"{out.stem}_s{seed}{out.suffix or '.png'}") if many else out


def picture_size(E, request: dict, references: list) -> tuple[int, int]:
    mp = float(request.get("mp") or 4.0)
    if request.get("width") and request.get("height"):
        return int(request["width"]), int(request["height"])
    if request.get("match_reference"):
        return E.size_matching(references[0], mp)
    return E.size_for_megapixels(str(request.get("aspect") or "1:1"), mp)


def run(runner: BatchRunner, request: dict) -> dict:
    """One request: {prompt, output, seeds|seed, aspect, mp, width, height, references, match_reference,
    metrics}. Saves every picture and returns the metrics (also written to ``metrics`` when given)."""
    from PIL import Image
    E = runner.E
    prompt = str(request.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("a prompt is required")
    if not request.get("output"):
        raise ValueError("an output path is required")
    seeds = request.get("seeds")
    seeds = [int(s) for s in seeds] if seeds else [int(request.get("seed") or 0)]
    paths = [str(p) for p in request.get("references") or []]
    if len(paths) > E.MAX_REFERENCES:
        raise ValueError(f"at most {E.MAX_REFERENCES} reference pictures")
    match = bool(request.get("match_reference"))
    if match and not paths:
        raise ValueError("match_reference needs a reference")
    references = [E.load_reference(p) for p in paths]
    width, height = picture_size(E, request, references)
    out = Path(request["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    many = len(seeds) > 1
    started = time.perf_counter()

    def save(index, seed, image, pic):
        path = output_for(out, seed, many)
        temp = path.with_name(path.stem + ".pending" + (path.suffix or ".png"))
        Image.fromarray(image).save(temp)
        temp.replace(path)
        pic.update({"output": str(path), "saved_at_seconds": time.perf_counter() - started})
        print(json.dumps({"saved": str(path), "seed": seed, "saved_at_seconds": round(pic["saved_at_seconds"], 1)}),
              flush=True)

    runner.generate_many(prompt, width, height, seeds, references=references, on_image=save,
                         min_generation_area=None if request.get("no_canvas") else E.DEFAULT_MIN_GENERATION_AREA)
    result = dict(runner.metrics, width=width, height=height, seed=seeds[0], seeds=seeds, prompt=prompt,
                  reference_paths=paths, output=str(out), request_seconds=time.perf_counter() - started)
    if not many:
        result.update(result["pictures"][0])
    if request.get("metrics"):
        path = Path(request["metrics"])
        temp = path.with_name(path.stem + ".pending" + (path.suffix or ".json"))
        temp.write_text(json.dumps(result, indent=2))
        temp.replace(path)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt")
    ap.add_argument("-o", "--output", default="out.png")
    ap.add_argument("--engine-root", required=True, help="the installed H3 engine checkout")
    ap.add_argument("--root", required=True, help="the h3-image pack folder (dit/, vae/, pdd_fused_heads.safetensors)")
    ap.add_argument("--text-dir", required=True)
    ap.add_argument("--text-config", required=True)
    ap.add_argument("--aspect", default="1:1")
    ap.add_argument("--mp", type=float, default=4.0)
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", default=None, help="several pictures of the same prompt, e.g. 7,8,9")
    ap.add_argument("--metrics", default=None)
    ap.add_argument("--no-canvas", action="store_true")
    ap.add_argument("--reference", action="append", default=[])
    ap.add_argument("--match-reference", action="store_true")
    args = ap.parse_args(argv)
    if not args.prompt.strip():
        raise SystemExit("a prompt is required")
    if bool(args.width) != bool(args.height):
        raise SystemExit("width and height must be supplied together")
    missing = engine_has_names(args.engine_root)
    if missing:
        print("the H3 engine checkout lacks what this runner stages: " + ", ".join(missing), file=sys.stderr, flush=True)
        return ENGINE_TOO_OLD
    try:
        E = load_engine(args.engine_root)
    except EngineTooOld as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return ENGINE_TOO_OLD
    runner = BatchRunner(E, args.root, args.text_dir, args.text_config)
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()] if args.seeds else None
    try:
        result = run(runner, {"prompt": args.prompt, "output": args.output, "seeds": seeds, "seed": args.seed,
                              "aspect": args.aspect, "mp": args.mp, "width": args.width, "height": args.height,
                              "references": args.reference, "match_reference": args.match_reference,
                              "metrics": args.metrics, "no_canvas": args.no_canvas})
    finally:
        runner.close()
    print(json.dumps({k: v for k, v in result.items() if k not in ("step_seconds", "pictures")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
