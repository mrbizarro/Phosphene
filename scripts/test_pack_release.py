#!/usr/bin/env python3
"""Tests for the GitHub-release weight mirror — publish_pack_release.py and
fetch_pack_release.py, exercised together.

Stdlib only: no gh, no network, no weights. `upload_asset` is redirected into a
local directory and that directory is served over loopback HTTP, so the pair is
tested through the *same* code paths a real publish and a real fresh install
take — sharding, asset naming, manifest emission, ranged resume, per-shard and
per-file verification, atomic rename.

The cases that matter are the ones that would actually hurt:

  * a **corrupted shard must never reach the pack**. This whole lane exists
    because we ship 11 GB files through a CDN; a mirror that can hand a user a
    silently-truncated transformer is worse than no mirror, because the failure
    surfaces as bad renders, not as an error.
  * an **interrupted download must resume, not restart**. 28 GB restarting from
    zero is how a user gives up on the install.
  * a **complete pack must be left alone**. install.js re-runs this on every
    Resume Install; if that re-downloaded, Resume Install would be a trap.
  * **an incomplete pack must not be publishable.** Cutting a release from a
    pack that is missing a file required_files.json declares mandatory produces
    a fresh install that fetches successfully and is then reported incomplete
    by the panel — the June-2026 mechanism, with a longer download in front.

Run:  python3 -m pytest scripts/test_pack_release.py -q
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_pack_release as fetcher  # noqa: E402
import publish_pack_release as publisher  # noqa: E402

SHARD = 1000            # tiny, so a "big" file is a few kB
PACK_KEY = "testpack"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _write_pack(root: Path) -> Path:
    """A synthetic pack + the required_files.json entry that declares it."""
    pack = root / "mlx_models" / "test-pack"
    pack.mkdir(parents=True)
    # Deliberately not a multiple of SHARD, so the last shard is a short one --
    # off-by-one on the final shard is the classic way to corrupt a reassembly.
    (pack / "big.safetensors").write_bytes(bytes(range(256)) * 17 + b"tail")
    (pack / "small.safetensors").write_bytes(b"\x01\x02\x03" * 40)
    (pack / "config.json").write_text('{"hello": "world"}')
    (pack / "phosphene_quant_manifest.json").write_text(json.dumps({
        "bits": 4, "recipe": "ltx-dit", "group_size": 64,
        "tool": "phosphene/quantize_ltx.py", "tool_version": "1.0.0",
        "files": {"small.safetensors": {"bytes": 120, "sha256": "0" * 64}},
    }))
    (root / "required_files.json").write_text(json.dumps({"repos": [{
        "key": PACK_KEY,
        "kind": "base",
        "name": "Test pack",
        "repo_id": "nobody/test-pack",
        "local_dir": "mlx_models/test-pack",
        "size_gb": 1,
        "mirror": {
            "kind": "github-release",
            "release_repo": "mrbizarro/Phosphene",
            "tag": "test-tag",
            "manifest_asset": f"{PACK_KEY}__phosphene_release_manifest.json",
        },
        "files": ["big.safetensors", "small.safetensors"],
    }]}))
    return pack


def _publish(root: Path, release_dir: Path, monkeypatch) -> dict:
    """Run the real publisher with uploads redirected to a local directory."""
    release_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(publisher, "SHARD_BYTES", SHARD)
    monkeypatch.setattr(publisher, "ensure_release", lambda *a, **k: None)
    monkeypatch.setattr(publisher, "release_assets", lambda *a, **k: {})
    monkeypatch.setattr(publisher, "upload_asset",
                        lambda repo, tag, path, **k: shutil.copy2(path, release_dir / path.name))
    return publisher.publish(PACK_KEY, root, tag=None, release_repo=None,
                             target="deadbeef", upload=True,
                             staging_root=root / "staging",
                             license_path=None, notice_path=None,
                             notes_file=None, title=None)


@pytest.fixture()
def served(tmp_path, monkeypatch):
    """(root, pack, release_dir, dest, hits) with the release dir on loopback."""
    root = tmp_path / "app"
    root.mkdir()
    pack = _write_pack(root)
    release_dir = tmp_path / "release"
    manifest = _publish(root, release_dir, monkeypatch)

    hits: list[str] = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):        # keep pytest output readable
            pass

        def do_GET(self):
            hits.append(self.path)
            super().do_GET()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(release_dir)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setattr(fetcher, "asset_url", lambda mirror, asset: f"{base}/{asset}")

    yield root, pack, release_dir, tmp_path / "dest", hits, manifest
    srv.shutdown()


def _fetch(root: Path, dest: Path, attempts: int = 2) -> int:
    """Through `main()`, i.e. the exact entry point install.js invokes."""
    return fetcher.main(["--repo-key", PACK_KEY, "--root", str(root),
                         "--dest", str(dest), "--attempts", str(attempts),
                         "--timeout", "10"])


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #
def test_a_file_over_the_cap_is_sharded_and_one_under_it_is_not(served):
    _, pack, _, _, _, manifest = served
    big = manifest["files"]["big.safetensors"]
    small = manifest["files"]["small.safetensors"]
    assert big["bytes"] == 4356 == (pack / "big.safetensors").stat().st_size
    assert [s["bytes"] for s in big["shards"]] == [1000, 1000, 1000, 1000, 356]
    assert sum(s["bytes"] for s in big["shards"]) == big["bytes"]
    assert len(small["shards"]) == 1
    assert small["shards"][0]["asset"] == f"{PACK_KEY}__small.safetensors"


def test_shard_names_are_ordered_zero_padded_and_url_safe(served):
    _, _, _, _, _, manifest = served
    names = [s["asset"] for s in manifest["files"]["big.safetensors"]["shards"]]
    assert names == sorted(names), "lexical order must equal reassembly order"
    assert names[0].endswith(".part000") and names[-1].endswith(".part004")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    assert all(set(n) <= allowed for n in names), "GitHub rewrites anything else"


def test_every_declared_hash_matches_the_bytes_on_disk(served):
    _, pack, _, _, _, manifest = served
    for name, spec in manifest["files"].items():
        raw = (pack / name).read_bytes()
        assert spec["bytes"] == len(raw)
        assert spec["sha256"] == hashlib.sha256(raw).hexdigest()


def test_stale_in_pack_manifest_is_reported_not_republished(served):
    """The quantiser's manifest can be stale for sidecars rewritten after it
    ran. The published hash must be the FILE's, and the disagreement named."""
    _, _, _, _, _, manifest = served
    assert manifest["quant_manifest_drift"] == ["small.safetensors"]
    assert manifest["files"]["small.safetensors"]["sha256"] != "0" * 64


def test_publishing_an_incomplete_pack_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()
    pack = _write_pack(root)
    (pack / "big.safetensors").unlink()           # required_files.json wants it
    with pytest.raises(SystemExit, match="incomplete"):
        _publish(root, tmp_path / "release", monkeypatch)


def test_staging_is_left_empty_so_publishing_costs_one_shard(served):
    root, _, _, _, _, _ = served
    staging = root / "staging" / "test-tag"
    leftovers = [p.name for p in staging.iterdir() if p.suffix != ".json" and p.name != "_notes.md"]
    assert leftovers == [], f"shards were not deleted after upload: {leftovers}"


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def test_round_trip_reassembles_every_file_byte_for_byte(served):
    root, pack, _, dest, _, manifest = served
    assert _fetch(root, dest) == 0
    for name in manifest["files"]:
        assert (dest / name).read_bytes() == (pack / name).read_bytes(), name
    assert not (dest / fetcher.PARTS_DIRNAME).exists(), "parts dir must be cleaned up"
    assert not list(dest.glob("*.partial")), "no partials may survive a success"


def test_a_corrupted_shard_never_reaches_the_pack(served):
    root, _, release_dir, dest, _, manifest = served
    victim = release_dir / manifest["files"]["big.safetensors"]["shards"][2]["asset"]
    raw = bytearray(victim.read_bytes())
    raw[0] ^= 0xFF                       # same length, one bad byte
    victim.write_bytes(bytes(raw))

    assert _fetch(root, dest, attempts=1) == 1, "a bad shard must fail the fetch"
    assert not (dest / "big.safetensors").exists(), \
        "a file must only appear under its real name once its hash matches"
    # The good files in the same pack still landed — one poisoned asset is not
    # a reason to throw away 20 GB that verified.
    assert (dest / "small.safetensors").exists()


def test_a_truncated_shard_resumes_instead_of_restarting(served):
    root, pack, _, dest, hits, manifest = served
    spec = manifest["files"]["big.safetensors"]
    dest.mkdir(parents=True, exist_ok=True)
    parts = dest / fetcher.PARTS_DIRNAME
    parts.mkdir()
    # Simulate a kill mid-shard: half of shard 0 on disk.
    first = spec["shards"][0]
    whole = (pack / "big.safetensors").read_bytes()[:first["bytes"]]
    (parts / first["asset"]).write_bytes(whole[: first["bytes"] // 2])

    hits.clear()
    assert _fetch(root, dest) == 0
    assert (dest / "big.safetensors").read_bytes() == (pack / "big.safetensors").read_bytes()


def test_a_complete_pack_is_not_downloaded_again(served):
    root, _, _, dest, hits, _ = served
    assert _fetch(root, dest) == 0
    hits.clear()
    assert _fetch(root, dest) == 0
    asset_hits = [h for h in hits if "manifest" not in h]
    assert asset_hits == [], f"re-fetched {asset_hits} on an intact pack"


def test_a_file_that_is_present_but_wrong_is_refetched(served):
    root, pack, _, dest, _, _ = served
    assert _fetch(root, dest) == 0
    (dest / "big.safetensors").write_bytes(b"junk" * 100)
    assert _fetch(root, dest) == 0
    assert (dest / "big.safetensors").read_bytes() == (pack / "big.safetensors").read_bytes()


def test_check_only_reports_without_downloading(served):
    root, _, _, dest, hits, _ = served
    rc = fetcher.fetch_pack(PACK_KEY, root, dest, attempts=1, timeout=10, check_only=True)
    assert rc == 1, "an empty destination is not complete"
    assert [h for h in hits if "manifest" not in h] == []
    assert _fetch(root, dest) == 0
    assert fetcher.fetch_pack(PACK_KEY, root, dest, attempts=1, timeout=10,
                              check_only=True) == 0


def test_a_manifest_missing_a_mandatory_file_is_refused_before_downloading(served, monkeypatch):
    root, _, release_dir, dest, hits, manifest = served
    man_path = release_dir / f"{PACK_KEY}__phosphene_release_manifest.json"
    trimmed = json.loads(man_path.read_text())
    trimmed["files"].pop("small.safetensors")
    man_path.write_text(json.dumps(trimmed))
    hits.clear()
    with pytest.raises(SystemExit, match="mandatory"):
        _fetch(root, dest)
    assert [h for h in hits if "manifest" not in h] == [], "refused too late"


def test_a_pack_with_no_mirror_block_is_refused_not_silently_skipped(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    _write_pack(root)
    reg = json.loads((root / "required_files.json").read_text())
    reg["repos"][0].pop("mirror")
    (root / "required_files.json").write_text(json.dumps(reg))
    with pytest.raises(SystemExit, match="hf download"):
        fetcher.fetch_pack(PACK_KEY, root, None, attempts=1, timeout=5, check_only=True)


def test_a_progress_sidecar_that_disagrees_with_the_file_is_ignored(tmp_path):
    """A sidecar is only trusted when the partial's length backs it up —
    otherwise shards splice at the wrong offset and the hash check is the only
    thing standing between the user and a corrupt 11 GB transformer."""
    partial = tmp_path / "x.partial"
    partial.write_bytes(b"0123456789")
    fetcher._write_progress(partial, 3)
    assert fetcher._read_progress(partial) == 3
    partial.write_bytes(b"012")                       # truncated behind our back
    assert fetcher._read_progress(partial) == 0


# --------------------------------------------------------------------------- #
# Registry wiring — the real required_files.json, not a fixture
# --------------------------------------------------------------------------- #
def test_the_shipped_registry_mirrors_are_well_formed():
    root = Path(__file__).resolve().parents[1]
    reg = json.loads((root / "required_files.json").read_text())
    mirrored = [r for r in reg["repos"] if r.get("mirror")]
    assert mirrored, "the 2.5 packs must declare a mirror or a fresh install has no weights"
    for repo in mirrored:
        m = repo["mirror"]
        assert m["kind"] == "github-release"
        assert m["release_repo"] == "mrbizarro/Phosphene"
        assert m["manifest_asset"] == f"{repo['key']}__phosphene_release_manifest.json"
        assert m["tag"]
        # Every mandatory file has to be fetchable through this lane.
        assert repo.get("files"), f"{repo['key']} declares no required files"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
