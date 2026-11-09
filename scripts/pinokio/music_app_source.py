"""Verified YuE2 source for Mac installations without Git/developer tools."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request


def install(target, cache, manifest, pin):
    target, cache = Path(target), Path(cache)
    if manifest["commit"] != pin:
        raise ValueError("the music source archive does not match this release")
    marker = target / ".phosphene_music_source"
    if marker.is_file() and marker.read_text().strip() == pin and (target / "pyproject.toml").is_file():
        return
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / ("yue2-" + pin + ".tar.gz")
    def valid():
        return archive.is_file() and hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["sha256"]
    if not valid():
        with tempfile.NamedTemporaryFile(dir=cache, prefix="music-source-", delete=False) as out:
            pending = Path(out.name)
            try:
                with urllib.request.urlopen(manifest["url"], timeout=60) as response:
                    shutil.copyfileobj(response, out)
                out.flush()
                if hashlib.sha256(pending.read_bytes()).hexdigest() != manifest["sha256"]:
                    raise ValueError("music source checksum did not match; retry the download")
                os.replace(pending, archive)
            finally:
                pending.unlink(missing_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    # Extract into a private staging folder, accepting only plain files and
    # directories. Preserve existing environments and partial model downloads.
    with tempfile.TemporaryDirectory(dir=cache, prefix="music-unpack-") as stage:
        stage = Path(stage)
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                parts = PurePosixPath(member.name).parts
                if not parts or member.name.startswith("/") or ".." in parts:
                    raise ValueError("unsafe path in music source archive")
                if len(parts) == 1:
                    continue
                rel = Path(*parts[1:])
                dest = stage / rel
                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as inp, dest.open("wb") as out:
                        shutil.copyfileobj(inp, out)
                    dest.chmod(member.mode & 0o777)
                else:
                    raise ValueError("unsupported link in music source archive")
        if not (stage / "pyproject.toml").is_file():
            raise ValueError("music source archive is incomplete")
        base = target.resolve()
        for source in stage.rglob("*"):
            if not source.is_file():
                continue
            dest = target / source.relative_to(stage)
            if not dest.resolve().is_relative_to(base):
                raise ValueError("music source folder points outside the app engine")
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, dest)
        pending = target / ".phosphene_music_source.tmp"
        pending.write_text(pin + "\n")
        os.replace(pending, marker)


def main():
    scripts = Path(__file__).resolve().parents[1]
    manifest = json.loads((scripts / "music/source_archive.json").read_text())
    pin = (scripts / "music/engine_pin.txt").read_text().strip()
    home = Path(os.environ["PHOSPHENE_HOME"])
    target = Path(os.environ.get("LTX_MUSIC_ROOT") or Path.cwd() / "yue2-mlx")
    try:
        install(target, home / "downloads", manifest, pin)
    except Exception as exc:
        # Do not print remote response bodies or user environment values.
        reason = str(exc) if isinstance(exc, ValueError) else "connection or disk access failed"
        print("FATAL error: the verified music engine download could not finish ("
              + reason + "). Check your connection and free disk space, then click Install again. Downloaded models are kept.")
        return 1
    print("Music engine source is ready; downloaded models are kept.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
