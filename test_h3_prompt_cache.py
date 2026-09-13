#!/usr/bin/env python3
"""H3's memory ceiling is a 7-second phase, and this is the switch that skips it.

MEASURED 2026-08-29 (staged-runner phase peaks, identical across every arm):

    text_encode_q8    25.71 GiB   <- the RUN PEAK on the Q8 path
    dit_load          20.22 GiB   (Q8)   /  38.56 GiB (bf16)
    joint_denoise     21.28 GiB   (5 s)  /  24.68 GiB (10 s)

So on Q8, what a Mac must fit to run H3 at all is decided by loading a 26.28 GB
text encoder for seven seconds — not by the twenty-minute render. The encoder is
already 88% 8-bit, so there is nothing cheap left inside the file; the win is
not loading it.

`--prompt-cache` has always been in the runner and the panel never passed it.
This suite pins that it is passed, and pins the KEY, because the runner RAISES
on a cache built for a different prompt or first frame — a key that is too
coarse turns a silent cache hit into a failed render.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-h3-pc-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8301")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

PANEL_SRC = ((ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
             + "\n" + (ROOT / "webapp" / "index.html").read_text(encoding="utf-8"))
for _m in sorted((ROOT / "webapp" / "js").glob("*.js")):
    PANEL_SRC += "\n" + _m.read_text(encoding="utf-8")


class TheKeyIsFineEnoughToBeSafe(unittest.TestCase):

    def test_same_prompt_same_frame_is_the_same_entry(self):
        a = P.h3_prompt_cache_path("a marble bust", None)
        b = P.h3_prompt_cache_path("a marble bust", None)
        self.assertEqual(a, b)
        self.assertTrue(str(a).endswith(".npz"))

    def test_different_prompt_is_a_different_entry(self):
        self.assertNotEqual(P.h3_prompt_cache_path("bust", None),
                            P.h3_prompt_cache_path("bust ", None))
        self.assertNotEqual(P.h3_prompt_cache_path("a", None),
                            P.h3_prompt_cache_path("b", None))

    def test_a_first_frame_changes_the_entry(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "ff.png"
            f.write_bytes(b"x" * 100)
            self.assertNotEqual(P.h3_prompt_cache_path("p", None),
                                P.h3_prompt_cache_path("p", f))

    def test_editing_the_first_frame_in_place_changes_the_entry(self):
        """The runner keys on the PATH alone, so an edited file would pass its
        own check while carrying different pixels. Ours must not."""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "ff.png"
            f.write_bytes(b"x" * 100)
            before = P.h3_prompt_cache_path("p", f)
            time.sleep(1.1)                     # mtime has 1 s resolution
            f.write_bytes(b"y" * 250)           # same path, different content
            after = P.h3_prompt_cache_path("p", f)
            self.assertNotEqual(before, after)

    def test_missing_first_frame_still_yields_a_key(self):
        """An unreadable frame must not take the render down."""
        got = P.h3_prompt_cache_path("p", Path("/nope/does/not/exist.png"))
        self.assertIsNotNone(got)

    def test_empty_prompt_disables_the_cache(self):
        """The chained shot-list path passes no positional prompt, and the
        runner disables the cache for every window after the first anyway."""
        self.assertIsNone(P.h3_prompt_cache_path("", None))
        self.assertIsNone(P.h3_prompt_cache_path("   ", None))
        self.assertIsNone(P.h3_prompt_cache_path(None, None))


class APoisonedEntryIsThrownAwayNotInherited(unittest.TestCase):
    """`np.savez_compressed` writes straight to the destination, so a render
    stopped mid-write leaves a truncated .npz — and the runner's loader would
    then raise on THAT prompt forever, with nothing telling the user why one
    prompt had quietly stopped working."""

    def test_a_truncated_entry_is_deleted(self):
        d = P.h3_prompt_cache_dir(); d.mkdir(parents=True, exist_ok=True)
        path = P.h3_prompt_cache_path("poison me", None)
        path.write_bytes(b"PK\x03\x04 truncated mid-write")   # torn zip
        again = P.h3_prompt_cache_path("poison me", None)
        self.assertEqual(again, path)
        self.assertFalse(path.exists(), "the torn entry survived")

    def test_a_valid_entry_is_left_alone(self):
        import zipfile as _z
        d = P.h3_prompt_cache_dir(); d.mkdir(parents=True, exist_ok=True)
        path = P.h3_prompt_cache_path("keep me", None)
        with _z.ZipFile(path, "w") as z:
            z.writestr("embeds.npy", b"\x00" * 32)
        self.assertTrue(_z.is_zipfile(path))
        again = P.h3_prompt_cache_path("keep me", None)
        self.assertEqual(again, path)
        self.assertTrue(path.exists(), "a good entry was deleted")

    def test_an_empty_file_counts_as_torn(self):
        d = P.h3_prompt_cache_dir(); d.mkdir(parents=True, exist_ok=True)
        path = P.h3_prompt_cache_path("zero bytes", None)
        path.write_bytes(b"")
        P.h3_prompt_cache_path("zero bytes", None)
        self.assertFalse(path.exists())


class ThePruneIsBounded(unittest.TestCase):

    def test_prune_keeps_the_newest_and_drops_the_rest(self):
        d = P.h3_prompt_cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.npz"):
            f.unlink()
        made = []
        for i in range(8):
            f = d / f"{i:032x}.npz"
            f.write_bytes(b"0")
            os.utime(f, (1000 + i, 1000 + i))
            made.append(f)
        dropped = P.h3_prune_prompt_cache(keep=3)
        left = sorted(x.name for x in d.glob("*.npz"))
        self.assertEqual(dropped, 5)
        self.assertEqual(len(left), 3)
        self.assertEqual(left, sorted(x.name for x in made[-3:]))

    def test_prune_on_an_absent_dir_is_a_no_op(self):
        import shutil
        shutil.rmtree(P.h3_prompt_cache_dir(), ignore_errors=True)
        self.assertEqual(P.h3_prune_prompt_cache(), 0)


class AnOlderRunnerMustNotBeHandedAFlagItCannotParse(unittest.TestCase):
    """The bug this closes: `--prompt-cache` was passed unconditionally.

    A pack cloned before the flag existed renders every tier correctly and
    would have died on an argparse error thirty seconds into a render. The
    panel already probes for --chain-prompts, --draft-decode and
    --live-preview for exactly this reason; the cache must use the same gate.
    """

    def test_the_probe_exists_and_asks_for_the_right_flag(self):
        self.assertTrue(hasattr(P, "h3_supports_prompt_cache"))
        import inspect
        src = inspect.getsource(P.h3_supports_prompt_cache)
        self.assertIn('_h3_runner_has_flag("--prompt-cache")', src)

    def test_the_argv_is_gated_on_the_probe(self):
        line = next(ln for ln in PANEL_SRC.splitlines()
                    if "_pc = h3_prompt_cache_path(" in ln)
        self.assertIn("h3_supports_prompt_cache()", line,
                      "the cache path is computed without probing the runner")

    def test_no_probe_means_no_flag(self):
        real = P.h3_supports_prompt_cache
        try:
            P.h3_supports_prompt_cache = lambda: False
            self.assertFalse(P.h3_supports_prompt_cache())
        finally:
            P.h3_supports_prompt_cache = real


class ItIsActuallyWiredIntoTheRender(unittest.TestCase):

    def test_the_argv_passes_prompt_cache(self):
        self.assertIn('cmd += ["--prompt-cache", str(_pc)]', PANEL_SRC,
                      "the H3 argv no longer passes --prompt-cache")

    def test_it_prunes_before_writing_not_after(self):
        i_prune = PANEL_SRC.index("h3_prune_prompt_cache()")
        i_pass = PANEL_SRC.index('cmd += ["--prompt-cache"')
        self.assertLess(i_prune, i_pass,
                        "prune must run before the entry this render writes")

    def test_draft_cache_dir_is_never_passed_to_the_runner(self):
        """`--draft-cache-dir` is refused by the runner outside --draft-decode
        tae, so passing it on a delivery render would hard-fail. Prose may name
        it (the comment explains why we chose the other one); an argv line may
        not."""
        offenders = [ln.strip() for ln in PANEL_SRC.splitlines()
                     if "--draft-cache-dir" in ln
                     and not ln.lstrip().startswith("#")]
        self.assertEqual(offenders, [],
                         "--draft-cache-dir reached the runner argv")


class APoisonedEntryIsAMiss(unittest.TestCase):
    """Before this fix the shot-list path keyed the cache on the run's prompt while
    the runner wrote window 1's prompt into the entry, and every later render of
    that prompt raised "The prompt cache belongs to a different prompt" (fleet
    4.12.3). An entry holding another prompt is thrown away, not served."""

    def test_entry_for_another_prompt_is_removed(self):
        import numpy as np
        path = P.h3_prompt_cache_path("the run prompt", None)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, prompt=np.array("window one prompt"),
                            embeds=np.zeros((1, 2), np.float32))
        self.assertEqual(P.h3_prompt_cache_path("the run prompt", None), path)
        self.assertFalse(path.exists(), "a mismatched entry was left for the runner to raise on")

    def test_entry_for_the_same_prompt_is_kept(self):
        import numpy as np
        path = P.h3_prompt_cache_path("same prompt kept", None)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, prompt=np.array("same prompt kept"),
                            embeds=np.zeros((1, 2), np.float32))
        P.h3_prompt_cache_path("same prompt kept", None)
        self.assertTrue(path.exists())

    def test_shot_list_keys_on_the_prompt_the_runner_encodes(self):
        self.assertIn("_pc_prompt = (chain_prompts[0]", PANEL_SRC)
        self.assertIn("h3_prompt_cache_path(_pc_prompt, first_frame)", PANEL_SRC)


if __name__ == "__main__":
    unittest.main()
