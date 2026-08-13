#!/usr/bin/env python3
"""The Character round-trip gate.

THE TEST THE EXTERNAL REVIEW ASKED FOR, and the one the existing gates could not
have caught: generate a job payload, serialize its sidecar, run Load Params over
that sidecar, and assert the NEXT payload is equivalent.

Every gate this repo had was structural — dispatch length, script ordering, pin
text, schema. None of them could see that the Characters surface and the Manual
surface produced different jobs for the same character, that Load Params
recognised a canvas size the server had stopped emitting, or that the voice
strength was never restored. Those are round-trip properties, so this is a
round-trip test.

What it pins, in the reviewer's words: "generate a job payload, serialize its
sidecar, Load Params, and assert the next payload is equivalent."

The client half is JavaScript inside mlx_ltx_panel.py's HTML string, so the
relevant client rules are extracted from that source and checked directly —
that is deliberate: reimplementing them here would test this file, not the
panel.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PANEL_SRC = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")

import mlx_ltx_panel as P  # noqa: E402


# ---------------------------------------------------------------------------
# The client rules, read out of the panel source rather than restated
# ---------------------------------------------------------------------------

def _client_default(field):
    """`charStrength: 1.0` / `voiceStrength: 1.0` from window.CHARACTERS."""
    m = re.search(r"^\s*%s:\s*([0-9.]+)\s*," % re.escape(field), PANEL_SRC, re.M)
    return float(m.group(1)) if m else None


def _client_submits(name):
    """Does charactersGenerate() actually put this field on the form?"""
    return re.search(r"fd\.set\(\s*'%s'" % re.escape(name), PANEL_SRC) is not None


def _client_draft_pairs():
    """The (w,h) pairs Load Params maps back to the Draft chip."""
    m = re.search(r"_draftPairs\s*=\s*\[(.*?)\]\s*;", PANEL_SRC, re.S)
    if not m:
        return []
    return [(int(a), int(b)) for a, b in re.findall(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]", m.group(1))]


def _client_restores(param, input_id):
    return re.search(r"_restoreStrength\(\s*p\.%s\s*,\s*'%s'\s*\)"
                     % (re.escape(param), re.escape(input_id)), PANEL_SRC) is not None


class TestCharacterDefaultsConverge(unittest.TestCase):
    """One character, one pair of strengths, whichever surface launched it."""

    def test_ui_and_server_agree_on_face(self):
        self.assertEqual(_client_default("charStrength"), 1.0,
                         "the Characters surface must initialise face at the server's default")

    def test_ui_and_server_agree_on_voice(self):
        self.assertEqual(_client_default("voiceStrength"), 1.0)

    def test_both_strengths_are_submitted(self):
        # Submitting only the face left the voice to the server default, so the
        # surface could not express the pair it was displaying.
        self.assertTrue(_client_submits("character_strength"))
        self.assertTrue(_client_submits("character_voice_strength"),
                        "the voice strength was never submitted, so it could never round-trip")

    def test_server_defaults_are_the_same_pair(self):
        face = re.search(r'form\.get\("character_strength",\s*\["([0-9.]+)"\]', PANEL_SRC)
        voice = re.search(r'form\.get\("character_voice_strength",\s*\["([0-9.]+)"\]', PANEL_SRC)
        self.assertIsNotNone(face)
        self.assertIsNotNone(voice)
        self.assertEqual(float(face.group(1)), _client_default("charStrength"))
        self.assertEqual(float(voice.group(1)), _client_default("voiceStrength"))


class TestDraftCanvasRoundTrips(unittest.TestCase):
    """A Draft render must reopen as Draft."""

    def test_the_size_the_server_emits_is_recognised(self):
        server_draft = P._CHARACTER_QUALITY_RESOLUTION["draft"]
        pairs = _client_draft_pairs()
        self.assertIn(server_draft, pairs,
                      "Load Params does not recognise the Draft canvas the server emits "
                      "(%r), so every Draft render reopens as Pro" % (server_draft,))

    def test_the_legacy_size_still_round_trips(self):
        # Sidecars written before the canvas moved are still on disk.
        self.assertIn((736, 416), _client_draft_pairs())

    def test_pro_is_not_mistaken_for_draft(self):
        self.assertNotIn(P._CHARACTER_QUALITY_RESOLUTION["high"], _client_draft_pairs())


class TestSidecarRoundTrip(unittest.TestCase):
    """payload -> sidecar -> Load Params -> payload, and the two must match."""

    #: the fields Load Params is responsible for carrying back
    CARRIED = ("character_id", "width", "height", "frames",
               "character_strength", "character_voice_strength")

    def _payload(self, quality="draft", face=1.0, voice=1.0):
        w, h = P._CHARACTER_QUALITY_RESOLUTION[quality]
        return {
            "character_id": "bizarrotrn",
            "mode": "t2v",
            "width": w, "height": h, "frames": 121,
            "quality": P.character_render_quality(),
            "character_strength": face,
            "character_voice_strength": voice,
            "source": "characters",
        }

    def _load_params(self, sidecar):
        """The client's restoration, applied to a sidecar. Mirrors the rules the
        panel source declares — each one asserted against that source above."""
        out = dict(sidecar)
        pairs = _client_draft_pairs()
        w, h = int(sidecar.get("width") or 0), int(sidecar.get("height") or 0)
        is_draft = any((w, h) == p or (h, w) == p for p in pairs)
        quality = "draft" if is_draft else "high"
        out["width"], out["height"] = P._CHARACTER_QUALITY_RESOLUTION[quality]
        for param, input_id in (("character_strength", "characterStrength"),
                                ("character_voice_strength", "characterVoiceStrength")):
            if not _client_restores(param, input_id):
                out.pop(param, None)          # not restored -> falls back to default
        return out

    def _assert_round_trip(self, first):
        sidecar = json.loads(json.dumps({"params": first}))["params"]
        second = self._load_params(sidecar)
        for k in self.CARRIED:
            self.assertEqual(first.get(k), second.get(k),
                             "%s did not survive the round trip: %r -> %r"
                             % (k, first.get(k), second.get(k)))

    def test_draft_default_strengths(self):
        self._assert_round_trip(self._payload("draft"))

    def test_pro_default_strengths(self):
        self._assert_round_trip(self._payload("high"))

    def test_split_strengths_survive(self):
        # The case the review named: a clip rendered with a deliberately hotter
        # voice reopened with the voice back at its default.
        self._assert_round_trip(self._payload("high", face=0.9, voice=1.4))

    def test_draft_with_split_strengths(self):
        self._assert_round_trip(self._payload("draft", face=1.15, voice=0.75))


class TestNoUnreachableLoader(unittest.TestCase):
    def test_the_dead_characters_loader_is_gone(self):
        # It held newer-looking quality-mapping logic than the live path, so a
        # reader had two plausible implementations and only one that ran.
        self.assertNotIn("eslint-disable-next-line no-unreachable", PANEL_SRC)
        body = re.search(r"async function charactersLoadParams\(p\)\s*\{(.*?)\n\}",
                         PANEL_SRC, re.S)
        self.assertIsNotNone(body)
        self.assertNotIn("charactersOpenCompose(p.character_id)", body.group(1),
                         "the unreachable Characters-tab block is still in the loader")


if __name__ == "__main__":
    unittest.main(verbosity=2)
