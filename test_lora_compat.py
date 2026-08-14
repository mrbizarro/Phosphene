#!/usr/bin/env python3
"""Regression tests for fail-closed LTX LoRA compatibility routing."""

import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lora_compat import (
    LoraCompatibilityError,
    inspect_lora_compatibility,
    validate_runtime_application,
    validate_lora_stack,
)


def _write_header(path: Path, tensors: dict[str, list[int]]) -> None:
    header = {
        key: {"dtype": "F32", "shape": shape, "data_offsets": [0, 0]}
        for key, shape in tensors.items()
    }
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(raw)) + raw)


def _lora_pair(prefix: str) -> dict[str, list[int]]:
    return {
        f"{prefix}.lora_A.weight": [4, 8],
        f"{prefix}.lora_B.weight": [8, 4],
    }


class LoraCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.transformer = self.root / "transformer-distilled.safetensors"
        _write_header(
            self.transformer,
            {
                "transformer.transformer_blocks.0.attn1.to_q.weight": [8, 8],
                "transformer.transformer_blocks.1.attn1.to_q.weight": [8, 8],
            },
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_comfy_prefix_is_remapped_and_fully_matches(self) -> None:
        lora = self.root / "character.safetensors"
        _write_header(
            lora,
            _lora_pair("diffusion_model.transformer_blocks.0.attn1.to_q"),
        )
        report = inspect_lora_compatibility(lora, self.transformer)
        self.assertTrue(report.compatible)
        self.assertEqual(report.tally, "FUSED=2/2 tensors (1/1 modules)")

    def test_zero_match_refuses_and_names_the_file(self) -> None:
        lora = self.root / "wrong-layout.safetensors"
        _write_header(lora, _lora_pair("other_model.blocks.0.to_q"))
        lines: list[str] = []
        with self.assertRaisesRegex(
            LoraCompatibilityError,
            r"wrong-layout\.safetensors.*FUSED=0/2",
        ):
            validate_lora_stack([(str(lora), 1.0)], self.transformer,
                                reporter=lines.append)
        self.assertEqual(
            lines,
            ["LoRA[1] file=wrong-layout.safetensors strength=1.00 "
             "FUSED=0/2 tensors (0/1 modules)"],
        )

    def test_anomalously_partial_layout_refuses(self) -> None:
        tensors: dict[str, list[int]] = {}
        for index in range(10):
            tensors.update(_lora_pair(
                f"diffusion_model.transformer_blocks.{index}.attn1.to_q"
            ))
        lora = self.root / "partial.safetensors"
        _write_header(lora, tensors)
        report = inspect_lora_compatibility(lora, self.transformer)
        self.assertFalse(report.compatible)
        self.assertEqual(report.tally, "FUSED=4/20 tensors (2/10 modules)")
        with self.assertRaises(LoraCompatibilityError):
            report.require_compatible()

    def test_ninety_percent_module_coverage_is_accepted(self) -> None:
        tensors: dict[str, list[int]] = {}
        model_tensors: dict[str, list[int]] = {}
        for index in range(10):
            module = f"transformer_blocks.{index}.attn1.to_q"
            tensors.update(_lora_pair(f"diffusion_model.{module}"))
            if index < 9:
                model_tensors[f"transformer.{module}.weight"] = [8, 8]
        transformer = self.root / "ninety-percent-transformer.safetensors"
        lora = self.root / "ninety-percent.safetensors"
        _write_header(transformer, model_tensors)
        _write_header(lora, tensors)
        report = inspect_lora_compatibility(lora, transformer)
        self.assertTrue(report.compatible)
        self.assertEqual(report.tally, "FUSED=18/20 tensors (9/10 modules)")

    def test_dangling_tensor_refuses_as_incomplete_pair(self) -> None:
        lora = self.root / "dangling.safetensors"
        tensors = _lora_pair(
            "diffusion_model.transformer_blocks.0.attn1.to_q"
        )
        tensors[
            "diffusion_model.transformer_blocks.1.attn1.to_q.lora_A.weight"
        ] = [4, 8]
        _write_header(lora, tensors)
        report = inspect_lora_compatibility(lora, self.transformer)
        self.assertFalse(report.compatible)
        self.assertIn("only 2 of 3 LoRA tensors form complete A/B pairs",
                      report.failure_message())

    def test_zero_strength_is_an_explicit_disable_not_a_failure(self) -> None:
        lora = self.root / "wrong-layout.safetensors"
        _write_header(lora, _lora_pair("other_model.blocks.0.to_q"))
        lines: list[str] = []
        reports = validate_lora_stack(
            [(str(lora), 0.0)], self.transformer, reporter=lines.append
        )
        self.assertEqual(reports, [])
        self.assertEqual(
            lines,
            ["LoRA[1] file=wrong-layout.safetensors strength=0.00 "
            "SKIPPED=disabled"],
        )

    def test_live_loader_zero_match_refuses_with_fusion_tally(self) -> None:
        lora = self.root / "character.safetensors"
        _write_header(
            lora,
            _lora_pair("diffusion_model.transformer_blocks.0.attn1.to_q"),
        )
        report = inspect_lora_compatibility(lora, self.transformer)
        lines: list[str] = []
        with self.assertRaisesRegex(
            LoraCompatibilityError,
            r"character\.safetensors.*FUSED=0/2",
        ):
            validate_runtime_application(
                [(report, 1.0)], [], reporter=lines.append
            )
        self.assertEqual(
            lines,
            ["LoRA[1] strength=1.00 FUSED=0/2 tensors (0/1 modules) "
             "file=character.safetensors"],
        )

    def test_live_loader_full_match_reports_file_and_strength(self) -> None:
        lora = self.root / "character.safetensors"
        _write_header(
            lora,
            _lora_pair("diffusion_model.transformer_blocks.0.attn1.to_q"),
        )
        report = inspect_lora_compatibility(lora, self.transformer)
        lines: list[str] = []
        validate_runtime_application(
            [(report, 0.85)],
            ["transformer_blocks.0.attn1.to_q"],
            reporter=lines.append,
        )
        self.assertEqual(
            lines,
            ["LoRA[1] strength=0.85 FUSED=2/2 tensors (1/1 modules) "
             "file=character.safetensors"],
        )

    def test_character_library_hides_incompatible_active_generation(self) -> None:
        import mlx_ltx_panel as panel

        good = self.root / "good_v2.safetensors"
        bad = self.root / "bad_v2.safetensors"
        _write_header(
            good,
            _lora_pair("diffusion_model.transformer_blocks.0.attn1.to_q"),
        )
        _write_header(bad, _lora_pair("other_model.blocks.0.to_q"))
        with (
            patch.object(panel, "LORAS_DIR", self.root),
            patch.object(panel, "_safe_loras_dir", return_value=self.root),
            patch.object(panel, "_active_ltx_transformer_path",
                         return_value=self.transformer),
            patch.object(panel, "_CHARACTERS_CACHE_PATH",
                         self.root / "characters"),
            patch.object(panel, "LORA_LAB_ROOT", self.root / "lab"),
        ):
            all_characters = {c["id"]: c for c in panel.list_characters()}
            offered = {c["id"] for c in panel.list_library_characters()}
            self.assertTrue(all_characters["good"]["ltx_compatible"])
            self.assertFalse(all_characters["bad"]["ltx_compatible"])
            self.assertIn("good", offered)
            self.assertNotIn("bad", offered)
            refusal = panel._validate_character_quality({"character_id": "bad"})
            self.assertIn("bad_v2.safetensors", refusal or "")


if __name__ == "__main__":
    unittest.main()
