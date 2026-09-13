"""A settings write may only change the keys it is setting (2026-09-08).

Two panels shared one state dir on the dev box. A panel that had been running
since Sep 5 wrote its own bookkeeping (analytics_last_version and friends) by
dumping its whole in-memory dict, and the PostHog query key another panel had
saved into the file in the meantime was gone. Every writer now merges its
delta onto the file as it is on disk, and a reader re-loads the file when
another process changed it."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ["LTX_STATE_DIR"] = str(Path(tempfile.mkdtemp(prefix="phos-settings-")))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"

import mlx_ltx_panel as P  # noqa: E402


def _other_process_writes(**kv) -> None:
    """Edit the file the way a second panel (or the owner) would."""
    data = json.loads(P.SETTINGS_FILE.read_text())
    data.update(kv)
    time.sleep(0.01)                     # a distinct mtime on coarse filesystems
    P.SETTINGS_FILE.write_text(json.dumps(data, indent=1))


class AWriteKeepsWhatOthersSaved(unittest.TestCase):
    def test_internal_bookkeeping_does_not_blank_a_key_saved_elsewhere(self):
        P.get_settings()                                   # this process has its copy
        _other_process_writes(analytics_query_key="phx_secret_from_the_other_panel")
        P._settings_set_internal(analytics_last_version="9.9.9")
        on_disk = json.loads(P.SETTINGS_FILE.read_text())
        self.assertEqual(on_disk["analytics_query_key"], "phx_secret_from_the_other_panel")
        self.assertEqual(on_disk["analytics_last_version"], "9.9.9")

    def test_a_form_save_does_not_blank_a_key_saved_elsewhere(self):
        P.get_settings()
        _other_process_writes(hf_token="hf_from_the_other_panel")
        cur, err = P.update_settings({"output_preset": "web"})
        self.assertIsNone(err)
        on_disk = json.loads(P.SETTINGS_FILE.read_text())
        self.assertEqual(on_disk["hf_token"], "hf_from_the_other_panel")
        self.assertEqual(on_disk["output_preset"], "web")
        self.assertEqual(cur["hf_token"], "hf_from_the_other_panel")

    def test_a_reader_sees_what_another_process_wrote(self):
        P.get_settings()
        _other_process_writes(civitai_api_key="civ_from_the_other_panel")
        self.assertEqual(P.get_settings()["civitai_api_key"], "civ_from_the_other_panel")

    def test_an_explicit_clear_still_clears(self):
        P.update_settings({"hf_token": "abc"})
        cur, err = P.update_settings({"hf_token": ""})
        self.assertIsNone(err)
        self.assertEqual(json.loads(P.SETTINGS_FILE.read_text())["hf_token"], "")


if __name__ == "__main__":
    unittest.main()
