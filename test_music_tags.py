"""Section tags come back bare, whatever Gemma wrote."""
import unittest

from music_tags import canonical_tag, normalize_section_tags


class BareTags(unittest.TestCase):
    def test_numbers_are_dropped(self):
        self.assertEqual(normalize_section_tags("[Verse 1]\nla\n[Chorus 2]\nlo"), "[Verse]\nla\n[Chorus]\nlo")

    def test_aliases_fold_into_the_closed_list(self):
        self.assertEqual(normalize_section_tags("[Hook]\n[Solo]\n[Pre Chorus 3]\n[Refrain]"),
                         "[Chorus]\n[Instrumental]\n[Pre-Chorus]\n[Chorus]")

    def test_lines_are_never_touched(self):
        text = "[Verse]\n[bracketed words] inside a line stay\nso does [this 1]"
        self.assertEqual(normalize_section_tags(text), text)

    def test_unknown_tags_are_left_for_a_human(self):
        self.assertEqual(normalize_section_tags("[Spoken Word 1]"), "[Spoken Word 1]")
        self.assertIsNone(canonical_tag("Spoken Word"))

    def test_whitespace_and_case(self):
        self.assertEqual(normalize_section_tags("  [ verse 12 ]  "), "[Verse]")


if __name__ == "__main__":
    unittest.main()
