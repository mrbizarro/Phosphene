#!/usr/bin/env python3
"""A user-provided caption dataset that doesn't mention the job's trigger
word trains cleanly and produces a LoRA that never responds to the word the
user was told to prompt with (#62, `valeriosan_v2`, 2026-09-30).

Root cause: `caption_strategy=user_provided` trusts on-disk caption files
verbatim, and nothing ever compared their content against `spec["trigger"]`.
A dataset reused across training attempts (same cropped photos, stale
caption .txt files from an earlier trigger) silently retrained a working
identity under the OLD trigger while every surface a user could check
(sidecar JSON, Train tab, Characters picker) reported the NEW one. Verified
against a real case: 42/42 existing captions used a different trigger
(`cvjtrn`) than the one the job was submitted with (`valeriosan`); the
resulting LoRA attached cleanly (576/576 modules) and never responded to
`valeriosan` in a direct render test.

This pins the two pure helpers the fix introduces —
`_caption_declared_trigger` and `_caption_trigger_mismatches` — plus the
exact real-world shape (every caption using one consistent OTHER trigger).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["LTX_STATE_DIR"] = tempfile.mkdtemp(prefix="phos-train-trigger-")
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8314")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class CaptionDeclaredTrigger(unittest.TestCase):
    def test_canonical_visual_prefix(self) -> None:
        self.assertEqual(
            P._caption_declared_trigger(
                "[VISUAL]: cvjtrn, The subject stands in a doorway.\n[TEXT]: None\n"
            ),
            "cvjtrn",
        )

    def test_naked_trigger_class_word_caption(self) -> None:
        self.assertEqual(P._caption_declared_trigger("cvjtrn man"), "cvjtrn man")

    def test_comma_separated_falls_back_to_first_segment(self) -> None:
        self.assertEqual(
            P._caption_declared_trigger("cvjtrn, a photo of a man"), "cvjtrn"
        )

    def test_empty_text_returns_none(self) -> None:
        self.assertIsNone(P._caption_declared_trigger(""))
        self.assertIsNone(P._caption_declared_trigger("   \n"))


class CaptionTriggerMismatches(unittest.TestCase):
    def _write(self, d: Path, name: str, text: str) -> Path:
        p = d / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_all_files_match_no_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            files = [
                self._write(d, "1.txt", "[VISUAL]: valeriosan, a man\n[TEXT]: None\n"),
                self._write(d, "2.txt", "[VISUAL]: valeriosan, another shot\n[TEXT]: None\n"),
            ]
            mismatched, dominant = P._caption_trigger_mismatches(files, "valeriosan")
            self.assertEqual(mismatched, [])
            self.assertIsNone(dominant)

    def test_the_real_world_case_every_caption_uses_a_different_trigger(self) -> None:
        """Reproduces the exact `valeriosan_v2` shape: every one of N caption
        files carries the SAME other trigger, none carry the requested one."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            files = [
                self._write(
                    d, f"char_{i:03d}.txt",
                    f"[VISUAL]: cvjtrn, The subject stands in scene {i}.\n[TEXT]: None\n",
                )
                for i in range(42)
            ]
            mismatched, dominant = P._caption_trigger_mismatches(files, "valeriosan")
            self.assertEqual(len(mismatched), 42, "every file should be flagged")
            self.assertEqual(dominant, ("cvjtrn", 42))

    def test_partial_mismatch_reports_the_dominant_other_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            files = [
                self._write(d, "1.txt", "[VISUAL]: valeriosan, a man\n[TEXT]: None\n"),
                self._write(d, "2.txt", "[VISUAL]: cvjtrn, a man\n[TEXT]: None\n"),
                self._write(d, "3.txt", "[VISUAL]: cvjtrn, another shot\n[TEXT]: None\n"),
            ]
            mismatched, dominant = P._caption_trigger_mismatches(files, "valeriosan")
            self.assertEqual(len(mismatched), 2)
            self.assertEqual(dominant, ("cvjtrn", 2))

    def test_trigger_match_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            files = [self._write(d, "1.txt", "[VISUAL]: VALERIOSAN, a man\n[TEXT]: None\n")]
            mismatched, dominant = P._caption_trigger_mismatches(files, "valeriosan")
            self.assertEqual(mismatched, [])

    def test_unreadable_file_is_skipped_not_counted_as_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            missing = d / "does_not_exist.txt"
            mismatched, dominant = P._caption_trigger_mismatches([missing], "valeriosan")
            self.assertEqual(mismatched, [])

    def test_unparseable_mismatches_still_counted_but_not_dominant(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            files = [self._write(d, "1.txt", "")]
            mismatched, dominant = P._caption_trigger_mismatches(files, "valeriosan")
            self.assertEqual(mismatched, ["?"])
            self.assertIsNone(dominant)


if __name__ == "__main__":
    unittest.main()
