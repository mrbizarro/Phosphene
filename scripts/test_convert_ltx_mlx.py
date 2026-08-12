#!/usr/bin/env python3
"""Tests for scripts/convert_ltx_mlx.py — the PyTorch/Comfy → MLX pack converter.

Everything here runs on synthetic safetensors at tiny dims, built from the
2.5-style key names taken from the merged ComfyUI implementation (commit
57ce8e1a) and from the real 2.3 MLX pack headers. No 42 GB checkpoint, no
gated download, no GPU.

That is not a compromise. The converter's whole job is naming and layout, and
naming and layout are exactly what a 4-tensor file can prove. What it cannot
prove is covered honestly in the port note: that the vendor's real key set
contains no key our Route table has never seen. The unmapped-keys abort is
what turns that unknown into a loud failure instead of a silent one.

Run:  ./ltx-2-mlx/env/bin/python -m pytest scripts/test_convert_ltx_mlx.py -q
"""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from convert_ltx_mlx import (  # noqa: E402
    DEFAULT_ROUTES,
    ComponentPlan,
    PlannedTensor,
    UnmappedKeys,
    apply_leaf_map,
    convert,
    parse_input_spec,
    plan_conversion,
    read_header,
    rename_key,
    route_key,
    tensor_nbytes,
    write_component,
)


# --------------------------------------------------------------------------
# Minimal safetensors writer — deliberately independent of the converter's
# own writer, so a bug in one cannot hide a bug in the other.
# --------------------------------------------------------------------------


def write_safetensors(path: Path, tensors: dict, metadata: dict | None = None) -> Path:
    """`tensors`: {key: (dtype, shape, bytes)}."""
    header = {}
    cursor = 0
    blob = bytearray()
    for key, (dtype, shape, payload) in tensors.items():
        header[key] = {"dtype": dtype, "shape": list(shape), "data_offsets": [cursor, cursor + len(payload)]}
        blob += payload
        cursor += len(payload)
    if metadata:
        header["__metadata__"] = metadata
    raw = json.dumps(header, separators=(",", ":")).encode()
    raw += b" " * ((-len(raw)) % 8)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<Q", len(raw)))
        fh.write(raw)
        fh.write(bytes(blob))
    return path


def bf16(n: int, fill: int = 0xAB) -> bytes:
    return bytes([fill]) * (n * 2)


def comfy_dit_tensors(num_blocks: int = 2) -> dict:
    """A miniature monolithic DiT in ComfyUI single-file key layout."""
    t = {
        "model.diffusion_model.patchify_proj.weight": ("BF16", [8, 4], bf16(32)),
        "model.diffusion_model.patchify_proj.bias": ("BF16", [8], bf16(8)),
        "model.diffusion_model.proj_out.weight": ("BF16", [4, 8], bf16(32)),
        "model.diffusion_model.scale_shift_table": ("F32", [2, 8], b"\x01" * (2 * 8 * 4)),
        "model.diffusion_model.adaln_single.linear.weight": ("BF16", [72, 8], bf16(576)),
        "model.diffusion_model.adaln_single.emb.timestep_embedder.linear_1.weight": ("BF16", [8, 4], bf16(32)),
        "model.diffusion_model.adaln_single.emb.timestep_embedder.linear_2.weight": ("BF16", [8, 8], bf16(64)),
        # 2.5: no keyframes marker unless the checkpoint ships it
        "model.diffusion_model.keyframes_abs_pos_embedding": ("BF16", [1, 8], bf16(8)),
    }
    for i in range(num_blocks):
        p = f"model.diffusion_model.transformer_blocks.{i}."
        t[p + "attn1.to_q.weight"] = ("BF16", [8, 8], bf16(64))
        t[p + "attn1.to_out.0.weight"] = ("BF16", [8, 8], bf16(64))
        t[p + "attn1.q_norm.weight"] = ("BF16", [8], bf16(8))
        # LTX-2.5: ff carries NO bias — only the two weights
        t[p + "ff.net.0.proj.weight"] = ("BF16", [32, 8], bf16(256))
        t[p + "ff.net.2.weight"] = ("BF16", [8, 32], bf16(256))
        t[p + "audio_ff.net.0.proj.weight"] = ("BF16", [16, 4], bf16(64))
        t[p + "audio_ff.net.2.weight"] = ("BF16", [4, 16], bf16(64))
        t[p + "scale_shift_table"] = ("F32", [9, 8], b"\x02" * (9 * 8 * 4))
    return t


class TestHeaderPrimitives:
    def test_round_trips_a_header(self, tmp_path):
        path = write_safetensors(tmp_path / "x.safetensors", {"a": ("BF16", [2, 3], bf16(6))})
        header, offset = read_header(path)
        assert header["a"]["shape"] == [2, 3]
        assert offset % 8 == 0

    def test_nbytes_matches_dtype_and_shape(self):
        assert tensor_nbytes({"dtype": "BF16", "shape": [4, 8]}) == 64
        assert tensor_nbytes({"dtype": "F32", "shape": [2, 8]}) == 64
        assert tensor_nbytes({"dtype": "U32", "shape": [4096, 512]}) == 4096 * 512 * 4

    def test_unknown_dtype_raises(self):
        with pytest.raises(ValueError, match="unknown safetensors dtype"):
            tensor_nbytes({"dtype": "F4_MADEUP", "shape": [1]})

    def test_metadata_survives_the_read(self, tmp_path):
        path = write_safetensors(
            tmp_path / "m.safetensors",
            {"a": ("BF16", [1], bf16(1))},
            metadata={"model_version": "2.5", "config": '{"transformer":{"ff_bias":false}}'},
        )
        header, _ = read_header(path)
        assert header["__metadata__"]["model_version"] == "2.5"


class TestRenaming:
    """Rules mirror LTXV_LORA_COMFY_RENAMING_MAP in the vendored loader."""

    @pytest.mark.parametrize(
        ("src", "want"),
        [
            ("transformer.transformer_blocks.0.attn1.to_out.0.weight", "transformer.transformer_blocks.0.attn1.to_out.weight"),
            ("transformer.transformer_blocks.0.ff.net.0.proj.weight", "transformer.transformer_blocks.0.ff.proj_in.weight"),
            ("transformer.transformer_blocks.0.ff.net.2.weight", "transformer.transformer_blocks.0.ff.proj_out.weight"),
            ("transformer.transformer_blocks.0.audio_ff.net.0.proj.weight", "transformer.transformer_blocks.0.audio_ff.proj_in.weight"),
            ("transformer.adaln_single.emb.timestep_embedder.linear_1.weight", "transformer.adaln_single.emb.timestep_embedder.linear1.weight"),
            ("transformer.patchify_proj.weight", "transformer.patchify_proj.weight"),
        ],
    )
    def test_rules(self, src, want):
        assert rename_key(src) == want

    def test_renaming_is_idempotent(self):
        """Running the table twice must not corrupt an already-renamed key."""
        once = rename_key("transformer.transformer_blocks.0.ff.net.0.proj.weight")
        assert rename_key(once) == once


def only(routes):
    """A key that must route to exactly one component."""
    assert len(routes) == 1, f"expected a single route, got {[r.stem for r in routes]}"
    return routes[0]


class TestRouting:
    def test_longest_prefix_wins(self):
        """duration_head must not be swallowed by model.diffusion_model."""
        route = only(route_key("model.diffusion_model.duration_head.mlp_out.bias", DEFAULT_ROUTES))
        assert route.stem == "duration_head"
        assert route.target_prefix == "duration_head."

    def test_connector_subtrees_land_in_one_file(self):
        video = only(route_key("model.diffusion_model.video_embeddings_connector.x", DEFAULT_ROUTES))
        audio = only(route_key("model.diffusion_model.audio_embeddings_connector.x", DEFAULT_ROUTES))
        assert video.stem == audio.stem == "connector"
        assert video.target_prefix.startswith("connector.")

    def test_dit_falls_through_to_transformer(self):
        route = only(route_key("model.diffusion_model.transformer_blocks.0.attn1.to_q.weight", DEFAULT_ROUTES))
        assert route.stem == "transformer"

    def test_unknown_prefix_returns_no_route(self):
        assert route_key("some.vendor.module.weight", DEFAULT_ROUTES) == []


class TestRealLTX25Shapes:
    """Pinned against the key sets of the actual Lightricks 2.5 release.

    Every assertion here failed against the route table as first written. Each
    one is a way the pack would have loaded and rendered wrongly rather than
    erroring — which is why they are tests and not a changelog line.
    """

    def test_connector_keeps_its_pytorch_spelling(self):
        """The vendored ConnectorAttention builds ``to_out.0`` and ``ff.net.*``.

        The DiT renames are correct for DiT blocks and wrong here. Applying them
        to the connector yields a file whose keys no shipped module claims.
        """
        route = only(
            route_key("model.diffusion_model.video_embeddings_connector.transformer_1d_blocks.0.attn1.to_out.0.weight", DEFAULT_ROUTES)
        )
        assert route.rename is False
        suffix = "transformer_1d_blocks.0.attn1.to_out.0.weight"
        assert route.target_prefix + suffix == (
            "connector.video_embeddings_connector.transformer_1d_blocks.0.attn1.to_out.0.weight"
        )

    def test_dit_blocks_still_get_renamed(self):
        route = only(route_key("model.diffusion_model.transformer_blocks.0.ff.net.0.proj.weight", DEFAULT_ROUTES))
        assert route.rename is True

    def test_video_vae_statistics_fan_out_to_both_halves(self):
        """One shipped copy; the encoder and decoder spell the leaves differently."""
        routes = route_key("per_channel_statistics.mean-of-means", DEFAULT_ROUTES)
        assert {r.stem for r in routes} == {"vae_encoder", "vae_decoder"}
        produced = {
            apply_leaf_map(r.target_prefix + "mean-of-means", r.leaf_map) for r in routes
        }
        assert produced == {
            "vae_encoder.per_channel_statistics._mean_of_means",
            "vae_decoder.per_channel_statistics.mean",
        }

    def test_audio_vae_statistics_get_the_underscore_spelling(self):
        route = only(route_key("audio_vae.per_channel_statistics.std-of-means", DEFAULT_ROUTES))
        assert route.stem == "audio_vae"
        assert (
            apply_leaf_map(route.target_prefix + "std-of-means", route.leaf_map)
            == "audio_vae.per_channel_statistics._std_of_means"
        )

    def test_vocoder_doubled_segment_is_collapsed(self):
        """2.5 nests the base vocoder one level deeper than 2.3."""
        base = only(route_key("vocoder.vocoder.resblocks.0.convs1.0.bias", DEFAULT_ROUTES))
        assert base.target_prefix + "resblocks.0.convs1.0.bias" == "vocoder.resblocks.0.convs1.0.bias"

    def test_vocoder_siblings_pass_through_unchanged(self):
        """bwe_generator and mel_stft are already at the right depth."""
        for tail in ("bwe_generator.conv_pre.bias", "mel_stft.window"):
            route = only(route_key(f"vocoder.{tail}", DEFAULT_ROUTES))
            assert route.target_prefix + tail == f"vocoder.{tail}"

    def test_upscalers_need_an_explicit_stem(self, tmp_path):
        """Spatial and temporal upscalers ship byte-identical key sets.

        Nothing in the key says which is which, so an unpinned run must refuse
        rather than guess — and a pinned run must land the whole file.
        """
        bare = {
            "final_conv.bias": ("BF16", [4], bf16(4)),
            "res_blocks.0.norm1.weight": ("BF16", [4], bf16(4)),
        }
        path = write_safetensors(tmp_path / "spatial.safetensors", bare)
        _, unmapped, _ = plan_conversion([path], allow_unmapped=True)
        assert len(unmapped) == 2

        plans, unmapped, _ = plan_conversion([(path, "spatial_upscaler_x2_v1_1")])
        assert unmapped == []
        assert {t.target_key for t in plans["spatial_upscaler_x2_v1_1"].tensors} == {
            "spatial_upscaler_x2_v1_1.final_conv.bias",
            "spatial_upscaler_x2_v1_1.res_blocks.0.norm1.weight",
        }

    def test_explicit_stem_does_not_rename(self, tmp_path):
        """A pinned file is copied verbatim; DiT rules must not reach into it."""
        path = write_safetensors(
            tmp_path / "u.safetensors",
            {"res_blocks.0.ff.net.2.weight": ("BF16", [4], bf16(4))},
        )
        plans, _, _ = plan_conversion([(path, "temporal_upscaler_x2_v1_0")])
        assert {t.target_key for t in plans["temporal_upscaler_x2_v1_0"].tensors} == {
            "temporal_upscaler_x2_v1_0.res_blocks.0.ff.net.2.weight"
        }


class TestPlanning:
    def test_plans_a_monolithic_dit(self, tmp_path):
        path = write_safetensors(tmp_path / "dit.safetensors", comfy_dit_tensors())
        plans, unmapped, _ = plan_conversion([path])
        assert unmapped == []
        assert set(plans) == {"transformer"}
        assert all(t.target_key.startswith("transformer.") for t in plans["transformer"].tensors)

    def test_renames_are_applied_in_the_plan(self, tmp_path):
        path = write_safetensors(tmp_path / "dit.safetensors", comfy_dit_tensors(num_blocks=1))
        plans, _, _ = plan_conversion([path])
        keys = {t.target_key for t in plans["transformer"].tensors}
        assert "transformer.transformer_blocks.0.ff.proj_in.weight" in keys
        assert "transformer.transformer_blocks.0.attn1.to_out.weight" in keys
        assert not any(".net." in k or ".to_out.0." in k for k in keys)

    def test_the_keyframe_marker_survives(self, tmp_path):
        """A 2.5-only tensor must not be quietly dropped."""
        path = write_safetensors(tmp_path / "dit.safetensors", comfy_dit_tensors())
        plans, _, _ = plan_conversion([path])
        keys = {t.target_key for t in plans["transformer"].tensors}
        assert "transformer.keyframes_abs_pos_embedding" in keys

    def test_multiple_inputs_merge_into_one_pack(self, tmp_path):
        dit = write_safetensors(tmp_path / "dit.safetensors", comfy_dit_tensors(num_blocks=1))
        vae = write_safetensors(
            tmp_path / "vae.safetensors",
            {
                "encoder.conv_in.weight": ("BF16", [4, 4], bf16(16)),
                "decoder.conv_out.weight": ("BF16", [4, 4], bf16(16)),
            },
        )
        head = write_safetensors(
            tmp_path / "head.safetensors", {"duration_head.mlp_out.bias": ("BF16", [1], bf16(1))}
        )
        plans, unmapped, _ = plan_conversion([dit, vae, head])
        assert unmapped == []
        assert set(plans) == {"transformer", "vae_encoder", "vae_decoder", "duration_head"}

    def test_unmapped_keys_abort_by_default(self, tmp_path):
        path = write_safetensors(
            tmp_path / "odd.safetensors",
            {"brand.new.module.weight": ("BF16", [2, 2], bf16(4))},
        )
        with pytest.raises(UnmappedKeys, match="matched no output component"):
            plan_conversion([path])

    def test_the_abort_message_names_the_mosaic(self, tmp_path):
        """Whoever hits this must understand why it is not a warning."""
        path = write_safetensors(tmp_path / "odd.safetensors", {"x.y.z": ("BF16", [1], bf16(1))})
        with pytest.raises(UnmappedKeys) as excinfo:
            plan_conversion([path])
        assert "mosaic" in str(excinfo.value)
        assert "--allow-unmapped" in str(excinfo.value)

    def test_allow_unmapped_reports_instead_of_raising(self, tmp_path):
        path = write_safetensors(
            tmp_path / "odd.safetensors",
            {
                "brand.new.module.weight": ("BF16", [2, 2], bf16(4)),
                "model.diffusion_model.proj_out.weight": ("BF16", [2, 2], bf16(4)),
            },
        )
        plans, unmapped, _ = plan_conversion([path], allow_unmapped=True)
        assert len(unmapped) == 1
        assert "brand.new.module.weight" in unmapped[0]
        assert set(plans) == {"transformer"}

    def test_a_self_inconsistent_header_is_refused(self, tmp_path):
        """Offsets that disagree with dtype*shape mean a corrupt download."""
        path = tmp_path / "bad.safetensors"
        # Routable key, so the run reaches the consistency check rather than
        # tripping the unmapped-keys abort first.
        header = {"transformer.w": {"dtype": "BF16", "shape": [4, 4], "data_offsets": [0, 8]}}  # needs 32
        raw = json.dumps(header).encode()
        raw += b" " * ((-len(raw)) % 8)
        with open(path, "wb") as fh:
            fh.write(struct.pack("<Q", len(raw)))
            fh.write(raw)
            fh.write(b"\x00" * 8)
        with pytest.raises(ValueError, match="self-inconsistent"):
            plan_conversion([path])


class TestWriting:
    def test_bytes_survive_the_round_trip(self, tmp_path):
        payload = bytes(range(256)) * 4
        src = write_safetensors(tmp_path / "src.safetensors", {"transformer.w": ("BF16", [8, 64], payload)})
        plans, _, _ = plan_conversion([src])
        out, digest, size = write_component(plans["transformer"], tmp_path / "pack")

        header, offset = read_header(out)
        assert list(header) == ["transformer.w"]
        begin, end = header["transformer.w"]["data_offsets"]
        with open(out, "rb") as fh:
            fh.seek(offset + begin)
            assert fh.read(end - begin) == payload
        assert len(digest) == 64
        assert size == out.stat().st_size

    def test_no_partial_file_is_left_behind_on_failure(self, tmp_path):
        out_dir = tmp_path / "pack"
        out_dir.mkdir()
        missing = tmp_path / "gone.safetensors"
        plan = ComponentPlan(
            stem="transformer",
            tensors=[PlannedTensor("transformer.w", "w", missing, 0, 16, "BF16", [8])],
        )
        with pytest.raises(Exception):
            write_component(plan, out_dir)
        assert list(out_dir.iterdir()) == [], "a failed write must leave nothing behind"

    def test_colliding_target_keys_are_refused(self, tmp_path):
        src = tmp_path / "s.safetensors"
        write_safetensors(src, {"transformer.w": ("BF16", [1], bf16(1))})
        plan = ComponentPlan(
            stem="transformer",
            tensors=[
                PlannedTensor("transformer.w", "a", src, 0, 2, "BF16", [1]),
                PlannedTensor("transformer.w", "b", src, 0, 2, "BF16", [1]),
            ],
        )
        with pytest.raises(ValueError, match="collide"):
            write_component(plan, tmp_path / "pack")

    def test_data_blob_is_eight_byte_aligned(self, tmp_path):
        src = write_safetensors(tmp_path / "s.safetensors", {"transformer.a": ("BF16", [3], bf16(3))})
        plans, _, _ = plan_conversion([src])
        out, _, _ = write_component(plans["transformer"], tmp_path / "pack")
        _, offset = read_header(out)
        assert offset % 8 == 0


class TestEndToEnd:
    def test_converts_a_monolith_into_a_pack(self, tmp_path):
        src = write_safetensors(
            tmp_path / "ltx-2.5-distilled.safetensors",
            comfy_dit_tensors(num_blocks=2)
            | {
                "model.diffusion_model.duration_head.mlp_out.bias": ("BF16", [1], bf16(1)),
                "encoder.conv_in.weight": ("BF16", [4, 4], bf16(16)),
                "decoder.conv_out.weight": ("BF16", [4, 4], bf16(16)),
            },
            metadata={
                "model_version": "2.5",
                "config": json.dumps(
                    {
                        "model_version": "2.5",
                        "transformer": {"ff_bias": False, "use_prompt_adaln_single": False, "num_layers": 2},
                    }
                ),
            },
        )
        out_dir = tmp_path / "ltx-2.5-mlx-bf16"
        report = convert([src], out_dir, transformer_stem="transformer-distilled")

        names = {p.name for p in out_dir.iterdir()}
        assert "transformer-distilled.safetensors" in names
        assert "duration_head.safetensors" in names
        assert {"config.json", "embedded_config.json", "split_model.json", "manifest.json"} <= names

        manifest = json.loads((out_dir / "manifest.json").read_text())
        assert len(manifest["files"]) == len(report["files"])
        for entry in manifest["files"]:
            path = out_dir / entry["file"]
            assert path.stat().st_size == entry["bytes"]
            assert len(entry["sha256"]) == 64

    def test_metadata_rides_on_the_transformer_and_becomes_config_json(self, tmp_path):
        config = {"model_version": "2.5", "transformer": {"ff_bias": False}}
        src = write_safetensors(
            tmp_path / "dit.safetensors",
            comfy_dit_tensors(num_blocks=1),
            metadata={"model_version": "2.5", "config": json.dumps(config)},
        )
        out_dir = tmp_path / "pack"
        convert([src], out_dir, transformer_stem="transformer-distilled")

        header, _ = read_header(out_dir / "transformer-distilled.safetensors")
        assert header["__metadata__"]["model_version"] == "2.5"

        on_disk = json.loads((out_dir / "config.json").read_text())
        assert on_disk["transformer"]["ff_bias"] is False

    def test_the_vendored_config_reader_accepts_the_result(self, tmp_path):
        """The real proof: our pack is readable by the code that will load it.

        Needs an ``ltx_core_mlx`` that carries the 2.5 config work (the
        ``feat/ltx-2.5`` branch of the fork). The app's vendored checkout is
        pinned to a released tag and does not, so this skips there rather than
        failing — the converter is not what is missing in that case.
        """
        pytest.importorskip("mlx.core")
        port_src = Path(
            os.environ.get(
                "LTX25_PORT_SRC",
                Path.home() / "AI/projects/phosphene/ltx25-port/ltx-2-mlx/packages/ltx-core-mlx/src",
            )
        )
        if port_src.exists():
            sys.path.insert(0, str(port_src))
        from ltx_core_mlx.model.transformer.model import LTXModelConfig

        if not hasattr(LTXModelConfig(), "ff_bias"):
            pytest.skip(
                "the importable ltx_core_mlx predates the 2.5 config work; "
                "set LTX25_PORT_SRC to a checkout of feat/ltx-2.5 to run this"
            )

        config = {
            "model_version": "2.5",
            "transformer": {
                "ff_bias": False,
                "audio_ff_bias": False,
                "use_prompt_adaln_single": False,
                "num_layers": 2,
            },
        }
        src = write_safetensors(
            tmp_path / "dit.safetensors",
            comfy_dit_tensors(num_blocks=1),
            metadata={"model_version": "2.5", "config": json.dumps(config)},
        )
        out_dir = tmp_path / "pack"
        convert([src], out_dir, transformer_stem="transformer-distilled")

        from_dir = LTXModelConfig.from_checkpoint_dir(out_dir)
        assert from_dir.ff_bias is False
        assert from_dir.use_prompt_adaln_single is False
        assert from_dir.num_layers == 2

        from_file = LTXModelConfig.from_checkpoint_file(out_dir / "transformer-distilled.safetensors")
        assert from_file is not None
        assert from_file.model_version == (2, 5)

    def test_dry_run_writes_nothing(self, tmp_path):
        src = write_safetensors(tmp_path / "dit.safetensors", comfy_dit_tensors(num_blocks=1))
        out_dir = tmp_path / "pack"
        report = convert([src], out_dir, dry_run=True)
        assert report["total_bytes"] > 0
        assert not out_dir.exists()

    def test_total_bytes_equals_the_sum_of_sources(self, tmp_path):
        tensors = comfy_dit_tensors(num_blocks=2)
        expected = sum(len(payload) for _, _, payload in tensors.values())
        src = write_safetensors(tmp_path / "dit.safetensors", tensors)
        report = convert([src], tmp_path / "pack", dry_run=True)
        assert report["total_bytes"] == expected


class TestConvLayout:
    """MLX puts input channels last; PyTorch puts them second.

    Derived by diffing every shared key of the converted 2.5 pack against the
    shipped 2.3 pack (ground truth for MLX layout): 768 tensors differ, and
    exactly four permutations explain all of them with no exceptions. Before
    this, the VAE encoder failed to load with
    "Expected shape (128, 3, 3, 3, 48) but received shape (128, 48, 3, 3, 3)".
    """

    @pytest.mark.parametrize(
        ("key", "shape", "want"),
        [
            ("vae_encoder.conv_in.conv.weight", [128, 48, 3, 3, 3], (0, 2, 3, 4, 1)),
            ("audio_vae.decoder.conv_in.conv.weight", [512, 8, 3, 3], (0, 2, 3, 1)),
            # A resampler kernel, not a weight — and it needs Conv1d layout
            # just the same. 402 of these were left unpermuted by an
            # earlier ``*.weight``-only predicate.
            ("vocoder.act_post.downsample.lowpass.filter", [1, 1, 12], (0, 2, 1)),
            ("vocoder.resblocks.0.convs1.0.weight", [512, 512, 3], (0, 2, 1)),
            ("vocoder.ups.0.weight", [1536, 768, 11], (1, 2, 0)),
            ("vocoder.bwe_generator.ups.0.weight", [512, 256, 12], (1, 2, 0)),
            ("vocoder.mel_stft.mel_basis", [64, 513], None),
            ("transformer.transformer_blocks.0.attn1.to_q.weight", [4096, 4096], None),
            ("transformer.scale_shift_table", [2, 4096], None),
            ("vae_encoder.conv_in.conv.bias", [128], None),
        ],
    )
    def test_permutation_rule(self, key, shape, want):
        from convert_ltx_mlx import conv_permutation

        assert conv_permutation(key, shape) == want

    def test_rank_decides_not_the_suffix(self):
        """Non-``.weight`` tensors of conv rank are permuted; rank 1/2 never are."""
        from convert_ltx_mlx import conv_permutation

        assert conv_permutation("vocoder.r.0.upsample.filter", [1, 1, 12]) == (0, 2, 1)
        assert conv_permutation("vocoder.ups.0.bias", [1536]) is None
        assert conv_permutation("vocoder.mel_stft.mel_basis", [64, 513]) is None

    def test_permutation_preserves_bytes_and_reorders_axes(self):
        import numpy as np

        from convert_ltx_mlx import permute_bytes, permuted_shape

        shape, perm = [2, 3, 4], (0, 2, 1)
        src = np.arange(24, dtype=np.uint16)
        out = permute_bytes(src.tobytes(), shape, perm, 2)
        assert len(out) == len(src.tobytes())
        assert permuted_shape(shape, perm) == [2, 4, 3]
        expect = src.reshape(shape).transpose(perm)
        assert np.array_equal(np.frombuffer(out, dtype=np.uint16).reshape(2, 4, 3), expect)

    def test_conv_weight_lands_permuted_in_the_written_file(self, tmp_path):
        """End to end: plan -> write -> header carries the MLX shape."""
        src = write_safetensors(
            tmp_path / "vae.safetensors",
            {"encoder.conv_in.conv.weight": ("BF16", [4, 2, 3, 3, 3], bf16(4 * 2 * 27))},
        )
        plans, unmapped, _ = plan_conversion([src])
        assert unmapped == []
        write_component(plans["vae_encoder"], tmp_path / "out")
        header, _ = read_header(tmp_path / "out" / "vae_encoder.safetensors")
        assert header["vae_encoder.conv_in.conv.weight"]["shape"] == [4, 3, 3, 3, 2]


class TestDiffusionVideoVAE:
    """LTX-2.5 ships two video VAEs whose decoders share the ``decoder.`` prefix.

    Routing the diffusion one with the default table would write transformer
    weights into ``vae_decoder.safetensors`` — the filename the conv decoder
    loads. These pin that the file's own header decides, the way ComfyUI decides.
    """

    def diffusion_tensors(self) -> dict:
        return {
            # The marker key, and enough of the module tree to show the shape.
            "decoder.conv_in.weight": ("BF16", [8, 4], bf16(32)),
            "decoder.conv_in_x_t.weight": ("BF16", [8, 12], bf16(96)),
            "decoder.det_stages.0.0.attn.qkv.weight": ("BF16", [24, 8], bf16(192)),
            "decoder.diff_blocks.0.scale_shift_table": ("BF16", [7, 8], bf16(56)),
            "decoder.type_emb": ("BF16", [4], bf16(4)),
            "encoder.conv_in.conv.weight": ("BF16", [4, 2, 3, 3, 3], bf16(4 * 2 * 27)),
            "per_channel_statistics.mean-of-means": ("F32", [4], b"\0" * 16),
            "per_channel_statistics.std-of-means": ("F32", [4], b"\0" * 16),
        }

    def conv_tensors(self) -> dict:
        return {
            "decoder.conv_in.conv.weight": ("BF16", [8, 4, 3, 3, 3], bf16(8 * 4 * 27)),
            "per_channel_statistics.mean-of-means": ("F32", [4], b"\0" * 16),
            "per_channel_statistics.std-of-means": ("F32", [4], b"\0" * 16),
        }

    def test_the_marker_key_alone_re_points_the_decoder(self, tmp_path):
        src = write_safetensors(tmp_path / "diff_vae.safetensors", self.diffusion_tensors())
        plans, unmapped, _ = plan_conversion([src])
        assert unmapped == []
        assert "vae_decoder" not in plans, "diffusion decoder must not claim the conv decoder's filename"
        assert set(plans) == {"vae_decoder_diffusion", "vae_encoder"}

    def test_decoder_keys_keep_the_decoder_level_the_module_tree_has(self, tmp_path):
        src = write_safetensors(tmp_path / "diff_vae.safetensors", self.diffusion_tensors())
        plans, _, _ = plan_conversion([src])
        keys = {t.target_key for t in plans["vae_decoder_diffusion"].tensors}
        assert "vae_decoder_diffusion.decoder.conv_in.weight" in keys
        assert "vae_decoder_diffusion.decoder.conv_in_x_t.weight" in keys
        # The wrapper owns the statistics, so those sit one level up.
        assert "vae_decoder_diffusion.per_channel_statistics.mean" in keys
        assert "vae_decoder_diffusion.per_channel_statistics.std" in keys

    def test_type_emb_is_routed_not_dropped(self, tmp_path):
        """Neither reference implementation reads ``decoder.type_emb``; ComfyUI loads
        non-strictly and drops it silently. Carrying it is what lets our loader stay
        strict — and a dropped tensor is the failure mode this converter exists to
        prevent."""
        src = write_safetensors(tmp_path / "diff_vae.safetensors", self.diffusion_tensors())
        plans, unmapped, _ = plan_conversion([src])
        assert unmapped == []
        keys = {t.target_key for t in plans["vae_decoder_diffusion"].tensors}
        assert "vae_decoder_diffusion.decoder.type_emb" in keys

    def test_the_conv_video_vae_is_untouched_by_any_of_this(self, tmp_path):
        src = write_safetensors(tmp_path / "conv_vae.safetensors", self.conv_tensors())
        plans, unmapped, _ = plan_conversion([src])
        assert unmapped == []
        # The statistics fan out to the encoder too, as they always have.
        assert set(plans) == {"vae_decoder", "vae_encoder"}
        keys = {t.target_key for t in plans["vae_decoder"].tensors}
        assert "vae_decoder.conv_in.conv.weight" in keys
        assert "vae_decoder.per_channel_statistics.mean" in keys

    def test_both_video_vaes_in_one_run_do_not_collide(self, tmp_path):
        """They carry the same encoder and the same statistics; only the decoders
        differ. One pass over both must yield three components, not a conflict."""
        a = write_safetensors(tmp_path / "diff_vae.safetensors", self.diffusion_tensors())
        b = write_safetensors(tmp_path / "conv_vae.safetensors", self.conv_tensors())
        plans, unmapped, _ = plan_conversion([a, b])
        assert unmapped == []
        assert set(plans) == {"vae_decoder_diffusion", "vae_decoder", "vae_encoder"}

    def test_the_decoder_carries_no_conv_and_so_no_permutation(self, tmp_path):
        """Every tensor in this decoder is rank 1 or 2 — it is Linear all the way
        down. If a permutation ever fires here, something is misrouted."""
        src = write_safetensors(tmp_path / "diff_vae.safetensors", self.diffusion_tensors())
        plans, _, _ = plan_conversion([src])
        assert all(t.permute == () for t in plans["vae_decoder_diffusion"].tensors)
