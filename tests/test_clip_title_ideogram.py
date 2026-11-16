"""clip_title.display_title on an Ideogram 4 Layout-mode prompt.

Layout mode sends its structured caption as the prompt, a JSON string
(webapp/js/stage.js JSON.stringify(caption); ideogram_caption.build_caption is
the Python port). The server's title must read like the webapp's own, which
shows high_level_description (queue.js _parseIdeoCaption / _displayPromptFor).

Run from the repo root:
    python3 -B -m unittest discover -s tests -p 'test_clip_title_ideogram.py' -v

Offline, stdlib only. The JS comparison runs queue.js's own functions under
`node` and is skipped when node is not installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import clip_title  # noqa: E402
import ideogram_caption  # noqa: E402

title = clip_title.display_title


def _js(obj) -> str:
    """What JSON.stringify(obj) posts: compact, non-ASCII kept as is."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _caption(**spec) -> dict:
    return ideogram_caption.build_caption(spec)


def _box(text, x=.1, y=.1):
    return {"type": "text", "text": text, "x": x, "y": y, "w": .5, "h": .2}


CAPTIONS = [
    _caption(render="design", scene="A clean white poster.", boxes=[_box("SUMMER SALE")]),
    _caption(render="photo", scene="A cafe window at night.", boxes=[_box("OPEN")]),
    _caption(render="design", scene="A plain background.", boxes=[]),
    _caption(render="design", scene="A poster.",
             boxes=[_box("여름 세일"), _box("최대 50% 할인", y=.4)]),
    _caption(render="design", scene="ポスター", boxes=[_box("夏のセール")]),
    _caption(render="design", scene="海报", boxes=[_box("夏季大促销")]),
    _caption(render="design", scene="A birthday card.", boxes=[_box("Happy Birthday 🎉")]),
    _caption(render="design", scene="A busy flyer.",
             boxes=[_box(t, y=i / 10) for i, t in enumerate(
                 ["GRAND OPENING", "Friday", "Live music", "Free entry", "Downtown"])]),
]


class IdeogramCaptionTitle(unittest.TestCase):
    def test_reported_prompt(self):
        prompt = _js(CAPTIONS[0])
        self.assertTrue(prompt.startswith('{"high_level_description":'))
        self.assertEqual(title(prompt), 'A graphic design featuring text reading "SUMMER SALE"')

    def test_every_caption_titles_from_its_description(self):
        for cap in CAPTIONS:
            hld = cap["high_level_description"]
            for prompt in (_js(cap), json.dumps(cap), json.dumps(cap, indent=2),
                           "  \n" + _js(cap) + "\n"):
                with self.subTest(prompt=prompt[:60]):
                    got = title(prompt)
                    self.assertNotIn("{", got)
                    self.assertNotIn("high_level_description", got)
                    self.assertEqual(got, title(hld))

    def test_korean_text_box(self):
        self.assertEqual(title(_js(CAPTIONS[3])),
                         'A graphic design featuring text reading "여름 세일", "최대 50% 할인"')

    def test_long_description_is_trimmed_like_any_title(self):
        got = title(_js(CAPTIONS[-1]))
        self.assertLessEqual(len(got), clip_title.TITLE_MAX + 1)
        self.assertTrue(got.startswith("A graphic design featuring text reading"))
        self.assertTrue(got.endswith("…"))

    def test_label_still_wins(self):
        self.assertEqual(title(_js(CAPTIONS[0]), "Sale poster v2"), "Sale poster v2")

    def test_empty_description_is_empty_like_the_webapp(self):
        # _displayPromptFor returns "" and displayTitle("") is "".
        self.assertEqual(title('{"high_level_description":""}'), "")


class NotACaption(unittest.TestCase):
    """Only a JSON object with a string high_level_description is a caption."""

    def test_left_as_text(self):
        cases = [
            ("{style} a cat on a windowsill at dawn.", "{style} a cat on a windowsill at dawn"),
            ('{"prompt": "A cat on a windowsill at dawn."}',
             '{"prompt": "A cat on a windowsill at dawn."}'),
            ('{"high_level_description": 42}', '{"high_level_description": 42}'),
            ('{"high_level_description": "A poster."',            # truncated JSON
             '{"high_level_description": "A poster."'),
            ('["high_level_description", "A poster."]', '["high_level_description", "A poster."]'),
            # The H3 text field label is scaffolding, handled as before.
            ("high_level_description: A neon sign flickers. More text.", "A neon sign flickers"),
        ]
        for prompt, want in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(title(prompt), want)

    def test_deeply_nested_json_does_not_raise(self):
        prompt = '{"a":' + "[" * 100000
        self.assertTrue(title(prompt).startswith('{"a":[[['))

    def test_none_and_blank(self):
        self.assertEqual(title(None), "")
        self.assertEqual(title(""), "")
        self.assertEqual(title("   "), "")


# Plain prompts: every title is what origin/main gave.
UNCHANGED = [
    ("A red fox runs through the snow. The camera follows.", "A red fox runs through the snow"),
    ("Cinematic, warm film look, a girl reads a book by the window. Soft light.",
     "A girl reads a book by the window"),
    ("vh5tape, worn VHS tape recording of a birthday party in a living room.",
     "Worn VHS tape recording of a birthday party in a living room"),
    ("[Shot 1] At 0:00, a man opens the door. (S1) <d>Hi</d>", "A man opens the door"),
    ("<d>Hello there, how are you doing today my friend.</d>",
     "Hello there, how are you doing today my friend"),
    ("integrated_multimodal_description: A chef plates a dish. overall_soundscape: sizzling",
     "A chef plates a dish"),
    ("A sign that reads {OPEN} hangs in a cafe window.",
     "A sign that reads {OPEN} hangs in a cafe window"),
    ("한 여자가 바닷가를 걷는다. 파도가 밀려온다.", "한 여자가 바닷가를 걷는다"),
    ("비 오는 서울의 골목, 네온 간판이 깜빡인다. 우산을 든 남자가 천천히 걸어간다.",
     "비 오는 서울의 골목, 네온 간판이 깜빡인다"),
    ("🎉🎉 A birthday party in a small apartment! Balloons float to the ceiling.",
     "🎉🎉 A birthday party in a small apartment"),
    ("A" * 100, "A" * 60 + "…"),
]


class PlainPromptsUnchanged(unittest.TestCase):
    def test_unchanged(self):
        for prompt, want in UNCHANGED:
            with self.subTest(prompt=prompt[:50]):
                self.assertEqual(title(prompt), want)


def _queue_js_functions(*names: str) -> str:
    """The named top-level functions, cut from queue.js's text."""
    src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
    out = []
    for name in names:
        start = src.index(f"\nfunction {name}(") + 1
        end = src.index("\n}\n", start) + 3
        out.append(src[start:end])
    return "\n".join(out)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class AgreesWithWebapp(unittest.TestCase):
    def test_same_title_as_the_job_row(self):
        # queue.js renders a row as displayTitle(_displayPromptFor(prompt)) when
        # the server sends no title; the server's title must say the same.
        prompts = [p for cap in CAPTIONS for p in (_js(cap), json.dumps(cap, indent=2))]
        prompts += [p for p, _ in UNCHANGED] + [
            '{"high_level_description":""}', "{style} a cat on a windowsill at dawn."]
        script = _queue_js_functions("_parseIdeoCaption", "_displayPromptFor", "displayTitle") + """
const ps = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(ps.map(p => displayTitle(_displayPromptFor(p)))));
"""
        res = subprocess.run(["node", "-e", script], input=json.dumps(prompts),
                             capture_output=True, text=True, timeout=60, check=True)
        for prompt, js in zip(prompts, json.loads(res.stdout)):
            with self.subTest(prompt=prompt[:60]):
                self.assertEqual(title(prompt), js)


if __name__ == "__main__":
    unittest.main()
