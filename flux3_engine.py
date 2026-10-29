"""FLUX 3's isolated worker contract. No MLX or mflux imports in the studio."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import platform
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
CREDIT = "FLUX 3 Klein by Black Forest Labs"
LICENCE_URL = "https://huggingface.co/black-forest-labs/flux-3-image-klein/blob/main/LICENSE.md"
# Free space FLUX 3 leaves untouched while it GROWS the disk (worker install,
# weight downloads). Rendering with everything installed writes one small PNG,
# so it only needs output headroom: a full reserve there refused every image
# on a Mac that had installed FLUX 3 and then had under the reserve free.
RESERVE = 5 * 1024**3
OUTPUT_HEADROOM = 64 * 1024**2
INSTALL_BUDGET = 3 * 1024**3
PACKS = json.loads((ROOT / "data/flux3_models.json").read_text())
PRESETS = {
    f"flux3_{variant}_q{q}": {"variant": variant, "quantize": q,
        "steps": 50 if variant == "base" else 16,
        "guidance": 3.5 if variant == "base" else None,
        "label": f"{CREDIT} / {'Base Quality' if variant == 'base' else 'Standard'} / Q{q}"}
    for variant in ("distilled", "base") for q in (8, 6, 4)
}


def worker_root():
    return Path(os.environ.get("PHOSPHENE_FLUX3_ROOT") or ROOT / "flux3-worker").expanduser().resolve()


def worker_python():
    return worker_root() / "env/bin/python"


def requirements_digest():
    return hashlib.sha256((ROOT / "scripts/pinokio/flux3_requirements.txt").read_bytes()).hexdigest()


def worker_ready():
    try:
        return worker_python().is_file() and (worker_root() / ".ready").read_text().strip() == requirements_digest()
    except OSError:
        return False


def clean_env(base=None):
    source = os.environ if base is None else base
    stripped = {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                "__PYVENV_LAUNCHER__", "PYTORCH_ENABLE_MPS_FALLBACK",
                "PYTORCH_MPS_FAST_MATH", "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"}
    env = {k: v for k, v in source.items() if k not in stripped
           and not k.startswith(("Malloc", "MFLUX_"))}
    env.update(PYTHONUNBUFFERED="1", HF_HUB_DISABLE_TELEMETRY="1",
               HF_HUB_DISABLE_PROGRESS_BARS="0", MLX_ENABLE_TF32="0")
    return env


def cache_root(env=None):
    env = os.environ if env is None else env
    return Path(env.get("HF_HUB_CACHE") or
                Path(env.get("HF_HOME") or Path.home() / ".cache/huggingface") / "hub").expanduser()


def pack_for(config):
    key = f"{config.flux3_variant}-q{config.mflux_quantize}"
    if key not in PACKS:
        raise ValueError("FLUX 3 needs Standard or Base Quality and Q8, Q6 or Q4.")
    return PACKS[key]


def snapshot_path(pack, env=None):
    return cache_root(env) / ("models--" + pack["repo"].replace("/", "--")) / "snapshots" / pack["revision"]


def cached_bytes(pack, env=None):
    snap = snapshot_path(pack, env)
    total = 0
    for item in pack["files"]:
        try:
            if (snap / item["path"]).stat().st_size == item["size"]:
                total += item["size"]
        except OSError:
            pass
    return total


def downloaded_bytes(pack, env=None):
    """Completed files plus resumable Hub blobs; never count a partial as ready."""
    snap = snapshot_path(pack, env)
    blobs = snap.parent.parent / "blobs"
    total = cached_bytes(pack, env)
    for item in pack["files"]:
        try:
            if (snap / item["path"]).stat().st_size == item["size"]:
                continue
        except OSError:
            pass
        digest = item.get("sha256")
        if digest:
            total += min(item["size"], partial_blob_bytes(blobs, digest))
    return total


def partial_blob_bytes(blobs, digest):
    """Largest in-flight blob for this file. huggingface_hub 1.x writes
    `<etag>.<uuid8>.incomplete` (older hubs `<etag>.incomplete`); counting
    only the old name showed 0.00 GB for the whole download."""
    best = 0
    try:
        for part in Path(blobs).glob(digest + "*.incomplete"):
            try:
                best = max(best, part.stat().st_size)
            except OSError:
                pass
    except OSError:
        pass
    return best


def require_space(path, extra=0, reserve=RESERVE):
    path = Path(path)
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    if free < reserve + extra:
        why = (f", keeping {reserve / 1024**3:.0f} GB free for your Mac" if reserve >= 1024**3 else "")
        raise RuntimeError(f"FLUX 3 needs {(reserve + extra) / 1024**3:.1f} GB free here "
                           f"({free / 1024**3:.1f} GB available){why}. "
                           "Free space or choose another model location, then retry.")


def ram_need(config):
    pack_for(config)
    return {4: 18.5, 6: 20.6, 8: 22.8}[config.mflux_quantize]


def validate(config, width, height, refs, total_gb=None):
    pack_for(config)
    if total_gb is None:
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise RuntimeError("FLUX 3 requires an Apple Silicon Mac.")
        total_gb = int(subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.memsize"])) / 1024**3
    floor = 24 if config.mflux_quantize == 4 else 32
    if total_gb < floor:
        raise ValueError(f"FLUX 3 Q{config.mflux_quantize} needs a {floor} GB Mac. "
                         "Q4 is available on 24 GB Macs; 32 GB or more is recommended.")
    if width % 16 or height % 16 or width * height > 1024**2:
        raise ValueError("FLUX 3 supports up to 1024 x 1024 pixels, in multiples of 16.")
    if len(refs) > 10:
        raise ValueError("FLUX 3 supports up to ten reference images.")
    if total_gb < 32 and (width * height > 768**2 or len(refs) > 1):
        raise ValueError("On a 24 GB Mac, use Q4, a small image size and at most one reference.")


def status(engine, total_gb):
    recipe = PRESETS[engine]
    pack = PACKS[f"{recipe['variant']}-q{recipe['quantize']}"]
    complete = cached_bytes(pack)
    done = downloaded_bytes(pack)
    floor = 24 if recipe["quantize"] == 4 else 32
    return dict(engine=engine, repo_id=pack["repo"], cached=complete == pack["bytes"],
                partial=done > 0 and complete < pack["bytes"], partial_gb=done / 1e9,
                family_installed=True, worker_installed=worker_ready(),
                lazy_install=True, download_gb=pack["bytes"] / 1e9,
                ram_need_gb={4: 18.5, 6: 20.6, 8: 22.8}[recipe["quantize"]],
                mac_gb_needed=floor, fits_mac=total_gb >= floor,
                sec_per_image=0, cold_start_sec=0, samples_source="unmeasured",
                gated=False, license_url=LICENCE_URL)


def _redact(line):
    return re.sub(r"hf_[A-Za-z0-9]+", "[redacted]", line)


def _run(cmd, env, on_log, request=None, timeout=7200, guard=()):
    """Stream progress and share the studio's cancellation registry, including install."""
    import image_engine
    events = queue.Queue()
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", start_new_session=True)
    try:
        image_engine._register_active_proc(proc)
    except BaseException:
        proc.stdin.close()
        proc.stdout.close()
        raise
    tail, receipts = [], []
    def read():
        try:
            for line in proc.stdout:
                events.put(line.rstrip())
        finally:
            events.put(None)
    threading.Thread(target=read, daemon=True).start()
    started = time.monotonic()
    try:
        if request is not None:
            proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.close()
        while True:
            check = image_engine._CANCEL_CHECK
            if check and check():
                raise image_engine.ImageJobCancelled("FLUX 3 stopped.")
            if time.monotonic() - started > timeout:
                raise RuntimeError("FLUX 3 timed out. Retry with a smaller image or fewer references.")
            for path, reserve in guard:
                require_space(path, reserve=reserve)
            try:
                line = events.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                break
            if line.startswith("FLUX3_RESULT "):
                receipts.append(json.loads(line[len("FLUX3_RESULT "):]))
            else:
                line = _redact(line)
                tail = (tail + [line])[-15:]
                if on_log:
                    on_log(line)
        rc = proc.wait(timeout=10)
        if rc < 0:
            raise image_engine.ImageJobCancelled("FLUX 3 stopped.")
        if rc:
            raise RuntimeError("FLUX 3 did not finish: " + "\n".join(tail))
        return receipts
    finally:
        if proc.poll() is None:
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)
        image_engine._unregister_active_proc(proc)
        proc.stdout.close()


def generate(prompt, n, width, height, output_dir, base_seed, config, refs, on_log, install_env=None):
    validate(config, width, height, refs)
    env = clean_env(install_env)
    pack = pack_for(config)
    # The reserve guards GROWTH only: nothing to fetch or install, no reserve.
    missing = pack["bytes"] - downloaded_bytes(pack, env)
    if missing > 0:
        require_space(cache_root(env), missing)
    require_space(output_dir, OUTPUT_HEADROOM, reserve=0)
    installing = not worker_ready()
    if installing and not worker_python().is_file():
        require_space(worker_root(), INSTALL_BUDGET)
    # The bootstrap checks exact package versions without importing MLX.
    _run([sys.executable, "-S", str(ROOT / "scripts/pinokio/flux3_env.py"), str(worker_root())], env, on_log,
         guard=[(worker_root(), RESERVE)] if installing else [])
    first_seed = base_seed if base_seed is not None else secrets.randbelow(2**31)
    request = dict(prompt=prompt, seeds=[first_seed + i for i in range(n)],
                   width=width, height=height, output_dir=str(output_dir), refs=refs,
                   variant=config.flux3_variant, quantize=config.mflux_quantize)
    result = _run([str(worker_python()), str(ROOT / "scripts/flux3_worker.py")], env, on_log, request,
                  timeout=1800 + 3600 * n,
                  # The worker guards its own downloads with RESERVE; here only
                  # the output volume matters, so a full disk still stops it.
                  guard=[(output_dir, OUTPUT_HEADROOM)])
    if len(result) != n or any(not Path(r["png_path"]).is_file() for r in result):
        raise RuntimeError("FLUX 3 returned no complete image. Check the log and retry.")
    return result
