"""Lazy, locked FLUX 3 install. Called by the panel and Pinokio, never the studio pip."""
import fcntl
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from flux3_engine import INSTALL_BUDGET, clean_env, require_space, requirements_digest


def check_destination(root):
    target = (root / "env").resolve()
    if target in {Path(sys.prefix).resolve(), (ROOT / "ltx-2-mlx/env").resolve()}:
        raise RuntimeError("FLUX 3 cannot use the studio environment. Choose a separate folder.")
    if (root / "env").exists() and not (root / ".phosphene-flux3-worker").is_file():
        raise RuntimeError("This folder already contains another environment. Choose a new FLUX 3 folder.")


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("FLUX 3 requires an Apple Silicon Mac.")
    if int(platform.mac_ver()[0].split(".")[0]) < 14:
        raise RuntimeError("FLUX 3 requires macOS 14 or later.")
    root = Path(sys.argv[1]).resolve()
    check_destination(root)
    root.mkdir(parents=True, exist_ok=True)
    py = root / "env/bin/python"
    requirements = Path(__file__).with_name("flux3_requirements.txt")
    expected = dict(line.split("==", 1) for line in requirements.read_text().splitlines()
                    if line and not line.startswith("#"))
    check = ("import importlib.metadata as m,json; "
             "print(json.dumps({p:m.version(p) for p in " + repr(list(expected)) + "}))")
    def mark_ready():
        temp = root / f".ready.{os.getpid()}"
        temp.write_text(requirements_digest() + "\n")
        os.replace(temp, root / ".ready")

    def ready():
        if not py.is_file():
            return False
        p = subprocess.run([str(py), "-I", "-c", check], capture_output=True,
                           text=True, errors="replace", env=clean_env(), timeout=30)
        try:
            return p.returncode == 0 and json.loads(p.stdout) == expected
        except ValueError:
            return False
    with (root / ".install.lock").open("a") as lock:
        print("FLUX 3: checking its separate image engine...", flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        check_destination(root)
        (root / ".phosphene-flux3-worker").write_text("Phosphene FLUX 3 worker\n")
        if ready():
            mark_ready()
            return
        (root / ".ready").unlink(missing_ok=True)
        require_space(root, INSTALL_BUDGET)
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("uv was not found. Start Phosphene through Pinokio and retry.")
        print("FLUX 3: installing its separate image engine. Stop cancels this step.", flush=True)
        if not py.is_file():
            subprocess.run([uv, "venv", "--python", "3.12", str(root / "env")], check=True, env=clean_env())
        subprocess.run([uv, "pip", "install", "--no-cache", "--python", str(py),
                        "-r", str(requirements)], check=True, env=clean_env())
        if not ready():
            raise RuntimeError("FLUX 3 packages did not match their pins. Retry the install.")
        mark_ready()
        print("FLUX 3: image engine ready.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FATAL error: {exc}", flush=True)
        sys.exit(1)
