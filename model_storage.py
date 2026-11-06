"""Conservative storage inventory. Cleanup only unlinks reviewed, retired files.

Never infer retirement from age, an unselected engine, or a missing registry row.
The initial list comes from post_update.sh's pre-Y1.024 trim list, excluding
ALL LoRAs. Future swaps must add exact relative filenames here after review.
"""
from pathlib import Path
import hashlib
import json
import os
import stat
from scripts import model_path_guard

RETIRED = {
    'ltx-2.3-mlx-q4': (
        'transformer-distilled-1.1.safetensors',
        'spatial_upscaler_x1_5_v1_0.safetensors',
        'temporal_upscaler_x2_v1_0.safetensors',
    ),
    'ltx-2.3-mlx-q8': (
        'transformer-distilled.safetensors',
        'transformer-distilled-1.1.safetensors',
        'spatial_upscaler_x1_5_v1_0.safetensors',
        'temporal_upscaler_x2_v1_0.safetensors',
    ),
}


def overlaps(path, protected):
    path = path.resolve()
    return any(path == p.resolve() or path.is_relative_to(p.resolve())
               or p.resolve().is_relative_to(path) for p in protected)


def measure(folder):
    """Do not follow links or count shared hardlinks as reclaimable space."""
    size = 0
    for base, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
        for name in files:
            try:
                info = (Path(base) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    size += info.st_size
            except OSError:
                pass
    return size


def candidates(root, protected=(), required=()):
    root = Path(root).resolve()
    required = {Path(p).resolve() for p in required}
    found = []
    for folder, names in RETIRED.items():
        parent = root / folder
        if (parent.is_symlink() or overlaps(parent, protected)
                or model_path_guard.problem(root, parent, write=True)):
            continue
        for name in names:
            path = parent / name
            if path in required or 'lora' in name.lower():
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                found.append((path, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
    return found


def fingerprint(items):
    return hashlib.sha256(json.dumps([[str(p), *s] for p, *s in items]).encode()).hexdigest()


def inventory(root, known=(), protected=(), required=(), extra=()):
    root = Path(root).resolve()
    items = candidates(root, protected, required)
    by_folder = {}
    for p, _, _, size, _ in items:
        by_folder[p.parent] = by_folder.get(p.parent, 0) + size
    try:
        folders = set(root.iterdir()) if root.is_dir() else set()
    except OSError:
        folders = set()
    folders.update(Path(p) for p in extra if Path(p).is_dir())
    rows = []
    seen = set()
    for folder in sorted(folders):
        if (not folder.is_dir() and not folder.is_symlink()) or folder.resolve() in seen:
            continue
        seen.add(folder.resolve())
        protected_row = folder.is_symlink() or overlaps(folder, protected)
        retired = by_folder.get(folder, 0) if not protected_row else 0
        used = folder.resolve() in {Path(p).resolve() for p in known}
        status = 'Protected' if protected_row else ('In use' if used else 'Unrecognized, kept')
        if retired:
            status += ' · superseded files'
        reason = model_path_guard.problem(root, folder)
        write_reason = model_path_guard.problem(root, folder, write=True)
        if reason or write_reason:
            status = reason or write_reason
            retired = 0
        rows.append({'name': folder.name, 'paths': [str(folder)],
                     'bytes': measure(folder), 'superseded_bytes': retired,
                     'status': status})
    return {'rows': rows, 'reclaimable': sum(i[3] for i in items),
            'confirmation': fingerprint(items)}


def cleanup(root, confirmation, protected=(), required=()):
    """Called with folder writer locks held. No recursive deletion, ever."""
    items = candidates(root, protected, required)
    if not confirmation or confirmation != fingerprint(items):
        raise ValueError('Storage changed. Refresh and confirm again.')
    freed = 0
    for path, device, inode, size, modified in items:
        # Open the directory without following a substituted symlink, then
        # unlink relative to that descriptor so a parent rename cannot escape.
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            current = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            if current.st_nlink != 1 or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (device, inode, size, modified):
                raise ValueError('Storage changed. Refresh and confirm again.')
            os.unlink(path.name, dir_fd=fd)
            freed += size
        finally:
            os.close(fd)
    return freed
