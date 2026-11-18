"""Where finished videos and pictures are kept (Settings > Saved files).

Asked for by a Mac app user who works from an external SSD (ticket S5BHS43A,
18 Nov 2026). The design keeps every render safe first and tidy second:

* Renders are made exactly where they always were (the Studio's own outputs
  folder and picture library). Nothing in the render paths changes, so a
  drive that is unplugged in the middle of a queue can never fail or lose a
  render.
* Once a file is finished, a background pass copies it to the chosen folder,
  flushes it, reads it back and compares SHA-256 with the original. Only a
  verified copy replaces the original, and it is replaced by a link at the
  very same path, so the gallery, the Editor, Extend, Upscale, sidecars and
  every saved project keep working without knowing anything moved.
* When the chosen folder is not reachable (drive unplugged, renamed, full),
  files simply stay on this Mac and are listed as waiting; they are copied
  over by themselves when the folder is back. The Studio says so in plain
  words.
* Old files stay where they are. Only files finished after the folder was
  chosen are copied.

The settings file lives in the Studio's state folder and remembers every
folder ever chosen, so links made into an earlier choice stay servable after
the person switches folders or goes back to the default.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

SETTINGS_NAME = "outputs_folder.json"

#: What gets copied: the finished media itself. Sidecars (`<file>.json`) go
#: with their media so a lookup through either path finds them.
VIDEO_EXTS = frozenset({".mp4", ".mov", ".m4v", ".webm"})
AUDIO_EXTS = frozenset({".wav", ".flac", ".mp3", ".m4a"})
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS

#: A file must have been quiet this long before it is copied (a writer that
#: is still flushing, a sidecar written right after the media).
SETTLE_SEC = 20.0

PARTIAL_SUFFIX = ".phos-copying"

_LOCK = threading.RLock()        # settings file + the status dict; held briefly
_SWEEP_LOCK = threading.Lock()   # one copy pass at a time; never held by /status
_STATUS: dict = {"waiting": 0, "moved": 0, "last_error": "", "last_sweep": 0.0,
                 "reachable": True, "offline_since": 0.0}

# Folders a person must never pick: the disk itself, their whole home, the
# system's own places. Anything under them that is a normal folder is fine.
_REFUSED_EXACT = ("/", "/Volumes", "/Users", "/System", "/Library",
                  "/Applications", "/private", "/usr", "/bin", "/sbin",
                  "/etc", "/var", "/tmp", "/opt", "/cores", "/dev")
_REFUSED_UNDER = ("/System", "/Library", "/Applications", "/usr", "/bin",
                  "/sbin", "/etc", "/dev", "/cores", "/private/etc",
                  "/private/var/db", "/private/var/root", "/private/var/log")


# ---------------------------------------------------------------- settings

def _settings_path(state_dir) -> Path:
    return Path(state_dir) / SETTINGS_NAME


def load(state_dir) -> dict:
    try:
        data = json.loads(_settings_path(state_dir).read_text())
        if isinstance(data, dict):
            data.setdefault("past", [])
            return data
    except (OSError, ValueError):
        pass
    return {"path": None, "since": 0.0, "past": []}


def _save(state_dir, data: dict) -> None:
    p = _settings_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + "." + uuid.uuid4().hex[:6] + ".tmp")
    with tmp.open("w") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def chosen(state_dir) -> Path | None:
    raw = load(state_dir).get("path")
    return Path(raw) if raw else None


def all_roots(state_dir) -> list[Path]:
    """Every folder this install has ever copied finished files into. The
    file routes accept media under these, because the links in the outputs
    folder point at them. Never resolved here: an unplugged drive must not
    make a status call hang or raise."""
    data = load(state_dir)
    out: list[Path] = []
    for raw in [data.get("path")] + list(data.get("past") or []):
        if raw and Path(raw) not in out:
            out.append(Path(raw))
    return out


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def validate_choice(raw: str, forbidden: list) -> Path:
    """Return the folder to use, or raise ValueError with a plain sentence."""
    text = (raw or "").strip()
    if text.startswith("file://"):
        from urllib.parse import unquote, urlparse
        text = unquote(urlparse(text).path)
    if not text:
        raise ValueError("Choose a folder first.")
    p = Path(os.path.expanduser(text))
    if not p.is_absolute():
        raise ValueError("Enter the full path of a folder, starting with /.")
    try:
        p = p.resolve()
    except OSError:
        raise ValueError("That folder can't be reached. Check that the drive is connected.")
    if not p.is_dir():
        raise ValueError("That folder doesn't exist. Check that the drive is connected, "
                         "or create the folder first.")
    s = str(p)
    home = str(_home().resolve())
    if s in _REFUSED_EXACT or s == home or any(s == r or s.startswith(r + "/") for r in _REFUSED_UNDER):
        raise ValueError("Pick a folder of its own, for example a folder on your external drive. "
                         "A whole disk, your home folder or a system folder can't be used.")
    if s.startswith("/Volumes/") and s.count("/") == 2:
        # A whole external drive picked: keep its root tidy with a folder of
        # our own on it. Safe to create: the drive is mounted right now.
        if not os.path.ismount(s):
            raise ValueError("That drive isn't connected. Connect it and try again.")
        p = p / "Phosphene"
        try:
            p.mkdir(exist_ok=True)
        except OSError:
            raise ValueError("Phosphene can't write to that drive. Pick another one.")
        s = str(p)
    for f in forbidden:
        try:
            fr = Path(f).resolve()
        except OSError:
            continue
        if p == fr or p.is_relative_to(fr) or fr.is_relative_to(p):
            raise ValueError("Pick a folder outside Phosphene's own folders.")
    if not os.access(s, os.W_OK):
        raise ValueError("Phosphene can't write to that folder. Pick another one.")
    probe = p / (".phosphene-write-test-" + uuid.uuid4().hex[:6])
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError:
        raise ValueError("Phosphene can't write to that folder. Pick another one.")
    return p


def set_folder(state_dir, raw: str, forbidden: list) -> dict:
    p = validate_choice(raw, forbidden)
    with _LOCK:
        data = load(state_dir)
        old = data.get("path")
        past = [x for x in (data.get("past") or []) if x != str(p)]
        if old and old != str(p) and old not in past:
            past.append(old)
        data.update(path=str(p), since=time.time(), past=past, label=label_for(p))
        _save(state_dir, data)
        _STATUS.update(waiting=0, last_error="", reachable=True, offline_since=0.0)
    return data


def reset_folder(state_dir) -> dict:
    with _LOCK:
        data = load(state_dir)
        old = data.get("path")
        past = list(data.get("past") or [])
        if old and old not in past:
            past.append(old)
        data.update(path=None, since=0.0, past=past, label="")
        _save(state_dir, data)
        _STATUS.update(waiting=0, last_error="", reachable=True, offline_since=0.0)
    return data


def label_for(p: Path) -> str:
    """'the folder Renders on the drive T7' in short: '<drive> › <folder>'."""
    parts = Path(p).parts
    if len(parts) > 2 and parts[1] == "Volumes":
        return f"{parts[2]} › {Path(p).name}" if len(parts) > 3 else parts[2]
    return Path(p).name or str(p)


# ------------------------------------------------------------ reachability

def reachable(root: Path | None) -> bool:
    """True when finished files can be copied there right now.

    An external drive mounts under /Volumes/<name>. When it is unplugged the
    folder disappears, but a careless `mkdir -p` would create an ordinary
    /Volumes/<name> folder on the Mac's own disk and quietly fill it. So a
    path under /Volumes counts only while its volume is really mounted, and
    this module never creates the chosen folder or any of its parents."""
    if root is None:
        return False
    parts = Path(root).parts
    try:
        if len(parts) > 2 and parts[1] == "Volumes":
            if not os.path.ismount(os.path.join("/", "Volumes", parts[2])):
                return False
        return Path(root).is_dir() and os.access(str(root), os.W_OK)
    except OSError:
        return False


def is_under_roots(path: Path, roots: list[Path]) -> bool:
    for r in roots:
        try:
            if path.is_relative_to(r) or (r.exists() and path.is_relative_to(r.resolve())):
                return True
        except OSError:
            continue
    return False


# ----------------------------------------------------------------- copying

def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _free_name(root: Path, name: str) -> Path:
    cand = root / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while cand.exists() or cand.is_symlink() or Path(str(cand) + ".json").exists():
        cand = root / f"{stem}_{n}{suffix}"
        n += 1
    return cand


def _full_sync(fd: int) -> None:
    """Make the bytes reach the drive itself. On macOS fsync() only hands
    them to the drive, whose own cache can still lose them when the cable is
    pulled; F_FULLFSYNC asks the drive to flush that cache too. Some file
    systems (network shares) refuse it: fall back to fsync."""
    try:
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    except (OSError, AttributeError):
        os.fsync(fd)


def _sync_dir(d: Path) -> None:
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        _full_sync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _sha256_from_drive(p: Path) -> str:
    """SHA-256 read with the cache bypassed where the system allows it, so the
    check reads what is on the drive rather than what we just wrote to memory."""
    h = hashlib.sha256()
    fd = os.open(str(p), os.O_RDONLY)
    try:
        try:
            fcntl.fcntl(fd, fcntl.F_NOCACHE, 1)
        except (OSError, AttributeError):
            pass
        while True:
            chunk = os.read(fd, 4 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    finally:
        os.close(fd)
    return h.hexdigest()


def _copy_verified(src: Path, dest: Path) -> None:
    """Copy src to dest through a hidden partial name, flush it to the drive,
    read it back and compare SHA-256, then give it its name and flush the
    folder. Raises OSError/ValueError and leaves no half file under the final
    name."""
    partial = dest.with_name("." + dest.name + PARTIAL_SUFFIX)
    want = _sha256(src)
    try:
        # A partial left by a drive pulled mid-copy would block this file
        # forever; it is ours (hidden name, our suffix), so start over.
        if partial.exists() or partial.is_symlink():
            partial.unlink()
        with src.open("rb") as fi, partial.open("xb") as fo:
            shutil.copyfileobj(fi, fo, 4 * 1024 * 1024)
            fo.flush()
            _full_sync(fo.fileno())
        shutil.copystat(src, partial)
        if partial.stat().st_size != src.stat().st_size or _sha256_from_drive(partial) != want:
            raise ValueError(f"the copy of {src.name} did not match the original")
        os.replace(partial, dest)
        _sync_dir(dest.parent)
    except BaseException:
        try:
            partial.unlink()
        except OSError:
            pass
        raise


def _swap_for_link(src: Path, dest: Path, size: int, mtime: float) -> bool:
    """Replace src by a link to dest, atomically, only if src is unchanged."""
    st = src.lstat()
    if src.is_symlink() or st.st_size != size or st.st_mtime != mtime:
        return False
    tmp = src.with_name("." + src.name + ".phos-link-" + uuid.uuid4().hex[:6])
    os.symlink(str(dest), str(tmp))
    try:
        os.replace(tmp, src)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return True


def deliver(src: Path, root: Path, dest_name: str | None = None) -> Path | None:
    """Copy one finished media file (and its sidecars) into root, verify, and
    leave links at the old paths. Returns the new path, or None when the file
    changed during the copy (it is retried on a later pass)."""
    st = src.lstat()
    dest = _free_name(root, dest_name or src.name)
    _copy_verified(src, dest)
    sidecars = []
    for sc in (Path(str(src) + ".json"), src.with_suffix(".json")):
        if sc != src and sc.is_file() and not sc.is_symlink() and sc not in [s for s, _ in sidecars]:
            sc_dest = Path(str(dest) + ".json") if sc.name == src.name + ".json" else dest.with_suffix(".json")
            sidecars.append((sc, sc_dest))
    if not _swap_for_link(src, dest, st.st_size, st.st_mtime):
        # Rewritten while we copied: drop our copy, try again next pass.
        try:
            dest.unlink()
        except OSError:
            pass
        return None
    for sc, sc_dest in sidecars:
        try:
            sst = sc.lstat()
            if sc_dest.exists():
                continue
            _copy_verified(sc, sc_dest)
            _swap_for_link(sc, sc_dest, sst.st_size, sst.st_mtime)
        except (OSError, ValueError):
            # The media is safe on the drive; its settings record simply
            # stays on this Mac, where the gallery reads it too.
            pass
    return dest


# ------------------------------------------------------------------- sweep

def candidates(output_dir: Path, library_dir: Path, since: float, busy: set,
               busy_after: float | None, now: float) -> list[tuple[Path, str]]:
    """(file, name on the drive) for every finished file that should go."""
    out: list[tuple[Path, str]] = []

    def eligible(p: Path) -> bool:
        try:
            if p.is_symlink() or not p.is_file():
                return False
            if p.suffix.lower() not in MEDIA_EXTS or p.name.startswith("."):
                return False
            if ".partial" in p.name or p.name.endswith(PARTIAL_SUFFIX):
                return False
            st = p.stat()
            if st.st_mtime < since or now - st.st_mtime < SETTLE_SEC:
                return False
            if busy_after is not None and st.st_mtime >= busy_after:
                return False
            if str(p) in busy:
                return False
            for sc in (Path(str(p) + ".json"), p.with_suffix(".json")):
                if sc.is_file() and now - sc.stat().st_mtime < SETTLE_SEC:
                    return False
            return True
        except OSError:
            return False

    try:
        for p in output_dir.iterdir():
            # Extend's downscale cache (`<clip>_dnWxH.mp4` with no sidecar)
            # is an input the next extend reads, not something anyone made.
            stem = p.stem
            if "_dn" in stem and stem.rsplit("_dn", 1)[1].replace("x", "").isdigit() \
                    and not Path(str(p) + ".json").exists():
                continue
            if eligible(p):
                out.append((p, p.name))
    except OSError:
        pass
    if library_dir and library_dir.is_dir():
        try:
            for p in library_dir.rglob("*"):
                rel = p.relative_to(library_dir).parts
                # Pictures people imported are their own files, not renders.
                if "uploads" in rel[:-1]:
                    continue
                if p.suffix.lower() not in IMAGE_EXTS or not eligible(p):
                    continue
                # cand_00_flux.png repeats in every batch folder; the
                # batch's folders (<date>/<ms>) make the name unique and
                # keep the drive folder in time order.
                prefix = "_".join(rel[:-1])
                out.append((p, f"{prefix}_{p.name}" if prefix else p.name))
        except OSError:
            pass
    return out


def sweep(state_dir, output_dir, library_dir, busy: set | None = None,
          busy_after: float | None = None, log=None, limit: int = 200) -> dict:
    """One pass. Never raises. Returns the status dict.

    Copies run under _SWEEP_LOCK only, so /status (which reads the status
    dict under _LOCK) never waits behind a large file going to a slow drive."""
    if not _SWEEP_LOCK.acquire(blocking=False):
        return status(state_dir)
    try:
        return _sweep(state_dir, output_dir, library_dir, busy, busy_after, log, limit)
    finally:
        _SWEEP_LOCK.release()


def _set_status(**kw) -> None:
    with _LOCK:
        _STATUS.update(**kw)


def _sweep(state_dir, output_dir, library_dir, busy, busy_after, log, limit) -> dict:
    now = time.time()
    data = load(state_dir)
    root = Path(data["path"]) if data.get("path") else None
    _set_status(last_sweep=now)
    if root is None:
        _set_status(waiting=0, reachable=True, offline_since=0.0)
        return status(state_dir)
    todo = candidates(Path(output_dir), Path(library_dir) if library_dir else None,
                      float(data.get("since") or 0), busy or set(), busy_after, now)
    if not reachable(root):
        if _STATUS.get("reachable", True):
            _set_status(offline_since=now)
            if log:
                log(f"Saved files folder {root} is not reachable; finished files stay "
                    f"on this Mac until it is back.")
        _set_status(waiting=len(todo), reachable=False)
        return status(state_dir)
    if not _STATUS.get("reachable", True) and log:
        log(f"Saved files folder {root} is back; copying waiting files.")
    _set_status(reachable=True, offline_since=0.0)
    moved, left, err = 0, 0, ""
    for src, name in todo[:limit]:
        try:
            if not reachable(root):
                left += 1
                continue
            need = src.stat().st_size + 64 * 1024 * 1024
            if shutil.disk_usage(str(root)).free < need:
                err = "The saved files folder is full. New files stay on this Mac."
                left += 1
                continue
            if deliver(src, root, name) is not None:
                moved += 1
            else:
                left += 1
        except (OSError, ValueError) as exc:
            left += 1
            err = f"Couldn't copy {src.name}: {exc}. It stays on this Mac and is tried again."
    left += max(0, len(todo) - limit)
    with _LOCK:
        _STATUS["moved"] = int(_STATUS.get("moved") or 0) + moved
        _STATUS.update(waiting=left, last_error=err)
    if moved and log:
        log(f"Copied {moved} finished file(s) to {root} (checked).")
    if err and log:
        log(err)
    return status(state_dir)


def status(state_dir) -> dict:
    data = load(state_dir)
    root = data.get("path")
    with _LOCK:
        s = dict(_STATUS)
    return {
        "path": root,
        "label": data.get("label") or (label_for(Path(root)) if root else ""),
        "since": data.get("since") or 0,
        "reachable": bool(s.get("reachable", True)) if root else True,
        "offline_since": s.get("offline_since") or 0,
        "waiting": int(s.get("waiting") or 0) if root else 0,
        "moved": int(s.get("moved") or 0),
        "last_error": s.get("last_error") or "",
    }


def unlink_dangling_links(paths) -> None:
    """After a delete took the real files, remove links left pointing at
    nothing (the media and its sidecars at their old paths)."""
    for p in paths:
        try:
            p = Path(p)
            if p.is_symlink() and not p.exists():
                p.unlink()
        except OSError:
            continue
