#!/usr/bin/env python3
"""Regression tests for fail-closed LTX LoRA compatibility routing."""

import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lora_compat
from lora_compat import (
    WEAK_DELTA_RMS,
    LoraCompatibilityError,
    inspect_lora_compatibility,
    measure_adapter_effect,
    read_tensor_header,
    validate_adapter_effects,
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


def _write_adapter(path: Path, modules: dict[str, tuple["np.ndarray", "np.ndarray"]]) -> None:
    """Write a real safetensors file (header + payload) for one adapter."""
    import numpy as np

    header: dict[str, dict] = {}
    blobs: list[bytes] = []
    offset = 0
    for name, (a, b) in modules.items():
        for suffix, array in ((".lora_A.weight", a), (".lora_B.weight", b)):
            raw = np.ascontiguousarray(array, dtype=np.float32).tobytes()
            header[f"{name}{suffix}"] = {
                "dtype": "F32",
                "shape": list(array.shape),
                "data_offsets": [offset, offset + len(raw)],
            }
            blobs.append(raw)
            offset += len(raw)
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded + b"".join(blobs))


class AdapterEffectTests(unittest.TestCase):
    """A green key tally is not evidence that an adapter can do anything (#62)."""

    def setUp(self) -> None:
        import numpy as np

        self.np = np
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rng = np.random.default_rng(11)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _adapter(self, name: str, b_scale: float, modules: int = 3) -> Path:
        np = self.np
        payload = {}
        for index in range(modules):
            a = self.rng.normal(0, 1 / 64, size=(8, 4096)).astype(np.float32)
            b = (self.rng.normal(0, 1, size=(4096, 8)) * b_scale).astype(np.float32)
            payload[f"diffusion_model.transformer_blocks.{index}.attn1.to_q"] = (a, b)
        path = self.root / name
        _write_adapter(path, payload)
        return path

    def test_frobenius_norm_matches_the_explicit_product(self) -> None:
        """The trace identity must agree with actually forming ``B @ A``."""
        np = self.np
        path = self._adapter("exact.safetensors", b_scale=1e-2, modules=1)
        effect = measure_adapter_effect(path)
        header = read_tensor_header(path)
        module = "diffusion_model.transformer_blocks.0.attn1.to_q"
        with path.open("rb") as fh:
            size = struct.unpack("<Q", fh.read(8))[0]
            base = 8 + size

            def load(key: str):
                info = header[key]
                start, end = info["data_offsets"]
                fh.seek(base + start)
                return np.frombuffer(fh.read(end - start), dtype="<f4").reshape(
                    info["shape"]
                )

            delta = load(module + ".lora_B.weight") @ load(module + ".lora_A.weight")
        expected = float(np.linalg.norm(delta)) / (4096 * 4096) ** 0.5
        self.assertAlmostEqual(effect.median_rms, expected, places=7)

    def test_untrained_adapter_is_inert_and_refused(self) -> None:
        path = self._adapter("never_trained.safetensors", b_scale=0.0)
        effect = measure_adapter_effect(path)
        self.assertTrue(effect.inert)
        self.assertEqual(effect.zero_modules, effect.modules)
        with self.assertRaisesRegex(
            LoraCompatibilityError, r"never_trained\.safetensors.*no weight delta"
        ):
            validate_adapter_effects([(str(path), 1.0)])

    def test_weak_adapter_is_reported_but_never_refused(self) -> None:
        """Below the floor is a warning: a light touch is the owner's call."""
        path = self._adapter("barely_trained.safetensors", b_scale=1e-6)
        lines: list[str] = []
        effects = validate_adapter_effects([(str(path), 1.0)], reporter=lines.append)
        self.assertEqual(len(effects), 1)
        self.assertTrue(effects[0].weak)
        self.assertFalse(effects[0].inert)
        self.assertEqual(len(lines), 2)
        self.assertIn("delta_rms median=", lines[0])
        self.assertIn("WARNING", lines[1])

    def test_identity_grade_adapter_passes_clean(self) -> None:
        """Sized to the shipped characters: median rms ~1.4e-03."""
        path = self._adapter("carries_identity.safetensors", b_scale=1.2e-2)
        lines: list[str] = []
        effect = validate_adapter_effects(
            [(str(path), 1.0)], reporter=lines.append
        )[0]
        self.assertFalse(effect.weak)
        self.assertFalse(effect.inert)
        self.assertGreater(effect.median_rms, WEAK_DELTA_RMS)
        self.assertEqual(len(lines), 1)
        self.assertIn("3/3 modules carry a delta", lines[0])

    def test_disabled_adapter_is_not_measured(self) -> None:
        path = self._adapter("switched_off.safetensors", b_scale=0.0)
        lines: list[str] = []
        self.assertEqual(
            validate_adapter_effects([(str(path), 0.0)], reporter=lines.append), []
        )
        self.assertEqual(lines, [])

    def test_training_reports_the_number_and_shouts_when_it_is_low(self) -> None:
        """The trainer's own verdict — the line #62 never got."""
        import io
        import contextlib

        from lora_lab import train_character

        path = self._adapter("weak_v2.safetensors", b_scale=1e-6)
        stream = io.StringIO()
        with patch.object(train_character, "_REAL_STDOUT", stream):
            payload = train_character.report_adapter_strength(path)
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        kinds = [event["event"] for event in events]
        self.assertEqual(payload["verdict"], "weak")
        self.assertIn("adapter_strength", kinds)
        self.assertIn("warning", kinds)
        warning = next(e for e in events if e["event"] == "warning")
        self.assertEqual(warning["stage"], "verify")
        self.assertIn("weak_v2.safetensors", warning["message"])

    def test_training_verdict_is_ok_for_an_identity_grade_adapter(self) -> None:
        import io

        from lora_lab import train_character

        path = self._adapter("strong_v2.safetensors", b_scale=1.2e-2)
        stream = io.StringIO()
        with patch.object(train_character, "_REAL_STDOUT", stream):
            payload = train_character.report_adapter_strength(path)
        self.assertEqual(payload["verdict"], "ok")
        self.assertEqual(payload["carrying_modules"], payload["modules"])
        self.assertNotIn("warning", stream.getvalue())


class AdapterCliTests(unittest.TestCase):
    """`python3 lora_compat.py <file>` — the answer without a render.

    #62 cost its reporter days of proving a negative about a file already on
    his disk, and the owner's only way to settle it was to ask for a 500 MB
    upload. The measurement takes about a second, so it has to be reachable
    from a shell on the machine that holds the file.
    """

    def setUp(self) -> None:
        try:
            import numpy
        except ImportError:  # pragma: no cover
            self.skipTest("numpy unavailable")
        self.np = numpy
        self.rng = numpy.random.default_rng(11)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _adapter(self, name: str, b_scale: float) -> Path:
        np = self.np
        a = self.rng.normal(0, 1 / 64, size=(8, 4096)).astype(np.float32)
        b = (self.rng.normal(0, 1, size=(4096, 8)) * b_scale).astype(np.float32)
        path = self.root / name
        _write_adapter(
            path, {"diffusion_model.transformer_blocks.0.attn1.to_q": (a, b)}
        )
        return path

    def _run(self, *argv: str) -> tuple[int, str]:
        import io

        stream = io.StringIO()
        with patch.object(lora_compat.sys, "stdout", stream):
            code = lora_compat.main(list(argv))
        return code, stream.getvalue()

    def test_identity_grade_adapter_reports_ok_and_exits_zero(self) -> None:
        path = self._adapter("carries_identity.safetensors", b_scale=1.2e-2)
        code, out = self._run(str(path))
        self.assertEqual(code, 0)
        self.assertIn("OK", out)
        self.assertIn("delta_rms median=", out)

    def test_weak_is_reported_and_still_exits_zero(self) -> None:
        """A light touch is the owner's call, so WEAK must not fail the run."""
        path = self._adapter("barely_trained.safetensors", b_scale=1e-6)
        code, out = self._run(str(path))
        self.assertEqual(code, 0)
        self.assertIn("WEAK", out)

    def test_inert_adapter_exits_one(self) -> None:
        path = self._adapter("never_trained.safetensors", b_scale=0.0)
        code, out = self._run(str(path))
        self.assertEqual(code, 1)
        self.assertIn("INERT", out)

    def test_worst_status_across_several_files_decides_the_exit(self) -> None:
        good = self._adapter("good.safetensors", b_scale=1.2e-2)
        dead = self._adapter("dead.safetensors", b_scale=0.0)
        code, out = self._run(str(good), str(dead))
        self.assertEqual(code, 1)
        self.assertIn("good.safetensors", out)
        self.assertIn("dead.safetensors", out)

    def test_a_path_that_does_not_exist_is_the_callers_mistake(self) -> None:
        code, out = self._run(str(self.root / "absent.safetensors"))
        self.assertEqual(code, 2)
        self.assertIn("NOT FOUND", out)

    def test_no_arguments_prints_usage_and_exits_two(self) -> None:
        code, out = self._run()
        self.assertEqual(code, 2)
        self.assertIn("usage:", out)

    def test_help_is_not_an_error(self) -> None:
        code, out = self._run("--help")
        self.assertEqual(code, 0)
        self.assertIn("usage:", out)

    def test_describe_adapter_returns_a_status_not_a_string_to_sniff(self) -> None:
        path = self._adapter("strong.safetensors", b_scale=1.2e-2)
        status, line = lora_compat.describe_adapter(path)
        self.assertEqual(status, "ok")
        self.assertIn("strong.safetensors", line)



def _full_training_presets() -> dict:
    """TRAIN_PRESETS as a >=64 GB Mac boots with it, on any Mac.

    Issue #90: below 64 GB, `_select_train_profile` REPLACES the table at
    import with the compact one (TheSub64GbHighIsNotTheGradedRecipe asserts
    those pills), and nothing keeps a copy of the original. Reading the live
    table made the >=64 GB pill assertions fail on every 48 GB Mac. The full
    table is the literal assigned in the panel source, evaluated (not
    grepped); on a Mac that runs it, it must also BE the live table, so the
    literal cannot drift from what such a Mac is served."""
    import ast
    import mlx_ltx_panel as panel
    tree = ast.parse(Path(panel.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "TRAIN_PRESETS" for t in node.targets):
            table = ast.literal_eval(node.value)
            if not panel.TRAIN_PROFILE.get("compact"):
                assert table == panel.TRAIN_PRESETS, \
                    "a >=64 GB Mac is not served the TRAIN_PRESETS literal"
            return table
    raise AssertionError("TRAIN_PRESETS is no longer a literal assignment")


class AGreenTallyIsNotProof(unittest.TestCase):
    """A LoRA can train to completion, write a valid safetensors, load without
    a warning and change NOTHING — the deltas are too small to move the model.
    Every gate said "done"; only the render said otherwise, hours later."""

    def test_the_verdict_rides_out_of_the_sidecar(self):
        import mlx_ltx_panel as panel
        self.assertEqual(panel._adapter_verdict(
            {"adapter_strength": {"verdict": "weak"}}), "weak")
        self.assertEqual(panel._adapter_verdict(
            {"adapter_strength": {"verdict": "ok"}}), "ok")

    def test_a_run_that_never_measured_is_unknown_and_not_weak(self):
        # Silence is not weakness: a chip on every LoRA trained before the
        # trainer measured would be noise nobody could act on.
        import mlx_ltx_panel as panel
        for meta in ({}, {"adapter_strength": None},
                     {"adapter_strength": {}}, {"adapter_strength": "yes"}):
            self.assertEqual(panel._adapter_verdict(meta), "unknown")

    def test_the_two_ungraded_presets_say_so_on_the_pill(self):
        # The rank-32 recipe is the one measured on faces. Saying "fast" and
        # letting the user infer "as good, sooner" is the pill doing the lying.
        presets = _full_training_presets()
        self.assertIn("ungraded", presets["quick"]["subtitle"])
        self.assertIn("ungraded", presets["medium"]["subtitle"])
        self.assertNotIn("ungraded", presets["high"]["subtitle"])
        self.assertIn("validated", presets["high"]["subtitle"])

    def test_the_trainer_event_is_read_and_a_weak_run_is_not_a_plain_done(self):
        root = Path(__file__).resolve().parent
        # Server source + the page (webapp/index.html since slice 2 of
        # the extraction — docs/ARCHITECTURE.md): the asserts span both.
        src = (root / "mlx_ltx_panel.py").read_text() + "\n" + (
            root / "webapp" / "index.html").read_text()
        for _m in sorted((root / "webapp" / "js").glob("*.js")):
            src += "\n" + _m.read_text()
        self.assertIn('elif evt == "adapter_strength":', src)
        self.assertIn("[train] adapter strength: delta_rms", src)
        self.assertIn("delta_rms_median", src)
        self.assertIn("carrying_modules", src)
        # ...and the job carries the warning rather than reporting success.
        self.assertIn('job["warning"]', src)
        self.assertIn('if train_verdict not in ("ok", "unknown"):', src)


class TheDefaultIsTheRecommendation(unittest.TestCase):
    """#62: the Train tab both pre-selected AND badged "Recommended" on Quick,
    which trains at rank 8 — the one tier that has never carried a face. Two
    users walked into it. Deleting the badge and leaving Quick pre-selected
    would have fixed nothing: to most people the default IS the recommendation.
    """

    def test_character_recommends_and_defaults_to_the_graded_recipe(self):
        import mlx_ltx_panel as panel
        self.assertEqual(panel.TRAIN_DEFAULT_PRESET["character"], "high")
        # Style is what rank 8/16 is genuinely good at — a look, cheaply.
        self.assertEqual(panel.TRAIN_DEFAULT_PRESET["style"], "quick")

    def test_the_pill_that_is_preselected_is_the_pill_that_is_badged(self):
        root = Path(__file__).resolve().parent
        # Server source + the page (webapp/index.html since slice 2 of
        # the extraction — docs/ARCHITECTURE.md): the asserts span both.
        src = (root / "mlx_ltx_panel.py").read_text() + "\n" + (
            root / "webapp" / "index.html").read_text()
        for _m in sorted((root / "webapp" / "js").glob("*.js")):
            src += "\n" + _m.read_text()
        pills = [ln for ln in src.splitlines() if "data-train-preset=" in ln]
        self.assertEqual(len(pills), 3, pills)
        quick = next(p for p in pills if 'data-train-preset="quick"' in p)
        high = next(p for p in pills if 'data-train-preset="high"' in p)
        # Quick is no longer pre-selected and its badge slot ships hidden.
        self.assertNotIn("pill-btn active", quick)
        self.assertIn("data-rec-slot hidden", quick)
        # High is pre-selected and carries the visible badge.
        self.assertIn("pill-btn active", high)
        self.assertIn("<span class=\"rec-badge\" data-rec-slot>", high)
        # And the badge follows the server's table at runtime rather than
        # being nailed to one pill, so pill and make_job cannot drift.
        self.assertIn("function trainRecommendedPreset()", src)
        self.assertIn("train_default_preset", src)

    def test_quick_says_what_it_is_for_rather_than_only_that_it_is_fast(self):
        self.assertIn("a look, not a face",
                      _full_training_presets()["quick"]["subtitle"])


class TheSub64GbHighIsNotTheGradedRecipe(unittest.TestCase):
    """`_select_train_profile` rewrites the whole preset table under 64 GB.
    "High" there is rank 8 / 500 steps / 448px on HALF the projections — less
    adapter than the >=64 GB *Quick* that has measured 1.54e-04 and 1.98e-04.
    Telling those users "just use High" would be advice their Mac cannot
    honour, so the table and the advice both have to say so.
    """

    def setUp(self):
        import copy
        import mlx_ltx_panel as panel
        self._panel = panel
        self._saved = (copy.deepcopy(panel.TRAIN_PRESETS),
                       copy.deepcopy(panel.TRAIN_STYLE_PRESETS),
                       copy.deepcopy(panel.TRAIN_PROFILE))

    def tearDown(self):
        panel = self._panel
        panel.TRAIN_PRESETS.clear()
        panel.TRAIN_PRESETS.update(self._saved[0])
        panel.TRAIN_STYLE_PRESETS.clear()
        panel.TRAIN_STYLE_PRESETS.update(self._saved[1])
        panel.TRAIN_PROFILE = self._saved[2]

    def test_high_on_a_48gb_mac_is_rank_8_on_two_projections(self):
        panel = self._panel
        profile = panel._select_train_profile(48.0, "compact")
        self.assertTrue(profile["compact"])
        high = panel.TRAIN_PRESETS["high"]
        self.assertEqual(high["rank"], 8)
        self.assertEqual(high["max_steps"], 500)
        self.assertEqual(high["resolution"], 448)
        self.assertEqual(high["target_modules"], ["to_q", "to_v"])
        # The graded recipe is rank 32 on all four projections. Nothing in
        # this table reaches it, so the pill has to stop implying it does.
        self.assertIn("NOT the rank-32 recipe", high["subtitle"])
        for key in ("quick", "medium", "high"):
            self.assertIn("ungraded", panel.TRAIN_PRESETS[key]["subtitle"])

    def test_the_advice_does_not_tell_a_48gb_mac_to_use_high(self):
        panel = self._panel
        panel.TRAIN_PROFILE = panel._select_train_profile(48.0, "compact")
        advice = panel._train_weak_advice("weak", "high", False)
        self.assertIn("compact profile", advice)
        self.assertNotIn("Train again on the High preset", advice)


class AVerdictWithoutARemedyIsNotAnAnswer(unittest.TestCase):
    """The number shipped in v4.6.0 and was correct; a first-time user still
    had to read a 21-comment GitHub thread to learn what his own 1.98e-04
    meant for him. The remedy travels with the verdict now."""

    def test_a_weak_quick_run_is_told_to_train_on_high(self):
        import mlx_ltx_panel as panel
        advice = panel._train_weak_advice("weak", "quick", False)
        self.assertIn("High", advice)
        self.assertIn("rank 8", advice)

    def test_inert_is_a_failed_run_not_a_weak_one(self):
        import mlx_ltx_panel as panel
        advice = panel._train_weak_advice("inert", "high", False)
        self.assertIn("no weight delta at all", advice)
        self.assertIn("failed", advice)

    def test_the_library_carries_the_remedy_and_the_ui_renders_it(self):
        root = Path(__file__).resolve().parent
        # Server source + the page (webapp/index.html since slice 2 of
        # the extraction — docs/ARCHITECTURE.md): the asserts span both.
        src = (root / "mlx_ltx_panel.py").read_text() + "\n" + (
            root / "train_support.py").read_text() + "\n" + (
            root / "webapp" / "index.html").read_text()
        for _m in sorted((root / "webapp" / "js").glob("*.js")):
            src += "\n" + _m.read_text()
        # /train/list ships the remedy beside the verdict...
        self.assertIn('"adapter_advice"', src)
        # ...the trained-LoRA list renders both...
        self.assertIn("function trainRenderVerdictBanner(", src)
        self.assertIn("train-lora-verdict", src)
        # ...and a job that finished WEAK no longer reads as a plain "done"
        # in the history row.
        self.assertIn("warn-inline", src)


if __name__ == "__main__":
    unittest.main()
