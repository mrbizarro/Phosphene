"""Mac-app choices belong to persistent state, outside verified Studio code."""
import json
import os
from pathlib import Path
import tempfile


def generation_path():
    home = Path(os.environ.get("PHOSPHENE_HOME") or Path.home() / "Library/Application Support/Phosphene")
    return Path(os.environ.get("LTX_STATE_DIR") or home / "state") / "model-generation.json"


def load_generation():
    try:
        value = json.loads(generation_path().read_text())["version"]
        return value if value in ("ltx23", "ltx25") else ""
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def save_generation(value):
    if value not in ("ltx23", "ltx25"):
        raise ValueError("unknown video generation")
    path = generation_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".generation-", delete=False) as f:
        pending = Path(f.name)
        json.dump({"version": value}, f)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)
