#!/usr/bin/env python3
"""Build scripts/hf_mirror_catalog.json straight from huggingface.co (maintainer tool).

The China mirror is not trusted, so everything Phosphene may take from it is written down
here first, from Hugging Face itself, at a pinned commit: path, size and SHA-256 of every
file the loaders use. Large files: the LFS SHA-256 Hugging Face lists. Small (git) files:
downloaded from Hugging Face and hashed here, and their git blob id checked against the one
Hugging Face lists. The catalog ships inside the Studio (sha256-pinned by the Mac app's
signed channel), so a mirror can never change what lands on a Mac.

  usage : build_hf_mirror_catalog.py [--out scripts/hf_mirror_catalog.json] [--check]
          --check: rebuild in memory and fail if the shipped file differs (no write)

Run it on a Mac that can reach huggingface.co. Add a model: one line in SPECS (and keep the
first-run download chooser in sync: every public model it offers belongs here).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HF = "https://huggingface.co"


def _music_sources() -> dict:
    sys.path.insert(0, str(ROOT / "scripts" / "pinokio"))
    import music_fetch                                      # noqa: E402
    return music_fetch


def _h3_files() -> list:
    sys.path.insert(0, str(ROOT / "scripts" / "pinokio"))
    import h3_app_fetch                                     # noqa: E402
    return list(h3_app_fetch.FILES)


def specs() -> list[tuple[str, str, "list[str] | None", str]]:
    """(repo, revision, files or None for the whole repo, what it is)."""
    out: list = []
    by_repo: dict = {}
    for repo, rev, path, *_ in _h3_files():
        by_repo.setdefault((repo, rev), []).append(path)
    for (repo, rev), paths in by_repo.items():
        out.append((repo, rev, sorted(paths), "Hailuo H3 video (the in-app download)"))
    # The default picture model (FLUX.2 Klein 4B, Q4) and the FLUX 3 packs.
    out.append(("Runpod/FLUX.2-klein-4B-mflux-4bit", "7ee1b3aa8178a1240050490072196a57da2bf2a9", None,
                "Pictures: FLUX.2 Klein 4B (the default picture model)"))
    for key, pack in json.loads((ROOT / "data" / "flux3_models.json").read_text()).items():
        out.append((pack["repo"], pack["revision"], sorted(f["path"] for f in pack["files"]),
                    f"Pictures: FLUX 3 {key}"))
    mf = _music_sources()
    g, v = mf.SOURCES["generator"], mf.SOURCES["vae"]
    out.append((g["repo"], g["revision"], sorted(set(mf.GENERATOR_FILES) | {"conversion.json"}),
                "Music: YuE2 generator"))
    out.append((v["repo"], v["revision"], None, "Music: YuE2 decoder"))
    return out


#: Models the app downloads that can never come from the mirror, and why (plain words).
HF_ONLY = {
    "Lightricks/LTX-2.3": "it needs a Hugging Face login (the official LTX file is gated)",
    "Lightricks/LTX-2": "it needs a Hugging Face login (gated)",
    "black-forest-labs/FLUX.1-Krea-dev": "it needs a Hugging Face login (gated)",
    "black-forest-labs/flux-3-image-klein": "it needs a Hugging Face login (gated)",
}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Phosphene-catalog"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _download_hash(repo: str, rev: str, path: str) -> tuple[str, str, int]:
    url = f"{HF}/{repo}/resolve/{rev}/{urllib.parse.quote(path)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Phosphene-catalog"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    git = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    return hashlib.sha256(data).hexdigest(), git, len(data)


def build_repo(repo: str, rev: str, want: "list[str] | None", what: str) -> dict:
    info = _get_json(f"{HF}/api/models/{repo}/revision/{rev}?blobs=true")
    if info.get("sha") != rev:
        raise SystemExit(f"{repo}: revision {rev} resolved to {info.get('sha')}")
    if info.get("gated") or info.get("private"):
        raise SystemExit(f"{repo} is gated or private: it belongs in HF_ONLY, not the catalog")
    sib = {s["rfilename"]: s for s in info["siblings"]}
    paths = sorted(sib) if want is None else want
    files = []
    for p in paths:
        s = sib.get(p)
        if s is None:
            raise SystemExit(f"{repo}@{rev[:7]}: {p} is not in the repo")
        size = int(s["size"])
        lfs = s.get("lfs") or {}
        if lfs.get("sha256"):
            sha, blob = lfs["sha256"], lfs["sha256"]
            if int(lfs.get("size", size)) != size:
                raise SystemExit(f"{repo}: {p} size disagrees with its LFS record")
        else:
            sha, git, n = _download_hash(repo, rev, p)
            if n != size or (s.get("blobId") and s["blobId"] != git):
                raise SystemExit(f"{repo}: {p} downloaded bytes disagree with Hugging Face's record")
            blob = git
        files.append({"path": p, "size": size, "sha256": sha, "blob": blob})
    return {"revision": rev, "what": what, "files": files,
            "bytes": sum(f["size"] for f in files)}


def build() -> dict:
    repos = {}
    for repo, rev, want, what in specs():
        repos[repo] = build_repo(repo, rev, want, what)
        print(f"  {repo}@{rev[:7]}: {len(repos[repo]['files'])} files, "
              f"{repos[repo]['bytes'] / 1e9:.2f} GB", file=sys.stderr)
    return {
        "_comment": ("Files Phosphene may take from the China mirror (hf-mirror.com) when huggingface.co "
                     "cannot be reached. Every entry was read from huggingface.co at a pinned commit: size "
                     "and SHA-256 per file (LFS hash from Hugging Face; small files downloaded from Hugging "
                     "Face and hashed, git blob id cross-checked). Regenerate with "
                     "scripts/build_hf_mirror_catalog.py; never edit by hand."),
        "version": 1,
        "source": HF,
        "repos": repos,
        "hf_only": HF_ONLY,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "scripts" / "hf_mirror_catalog.json")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    cat = build()
    if a.check:
        have = json.loads(a.out.read_text())
        have.pop("built", None)
        if have != cat:
            print("hf_mirror_catalog.json differs from Hugging Face's records", file=sys.stderr)
            return 1
        print("hf_mirror_catalog.json matches Hugging Face", file=sys.stderr)
        return 0
    cat["built"] = _dt.date.today().isoformat()
    a.out.write_text(json.dumps(cat, indent=1, sort_keys=False) + "\n")
    print(f"wrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
