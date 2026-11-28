"""clip_title.display_title: the first sentence of Chinese, Japanese and Korean
prompts.

Run from the repo root:
    python3 -B -m unittest discover -s tests -p 'test_clip_title_cjk.py' -v

Offline, stdlib only. The webapp keeps a mirror of display_title() in
webapp/js/queue.js (displayTitle); the parity test runs that function under
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

title = clip_title.display_title


class ChineseJapaneseFirstSentence(unittest.TestCase):
    def test_reported(self):
        self.assertEqual(title("一个女人在海边行走。海浪涌来，她回头微笑。远处的灯塔亮起，天色渐渐暗下来，"
                               "海鸥掠过水面，整个画面安静而温柔。"), "一个女人在海边行走")
        self.assertEqual(title("女性が浜辺を歩いている。波が打ち寄せる。遠くの灯台に明かりがともり、"
                               "空が少しずつ暗くなっていく様子をゆっくり映す。"), "女性が浜辺を歩いている")

    def test_cases(self):
        cases = [
            ("雨の東京の路地、ネオンが瞬く。傘をさした男がゆっくり歩く。", "雨の東京の路地、ネオンが瞬く"),
            ("雨夜的上海街头，霓虹灯闪烁！一个男人撑着伞慢慢走过。", "雨夜的上海街头，霓虹灯闪烁"),
            ("一只猫。", "一只猫"),
            ("猫が寝ている。", "猫が寝ている"),
            ("ＡＩロボットが踊る。", "ＡＩロボットが踊る"),
            # A bare one-word lead-in is skipped, like "Wow!" or "Soft light."
            ("本当に？少女は驚いて目を見開く。", "少女は驚いて目を見開く"),
            ("本当に？！少女は驚いて目を見開いた。", "少女は驚いて目を見開いた"),
            ("风。一个女人在雨中奔跑，雨水打湿了她的头发。", "一个女人在雨中奔跑，雨水打湿了她的头发"),
            # A quoted 。 is not the end of the sentence (as `."` is not in English).
            ("彼は振り返って「また会おう。」と言った。そして駅へ向かった。", "彼は振り返って「また会おう。」と言った"),
            ("女の子が「おはよう！」と元気に挨拶する。朝日が差し込む。", "女の子が「おはよう！」と元気に挨拶する"),
            ("他说：“我们走吧。”然后转身离开了房间。", "他说：“我们走吧。”然后转身离开了房间"),
            # Mixed scripts.
            ("A man walks in。He sits down by the fire.", "A man walks in"),
            ("東京タワーの前で、a man in a suit checks his watch. 電車が通り過ぎる。",
             "東京タワーの前で、a man in a suit checks his watch"),
        ]
        for prompt, want in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(title(prompt), want)

    def test_long_sentence_trimmed_at_the_cap(self):
        prompt = "一个" + "很长" * 40 + "的句子。第二句。"
        self.assertEqual(title(prompt), ("一个" + "很长" * 40)[:clip_title.TITLE_MAX] + "…")


class KoreanFirstSentence(unittest.TestCase):
    """Korean ends sentences with ASCII punctuation, so it was always split;
    what changes is the short-sentence skip, which now counts a Hangul
    syllable as two characters."""

    def test_short_complete_sentence_is_kept(self):
        cases = [
            ("여자가 웃는다. 카메라가 천천히 다가간다.", "여자가 웃는다"),          # 다.
            ("고양이가 잔다. 창밖에 비가 내리고 천둥이 친다.", "고양이가 잔다"),
            ("한 남자가 걷는다. 그 뒤로 해가 진다.", "한 남자가 걷는다"),
            ("남자가 걷는다. 그 뒤로 해가 진다.", "남자가 걷는다"),
            ("오늘은 맑아요. 공원에 사람들이 가득하다.", "오늘은 맑아요"),          # 요.
            ("그는 왜 떠났을까? 기차역 플랫폼에 홀로 선 여자가 멀어지는 기차를 바라본다.",
             "그는 왜 떠났을까"),                                               # 까?
        ]
        for prompt, want in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(title(prompt), want)

    def test_short_lead_in_is_still_skipped(self):
        cases = [
            ("부드러운 빛. 여자가 창가에서 책을 읽는다.", "여자가 창가에서 책을 읽는다"),  # "Soft light."
            ("와! 거대한 고래가 작은 배 옆으로 뛰어오른다.", "거대한 고래가 작은 배 옆으로 뛰어오른다"),
            ("밤. 도시의 불빛이 하나둘 켜진다.", "도시의 불빛이 하나둘 켜진다"),
            ("실사. 시네마틱. 바닷가 절벽 위에 등대가 서 있다.", "바닷가 절벽 위에 등대가 서 있다"),
            ("슬로 모션. 물방울이 수면에 떨어진다.", "물방울이 수면에 떨어진다"),
            ("35mm 필름. 노인이 공원 벤치에 앉아 비둘기에게 모이를 준다.",
             "노인이 공원 벤치에 앉아 비둘기에게 모이를 준다"),
            ("그렇죠! 남자가 손뼉을 치며 일어선다.", "남자가 손뼉을 치며 일어선다"),  # 죠!
            ("맛있죠! 요리사가 접시를 내민다.", "요리사가 접시를 내민다"),
        ]
        for prompt, want in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(title(prompt), want)

    def test_boundary_at_twelve(self):
        self.assertEqual(clip_title._width("고양이 잔다"), 11)
        self.assertEqual(title("고양이 잔다. 창밖에 비가 내리고 천둥이 친다."), "창밖에 비가 내리고 천둥이 친다")
        self.assertEqual(clip_title._width("고양이가잔다"), 12)
        self.assertEqual(title("고양이가잔다. 창밖에 비가 내리고 천둥이 친다."), "고양이가잔다")
        self.assertEqual(clip_title._width("여자가 웃는다"), 13)

    def test_jamo_count_once(self):
        # Compatibility Jamo are single letters (ㅋㅋㅋ), not syllables.
        self.assertEqual(clip_title._width("ㅋㅋㅋ"), 3)
        self.assertEqual(clip_title._width("ㅋㅋ 웃김"), 7)

    def test_mixed_korean_english(self):
        cases = [
            ("A woman in Seoul 서울 walks past a 편의점 at night. Rain falls.",
             "A woman in Seoul 서울 walks past a 편의점 at night"),
            ("카페 앞. A barista steams milk behind the counter.", "A barista steams milk behind the counter"),
            ("Neon 간판 아래. 오토바이가 빠르게 지나간다.", "Neon 간판 아래"),
        ]
        for prompt, want in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(title(prompt), want)

    def test_quoted_speech(self):
        cases = [
            # ." is not a sentence end, so the quote stays inside its sentence.
            ("그녀가 웃으며 \"좋아.\" 하고 대답한다. 바람이 분다.", "그녀가 웃으며 \"좋아.\" 하고 대답한다"),
            ("카메라가 천천히 다가가자 할머니가 웃으며 말한다. \"밥은 먹었니?\" 부엌에서 김이 피어오른다.",
             "카메라가 천천히 다가가자 할머니가 웃으며 말한다"),
            # Was titled by the quote that followed it.
            ("할머니가 말한다. \"밥은 먹었니?\" 손녀가 고개를 끄덕인다.", "할머니가 말한다"),
        ]
        for prompt, want in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(title(prompt), want)

    def test_dialogue_line(self):
        self.assertEqual(
            title("남자가 카메라를 보며 <d>[Korean] 우리는 해낼 수 있어.</d> 말이 끝나자 입을 다문다."),
            "남자가 카메라를 보며 말이 끝나자 입을 다문다")
        self.assertEqual(title("<d>[Korean] 안녕하세요, 오늘 날씨가 정말 좋네요.</d>"),
                         "안녕하세요, 오늘 날씨가 정말 좋네요")

    def test_known_worse(self):
        # Titles that read worse than before, recorded rather than hidden.
        # The style-word skip (_STYLE_ONLY) knows English only, so a Korean
        # look-only sentence long enough to count is now the title...
        self.assertEqual(title("시네마틱 영상. 바닷가 절벽 위에 등대가 서 있다."), "시네마틱 영상")
        self.assertEqual(title("따뜻한 필름 룩. 소녀가 창가에서 차를 마신다."), "따뜻한 필름 룩")
        # ...and a sentence whose <d> line was cut out of its middle is no
        # longer too short to skip.
        self.assertEqual(title("그가 <d>[Korean] 가자.</d>라고 말한다. 둘은 문을 나선다."), "그가 라고 말한다")


class Boundaries(unittest.TestCase):
    def test_empty(self):
        for p in (None, "", "   ", "\n\t"):
            with self.subTest(p=p):
                self.assertEqual(title(p), "")

    def test_length_cap(self):
        self.assertEqual(title("x" * 60), "X" + "x" * 59)
        self.assertEqual(title("x" * 61), "X" + "x" * 59 + "…")
        self.assertEqual(title("가" * 60 + "."), "가" * 60)
        self.assertEqual(title("가" * 61 + "."), "가" * 60 + "…")

    def test_label_wins(self):
        self.assertEqual(title("一个女人在海边行走。海浪涌来。", "바닷가"), "바닷가")


# Every prompt below gave this exact title before the change and must still.
UNCHANGED = [
    ('integrated_multimodal_description: [Shot 1] Live-action, vintage 1990s multi-camera sitcom, a medium shot of GEORGE, a short balding man in glasses and a grey jacket, in a diner booth under warm light. The camera holds a static shot. He stirs his coffee in silence. Then George, with a nasal, agitated New York voice (S1), says: <d>[English] I thought it was ashwagandha!</d> Exactly as his voice stops, his jaw ceases speaking motion and his mouth settles closed, and a studio audience laughs. No text appears at any point.\n\noverall_soundscape: The room tone of a television studio, then a burst of studio-audience laughter right after the line.\n\nnon_diegetic_music: N/A',
     'Vintage 1990s multi-camera sitcom, a medium shot of GEORGE…'),
    ('Live-action, cinematic, a close two-shot in a bright kitchen: a woman with a freshly buzzed head sits still while her teenage daughter stands behind her running hair clippers over her scalp, warm window light across both faces. Photoreal, heavy 35mm film grain.',
     'A close two-shot in a bright kitchen: a woman with a freshly…'),
    ('Live-action, cinematic, a medium-wide shot at dusk of a man in an open-collared shirt beside a battered steel dumpster in an empty back street. Photoreal, heavy 35mm film grain. Blue dusk light.',
     'A medium-wide shot at dusk of a man in an open-collared…'),
    ('Live-action, cinematic, a medium close-up of a man in a dark curly fur hat and heavy fur coat on an open dune ridge. He faces the camera squarely. The man, with a warm, measured, slightly gravelled voice (S1), says: <d>[English] They said this was impossible.</d> Exactly as his voice stops, his jaw ceases speaking motion and his mouth settles into a closed steady half-smile.',
     'A medium close-up of a man in a dark curly fur hat and heavy…'),
    ('Live-action, cinematic, an extreme macro of a warm glass cup under a polished steel spout, filling with espresso. A hard raking key light from camera left.',
     'An extreme macro of a warm glass cup under a polished steel…'),
    ("A weary man in a soft grey jacket sits in a sterile interview room, medium close-up, fluorescent overhead light, shallow depth of field. He breathes in, looks down at his hands. He says quietly and clearly: 'I stopped leaving the chair.'",
     'A weary man in a soft grey jacket sits in a sterile…'),
    ('Live-action, cinematic, a medium shot moving backwards ahead of a nurse in pale blue scrubs as she walks the length of a hospital corridor at night under flat fluorescent light.',
     'A medium shot moving backwards ahead of a nurse in pale blue…'),
    ('A red fox runs through the snow. The camera follows.',
     'A red fox runs through the snow'),
    ('Mr. Smith walks into the room. He sits down.',
     'Smith walks into the room'),
    ('Dr. Jones, a tall man, enters. Rain falls.',
     'Jones, a tall man, enters'),
    ('Dr. Chen studies a glowing vial in a dark lab. She smiles.',
     'Chen studies a glowing vial in a dark lab'),
    ('e.g. a cat jumping onto a table in a bright kitchen with morning light. Slow motion.',
     'A cat jumping onto a table in a bright kitchen with morning…'),
    ('3.5 seconds of a dog running across a field. Wide shot.',
     '3.5 seconds of a dog running across a field'),
    ('vh5tape, worn VHS tape recording of a birthday party in a living room.',
     'Worn VHS tape recording of a birthday party in a living room'),
    ('mrztrn, a man in a leather jacket walks down a rainy alley.',
     'A man in a leather jacket walks down a rainy alley'),
    ('4K, a woman walks through a neon city at night.',
     'A woman walks through a neon city at night'),
    ('35mm, a man opens a door.',
     'A man opens a door'),
    ('24 fps, a boat drifts on a lake.',
     '24 fps, a boat drifts on a lake'),
    ('Cinematic, warm film look, a girl reads a book by the window. Soft light.',
     'A girl reads a book by the window'),
    ('A woman in a garden, shot on 16mm. She picks a rose and smells it slowly.',
     'She picks a rose and smells it slowly'),
    ('A woman says <d>Hello there.</d> She waves.',
     'A woman says She waves'),
    ('<d>Hello there, how are you doing today my friend.</d>',
     'Hello there, how are you doing today my friend'),
    ('<d>[Korean] 안녕하세요, 오늘 날씨가 정말 좋네요.</d>',
     '안녕하세요, 오늘 날씨가 정말 좋네요'),
    ('[Shot 1] At 0:00, a man opens the door. (S1) <d>Hi</d>',
     'A man opens the door'),
    ('integrated_multimodal_description: A chef plates a dish. overall_soundscape: sizzling',
     'A chef plates a dish'),
    ('For the target video, at 0 seconds the first frame is the reference picture.\nA dancer spins on a rooftop at sunrise.',
     'A dancer spins on a rooftop at sunrise'),
    ('<Picture 1> (from upload) A woman in a red coat crosses a snowy bridge.',
     'A woman in a red coat crosses a snowy bridge'),
    ('...and then the sun set over the hills.',
     '...and then the sun set over the hills'),
    ('Wow! Look at that! A giant whale breaches beside the little boat at sunrise.',
     'Look at that'),
    ('He walks. She runs. They meet in the middle of the old stone bridge at dusk.',
     'They meet in the middle of the old stone bridge at dusk'),
    ('Is this the end? A lone astronaut stares at the dying star.',
     'Is this the end'),
    ('A man is shot on a narrow bridge at night. Police arrive within seconds and close the street.',
     'A man is shot on a narrow bridge at night'),
    ('He plays an old tape recording. The room goes silent as the voice fills the air around them.',
     'The room goes silent as the voice fills the air around them'),
    ('A woman looks at the camera and says <d>We have to leave now.</d> She grabs her coat and runs out of the room.',
     'A woman looks at the camera and says She grabs her coat and…'),
    ('He whispers <d>run</d> and turns away into the dark forest.',
     'He whispers and turns away into the dark forest'),
    ('She smiles and says <d>Fine.</d> and walks off down the long hallway.',
     'She smiles and says and walks off down the long hallway'),
    ('a b c',
     'A b c'),
    ('word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word ',
     'Word word word word word word word word word word word word…'),
    ('AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
     'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA…'),
    ('',
     ''),
    ('   ',
     ''),
    ('Short. A very long second sentence describing a woman walking on a beach at sunset with waves.',
     'A very long second sentence describing a woman walking on a…'),
    ('Ok. Yes. A very long third sentence describing a woman walking on a beach at sunset with waves.',
     'A very long third sentence describing a woman walking on a…'),
    ('A lighthouse keeper climbs the spiral stairs as a storm batters the glass and the lamp begins to turn',
     'A lighthouse keeper climbs the spiral stairs as a storm…'),
    ('A cat sleeps.',
     'A cat sleeps'),
    ('{style} a cat on a windowsill at dawn.',
     '{style} a cat on a windowsill at dawn'),
    ('A sign that reads {OPEN} hangs in a cafe window.',
     'A sign that reads {OPEN} hangs in a cafe window'),
    ('한 여자가 바닷가를 걷는다. 파도가 밀려온다.',
     '한 여자가 바닷가를 걷는다'),
    ('비 오는 서울의 골목, 네온 간판이 깜빡인다. 우산을 든 남자가 천천히 걸어간다.',
     '비 오는 서울의 골목, 네온 간판이 깜빡인다'),
    ('카메라가 천천히 다가가자 할머니가 웃으며 말한다. "밥은 먹었니?" 부엌에서 김이 피어오른다.',
     '카메라가 천천히 다가가자 할머니가 웃으며 말한다'),
    ('실사, 시네마틱, 한옥 마당에 눈이 내린다. 아이가 장독대 옆에서 눈사람을 만든다.',
     '실사, 시네마틱, 한옥 마당에 눈이 내린다'),
    ('남자가 카메라를 보며 <d>[Korean] 우리는 해낼 수 있어.</d> 말이 끝나자 입을 다문다.',
     '남자가 카메라를 보며 말이 끝나자 입을 다문다'),
    ('편의점 앞에서 고양이가 하품을 한다!',
     '편의점 앞에서 고양이가 하품을 한다'),
    ('서울 야경 🌃 위로 드론이 날아간다. 한강 다리에 불이 켜진다.',
     '서울 야경 🌃 위로 드론이 날아간다'),
    ('봄날의 벚꽃길을 따라 자전거를 탄 소녀가 달려가고, 꽃잎이 바람에 흩날리며 하늘로 올라간다.',
     '봄날의 벚꽃길을 따라 자전거를 탄 소녀가 달려가고, 꽃잎이 바람에 흩날리며 하늘로 올라간다'),
    ('桜並木の下を自転車に乗った少年が走り抜け、花びらが風に舞う',
     '桜並木の下を自転車に乗った少年が走り抜け、花びらが風に舞う'),
    ('A woman in Seoul 서울 walks past a 편의점 at night. Rain falls.',
     'A woman in Seoul 서울 walks past a 편의점 at night'),
    ('東京タワーの前で、a man in a suit checks his watch. 電車が通り過ぎる。',
     '東京タワーの前で、a man in a suit checks his watch'),
    ('🎉🎉 A birthday party in a small apartment! Balloons float to the ceiling.',
     '🎉🎉 A birthday party in a small apartment'),
    ('😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀',
     '😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀…'),
    ('He whispers <d>run.</d> and turns away into the dark forest.',
     'He whispers and turns away into the dark forest'),
    ('She turns. (S1) <d>Hello there.</d>',
     'She turns'),
    ('(S1) <d>Hello there.</d>',
     'Hello there'),
    ('<d>We have to go.</d> She grabs her coat and runs out.',
     'She grabs her coat and runs out'),
    ('At 0:00, she looks up and says: <d>[English] Not tonight.</d> At 0:03, the lights go out across the city.',
     'She looks up and says: the lights go out across the city'),
    ('The man (S1) says <d>[English] Wait!</d> (S2) The woman stops at the door.',
     'The man says The woman stops at the door'),
    ('She asks <d>Are you sure?</d> He nods slowly and looks away from the window.',
     'She asks He nods slowly and looks away from the window'),
    ('He says <d>Hi</d> She waves back from across the busy street.',
     'He says She waves back from across the busy street'),
    ('실사. 시네마틱. 바닷가 절벽 위에 등대가 서 있다.',
     '바닷가 절벽 위에 등대가 서 있다'),
    ('그녀가 웃으며 "좋아." 하고 대답한다. 바람이 분다.',
     '그녀가 웃으며 "좋아." 하고 대답한다'),
    ('할머니가 손녀에게 <d>[Korean] 밥 먹자.</d> 하고 부른다.',
     '할머니가 손녀에게 하고 부른다'),
    ('할머니가 손녀를 부른다. <d>[Korean] 밥 먹자.</d> 손녀가 달려온다.',
     '할머니가 손녀를 부른다'),
    ('이 영상은 4K로 촬영되었다. 남자가 해변을 달린다.',
     '이 영상은 4K로 촬영되었다'),
    ('一个很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长的句子。第二句。',
     '一个很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长…'),
    ('xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
     'Xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…'),
    ('xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
     'Xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'),
    ('word word word word word word word word word word word end',
     'Word word word word word word word word word word word end'),
    ('부드러운 빛. 여자가 창가에서 책을 읽는다.',
     '여자가 창가에서 책을 읽는다'),
    ('와! 거대한 고래가 작은 배 옆으로 뛰어오른다.',
     '거대한 고래가 작은 배 옆으로 뛰어오른다'),
    ('비가 온다. 우산을 쓴 남자가 골목을 걸어간다.',
     '우산을 쓴 남자가 골목을 걸어간다'),
    ('눈이 내린다. 아이들이 운동장에서 눈싸움을 한다.',
     '아이들이 운동장에서 눈싸움을 한다'),
    ('아이가 운다. 엄마가 달려와 아이를 안아 준다.',
     '엄마가 달려와 아이를 안아 준다'),
    ('괜찮을까? 소년이 낡은 다리 앞에서 망설인다.',
     '소년이 낡은 다리 앞에서 망설인다'),
    ('그렇죠! 남자가 손뼉을 치며 일어선다.',
     '남자가 손뼉을 치며 일어선다'),
    ('밤. 도시의 불빛이 하나둘 켜진다.',
     '도시의 불빛이 하나둘 켜진다'),
    ('슬로 모션. 물방울이 수면에 떨어진다.',
     '물방울이 수면에 떨어진다'),
    ('35mm 필름. 노인이 공원 벤치에 앉아 비둘기에게 모이를 준다.',
     '노인이 공원 벤치에 앉아 비둘기에게 모이를 준다'),
    ('카페 앞. A barista steams milk behind the counter.',
     'A barista steams milk behind the counter'),
    ('남자 걷다. 그 뒤로 해가 지고 하늘이 붉게 물든다.',
     '그 뒤로 해가 지고 하늘이 붉게 물든다'),
    ('맛있죠! 요리사가 접시를 내민다.',
     '요리사가 접시를 내민다'),
]


class UnchangedTitles(unittest.TestCase):
    def test_unchanged(self):
        for prompt, want in UNCHANGED:
            with self.subTest(prompt=prompt[:60]):
                self.assertEqual(title(prompt), want)


def _js_display_titles(prompts):
    src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
    start = src.index("\nfunction displayTitle(") + 1
    end = src.index("\n}\n", start) + 3
    script = src[start:end] + """
const ps = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(ps.map(p => displayTitle(p))));
"""
    res = subprocess.run(["node", "-e", script], input=json.dumps(prompts),
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(res.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class WebappMirrorAgrees(unittest.TestCase):
    def test_same_titles_as_queue_js(self):
        prompts = [p for p, _ in UNCHANGED] + [
            "一个女人在海边行走。海浪涌来，她回头微笑。远处的灯塔亮起。",
            "女性が浜辺を歩いている。波が打ち寄せる。",
            "本当に？！少女は驚いて目を見開いた。",
            "彼は振り返って「また会おう。」と言った。そして駅へ向かった。",
            "A man walks in。He sits down by the fire.",
            "一个" + "很长" * 40 + "的句子。第二句。",
            "여자가 웃는다. 카메라가 천천히 다가간다.",
            "부드러운 빛. 여자가 창가에서 책을 읽는다.",
            "고양이가잔다. 창밖에 비가 내리고 천둥이 친다.",
            "고양이 잔다. 창밖에 비가 내리고 천둥이 친다.",
            "ㅋㅋㅋ 웃긴 강아지. 강아지가 꼬리를 흔들며 뛰어다닌다.",
            "가" * 61 + ".",
        ]
        # JS counts UTF-16 units, so a title cut among emoji differs from
        # Python's; that predates this change and is left out here.
        prompts = [p for p in prompts if all(ord(c) < 0x10000 for c in p)]
        for prompt, js in zip(prompts, _js_display_titles(prompts)):
            with self.subTest(prompt=prompt[:60]):
                self.assertEqual(title(prompt), js)


if __name__ == "__main__":
    unittest.main()
