#!/usr/bin/env python3
"""Tests for scripts/ltx_pack_diff.py — the standing pack-contract gate.

Stdlib only: no mlx, no GPU, no weights. Every fixture is a hand-written
safetensors file of a few bytes, because the thing under test reads headers and
nothing else.

The cases that matter are the ones that bit us for real:

  * a shape mismatch on a shared key must FAIL, and must fail even when the
    caller declared an allowlist — an allowlist is about which keys exist, never
    about whether a shared key means the same thing;
  * the real 2.5-vs-2.3 DiT delta (96 absent ff biases, one extra keyframe
    embedding) must PASS when declared and FAIL when not;
  * a diff that compares nothing must not report success. "0 mismatches" over
    an empty set is how a gate turns into decoration.

Run:  python3 -m pytest scripts/test_ltx_pack_diff.py -q
 or:  python3 scripts/test_ltx_pack_diff.py
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ltx_pack_diff import (  # noqa: E402
    collapse_key,
    collapse_keys,
    diff_file,
    diff_packs,
    main,
    pair_files,
    read_header,
)

DTYPE_SIZES = {"BF16": 2, "F32": 4, "U32": 4}


def write_safetensors(path: Path, tensors: dict[str, tuple[str, list[int]]], metadata: dict | None = None) -> Path:
    """Write a real (if tiny) safetensors file: {name: (dtype, shape)}."""
    index: dict = {}
    offset = 0
    for name, (dtype, shape) in tensors.items():
        nbytes = DTYPE_SIZES[dtype]
        for dim in shape:
            nbytes *= dim
        index[name] = {"dtype": dtype, "shape": shape, "data_offsets": [offset, offset + nbytes]}
        offset += nbytes
    if metadata:
        index["__metadata__"] = metadata
    blob = json.dumps(index).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<Q", len(blob)))
        fh.write(blob)
        fh.write(b"\0" * offset)
    return path


def dit(prefix: str, blocks: int = 3, *, ff_bias: bool, keyframes: bool) -> dict:
    """A miniature of the real LTX DiT key set — enough structure that the
    collapsed patterns in the tests are the same strings the real pack yields."""
    tensors: dict[str, tuple[str, list[int]]] = {f"{prefix}patchify_proj.weight": ("BF16", [8, 4])}
    for i in range(blocks):
        base = f"{prefix}transformer_blocks.{i}"
        tensors[f"{base}.attn1.to_q.weight"] = ("BF16", [8, 8])
        tensors[f"{base}.ff.proj_in.weight"] = ("BF16", [16, 8])
        tensors[f"{base}.ff.proj_out.weight"] = ("BF16", [8, 16])
        tensors[f"{base}.scale_shift_table"] = ("F32", [6, 8])
        if ff_bias:
            tensors[f"{base}.ff.proj_in.bias"] = ("BF16", [16])
            tensors[f"{base}.ff.proj_out.bias"] = ("BF16", [8])
    if keyframes:
        tensors[f"{prefix}keyframes_abs_pos_embedding"] = ("BF16", [1, 8])
    return tensors


@pytest.fixture()
def packs(tmp_path: Path):
    """A 2.3-shaped reference pack and a 2.5-shaped candidate, plus a shared
    verbatim component that must diff to nothing."""
    ref, new = tmp_path / "ref", tmp_path / "new"
    write_safetensors(ref / "transformer-dev.safetensors", dit("transformer.", ff_bias=True, keyframes=False))
    write_safetensors(new / "transformer-dev.safetensors", dit("transformer.", ff_bias=False, keyframes=True))
    for root in (ref, new):
        write_safetensors(root / "vae_encoder.safetensors", {"vae_encoder.conv.weight": ("BF16", [4, 3, 3, 3, 2])})
    return ref, new


# ---------------------------------------------------------------------------
# header reading
# ---------------------------------------------------------------------------


def test_header_read_drops_metadata_and_keeps_tensors(tmp_path):
    path = write_safetensors(
        tmp_path / "x.safetensors", {"a.weight": ("BF16", [2, 2])}, metadata={"config": "{}"}
    )
    index = read_header(path)
    assert set(index) == {"a.weight"}
    assert index["a.weight"]["shape"] == [2, 2]


def test_truncated_file_raises_rather_than_reading_as_empty(tmp_path):
    """A half-downloaded pack must not diff clean against anything."""
    path = write_safetensors(tmp_path / "x.safetensors", {"a.weight": ("BF16", [2, 2])})
    raw = path.read_bytes()
    path.write_bytes(raw[:6])
    with pytest.raises(ValueError):
        read_header(path)

    path.write_bytes(struct.pack("<Q", 4096) + b"{}")
    with pytest.raises(ValueError):
        read_header(path)


# ---------------------------------------------------------------------------
# collapsing
# ---------------------------------------------------------------------------


def test_collapse_folds_block_indices_but_not_trailing_numbers():
    assert collapse_key("t.transformer_blocks.41.ff.proj_in.bias") == "t.transformer_blocks.N.ff.proj_in.bias"
    # a trailing digit is part of the name (to_out.0), not a stack index
    assert collapse_key("t.attn1.to_out.0") == "t.attn1.to_out.0"


def test_collapse_counts_every_member_of_a_stack():
    keys = [f"t.transformer_blocks.{i}.ff.proj_in.bias" for i in range(48)]
    assert collapse_keys(keys) == [("t.transformer_blocks.N.ff.proj_in.bias", 48)]


# ---------------------------------------------------------------------------
# the diff itself
# ---------------------------------------------------------------------------


def test_identical_files_diff_to_nothing(tmp_path):
    tensors = dit("transformer.", ff_bias=True, keyframes=False)
    a = write_safetensors(tmp_path / "a" / "t.safetensors", tensors)
    b = write_safetensors(tmp_path / "b" / "t.safetensors", tensors)
    d = diff_file(a, b)
    assert (d["only_new"], d["only_ref"], d["shape_mm"], d["dtype_mm"]) == ([], [], [], [])
    assert d["shared"] == d["n_ref"] == d["n_new"]


def test_shape_mismatch_is_reported_with_both_shapes(tmp_path):
    a = write_safetensors(tmp_path / "a" / "t.safetensors", {"k.weight": ("BF16", [4, 8])})
    b = write_safetensors(tmp_path / "b" / "t.safetensors", {"k.weight": ("BF16", [8, 4])})
    (key, ref_shape, new_shape), = diff_file(a, b)["shape_mm"]
    assert (key, ref_shape, new_shape) == ("k.weight", [4, 8], [8, 4])


def test_dtype_mismatch_is_reported_separately_from_shape(tmp_path):
    a = write_safetensors(tmp_path / "a" / "t.safetensors", {"k.scale_shift_table": ("F32", [6, 8])})
    b = write_safetensors(tmp_path / "b" / "t.safetensors", {"k.scale_shift_table": ("BF16", [6, 8])})
    d = diff_file(a, b)
    assert d["shape_mm"] == []
    assert d["dtype_mm"] == [("k.scale_shift_table", "F32", "BF16")]


# ---------------------------------------------------------------------------
# pairing
# ---------------------------------------------------------------------------


def test_pairing_defaults_to_same_named_files_present_in_both(packs):
    ref, new = packs
    names = [(r.name, n.name) for r, n in pair_files(ref, new)]
    assert names == [
        ("transformer-dev.safetensors", "transformer-dev.safetensors"),
        ("vae_encoder.safetensors", "vae_encoder.safetensors"),
    ]


def test_explicit_map_compares_files_that_changed_name(tmp_path):
    """2.3 ships transformer-dev; 2.5 shipped only transformer-distilled until
    the dev pack existed. Comparing them is the whole point of --map."""
    write_safetensors(tmp_path / "ref" / "transformer-dev.safetensors", {"transformer.a": ("BF16", [2, 2])})
    write_safetensors(tmp_path / "new" / "transformer-distilled.safetensors", {"transformer.a": ("BF16", [2, 2])})
    pairs = pair_files(
        tmp_path / "ref",
        tmp_path / "new",
        {"transformer-dev.safetensors": "transformer-distilled.safetensors"},
    )
    assert [(r.name, n.name) for r, n in pairs] == [
        ("transformer-dev.safetensors", "transformer-distilled.safetensors")
    ]


def test_a_mapped_file_is_not_also_paired_by_name(tmp_path):
    """The mapped file must be compared once, against what the caller said."""
    write_safetensors(tmp_path / "ref" / "a.safetensors", {"k": ("BF16", [2])})
    write_safetensors(tmp_path / "ref" / "b.safetensors", {"k": ("BF16", [2])})
    write_safetensors(tmp_path / "new" / "b.safetensors", {"k": ("BF16", [2])})
    pairs = pair_files(tmp_path / "ref", tmp_path / "new", {"a.safetensors": "b.safetensors"})
    assert [(r.name, n.name) for r, n in pairs] == [("a.safetensors", "b.safetensors")]


# ---------------------------------------------------------------------------
# pass/fail policy
# ---------------------------------------------------------------------------


def test_undeclared_key_difference_passes_when_nothing_was_declared(packs):
    """Default is report-only on key sets: the 2.5 pack legitimately differs
    from 2.3 and a bare run should say so without failing."""
    report = diff_packs(*packs)
    assert report["ok"] is True
    assert report["enforced"] is False
    assert report["mismatches"] == 0


def test_strict_keys_fails_on_the_real_25_delta(packs):
    report = diff_packs(*packs, strict_keys=True)
    assert report["ok"] is False
    assert report["undeclared_keys"] > 0


def test_declaring_the_real_25_delta_passes(packs):
    """The exact contract recorded in the module docstring."""
    report = diff_packs(
        *packs,
        allow_only_new=["transformer.keyframes_abs_pos_embedding"],
        allow_only_ref=[
            "transformer.transformer_blocks.N.ff.proj_in.bias",
            "transformer.transformer_blocks.N.ff.proj_out.bias",
        ],
    )
    assert report["ok"] is True
    assert report["enforced"] is True


def test_a_partial_declaration_still_fails_and_names_what_is_left(packs):
    report = diff_packs(
        *packs,
        allow_only_new=["transformer.keyframes_abs_pos_embedding"],
        allow_only_ref=["transformer.transformer_blocks.N.ff.proj_in.bias"],
    )
    assert report["ok"] is False
    entry = next(f for f in report["files"] if f["new"] == "transformer-dev.safetensors")
    assert [pat for pat, _ in entry["uncovered_ref"]] == ["transformer.transformer_blocks.N.ff.proj_out.bias"]


def test_an_allowlist_cannot_wave_through_a_shape_mismatch(tmp_path):
    """Shape is not a key-set question. This is the load-bearing assertion:
    an allowlist says which keys may be absent, never that a present key may
    mean something different."""
    write_safetensors(tmp_path / "ref" / "t.safetensors", {"transformer.a.weight": ("BF16", [4, 8])})
    write_safetensors(tmp_path / "new" / "t.safetensors", {"transformer.a.weight": ("BF16", [8, 4])})
    report = diff_packs(
        tmp_path / "ref",
        tmp_path / "new",
        allow_only_new=["anything"],
        allow_only_ref=["anything"],
    )
    assert report["ok"] is False
    assert report["mismatches"] == 1


def test_verbatim_components_diff_to_zero_across_generations(packs):
    """The vae/vocoder/upscaler are copied byte-for-byte between generations;
    if one of them ever moves, this is where it surfaces."""
    report = diff_packs(*packs)
    vae = next(f for f in report["files"] if f["new"] == "vae_encoder.safetensors")
    assert (vae["only_new"], vae["only_ref"], vae["shape_mm"], vae["dtype_mm"]) == ([], [], [], [])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_returns_1_on_a_mismatch_and_0_when_clean(tmp_path, capsys):
    write_safetensors(tmp_path / "ref" / "t.safetensors", {"transformer.a.weight": ("BF16", [4, 8])})
    write_safetensors(tmp_path / "new" / "t.safetensors", {"transformer.a.weight": ("BF16", [8, 4])})
    assert main([str(tmp_path / "ref"), str(tmp_path / "new")]) == 1
    assert "FAIL" in capsys.readouterr().out

    write_safetensors(tmp_path / "new" / "t.safetensors", {"transformer.a.weight": ("BF16", [4, 8])})
    assert main([str(tmp_path / "ref"), str(tmp_path / "new")]) == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_refuses_to_pass_when_it_compared_nothing(tmp_path, capsys):
    """0 mismatches over an empty comparison is the failure mode this whole
    script exists to stop being possible."""
    (tmp_path / "ref").mkdir()
    (tmp_path / "new").mkdir()
    assert main([str(tmp_path / "ref"), str(tmp_path / "new")]) == 2
    assert "no comparable" in capsys.readouterr().err


def test_cli_rejects_a_directory_that_does_not_exist(tmp_path, capsys):
    (tmp_path / "ref").mkdir()
    assert main([str(tmp_path / "ref"), str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_cli_json_is_machine_readable(packs, capsys):
    ref, new = packs
    main([str(ref), str(new), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["compared"] == 2


def test_cli_strict_files_flags_a_file_only_one_pack_has(packs, capsys):
    ref, new = packs
    write_safetensors(new / "duration_head.safetensors", {"duration_head.a": ("BF16", [2])})
    assert main([str(ref), str(new)]) == 0  # shared subset is still clean
    assert main([str(ref), str(new), "--strict-files"]) == 1
    assert "duration_head.safetensors" in capsys.readouterr().out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
