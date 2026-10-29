"""mflux 0.23.0 worker. Executed only with flux3-worker/env/bin/python."""
import gc
import json
import os
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flux3_engine import CREDIT, LICENCE_URL, OUTPUT_HEADROOM, PACKS, cache_root, cached_bytes, downloaded_bytes, clean_env, require_space

# Defence at both process boundaries, including direct Pinokio invocation.
_clean = clean_env()
os.environ.clear()
os.environ.update(_clean)


def fetch(pack):
    from huggingface_hub import hf_hub_download
    missing = pack["bytes"] - downloaded_bytes(pack)
    if missing > 0:   # the reserve guards downloads, not a fully cached model
        require_space(cache_root(), missing)
    # Progress and the disk reserve come from the cache itself, polled on a
    # thread: hf-xet transfers never call the tqdm hook, so a hook-only
    # report sat at 0.00 GB and the reserve went unchecked mid-download.
    import threading
    stop = threading.Event()
    def watch():
        while not stop.wait(3):
            try:
                require_space(cache_root())
            except RuntimeError as exc:
                print("FLUX 3: " + str(exc), flush=True)
                os._exit(1)
            print(f"[download] model weights: {downloaded_bytes(pack) / 1e9:.2f} GB "
                  f"of {pack['bytes'] / 1e9:.2f} GB", flush=True)
    watcher = threading.Thread(target=watch, daemon=True)
    if cached_bytes(pack) < pack["bytes"]:
        watcher.start()
    path = None
    for i, item in enumerate(pack["files"], 1):
        print(f"FLUX 3 download: file {i}/{len(pack['files'])}, {item['path']}", flush=True)
        path = Path(hf_hub_download(pack["repo"], item["path"], revision=pack["revision"],
                                  token=False))
        if path.stat().st_size != item["size"]:
            raise RuntimeError("Incomplete FLUX 3 download. Retry to resume it.")
        if item.get("sha256"):
            import hashlib
            print(f"FLUX 3: verifying {item['path']}", flush=True)
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != item["sha256"]:
                raise RuntimeError("FLUX 3 weights failed verification. Download a clean copy in another cache location.")
        print(f"[download] model weights: {cached_bytes(pack) / 1e9:.2f} GB "
              f"of {pack['bytes'] / 1e9:.2f} GB", flush=True)
    stop.set()
    # All files are in the same pinned snapshot. Retain the BFL licence next
    # to the model and document the conversion's authorship.
    from flux3_engine import snapshot_path
    snapshot = snapshot_path(pack)
    import shutil
    shutil.copyfile(ROOT / "docs/licenses/FLUX-Kommunity-1.0.txt", snapshot / "LICENSE.txt")
    shutil.copyfile(ROOT / "docs/licenses/FLUX3-NOTICE.txt", snapshot / "NOTICE.txt")
    return snapshot


def cli_args(request, model, seed, output):
    base = "flux3-image-klein" + ("-base" if request["variant"] == "base" else "")
    args = ["mflux-generate-flux3", "--model", str(model), "--base-model", base,
            "--prompt", request["prompt"], "--seed", str(seed),
            "--width", str(request["width"]), "--height", str(request["height"]),
            "--steps", "50" if request["variant"] == "base" else "16",
            "--output", str(output), "--metadata", "--low-ram"]
    if request["variant"] == "base":
        args += ["--guidance", "3.5"]
    if request["refs"]:
        args += ["--image-paths", *request["refs"]]
    return args


def main():
    # Pinokio removes SSL variables. Use this worker's certifi, not the
    # studio interpreter's certificate path, for all HTTPS downloads.
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    request = json.loads(sys.stdin.readline())
    pack = PACKS[f"{request['variant']}-q{request['quantize']}"]
    model = fetch(pack)
    import mlx.core as mx
    if request["refs"]:
        from mflux.models.flux3.cli.flux3_edit_generate import main as render
    else:
        from mflux.models.flux3.cli.flux3_generate import main as render
    from PIL import Image
    native_reset = mx.reset_peak_memory
    peaks = []
    def track_reset():
        peaks.append(mx.get_peak_memory())
        native_reset()
    # mflux resets its peak counter after loading. Keep that peak too.
    mx.reset_peak_memory = track_reset
    for seed in request["seeds"]:
        output = Path(request["output_dir"]) / f"flux3_{time.time_ns()}_{seed}.png"
        require_space(output.parent, OUTPUT_HEADROOM, reserve=0)
        mx.clear_cache()
        peaks.clear()
        native_reset()
        started = time.monotonic()
        print(f"FLUX 3: rendering seed {seed} ({'edit' if request['refs'] else 'text to image'})", flush=True)
        sys.argv = cli_args(request, model, seed, output)
        render()
        if not output.is_file():
            raise RuntimeError("The worker stopped before writing a complete image.")
        with Image.open(output) as im:
            im.verify()
        with Image.open(output) as im:
            width, height = im.size
        result = dict(png_path=str(output), seed=seed, engine="flux3", model=pack["repo"],
                      width=width, height=height, elapsed_seconds=round(time.monotonic() - started, 3),
                      peak_mlx_gb=round(max([mx.get_peak_memory(), *peaks]) / 1e9, 3),
                      peak_rss_gb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 3),
                      peak_rss_scope="worker process high-water mark",
                      credit=CREDIT, licence_url=LICENCE_URL, revision=pack["revision"],
                      variant=request["variant"], quantize=request["quantize"], refs=request["refs"])
        output.with_suffix(".flux3.json").write_text(json.dumps(result, indent=2) + "\n")
        print("FLUX3_RESULT " + json.dumps(result), flush=True)
        gc.collect()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        from flux3_engine import _redact
        print("FLUX 3: " + _redact(str(exc)), flush=True)
        sys.exit(1)
