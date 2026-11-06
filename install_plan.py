"""Runtime-only install preflight and persistent, requested model queues.

The registry's before_start and after_start lists are empty: opening the app
never opts into model downloads. A feature request writes the queue; restart
resumes that request. Legacy install.js markers are retained but ignored.
The compatibility `defer` command never adds models to a queue.
Stdlib only, so Pinokio can run the preflight before creating the venv.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER_NAME = "install_deferred.json"
GB = 1_000_000_000

# The speeds the size line quotes. Decimal megabits, as ISPs sell them.
QUOTED_MBPS = (100, 300, 1000)


# ---- registry ----------------------------------------------------------------

def load_registry(root: Path | str | None = None) -> dict:
    root = Path(root) if root else HERE
    return json.loads((root / "required_files.json").read_text(encoding="utf-8"))


def install_block(reg: dict) -> dict:
    blk = reg.get("install") or {}
    return {
        "before_start": [str(k) for k in (blk.get("before_start") or [])],
        "after_start": [str(k) for k in (blk.get("after_start") or [])],
        "env_gb": float(blk.get("env_gb") or 0),
        "headroom_gb": float(blk.get("headroom_gb") or 0),
        "background_floor_gb": float(blk.get("background_floor_gb") or 0),
    }


def repos_by_key(reg: dict) -> dict:
    return {r.get("key"): r for r in (reg.get("repos") or []) if isinstance(r, dict)}


def min_bytes(reg: dict) -> int:
    try:
        return int(reg.get("min_size_bytes") or 1024)
    except (TypeError, ValueError):
        return 1024


# ---- bytes on disk -----------------------------------------------------------

def expected_bytes(repo: dict) -> int:
    """The pack's size: per-file sizes where the registry has them, else size_gb."""
    sizes = repo.get("file_sizes") or {}
    known = sum(int(v) for v in sizes.values() if isinstance(v, (int, float)))
    declared = int(float(repo.get("size_gb") or 0) * GB)
    weights = [f for f in (repo.get("files") or []) if str(f).endswith(".safetensors")]
    if known and weights and all(f in sizes for f in weights):
        # Exact byte counts for every weight file beat a size_gb rounded up to
        # a whole GB (the three 2.3 IC-LoRAs say 1, 2 and 1 for 0.33/1.31/0.65).
        return known
    return max(known, declared)


def repo_missing(root: Path, repo: dict, min_b: int = 1024) -> list:
    """Declared files absent or under min_b bytes - the same rule pinokio.js's
    repoComplete() and the panel's _repo_missing() apply."""
    base = Path(root) / str(repo.get("local_dir") or "")
    sizes = repo.get("file_sizes") or {}
    sidecars = list(repo.get("sidecars") or [])
    out = []
    for name in list(repo.get("files") or []) + sidecars:
        if name in out:
            continue
        try:
            st = (base / name).stat()
        except OSError:
            out.append(name)
            continue
        want = sizes.get(name)
        # Same rule as the panel's pack_file_ok (fleet #206): an exact size when
        # the registry declares one, any bytes for a loader sidecar, else min_b.
        if isinstance(want, (int, float)) and want > 0:
            bad = st.st_size != int(want)
        elif name in sidecars:
            bad = st.st_size <= 0
        else:
            bad = st.st_size < min_b
        if bad:
            out.append(name)
    return out


def remaining_bytes(root: Path, repo: dict, min_b: int = 1024) -> int:
    """Roughly what is still to download for one pack. 0 when complete.

    Only the pack's DECLARED files count as present, so three IC-LoRAs sharing
    one directory never credit each other. Partial downloads in a downloader's
    own local staging cache count toward resumable progress.
    """
    if not repo_missing(root, repo, min_b):
        return 0
    base = Path(root) / str(repo.get("local_dir") or "")
    sizes = repo.get("file_sizes") or {}
    have = 0
    for name in repo.get("files") or []:
        n = 0
        # The mirror fetcher appends shards to `<name>.partial` and resumes
        # from it, so a half-downloaded file's bytes are bytes NOT to fetch
        # again (Codex, ov-install: Resume was asked for the whole file).
        rel = Path(name)
        # Hugging Face stages local-dir files beside their download metadata.
        # Count that resumable file too, or a multi-GB transfer reads as 0 GB.
        cache = base / ".cache" / "huggingface" / "download" / rel.parent
        candidates = [base / name, base / (name + ".partial")]
        candidates.extend(cache.glob(rel.name + ".*.incomplete"))
        for cand in candidates:
            try:
                n = max(n, cand.stat().st_size)
            except OSError:
                pass
        cap = sizes.get(name)
        have += min(n, int(cap)) if isinstance(cap, (int, float)) else n
    return max(0, expected_bytes(repo) - have)


# fetch_pack_release.py keeps one downloaded shard beside the `.partial` it is
# appending to, so a mirrored pack briefly needs one shard more than it adds.
SHARD_BYTES = 1_900_000_000


def peak_overhead(repo: dict) -> int:
    return SHARD_BYTES if (repo.get("mirror") or {}).get("kind") == "github-release" else 0


def models_volume_probe(root: Path, *, models_dir: Path | None = None) -> Path:
    """The nearest existing directory on the volume the weights land on.

    mlx_models/ is an fs.link symlink into Pinokio's drive on a real install,
    which may sit on another volume than the app folder - measure THAT one.
    """
    p = Path(models_dir) if models_dir is not None else Path(root) / "mlx_models"
    try:
        p = p.resolve()
    except OSError:
        pass
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def free_bytes(root: Path, *, models_dir: Path | None = None) -> int | None:
    try:
        return int(shutil.disk_usage(str(models_volume_probe(root, models_dir=models_dir))).free)
    except OSError:
        return None


def minutes_at(nbytes: int, mbps: float) -> float:
    return (nbytes * 8.0) / (mbps * 1_000_000.0) / 60.0


def fmt_gb(nbytes: float) -> str:
    gb = nbytes / GB
    if gb >= 100:
        return f"{gb:.0f} GB"
    if gb >= 0.05:
        return f"{gb:.1f} GB"
    return f"{max(gb * 1000, 1):.0f} MB"


def fmt_minutes(m: float) -> str:
    if m < 1:
        return "under a minute"
    if m < 60:
        return f"~{round(m)} min"
    h, mm = divmod(int(round(m)), 60)
    return f"~{h} h {mm:02d} min" if mm else f"~{h} h"


# ---- the plan ----------------------------------------------------------------

def plan(root: Path | str | None = None, reg: dict | None = None) -> dict:
    root = Path(root) if root else HERE
    reg = reg if reg is not None else load_registry(root)
    blk = install_block(reg)
    by_key = repos_by_key(reg)
    mb = min_bytes(reg)

    def rows(keys):
        out = []
        for k in keys:
            repo = by_key.get(k)
            if not repo:
                continue
            out.append({"key": k, "name": repo.get("name") or k,
                        "size": expected_bytes(repo),
                        "remaining": remaining_bytes(root, repo, mb)})
        return out

    before, after = rows(blk["before_start"]), rows(blk["after_start"])
    before_left = sum(r["remaining"] for r in before)
    after_left = sum(r["remaining"] for r in after)
    # The venv is built before the weights. Count it only when it is not there
    # yet: a Resume Install whose venv already runs needs no room for it.
    venv_ready = (root / "ltx-2-mlx" / "env" / "pyvenv.cfg").exists()
    env_bytes = 0 if venv_ready else int(blk["env_gb"] * GB)
    headroom = int(blk["headroom_gb"] * GB)
    need_start = before_left + env_bytes + headroom
    need_all = need_start + after_left
    return {
        "before": before, "after": after,
        "before_total": sum(r["size"] for r in before),
        "after_total": sum(r["size"] for r in after),
        "before_left": before_left, "after_left": after_left,
        "env_bytes": env_bytes, "headroom": headroom,
        "need_start": need_start, "need_all": need_all,
        "background_floor": int(blk["background_floor_gb"] * GB),
        "free": free_bytes(root), "probe": str(models_volume_probe(root)),
    }


def time_line(nbytes: int) -> str:
    parts = []
    for mbps in QUOTED_MBPS:
        label = f"{mbps // 1000} Gbps" if mbps >= 1000 else f"{mbps} Mbps"
        parts.append(f"{fmt_minutes(minutes_at(nbytes, mbps))} at {label}")
    return ", ".join(parts)


def preflight_report(p: dict) -> tuple[int, list]:
    """(exit code, lines). Exit 1 only when the free space is KNOWN to be short
    for the part before Start. Unknown free space is fail-open - a preflight
    that cannot read the disk must never be what blocks a fine install."""
    bar = "=" * 66
    names_before = ", ".join(f"{r['name']} {fmt_gb(r['size'])}" for r in p["before"])
    names_after = ", ".join(r["name"] for r in p["after"])
    lines = [bar, "PHOSPHENE INSTALL - what downloads, how long, how much room"]
    if not p["before"]:
        lines.append("Before Start: no model downloads. Choose a feature inside the app.")
    elif not p["before_left"]:
        lines.append(f"Before Start: already on disk ({names_before})")
    elif p["before_left"] < p["before_total"]:
        lines.append(f"Before Start: {fmt_gb(p['before_left'])} still to download "
                     f"(of {fmt_gb(p['before_total'])}: {names_before})")
    else:
        lines.append(f"Before Start: {fmt_gb(p['before_total'])} ({names_before})")
    if p["env_bytes"]:
        lines.append(f"              plus the Python engine, ~{fmt_gb(p['env_bytes'])} on disk")
    if p["before_left"]:
        lines.append(f"Time until Start (download only): {time_line(p['before_left'])}")
    if p["after_left"]:
        lines.append(f"After Start: {fmt_gb(p['after_left'])} more downloads in the background "
                     f"inside the panel while you work ({names_after}).")
    free = p["free"]
    if free is None:
        lines += [f"Disk: could not read the free space - continuing. Phosphene needs "
                  f"about {fmt_gb(p['need_start'])} free to start.", bar]
        return 0, lines
    if free < p["need_start"]:
        short = p["need_start"] - free
        # The "error:" is load-bearing. Pinokio 8.2 IGNORES a step's exit code: a
        # shell.run step stops the script only when its output matches /error:/i
        # or /errno /i (pinokiod kernel/shells.js, default `on` handlers). An
        # `exit 1` alone would print this and march on into a 27 GB download.
        lines = [bar, "FATAL error: not enough disk space to install Phosphene.",
                 "NOT ENOUGH DISK SPACE TO INSTALL PHOSPHENE",
                 f"Phosphene needs about {fmt_gb(p['need_start'])} free before it can start "
                 f"({fmt_gb(p['before_left'])} of models"
                 + (f" + ~{fmt_gb(p['env_bytes'])} for the Python engine" if p["env_bytes"] else "")
                 + f" + {fmt_gb(p['headroom'])} slack).",
                 f"This Mac has {fmt_gb(free)} free on the drive that holds the models.",
                 f"Free up at least {fmt_gb(short)}, then click Install again.",
                 "Nothing large has been downloaded yet, and whatever was already",
                 "downloaded is kept.", bar]
        return 1, lines
    lines.append(f"Disk: needs ~{fmt_gb(p['need_start'])} free to start, "
                 f"~{fmt_gb(p['need_all'])} for everything. This Mac has {fmt_gb(free)} free. OK.")
    if p["after_left"] and free < p["need_all"] + p["background_floor"]:
        lines.append(f"Note: the {fmt_gb(p['after_left'])} of extras wait until the drive has "
                     f"{fmt_gb(p['after_left'] + p['background_floor'])} free; the panel "
                     f"keeps at least {fmt_gb(p['background_floor'])} free for your renders.")
    lines.append(bar)
    return 0, lines


# ---- the marker --------------------------------------------------------------

def state_dir(root: Path | str | None = None) -> Path:
    """Where the panel keeps state - the same rule as the panel's STATE_DIR."""
    env = (os.environ.get("LTX_STATE_DIR") or "").strip()
    if env:
        return Path(env)
    return (Path(root) if root else HERE) / "state"


# One lock for every read-modify-write of the marker: the panel's background
# thread drops keys while a Download click moves one to the front, and an
# atomic rename alone does not stop the later writer from resurrecting what
# the earlier one removed (Codex, ov-install).
import threading  # noqa: E402
MARKER_LOCK = threading.RLock()


def read_marker(sdir: Path) -> dict | None:
    try:
        d = json.loads((Path(sdir) / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("pending"), list):
        return None
    d["pending"] = [str(k) for k in d["pending"] if isinstance(k, str) and k]
    return d


def write_marker(sdir: Path, data: dict) -> None:
    sdir = Path(sdir)
    sdir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".install_deferred.", dir=str(sdir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, str(sdir / MARKER_NAME))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def request_packs(sdir: Path, keys: list) -> list:
    """Persist only explicit choices. Old automatic queues are not consent."""
    with MARKER_LOCK:
        old = read_marker(sdir) or {}
        pending = old.get("pending", []) if old.get("created_by") == "feature-request" else []
        pending = list(dict.fromkeys(pending + keys))
        write_marker(sdir, {"schema": 1, "created_by": "feature-request",
                            "created_ts": time.time(), "pending": pending})
        return pending


def stop_requested_packs(sdir: Path) -> None:
    with MARKER_LOCK:
        write_marker(sdir, {"schema": 1, "created_by": "feature-request", "pending": []})


def drop_from_marker(sdir: Path, key: str) -> list:
    """Remove one key; the marker file goes when nothing is left. Returns pending."""
    with MARKER_LOCK:
        d = read_marker(sdir)
        if d is None:
            return []
        d["pending"] = [k for k in d["pending"] if k != key]
        if d["pending"]:
            write_marker(sdir, d)
        else:
            try:
                (Path(sdir) / MARKER_NAME).unlink()
            except OSError:
                pass
        return d["pending"]


def prioritize_in_marker(sdir: Path, key: str) -> bool:
    """Move one pending key to the front. False when it is not pending."""
    with MARKER_LOCK:
        d = read_marker(sdir)
        if d is None or key not in d["pending"]:
            return False
        d["pending"] = [key] + [k for k in d["pending"] if k != key]
        write_marker(sdir, d)
        return True


def defer(root: Path | str | None = None, sdir: Path | None = None) -> list:
    """Compatibility for an older install.js already loaded by Pinokio.

    No automatic downloads, and no changes to a queue the user requested.
    """
    marker = read_marker(Path(sdir) if sdir else state_dir(root)) or {}
    return marker.get("pending", []) if marker.get("created_by") == "feature-request" else []


# ---- CLI ---------------------------------------------------------------------

def _main(argv: list) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="install_plan.py")
    ap.add_argument("command", choices=("preflight", "defer", "show"))
    ap.add_argument("--root", default=str(HERE))
    a = ap.parse_args(argv)
    root = Path(a.root)
    if a.command == "defer":
        try:
            pending = defer(root)
        except Exception as exc:  # noqa: BLE001 - never fail the install over this
            print(f"WARN: could not queue the background downloads ({exc}). "
                  "Settings -> Models offers each one.")
            return 0
        if pending:
            reg = load_registry(root)
            by_key = repos_by_key(reg)
            mb = min_bytes(reg)
            left = sum(remaining_bytes(root, by_key[k], mb) for k in pending if k in by_key)
            print(f"Queued {len(pending)} extra pack(s), {fmt_gb(left)}, to download in the "
                  "background after Start: " + ", ".join(by_key[k].get("name") or k
                                                          for k in pending if k in by_key))
        else:
            print("Models download when you choose a feature inside Phosphene.")
        return 0
    try:
        p = plan(root)
    except Exception as exc:  # noqa: BLE001 - fail open
        print(f"Install preflight skipped - could not read the install plan ({exc}).")
        return 0
    if a.command == "show":
        print(json.dumps(p, indent=2))
        return 0
    code, lines = preflight_report(p)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
