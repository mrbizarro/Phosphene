"""scene_sound.sound_prompt: which sentences of the scene reach the ambience bed.

Run from the repo root:
    python3 -B -m unittest discover -s tests -p 'test_scene_sound.py' -v

Offline, stdlib only. mlx_ltx_panel.py is read as text, never imported.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scene_sound  # noqa: E402
import storyboard  # noqa: E402
import storyboard_planner  # noqa: E402

_HEAD = "from the surroundings. "
_TAIL = " Soundtrack: only the environment"


def context(scene: str, override: str = "") -> str:
    """The part of the bed prompt that came from the scene."""
    p = scene_sound.sound_prompt(scene, override)
    return p[p.index(_HEAD) + len(_HEAD):p.index(_TAIL)].strip()


def _h3_contract() -> str:
    src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
    m = re.search(r'H3_A2V_CONTRACT = \("([^"]*)"\s*"([^"]*)"\)', src)
    assert m, "H3_A2V_CONTRACT moved; update this test"
    return m.group(1) + m.group(2)


# Every sentence the panel itself writes to direct a lip-sync mouth. The bed
# must never hear them.
PANEL_DIRECTIONS = [
    storyboard.A2V_SYNC_CONTRACT,
    storyboard.A2V_SILENT_CONTRACT,
    storyboard_planner.A2V_CAMERA_SENTENCE,
    storyboard_planner._JAW_STOP.strip(),
    _h3_contract(),
    # Phrasings docs/H3_ENGINE.md, docs/PROMPTING.md and docs/LTX_DIALOGUE.md
    # tell people to write.
    "Exactly as his voice stops, his jaw ceases speaking motion and his mouth settles closed.",
    "A voiceover plays while his lips remain completely closed.",
    "Then he is silent, his mouth settles closed, and only the wind is heard.",
    "Lip sync.",
    "The tall one listens with its mouth closed.",
]

# Scene sentences that must reach the bed. Each one was deleted before the
# fix because a lip-sync word sat inside an ordinary word or named a place.
SCENERY = [
    "A field of tulips sways at dawn, wind in the grass, distant birds.",
    "Harbour at dusk, gulls and creaking ropes, a ship slips past the pier.",
    "A fisherman at the river mouth, gulls overhead, waves.",
    "Video clips flicker on a wall of monitors in a busy newsroom, phones ringing.",
    "A total solar eclipse darkens the desert, insects fall silent.",
    "Wind howls at the mouth of the cave and water drips from the rock.",
    "Rain hammers the tin roof as a skateboarder flips off the kerb.",
    "The trumpet's mouthpiece glints on a cafe table, espresso machine hissing.",
    "A woman in red lipstick waits on the platform as a train pulls in.",
]


class PanelDirectionsStillRemoved(unittest.TestCase):
    def test_each_direction_alone(self):
        for s in PANEL_DIRECTIONS:
            with self.subTest(s=s):
                self.assertEqual(context(s), "")

    def test_direction_after_scenery(self):
        for s in PANEL_DIRECTIONS:
            with self.subTest(s=s):
                got = context("Rain on a tin roof, a dog barks far away. " + s)
                self.assertEqual(got, "Rain on a tin roof, a dog barks far away.")

    def test_storyboard_prompt_keeps_scene_drops_contract(self):
        # What a Storyboard / music-video a2v shot carries in params.prompt.
        for silent in (False, True):
            p = storyboard.a2v_prompt(
                "A woman in a yellow raincoat in a field of tulips at dawn, wind in the grass.",
                silent=silent)
            with self.subTest(silent=silent):
                self.assertEqual(
                    context(p),
                    "A woman in a yellow raincoat in a field of tulips at dawn, wind in the grass.")

    def test_inflections_the_substring_used_to_catch(self):
        # Before the fix these went as substrings ("mouthing" holds "mouth",
        # "lipsyncs" holds "lips"); whole-word matching must not let them in.
        for s in ["She is mouthing the words along with the song.",
                  "He mouthed the lyric without a sound.",
                  "The singer lipsyncs the chorus to the crowd.",
                  "She keeps lip-syncing through the bridge.",
                  "Two mouths in frame move at once.",
                  "LIP-SYNC every syllable exactly.",
                  "Clear consonants on every word.",
                  "Matching the supplied voice exactly.",
                  "Mouth-to-mouth breaths, close on the lips."]:
            with self.subTest(s=s):
                self.assertEqual(context("Wind in the pines. " + s), "Wind in the pines.")

    def test_place_mouth_does_not_rescue_a_real_direction(self):
        for s in ["At the river mouth her mouth moves with every word.",
                  "By the cave mouth, lip-sync every syllable.",
                  "The mouth of the singer opens wide on the high note.",
                  "Gulls wheel over the harbour mouth while his lips shape each word."]:
            with self.subTest(s=s):
                self.assertEqual(context("Wind in the pines. " + s), "Wind in the pines.")


class ScenerySentencesKept(unittest.TestCase):
    def test_reported_scenes(self):
        # The cases from the report: the bed used to get only "She"/"He".
        self.assertEqual(
            context("A woman in a yellow raincoat stands in a field of tulips at dawn, wind "
                    "in the grass, distant birds. She speaks to the camera."),
            "A woman in a yellow raincoat stands in a field of tulips at dawn, wind in the "
            "grass, distant birds. She")
        self.assertEqual(
            context("Harbour at dusk, gulls and creaking ropes, a ship slips past the pier. "
                    "He says goodbye."),
            "Harbour at dusk, gulls and creaking ropes, a ship slips past the pier. He")
        self.assertEqual(
            context("A fisherman at the river mouth, gulls overhead, waves. He sings softly."),
            "A fisherman at the river mouth, gulls overhead, waves. He")
        self.assertEqual(
            context("A video clips montage in a busy newsroom, phones ringing."),
            "A video clips montage in a busy newsroom, phones ringing.")

    def test_each_scenery_sentence(self):
        for s in SCENERY:
            with self.subTest(s=s):
                self.assertEqual(context(s), s)

    def test_scenery_next_to_a_direction(self):
        for s in SCENERY:
            with self.subTest(s=s):
                self.assertEqual(context(s + " " + storyboard.A2V_SYNC_CONTRACT), s)

    def test_override_prompt_filtered_the_same_way(self):
        self.assertEqual(context("ignored", override=SCENERY[0]), SCENERY[0])
        self.assertEqual(context("ignored", override=storyboard.A2V_SYNC_CONTRACT), "")


# Prompts with none of the reported words: the bed prompt must be exactly what
# it was before the fix. Expected values were produced by origin/main.
UNCHANGED = [
    ("", ""),
    ("   ", ""),
    ("A quiet kitchen at night, the fridge hums.", "A quiet kitchen at night, the fridge hums."),
    ("Rain on a window. She says \"I'm leaving\" and turns away. Thunder rolls.",
     "Rain on a window. She Thunder rolls."),
    ("A busy market at noon, vendors shouting, scooters passing. He speaks to the camera.",
     "A busy market at noon, vendors shouting, scooters passing. He"),
    ("Live-action, cinematic, a medium close-up of a man in a dark curly fur hat and heavy "
     "fur coat on an open dune ridge. He faces the camera squarely as the wind lifts the fur "
     "at his collar. The man, with a warm, measured, slightly gravelled voice (S1), says: "
     "<d>[English] They said this was impossible.</d> Exactly as his voice stops, his jaw "
     "ceases speaking motion and his mouth settles into a closed steady half-smile.",
     "Live-action, cinematic, a medium close-up of a man in a dark curly fur hat and heavy "
     "fur coat on an open dune ridge. He faces the camera squarely as the wind lifts the fur "
     "at his collar."),
    ("A woman sings softly in an empty church, candles flicker.", "A woman"),
    # Non-ASCII: the filter is English-only; these pass through untouched.
    ("비 오는 서울의 골목, 네온 간판이 깜빡인다. 그녀가 카메라를 보며 이야기한다.",
     "비 오는 서울의 골목, 네온 간판이 깜빡인다. 그녀가 카메라를 보며 이야기한다."),
    ("雨の東京の路地、ネオンが瞬く。彼女はカメラに向かって話す。",
     "雨の東京の路地、ネオンが瞬く。彼女はカメラに向かって話す。"),
    ("雨夜的上海街头，霓虹灯闪烁。她对着镜头说话。", "雨夜的上海街头，霓虹灯闪烁。她对着镜头说话。"),
    ("해변의 파도 소리, 갈매기 🐦 우는 소리. " + storyboard.A2V_SYNC_CONTRACT,
     "해변의 파도 소리, 갈매기 🐦 우는 소리."),
]


class UnchangedPrompts(unittest.TestCase):
    def test_unchanged(self):
        for scene, want in UNCHANGED:
            with self.subTest(scene=scene[:50]):
                self.assertEqual(context(scene), want)

    def test_length_cap(self):
        long = "Waves roll onto a pebble beach under a grey sky. " * 60
        self.assertEqual(len(context(long)), 1800)
        self.assertEqual(len(context("tulips " * 400)), 1800)


if __name__ == "__main__":
    unittest.main()
