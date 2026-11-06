"""Reuse a model pack that a Pinokio install of Phosphene already holds (no download, no extra disk).

Read-only on Pinokio: the files are cloned (APFS copy-on-write, same volume) into this studio's own
models folder; Pinokio's copy is never touched. Stdlib only. Never runs inside Pinokio itself (its models
folder IS the source, so nothing is "elsewhere").
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

MIN_BYTES = 1  # a declared file must exist and not be empty


def pinokio_homes(env=None, home=None) -> list[Path]:
    env = os.environ if env is None else env
    me = Path(home) if home else Path.home()
    out: list[Path] = []
    if env.get("PHOSPHENE_PINOKIO_HOME"):
        out.append(Path(env["PHOSPHENE_PINOKIO_HOME"]))
    for cfg in (me / ".pinokio/config.json", me / "Library/Application Support/pinokio/config.json"):
        try:
            h = json.loads(cfg.read_text()).get("home")
            if h:
                out.append(Path(h))
        except (OSError, ValueError, AttributeError):
            pass
    out.append(me / "pinokio")
    try:
        out += sorted(Path("/Volumes").glob("*/pinokio"))
    except OSError:
        pass
    seen, uniq = set(), []
    for p in out:
        try:
            k = str(p.resolve())
        except OSError:
            continue
        if k not in seen and p.is_dir():
            seen.add(k)
            uniq.append(Path(k))
    return uniq


def model_roots(own_models: Path, env=None, home=None) -> list[Path]:
    """Every Pinokio `mlx_models` folder this Mac has, except this studio's own."""
    own = None
    try:
        own = own_models.resolve()
    except OSError:
        pass
    roots = []
    for h in pinokio_homes(env, home):
        api = h / "api"
        try:
            apps = sorted(api.glob("phosphene*.git"))
        except OSError:
            continue
        for a in apps:
            m = a / "mlx_models"
            try:
                r = m.resolve()
            except OSError:
                continue
            if m.is_dir() and r != own and r not in roots:
                roots.append(r)
    return roots


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return -1


def pack_source(repo: dict, roots: list[Path]) -> dict | None:
    """The first Pinokio folder that holds every declared file of `repo`, or None."""
    parts = Path(str(repo.get("local_dir") or "")).parts
    if not parts or parts[0] != "mlx_models":
        return None
    rel = Path(*parts[1:]) if len(parts) > 1 else Path(".")
    files = list(repo.get("files") or [])
    if not files:
        return None
    for root in roots:
        d = root / rel
        sizes = [_size(d / f) for f in files]
        if all(s >= MIN_BYTES for s in sizes):
            return {"dir": str(d), "bytes": sum(sizes), "files": files}
    return None


def _nearest_dev(p: Path) -> int | None:
    q = p
    while not q.exists():
        if q.parent == q:
            return None
        q = q.parent
    return q.stat().st_dev


def same_volume(src: Path, dest: Path) -> bool:
    try:
        return src.stat().st_dev == _nearest_dev(dest)
    except OSError:
        return False


def clone_pack(src: dict, dest: Path) -> tuple[bool, str]:
    """Clone the declared files into `dest`. Files already there stay (an existing file is never replaced)."""
    s = Path(src["dir"])
    if not same_volume(s, dest):
        return False, ("The copy in Pinokio is on another drive, so using it would take new disk space. "
                       "Download it instead, or move Pinokio's models onto this drive.")
    dest.mkdir(parents=True, exist_ok=True)
    done: list[Path] = []
    for f in src["files"]:
        to = dest / f
        if to.exists() and _size(to) >= MIN_BYTES:
            continue
        to.parent.mkdir(parents=True, exist_ok=True)
        tmp = to.with_name(to.name + ".reuse.partial")
        try:
            tmp.unlink()
        except OSError:
            pass
        r = subprocess.run(["/bin/cp", "-c", str(s / f), str(tmp)], capture_output=True, text=True)
        if r.returncode != 0:
            for p in done + [tmp]:
                try:
                    p.unlink()
                except OSError:
                    pass
            return False, "Could not reuse the file from Pinokio (" + (r.stderr.strip()[:120] or "copy failed") + ")."
        os.replace(tmp, to)
        done.append(to)
    return True, ""
