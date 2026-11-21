"""Prepare neutral visual conditioning, then run the existing audio trainer.

Only audio-attention/FF adapters are trained. A neutral still supplies the
paired visual latent required by the existing joint trainer; no face adapter
is trained or published. Each audio slice gets its own paired row.
"""
from pathlib import Path
import json
import math
import sys



# These are the existing trainer's 2.3 files, pinned independently of the
# inference generation. Do not route "q4" through the current (2.5) registry.
TRAIN_PACKS = (
    ("dgrauet/ltx-2.3-mlx-q4", "f306259444116d71927188af2fbbf7fdc1a8089a", "ltx-2.3-mlx-q4", {
        "config.json": 951, "embedded_config.json": 7234, "split_model.json": 1893,
        "quantize_config.json": 100, "audio_vae.safetensors": 106509048,
        "connector.safetensors": 6344495512, "vae_decoder.safetensors": 814349531,
        "vae_encoder.safetensors": 637885319, "vocoder.safetensors": 258313851}),
    ("dgrauet/ltx-2.3-mlx-q8", "69f8f11996b7b2ee54e82d0db7a5acad14b8685d", "ltx-2.3-mlx-q8", {
        "transformer-dev.safetensors": 20597189549}),
)


def prepare_models():
    """Resume the training-only downloads inside the cancellable queue job."""
    import shutil
    from huggingface_hub import hf_hub_download
    from lora_lab import resolve_default_model_dir
    root = Path(resolve_default_model_dir()).parent
    root.mkdir(parents=True, exist_ok=True)
    missing = [(repo, revision, folder, filename, size)
               for repo, revision, folder, files in TRAIN_PACKS
               for filename, size in files.items()
               if not (root / folder / filename).is_file()
               or (root / folder / filename).stat().st_size != size]
    required = sum(row[-1] for row in missing)
    if missing and shutil.disk_usage(root).free < required + 2 * 1024**3:
        raise RuntimeError(f"Voice training needs {required / 1024**3:.1f} GB of model downloads and 2 GB of working space. Free disk space, then train again.")
    for repo, revision, folder, filename, _ in missing:
        print(f"Preparing voice training: {filename}", flush=True)
        hf_hub_download(repo, filename, revision=revision, local_dir=str(root / folder))


def main(spec_path: str) -> int:
    from PIL import Image
    from lora_lab.train_character import run_preprocess
    from lora_lab import train_audio
    run_pipeline = train_audio.run_pipeline

    path = Path(spec_path)
    spec = json.loads(path.read_text())
    progress = path.parent / "progress.json"
    original_emit = train_audio.emit
    def publish(event, **values):
        temporary = progress.with_suffix(".tmp")
        temporary.write_text(json.dumps({"event": event, **values}))
        temporary.replace(progress)
        original_emit(event, **values)
    train_audio.emit = publish
    publish("preparing")
    prepare_models()
    root = Path(spec["dataset_dir"])
    images, captions = root / "images", root / "captions"
    images.mkdir(parents=True, exist_ok=True)
    captions.mkdir(parents=True, exist_ok=True)
    count = max(3, math.ceil(float(spec["seconds"]) / spec["audio_slice_seconds"]))
    for index in range(count):
        stem = f"voice_{index:04d}"
        Image.new("RGB", (256, 256), (127, 127, 127)).save(images / f"{stem}.png")
        (captions / f"{stem}.txt").write_text(f"{spec['trigger']} speaking clearly. Audio: a natural talking voice.")
    run_preprocess(images, captions, root, resolution=256, total=count)
    spec["image_count"] = count
    path.write_text(json.dumps(spec, indent=2))
    return run_pipeline(path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
