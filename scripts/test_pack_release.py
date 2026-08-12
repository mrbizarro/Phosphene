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
import re
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
    # This asserted `mirrored` was non-empty. A tree may legitimately declare
    # no mirrored pack — public v3.8.0 does, because the 2.5 entries are
    # curated out of a release until their mirror is published, and the 2.3
    # lane comes from HuggingFace. The assertion turned that correct state
    # into a red test. What must never happen is a mirror block the fetcher
    # cannot act on, so this asserts the SHAPE of whatever is declared rather
    # than that something is. (dev declares three today: q4_25, q8_25,
    # gemma4_25 — and `test_a_pack_with_no_mirror_yet_is_not_advertised_as_
    # fetchable` is what guards the other direction, a pack whose files exist
    # with no lane behind them.)
    for repo in mirrored:
        m = repo["mirror"]
        assert m["kind"] == "github-release"
        assert m["release_repo"] == "mrbizarro/Phosphene"
        assert m["manifest_asset"] == f"{repo['key']}__phosphene_release_manifest.json"
        assert m["tag"]
        # Every mandatory file has to be fetchable through this lane.
        assert repo.get("files"), f"{repo['key']} declares no required files"


def _registry():
    root = Path(__file__).resolve().parents[1]
    return root, json.loads((root / "required_files.json").read_text())


def test_the_hq_addon_is_its_own_download_unit_in_the_q8_directory():
    """The split: q8_25 is the pack, hq_25 is the two files loaded out of it.

    They share a directory on purpose -- the HQ weights are loaded from the q8
    pack BY NAME -- but they are separate download units, because they are
    separate builds and a unit has to be publishable the moment its bytes
    exist. Folding them together held a complete 20.6 GB pack hostage to a
    42 GB download.
    """
    _, reg = _registry()
    q8 = next(r for r in reg["repos"] if r["key"] == "q8_25")
    hq = next(r for r in reg["repos"] if r["key"] == "hq_25")
    assert hq["local_dir"] == q8["local_dir"], "the add-on must land in the q8 pack"
    overlap = set(q8["files"]) & set(hq["files"])
    assert not overlap, f"a file must belong to exactly one download unit: {overlap}"
    for f in ("transformer-dev.safetensors", "ltx-2.5-22b-distilled-lora-450.safetensors"):
        assert f in hq["files"], f
        assert f not in q8["files"], f"{f} must not hold the q8 pack hostage"


def test_the_addon_file_names_match_the_names_the_loader_asks_for():
    """The drift that would be invisible until a render.

    `hq_weights` in mlx_ltx_panel.py names the two files the pipeline loads;
    required_files.json names the two files the installer fetches. Rename one
    and not the other and the download succeeds, the pack reports complete, and
    High fails at load time with a file that was never asked for. Read out of
    the panel SOURCE rather than by importing it, the way the analytics
    coverage guard does.
    """
    root, reg = _registry()
    src = (root / "mlx_ltx_panel.py").read_text()
    block = re.search(r'"config_key":\s*"ltx-2\.5".*?"hq_weights":\s*\{(.*?)\}', src, re.S)
    assert block, "could not find the 2.5 hq_weights block"
    declared = set(re.findall(r'"([^"]+\.safetensors)"', block.group(1)))
    assert declared, "2.5 declares no hq_weights"
    hq = next(r for r in reg["repos"] if r["key"] == "hq_25")
    assert declared == set(hq["files"]), (
        f"panel loads {sorted(declared)}, installer fetches {sorted(hq['files'])}")


def test_an_addon_publishes_only_its_own_files_not_its_hosts_directory(tmp_path, monkeypatch):
    """Two entries, one directory -- the guest must not claim the host's files.

    Caught in production, mid-publish: the HQ add-on's weights landed in the q8
    pack directory and the q8 run, which publishes "the directory", started
    uploading 29 GB of add-on under a `q8_25__` prefix. That would have put the
    add-on inside the q8 manifest and forced every q8 install to download
    weights it did not ask for -- the exact coupling the split removed.

    Both directions are asserted: the host excludes the guest's declared files,
    and the guest (`publish_scope: "files"`) publishes ONLY what it declares, so
    it cannot sweep up the sidecar JSON the host owns.
    """
    root = tmp_path / "app"
    root.mkdir()
    pack = _write_pack(root)
    (pack / "addon.safetensors").write_bytes(b"\xAA" * 300)
    reg = json.loads((root / "required_files.json").read_text())
    host = reg["repos"][0]
    reg["repos"].append({
        "key": "addon", "kind": "optional", "name": "Add-on",
        "repo_id": "nobody/addon", "local_dir": host["local_dir"],
        "size_gb": 1, "publish_scope": "files",
        "files": ["addon.safetensors"],
        "mirror": dict(host["mirror"],
                       manifest_asset="addon__phosphene_release_manifest.json"),
    })
    (root / "required_files.json").write_text(json.dumps(reg))
    registry = publisher.load_registry(root)

    host_files = publisher.pack_files(pack, host, registry)
    assert "addon.safetensors" not in host_files, "the host published the guest's file"
    assert "config.json" in host_files, "the host must still pick up its own sidecars"

    guest = next(r for r in registry["repos"] if r["key"] == "addon")
    guest_files = publisher.pack_files(pack, guest, registry)
    assert guest_files == ["addon.safetensors"], guest_files
    assert "config.json" not in guest_files, "the guest claimed its host's sidecar"


def test_small_assets_are_always_reuploaded_even_when_the_size_matches():
    """GitHub publishes no checksum for an asset, so resume can only compare
    size -- and a file whose content changed without its size changing would be
    skipped. Not hypothetical: the in-pack quant manifest was rewritten by
    another agent between two publishes. Anything small is re-uploaded rather
    than trusted."""
    assert publisher.ALWAYS_REUPLOAD_MAX_BYTES >= (1 << 20), "too small to cover sidecars"
    assert publisher.ALWAYS_REUPLOAD_MAX_BYTES < publisher.SHARD_BYTES, "would defeat resume"


def test_a_pack_with_no_mirror_yet_is_not_advertised_as_fetchable():
    """hq_25 must not claim a mirror until its assets are published.

    A file in files[] with no download lane behind it is the June-2026 failure
    verbatim: the pack reports incomplete on arrival and nothing can fix it.
    When the dev build lands, the mirror block and the assets go live in the
    SAME commit -- so this test is expected to be UPDATED then, not deleted.
    """
    root, reg = _registry()
    hq = next(r for r in reg["repos"] if r["key"] == "hq_25")
    on_disk = all((root / hq["local_dir"] / f).exists() for f in hq["files"])
    if hq.get("mirror"):
        assert on_disk, "a mirror block was added before the files existed"
    else:
        assert not on_disk, "the files exist -- publish them and add the mirror block"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
