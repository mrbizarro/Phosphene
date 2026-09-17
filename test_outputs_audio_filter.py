"""Outputs and Recent get an Audio chip beside Videos / Photos (asked for on the
4.14 Pinokio post by @cocktailpeanut, 2026-09-17)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "webapp/index.html").read_text()
BOOT = (ROOT / "webapp/js/boot.js").read_text()
QJS = (ROOT / "webapp/js/queue.js").read_text()
CJS = (ROOT / "webapp/js/characters.js").read_text()


class AudioFilter(unittest.TestCase):
    def test_chips_exist(self):
        self.assertIn('id="mainOutputsFilterAudio"', HTML)
        self.assertIn("setMainOutputsFilter('audio')", HTML)
        self.assertIn('id="recentFilterAudio"', HTML)
        self.assertIn("setRecentFilter('audio')", HTML)

    def test_gallery_filters_by_kind_audio(self):
        self.assertIn("mainOutputsFilter === 'audio') all = all.filter(o => outputKind(o) === 'audio')", BOOT)
        self.assertIn("stored === 'audio'", BOOT)
        self.assertIn("mode !== 'audio'", BOOT)

    def test_history_filter_and_empty_state(self):
        self.assertIn("if (filterPhotos === 'audio') return isSong;", QJS)
        self.assertIn("No songs yet", QJS)
        self.assertIn("No audio outputs yet", QJS)

    def test_compose_lands_on_audio(self):
        self.assertNotIn("setMainOutputsFilter('all')", CJS)
        self.assertEqual(CJS.count("setMainOutputsFilter('audio')"), 3)


if __name__ == "__main__":
    unittest.main()
