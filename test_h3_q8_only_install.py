"""An install that built the Q8 pack and then deleted the 41 GB bf16 master is
still an installed H3 — on Linux, whose backend renders the pack directly. On
macOS the master stays required and no lookup may name a file that isn't there."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hostinfo
import mlx_ltx_panel as P


def _touch(path: Path, data: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class Q8OnlyInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phos-h3-q8only-"))
        m = self.tmp / "models"
        for f in P.H3_COMPACT_FILES:
            _touch(m / "ddalcu-q8" / f)
        _touch(m.joinpath("upstream-meta", *P.H3_TEXT_CONFIG_REL))
        pack = m / P.H3_DIT_Q8_DIRNAME
        _touch(pack / "config.json", b"{}")
        _touch(pack / "quant_config.json", b"{}")
        _touch(pack / ".built_ok")
        _touch(pack / "model-00001-of-00001.safetensors")
        _touch(pack / "model.safetensors.index.json",
               b'{"weight_map":{"w":"model-00001-of-00001.safetensors"}}')
        self.models = self.tmp / "models"

    def _paths(self):
        with mock.patch.object(P, "H3_MODELS", self.tmp), \
                mock.patch.object(hostinfo, "IS_MAC", False):
            return P.h3_paths(), P.h3_dit_choice()

    def test_q8_pack_alone_counts_as_installed(self):
        paths, (kind, pack) = self._paths()
        self.assertEqual([m for m in paths["missing"] if "pruned bf16" in m], [])
        self.assertTrue(paths["weights_ok"])
        self.assertEqual(kind, "q8")
        self.assertEqual(pack, self.models / P.H3_DIT_Q8_DIRNAME)

    def test_linux_q8_only_never_names_the_bf16_master(self):
        """No master on disk -> no bf16 path, whatever Settings asks; the
        pack is the renderable DiT and the choice routes to it."""
        master = self.models / "deepbeep-pruned-bf16" / P.H3_DIT_FILENAME
        with mock.patch.object(P, "H3_MODELS", self.tmp), \
                mock.patch.object(hostinfo, "IS_MAC", False):
            self.assertIsNone(P.h3_paths()["dit"])
            self.assertFalse(master.exists())
            for pref in ("auto", "q8", "bf16"):
                with mock.patch.object(P, "get_settings",
                                       lambda p=pref: {"h3_dit": p}):
                    self.assertEqual(P.h3_dit_choice(),
                                     ("q8", self.models / P.H3_DIT_Q8_DIRNAME),
                                     pref)

    def test_turbo_and_loras_follow_the_pack(self):
        with mock.patch.object(P, "H3_MODELS", self.tmp), \
                mock.patch.object(hostinfo, "IS_MAC", False):
            self.assertEqual(P._h3_turbo_dir(), self.models / P.H3_TURBO_DIRNAME)
            self.assertEqual(P._h3_loras_dir(), self.models / P.H3_LORAS_DIRNAME)

    def test_marker_without_configs_is_not_installed(self):
        pack = self.models / P.H3_DIT_Q8_DIRNAME
        (pack / "config.json").unlink()
        (pack / "quant_config.json").unlink()
        paths, _ = self._paths()
        self.assertFalse(paths["weights_ok"])
        self.assertTrue(any("pruned bf16" in m for m in paths["missing"]))

    def test_marker_with_a_missing_indexed_shard_is_not_installed(self):
        pack = self.models / P.H3_DIT_Q8_DIRNAME
        (pack / "model-00001-of-00001.safetensors").unlink()
        paths, _ = self._paths()
        self.assertFalse(paths["weights_ok"])
        self.assertTrue(any("pruned bf16" in m for m in paths["missing"]))


class MacOSRootDiscovery(unittest.TestCase):
    """macOS never accepted the Q8 pack as a stand-in for the 41 GB master:
    only a root with the real file drives dit, Turbo and the LoRA folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phos-h3-macos-"))
        models = self.tmp / "models"          # root 2, the canonical layout
        _touch(models.joinpath("deepbeep-pruned-bf16", P.H3_DIT_FILENAME), b"M")
        pack = self.tmp / P.H3_DIT_Q8_DIRNAME  # root 1, Q8-only
        _touch(pack / "config.json", b"{}")
        _touch(pack / "quant_config.json", b"{}")
        _touch(pack / ".built_ok")
        self.master = models / "deepbeep-pruned-bf16" / P.H3_DIT_FILENAME

    def test_multiroot_lookup_follows_only_the_real_master(self):
        with mock.patch.object(P, "H3_MODELS", self.tmp), \
                mock.patch.object(hostinfo, "IS_MAC", True):
            paths = P.h3_paths()
            self.assertEqual(paths["dit"], self.master)
            self.assertEqual(paths["models_root"], str(self.tmp / "models"))
            self.assertEqual(P._h3_turbo_dir(),
                             self.tmp / "models" / P.H3_TURBO_DIRNAME)
            self.assertEqual(P._h3_loras_dir(),
                             self.tmp / "models" / P.H3_LORAS_DIRNAME)

    def test_q8_only_root_is_not_installed(self):
        self.master.unlink()                  # only the Q8 root remains
        with mock.patch.object(P, "H3_MODELS", self.tmp), \
                mock.patch.object(hostinfo, "IS_MAC", True):
            paths = P.h3_paths()
            self.assertIsNone(paths["dit"])
            self.assertTrue(any("pruned bf16" in m for m in paths["missing"]))
            self.assertEqual(P._h3_turbo_dir(), self.tmp / P.H3_TURBO_DIRNAME)


class RunnerEnvironment(unittest.TestCase):
    def test_linux_decodes_the_vae_tile_by_tile(self):
        with mock.patch.object(hostinfo, "IS_MAC", False):
            self.assertEqual(hostinfo.h3_env_defaults(), {"H3_VAE_BATCH": "1"})

    def test_macos_keeps_the_runner_defaults(self):
        with mock.patch.object(hostinfo, "IS_MAC", True):
            self.assertEqual(hostinfo.h3_env_defaults(), {})


if __name__ == "__main__":
    unittest.main()
