"""H3 refuses to start on an MLX its runner cannot use.

Fleet, 4.14.3 and 4.15.0: `TypeError: seed(): incompatible function arguments`
and `repeat(): incompatible function arguments`, both ~8 minutes into a render,
neither naming MLX. They are signature drift. H3 needs mlx>=0.32 while the LTX
engine pins mlx==0.31.1 (0.31.2 regresses LTX audio by 22 dB) — the separate
venv exists for that reason, and a stray install into it puts the old MLX back.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import mlx_ltx_panel as P


class _H3Venv:
    def __init__(self, mlx_version="0.32.2"):
        self.v = mlx_version

    def __enter__(self):
        self.tmp = TemporaryDirectory()
        venv = Path(self.tmp.name) / ".venv"
        site = venv / "lib" / "python3.11" / "site-packages"
        site.mkdir(parents=True)
        (venv / "bin").mkdir(parents=True)
        self.python = venv / "bin" / "python3.11"
        self.python.write_text("#!/bin/sh\n")
        if self.v:
            (site / f"mlx-{self.v}.dist-info").mkdir()
        self.orig = P._h3_python
        P._h3_python = lambda: self.python
        return self

    def __exit__(self, *a):
        P._h3_python = self.orig
        self.tmp.cleanup()


class ReadingTheVersion(unittest.TestCase):

    def test_a_normal_version(self):
        with _H3Venv("0.32.2") as v:
            self.assertEqual(P._venv_dist_version(v.python, "mlx"), (0, 32, 2))

    def test_a_release_candidate_suffix_does_not_break_it(self):
        with _H3Venv("0.33.0rc1") as v:
            self.assertEqual(P._venv_dist_version(v.python, "mlx"), (0, 33, 0))

    def test_an_absent_package_reads_as_none(self):
        with _H3Venv(None) as v:
            self.assertIsNone(P._venv_dist_version(v.python, "mlx"))

    def test_a_similarly_named_package_is_not_mistaken_for_mlx(self):
        """`mlx_metal-0.32.2.dist-info` and `mlx_audio-0.5.1.dist-info` sit in
        the same folder on a real install — 0.5.1 would read as far too old."""
        with _H3Venv("0.32.2") as v:
            site = v.python.parent.parent / "lib/python3.11/site-packages"
            (site / "mlx_audio-0.5.1.dist-info").mkdir()
            self.assertEqual(P._venv_dist_version(v.python, "mlx"), (0, 32, 2))


class TheGuard(unittest.TestCase):

    def test_a_good_venv_is_no_fault(self):
        with _H3Venv("0.32.2"):
            self.assertEqual(P.h3_mlx_fault(), "")

    def test_the_old_mlx_is_caught_and_the_symptom_is_named(self):
        with _H3Venv("0.31.1"):
            fault = P.h3_mlx_fault()
            self.assertIn("0.31.1", fault)
            self.assertIn("seed()", fault)

    def test_a_venv_with_no_mlx_is_caught(self):
        with _H3Venv(None):
            self.assertIn("no MLX", P.h3_mlx_fault())

    def test_h3_not_installed_is_not_this_functions_problem(self):
        orig = P._h3_python
        P._h3_python = lambda: None
        try:
            self.assertEqual(P.h3_mlx_fault(), "")
        finally:
            P._h3_python = orig

    def test_an_unreadable_layout_is_not_called_broken(self):
        """Absence of evidence. LTX_H3_PYTHON can point somewhere this probe
        does not understand; refusing there would block H3 on a machine where
        it runs fine."""
        from tempfile import TemporaryDirectory as _TD
        with _TD() as tmp:
            lonely = Path(tmp) / "bin" / "python3.11"
            lonely.parent.mkdir(parents=True)
            lonely.write_text("#!/bin/sh\n")
            orig = P._h3_python
            P._h3_python = lambda: lonely
            try:
                self.assertEqual(P.h3_mlx_fault(), "")
            finally:
                P._h3_python = orig

    def test_the_render_path_checks_before_it_spends_anything(self):
        """Ordering is the point: the check has to run before the LTX helper
        is killed and before any GPU time is spent."""
        src = Path(__file__).with_name("mlx_ltx_panel.py").read_text()
        guard = src.index("_mlx_fault = h3_mlx_fault()")
        helper_kill = src.index("stopping the LTX warm helper", guard - 4000)
        self.assertLess(guard, helper_kill)

    def test_the_message_is_filed_under_venv_broken(self):
        with _H3Venv("0.31.1"):
            msg = f"H3 cannot start: {P.h3_mlx_fault()}. {P.H3_MLX_REPAIR}"
            self.assertEqual(P._analytics_error_class(msg), "venv_broken")

    def test_the_repair_names_the_sidebar_entry(self):
        self.assertIn("Repair Hailuo H3", P.H3_MLX_REPAIR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
