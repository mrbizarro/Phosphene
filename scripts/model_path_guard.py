"""Read-only checks for existing model destinations. Never repair a path by mkdir."""
import os
from pathlib import Path

REMEDY = ('Restore access to the model folder at the same path, then try again. '
          'Existing files and downloaded parts are kept.')
_seen = set()


def problem(root, target=None, *, write=False, allow_new=False):
    root = Path(root).absolute()
    target = Path(target).absolute() if target is not None else root
    # Inspect lexical ancestors before resolving: resolve() hides broken links.
    for path in dict.fromkeys([root, *root.parents, target, *target.parents]):
        if path.is_symlink() and not path.is_dir():
            return 'Model folder unavailable. ' + REMEDY
    if not root.is_dir():
        if not allow_new or str(root) in _seen or root.exists():
            return 'Model folder unavailable. ' + REMEDY
    else:
        _seen.add(str(root))
    # Imported packs contain local folders and links to their original files.
    # A disconnected source is unavailable, not an invitation to replace it.
    for path in [target, *target.parents]:
        marker = path / '.phosphene_linked_from'
        if marker.is_file():
            try:
                source = Path(marker.read_text().strip())
                if not source.is_dir() or not os.access(source, os.R_OK | os.X_OK):
                    return 'Linked model folder unavailable. ' + REMEDY
                if any(p.is_symlink() and not p.exists() for p in path.rglob('*')):
                    return 'Linked model files unavailable. ' + REMEDY
            except OSError:
                return 'Linked model folder unavailable. ' + REMEDY
        if path == root:
            break
    for path in dict.fromkeys([root, target]):
        while not path.exists() and not path.is_symlink() and path != path.parent:
            path = path.parent
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
            return 'Model folder unavailable. ' + REMEDY
        if write and not os.access(path, os.W_OK | os.X_OK):
            return 'Model folder is read-only. ' + REMEDY
    return ''


def require(root, target=None, **kwargs):
    reason = problem(root, target, **kwargs)
    if reason:
        raise OSError(reason)


def prepare_directory(root, target, *, allow_new=False):
    """Create pack descendants through an open root, never recreate that root.

    If it is renamed after admission, operations stay on the opened directory.
    A broken descendant link fails at open rather than creating its target.
    """
    root, target = Path(root).absolute(), Path(target).absolute()
    require(root, target, write=True, allow_new=allow_new)
    if not root.exists() and allow_new:
        root.mkdir(parents=True, exist_ok=True)  # first stock install only
        _seen.add(str(root))
    if not target.is_relative_to(root):
        # Explicit absolute registry destinations retain their precedence.
        # Their parent must already exist, just as an external model root must.
        root = target.parent
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in target.relative_to(root).parts:
            try:
                os.mkdir(name, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)
