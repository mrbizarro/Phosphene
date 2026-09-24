"""Training a voice: the rules the trainer has to obey before it burns an hour of GPU.

CPU only, no YuE2 weights, no MERT, no audio. What is pinned here:

  * a training sequence is EXACTLY `token_prefixes(request) + codes`, checked
    against the engine's own function, in both dialects;
  * the loss mask supervises the code tail and nothing else — the bug that
    teaches a model to write prompts instead of to sing;
  * a window's lyrics are the words sung inside that window, not the whole
    song, because a prefix that lies about its codes is what stopped new
    lyrics being pronounced at all;
  * the score dialect carries a real ABC score, and the engine still refuses
    the combination the score dialect exists to avoid (`abc` with `cot=off`);
  * a saved checkpoint reads back through the production loader with one rank
    and every target, and its sidecar says what the file is;
  * the CLI plans a real dataset and exits 0, and refuses a bad flag with 2.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import mlx.core as mx
import mlx.nn as nn
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts" / "music"))
sys.path.insert(0, str(ROOT / "yue2-mlx" / "vendor" / "yue" / "src"))
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="yue2-voice-state-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"

import yue2_lora as L                                            # noqa: E402
import yue2_train_voice as V                                     # noqa: E402
from yue2.protocol import (CODEC_OFFSET, MUSIC_END, MUSIC_START,  # noqa: E402
                           SongRequest, token_prefixes)

HIDDEN, KV, INTERMEDIATE, LAYERS, RANK = 16, 8, 32, 2, 4


class FakeTokenizer:
    """Deterministic text → ids, inside the ordinary text vocabulary."""

    def encode(self, text):
        return [(ord(ch) % 4000) + 7 for ch in text]


def codes(n, seed=0):
    return np.random.default_rng(seed).integers(0, 32768, size=n, dtype=np.int64)


# --------------------------------------------------------------- sequences --

class SequenceLayout(unittest.TestCase):
    def setUp(self):
        self.tok = FakeTokenizer()

    def test_a_direct_sequence_is_the_engines_prefix_then_the_codes(self):
        request = V.window_request("piano ballad", "frddtrn", "a line",
                                   dialect="direct", abc=None, cot="melody", identifier="w0")
        want = token_prefixes(request, self.tok)
        body = codes(120)
        ids, prefix_len = V.training_sequence(want, body, 4096)
        self.assertEqual(ids[:prefix_len], list(want))
        self.assertEqual(prefix_len, len(want))
        self.assertEqual(ids[prefix_len:-1], [int(c) + CODEC_OFFSET for c in body])
        self.assertEqual(ids[-1], MUSIC_END)
        self.assertEqual(want[-1], MUSIC_START)

    def test_a_score_sequence_carries_the_abc_inside_the_prefix(self):
        score = "X:1\nL:1/8\nK:Bb\nV:Vocal\nB c d2 |\n"
        request = V.window_request("piano ballad", "frddtrn", "a line",
                                   dialect="score", abc=score, cot="melody",
                                   identifier="w0")
        prefix = token_prefixes(request, self.tok)
        self.assertEqual(request.cot, "melody")
        # the ABC ids really are in there, between ABC_START and ABC_END
        abc_ids = self.tok.encode(score)
        joined = ",".join(map(str, prefix))
        self.assertIn(",".join(map(str, abc_ids)), joined)
        ids, prefix_len = V.training_sequence(prefix, codes(120), 4096)
        self.assertEqual(ids[:prefix_len], list(prefix))
        self.assertGreater(prefix_len, len(token_prefixes(
            SongRequest(style="piano ballad", lyrics="a line", cot="off"), self.tok)))

    def test_the_score_dialect_needs_a_score(self):
        with self.assertRaises(ValueError):
            V.window_request("s", "t", "l", dialect="score", abc=None,
                             cot="melody", identifier="w0")
        with self.assertRaises(ValueError):
            V.window_request("s", "t", "l", dialect="score", abc="   ",
                             cot="melody", identifier="w0")

    def test_the_engine_still_refuses_a_score_with_direct_planning(self):
        # The reason the score dialect exists at all.
        with self.assertRaises(ValueError):
            SongRequest(style="s", lyrics="l", cot="off", abc="X:1\n")

    def test_the_trigger_joins_the_style_once(self):
        first = V.window_request("piano ballad", "frddtrn", "l", dialect="direct",
                                 abc=None, cot="melody", identifier="w0")
        self.assertTrue(first.style.startswith("frddtrn,"))
        again = V.window_request(first.style, "frddtrn", "l", dialect="direct",
                                 abc=None, cot="melody", identifier="w0")
        self.assertEqual(again.style.lower().count("frddtrn"), 1)

    def test_codes_outside_the_vocabulary_never_reach_the_model(self):
        with self.assertRaises(ValueError):
            V.training_sequence([1, 2, 3], np.array([32768]), 4096)
        with self.assertRaises(ValueError):
            V.training_sequence([1, 2, 3], np.array([-1]), 4096)

    def test_a_prefix_that_leaves_no_room_is_refused_not_truncated(self):
        with self.assertRaises(ValueError):
            V.training_sequence(list(range(1000)), codes(600), 1024)

    def test_a_long_track_is_windowed_rather_than_truncated(self):
        prefix, body = [5] * 10, codes(5000)
        ids, prefix_len = V.training_sequence(prefix, body, 1024, rng=None)
        self.assertEqual(len(ids), 1024 - 1)
        self.assertEqual(ids[prefix_len], int(body[0]) + CODEC_OFFSET)
        import random
        picked = {V.training_sequence(prefix, body, 1024, rng=random.Random(s))[0][10]
                  for s in range(8)}
        self.assertGreater(len(picked), 1, "a random window should move")


# -------------------------------------------------------------- loss mask --

class LossMask(unittest.TestCase):
    def test_only_the_code_tail_is_supervised(self):
        prefix = token_prefixes(SongRequest(style="s", lyrics="l", cot="off"),
                                FakeTokenizer())
        body = codes(64)
        ids, prefix_len = V.training_sequence(prefix, body, 4096)
        states, labels = V.supervised_span(prefix_len, len(ids))
        targets = ids[labels]
        self.assertEqual(len(targets), len(body) + 1)          # codes + MUSIC_END
        self.assertTrue(all(t >= CODEC_OFFSET or t == MUSIC_END for t in targets))
        self.assertEqual(states.stop - states.start, labels.stop - labels.start)
        # the first scored hidden state is the LAST prefix position
        self.assertEqual(states.start, prefix_len - 1)
        self.assertEqual(labels.start, prefix_len)

    def test_no_prefix_token_is_ever_a_target(self):
        prefix = list(range(1, 40))
        ids, prefix_len = V.training_sequence(prefix, codes(80), 4096)
        _, labels = V.supervised_span(prefix_len, len(ids))
        self.assertEqual(set(ids[labels]) & set(prefix), set())

    def test_an_empty_tail_is_refused(self):
        with self.assertRaises(ValueError):
            V.supervised_span(10, 10)


# ----------------------------------------------------------------- windows --

class Windows(unittest.TestCase):
    def test_windows_cover_the_track_and_the_last_one_is_flush_with_the_end(self):
        spans = V.plan_windows(8988, 512, 256)
        self.assertEqual(spans[0], (0, 512))
        self.assertEqual(spans[-1][1], 8988)
        self.assertTrue(all(b - a == 512 for a, b in spans))
        covered = set()
        for a, b in spans:
            covered |= set(range(a, b))
        self.assertEqual(len(covered), 8988)

    def test_a_short_track_is_one_window_and_a_scrap_is_none(self):
        self.assertEqual(V.plan_windows(300, 512, 256), [(0, 300)])
        self.assertEqual(V.plan_windows(10, 512, 256), [])

    def test_timed_lyrics_put_the_sung_words_in_their_own_window(self):
        lines = [{"text": "one", "onset": 1.0, "end": 3.0, "section": "[Verse]"},
                 {"text": "two", "onset": 12.0, "end": 14.0},
                 {"text": "three", "onset": 25.0, "end": 27.0, "section": "[Chorus]"}]
        first = V.lyrics_for_window("", lines, 0.0, 20.48, 40.0)
        self.assertEqual(first.splitlines(), ["[Verse]", "one", "two"])
        second = V.lyrics_for_window("", lines, 20.48, 40.0, 40.0)
        self.assertEqual(second.splitlines(), ["[Chorus]", "three"])
        self.assertNotIn("one", second)

    def test_a_line_still_running_into_the_window_is_kept(self):
        lines = [{"text": "held", "onset": 18.0, "end": 24.0}]
        self.assertIn("held", V.lyrics_for_window("", lines, 20.48, 40.0, 40.0))

    def test_without_timings_the_window_gets_its_share_not_the_whole_song(self):
        sheet = "[Verse]\n" + "\n".join(f"line {i}" for i in range(20))
        early = V.lyrics_for_window(sheet, None, 0.0, 10.0, 100.0)
        late = V.lyrics_for_window(sheet, None, 90.0, 100.0, 100.0)
        self.assertIn("line 0", early)
        self.assertNotIn("line 19", early)
        self.assertIn("line 19", late)
        self.assertNotIn("line 0", late)
        self.assertLess(len(early.splitlines()), 20)

    def test_a_reader_drops_a_line_it_could_not_locate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.lines.json"
            path.write_text(json.dumps([{"text": "a", "onset": 1.0},
                                        {"text": "b"},
                                        {"text": "", "onset": 2.0}]))
            rows = V.read_timed_lines(path)
        self.assertEqual([row["text"] for row in rows], ["a"])

    def test_the_dataset_pairs_each_window_with_its_own_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "song.wav"
            audio.write_bytes(b"RIFF")
            (Path(tmp) / "song.txt").write_text("[Verse]\nalpha\nbeta\ngamma\ndelta")
            body = codes(1024, seed=3)
            items, windows, skipped = V.build_dataset(
                [audio], style="ballad", trigger="frddtrn", dialect="direct",
                cot="melody", lyrics_sources=[tmp], window_frames=512, hop_frames=256,
                tokenizer=FakeTokenizer(), codes_of=lambda _: body, say=lambda *a: None)
        self.assertEqual(len(items), len(windows))
        self.assertEqual([w.frames for w in windows], [512] * len(windows))
        self.assertEqual(len(items[0]["codec"]), 512)
        np.testing.assert_array_equal(items[0]["codec"], body[:512])
        np.testing.assert_array_equal(items[1]["codec"], body[256:768])
        self.assertEqual(skipped["no_score"], 0)
        self.assertTrue(all(item["src"] == "artist" for item in items))

    def test_one_lyric_sheet_for_one_recording_does_not_need_a_matching_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "frddtrn.wav"
            audio.write_bytes(b"RIFF")
            sheet = Path(tmp) / "whatever_the_sheet_is_called.txt"
            sheet.write_text("[Verse]\nalpha\nbeta\ngamma\ndelta")
            _, windows, skipped = V.build_dataset(
                [audio], style="ballad", trigger="t", dialect="direct", cot="melody",
                lyrics_sources=[str(sheet)], window_frames=512, hop_frames=256,
                tokenizer=FakeTokenizer(), codes_of=lambda _: codes(1024, seed=9),
                say=lambda *a: None)
        self.assertEqual(skipped["no_lyrics"], 0)
        self.assertTrue(all(w.lyrics.strip() for w in windows))
        # ...but two recordings and one unnamed sheet stay unmatched
        self.assertIsNone(V.match_sidecar(Path("a.wav"), ["b.txt"], (".txt",)))

    def test_a_score_dialect_window_without_a_score_is_skipped_not_faked(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "song.wav"
            audio.write_bytes(b"RIFF")
            scores = Path(tmp) / "scores"
            scores.mkdir()
            (scores / "song.w000.abc").write_text("X:1\nL:1/8\nK:C\nV:Vocal\nC D E2 |\n")
            items, windows, skipped = V.build_dataset(
                [audio], style="ballad", trigger="t", dialect="score", cot="melody",
                lyrics_sources=None, scores_dir=scores, window_frames=512,
                hop_frames=256, tokenizer=FakeTokenizer(),
                codes_of=lambda _: codes(1024, seed=4), say=lambda *a: None)
        self.assertEqual(len(items), 1)
        self.assertEqual(windows[0].index, 0)
        self.assertGreater(skipped["no_score"], 0)
        self.assertTrue(windows[0].abc)


# ------------------------------------------------------------- checkpoints --

class _Attention(nn.Module):
    def __init__(self):
        super().__init__()
        self.q_proj = nn.Linear(HIDDEN, HIDDEN, bias=False)
        self.k_proj = nn.Linear(HIDDEN, KV, bias=False)
        self.v_proj = nn.Linear(HIDDEN, KV, bias=False)
        self.o_proj = nn.Linear(HIDDEN, HIDDEN, bias=False)


class _MLP(nn.Module):
    def __init__(self):
        super().__init__()
        self.gate_proj = nn.Linear(HIDDEN, INTERMEDIATE, bias=False)
        self.up_proj = nn.Linear(HIDDEN, INTERMEDIATE, bias=False)
        self.down_proj = nn.Linear(INTERMEDIATE, HIDDEN, bias=False)


class _Layer(nn.Module):
    def __init__(self):
        super().__init__()
        self.self_attn = _Attention()
        self.mlp = _MLP()


class _Backbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = [_Layer() for _ in range(LAYERS)]

    def __call__(self, ids):
        return mx.zeros((ids.shape[0], ids.shape[1], HIDDEN))


class _Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.model = _Backbone()
        self.lm_head = nn.Linear(HIDDEN, 64, bias=False)


class Checkpoints(unittest.TestCase):
    def test_a_saved_file_reads_back_through_the_production_loader(self):
        model = _Model()
        slots, params = V.install_adapters(model, RANK, seed=7)
        self.assertEqual(len(params), LAYERS * 7)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frddtrn.safetensors"
            V.save_adapter(params, path, rank=RANK, dialect="direct", trigger="frddtrn")
            adapter = L.read_adapter(path)
        self.assertEqual(adapter.rank, RANK)
        self.assertEqual(adapter.branch, "ar")
        self.assertEqual(len(adapter.deltas), LAYERS * 7)
        for layer in range(LAYERS):
            for group, names in (("self_attn", V.ATTN_PROJECTIONS),
                                 ("mlp", V.MLP_PROJECTIONS)):
                for projection in names:
                    self.assertIn(V.adapter_key(layer, group, projection), adapter.deltas)

    def test_a_fresh_adapter_is_the_identity_so_step_zero_is_the_base_model(self):
        model = _Model()
        slots, params = V.install_adapters(model, RANK, seed=7)
        V.bind(slots, params)
        x = mx.random.normal((3, HIDDEN))
        base = nn.Linear(HIDDEN, HIDDEN, bias=False)
        base.weight = model.model.layers[0].self_attn.q_proj.weight
        self.assertTrue(mx.allclose(model.model.layers[0].self_attn.q_proj(x), base(x)))

    def test_wrapping_renames_not_one_parameter(self):
        from mlx.utils import tree_flatten
        before = {k for k, _ in tree_flatten(_Model().parameters())}
        model = _Model()
        V.install_adapters(model, RANK, seed=7)
        self.assertEqual({k for k, _ in tree_flatten(model.parameters())}, before)

    def test_the_sidecar_says_what_the_file_is_and_what_made_it(self):
        windows = [V.Window("song.wav", 0, 0.0, 20.48, "a line", None, 512),
                   V.Window("song.wav", 1, 10.24, 20.48, "", None, 512)]
        meta = V.sidecar_metadata(
            name="frddtrn", trigger="frddtrn", dialect="direct", cot="melody",
            style="piano ballad", model_dir=ROOT / "mlx_models" / "yue2" / "generator",
            steps=400, rank=32, lr=1e-4, window_seconds=20.48, hop_seconds=10.24,
            windows=windows, skipped={"no_score": 0}, fingerprint="abc123",
            metrics={"history": [{"step": 0, "artist": 5.3}]}, seed=4242,
            artist_fraction=0.5)
        self.assertEqual(meta["trigger"], "frddtrn")
        self.assertEqual(meta["dialect"], "direct")
        self.assertEqual(meta["branch"], "ar")
        self.assertEqual(meta["generate_with"], "--mode off")
        self.assertEqual(meta["training"]["steps"], 400)
        self.assertEqual(meta["data"]["fingerprint"], "abc123")
        self.assertEqual(meta["data"]["windows_with_lyrics"], 1)
        self.assertEqual(meta["data"]["recordings"], ["song.wav"])
        self.assertTrue(meta["metrics"]["history"])
        json.dumps(meta)                                          # has to serialise

    def test_the_score_dialect_sidecar_tells_the_caller_it_needs_a_score(self):
        meta = V.sidecar_metadata(
            name="v", trigger="t", dialect="score", cot="melody", style="s",
            model_dir=Path("/nowhere"), steps=1, rank=8, lr=1e-4, window_seconds=20.48,
            hop_seconds=10.24, windows=[], skipped={}, fingerprint="f", metrics={},
            seed=1, artist_fraction=0.5)
        self.assertIn("--abc-file", meta["generate_with"])
        self.assertIn("--mode melody", meta["generate_with"])


# ------------------------------------------------- caches and identity --

class RegulariserCache(unittest.TestCase):
    """The cache is data. It was a pickle, and a pickle is code."""

    def test_a_cache_round_trips_without_pickles(self):
        items = [{"src": "minted", "name": "m0", "prefix": np.array([1, 2, 3]),
                  "codec": codes(40, seed=1)},
                 {"src": "minted_val", "name": "m1", "prefix": np.array([4, 5]),
                  "codec": codes(7, seed=2)}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reg.npz"
            V.write_regularizer_cache(path, items)
            # the file itself must be loadable with pickles DISABLED
            with np.load(path, allow_pickle=False) as blob:
                self.assertIn("prefix_data", blob.files)
                self.assertNotIn("items", blob.files)
            back = V.read_regularizer_cache(path)
        self.assertEqual([row["src"] for row in back], ["minted", "minted_val"])
        self.assertEqual([row["name"] for row in back], ["m0", "m1"])
        for want, got in zip(items, back):
            np.testing.assert_array_equal(want["prefix"], got["prefix"])
            np.testing.assert_array_equal(want["codec"], got["codec"])

    def test_an_object_array_cache_is_refused_rather_than_unpickled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.npz"
            np.savez(path, items=np.asarray([{"src": "minted", "name": "m0",
                                              "prefix": np.array([1]),
                                              "codec": np.array([2])}], dtype=object))
            with self.assertRaises(ValueError) as caught:
                V.read_regularizer_cache(path)
        self.assertIn("regulariser cache", str(caught.exception))

    def test_a_cache_claiming_another_version_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "future.npz"
            empty = np.zeros(0, dtype=np.int64)
            np.savez(path, version=np.asarray(99, dtype=np.int64),
                     prefix_data=empty, prefix_offsets=np.zeros(1, dtype=np.int64),
                     codec_data=empty, codec_offsets=np.zeros(1, dtype=np.int64),
                     meta=np.asarray("[]"))
            with self.assertRaises(ValueError):
                V.read_regularizer_cache(path)


class CodesCacheIdentity(unittest.TestCase):
    """Two albums both holding a take.wav must not share one cache entry."""

    def test_the_key_follows_the_bytes_not_the_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            head = tmp / "head.safetensors"
            head.write_bytes(b"HEAD")
            (tmp / "a").mkdir()
            (tmp / "b").mkdir()
            first, second = tmp / "a" / "take.wav", tmp / "b" / "take.wav"
            first.write_bytes(b"RIFF" + b"\x01" * 512)
            second.write_bytes(b"RIFF" + b"\x02" * 512)
            same = tmp / "c.wav"
            same.write_bytes(first.read_bytes())
            key_first = V.codes_cache_key(first, head)
            key_second = V.codes_cache_key(second, head)
            key_same = V.codes_cache_key(same, head)
            other_head = tmp / "other.safetensors"
            other_head.write_bytes(b"OTHER")
            key_other_head = V.codes_cache_key(first, other_head)
        self.assertEqual(first.name, second.name)          # the collision that bit
        self.assertNotEqual(key_first, key_second)
        self.assertEqual(key_first, key_same)              # same bytes, same key
        self.assertNotEqual(key_first, key_other_head)     # the head is part of it

    def test_a_cache_entry_for_other_bytes_is_not_reused(self):
        calls = []

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            head = tmp / "head.safetensors"
            head.write_bytes(b"HEAD")
            audio = tmp / "take.wav"
            audio.write_bytes(b"RIFF" + b"\x01" * 256)
            cache = tmp / "codes"
            cache.mkdir()
            key = V.codes_cache_key(audio, head)
            np.save(cache / f"take.{key}.codes.npy", codes(64, seed=8).astype(np.int32))
            (cache / f"take.{key}.codes.json").write_text(json.dumps({"cache_key": key}))
            got = V._codes_for(audio, cache, head, None, lambda *a: calls.append(a))
            self.assertEqual(len(got), 64)                 # the hit, no encoding
            audio.write_bytes(b"RIFF" + b"\x09" * 256)    # the recording is replaced
            stale_key = V.codes_cache_key(audio, head)
            self.assertNotEqual(stale_key, key)
            self.assertFalse((cache / f"take.{stale_key}.codes.npy").is_file())


class HeadResolution(unittest.TestCase):
    def test_an_explicit_head_that_is_not_there_is_named(self):
        with self.assertRaises(FileNotFoundError):
            V.resolve_head(Path("/nowhere/head.safetensors"), None)

    def test_a_missing_pack_says_what_to_install_instead_of_TypeError(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as caught:
                V.resolve_head(None, Path(tmp))
        message = str(caught.exception)
        self.assertIn("tokenizer head", message)
        self.assertNotIn("NoneType", message)

    def test_an_explicit_head_is_returned_as_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            head = Path(tmp) / "head.safetensors"
            head.write_bytes(b"HEAD")
            self.assertEqual(V.resolve_head(head, None), head)


# --------------------------------------------------------------- the CLI --

class CommandLine(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "music" / "yue2_train_voice.py"

    def run_cli(self, *argv):
        return subprocess.run([sys.executable, str(self.SCRIPT), *argv],
                              capture_output=True, text=True, timeout=300)

    def test_help_exits_zero_and_advertises_the_flags_the_brief_asked_for(self):
        done = self.run_cli("--help")
        self.assertEqual(done.returncode, 0, done.stderr)
        for flag in ("--recordings", "--lyrics", "--trigger", "--out", "--steps",
                     "--rank", "--lr", "--dialect", "--reg-pack"):
            self.assertIn(flag, done.stdout)

    def test_a_flag_that_is_not_ours_exits_two(self):
        self.assertEqual(self.run_cli("--recordings", "x", "--out", "y",
                                      "--dialect", "karaoke").returncode, 2)
        self.assertEqual(self.run_cli("--out", "y").returncode, 2)

    def test_a_plan_only_run_builds_a_real_dataset_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            audio = tmp / "song.wav"
            audio.write_bytes(b"RIFF" + b"\0" * 64)
            (tmp / "song.txt").write_text("[Verse]\nalpha\nbeta\ngamma\ndelta\n")
            cache = tmp / "codes"
            cache.mkdir()
            head = tmp / "head.safetensors"
            head.write_bytes(b"HEAD")
            key = V.codes_cache_key(audio, head)
            np.save(cache / f"song.{key}.codes.npy", codes(1024, seed=5).astype(np.int32))
            (cache / f"song.{key}.codes.json").write_text(json.dumps({"cache_key": key}))
            out = tmp / "out"
            done = self.run_cli("--recordings", str(audio), "--lyrics", str(tmp / "song.txt"),
                                "--trigger", "frddtrn", "--out", str(out),
                                "--style", "piano ballad", "--codes-cache", str(cache),
                                "--head", str(head), "--plan-only")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            plan = json.loads((out / "plan.json").read_text())
        self.assertEqual(len(plan["windows"]), len(V.plan_windows(1024, 512, 256)))
        self.assertTrue(all(w["abc"] is False for w in plan["windows"]))
        self.assertTrue(all(t > 0 for t in plan["prefix_tokens"]))
        self.assertIn("fingerprint", plan)

    def test_a_score_run_with_no_scores_folder_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            audio = tmp / "song.wav"
            audio.write_bytes(b"RIFF" + b"\0" * 64)
            cache = tmp / "codes"
            cache.mkdir()
            head = tmp / "head.safetensors"
            head.write_bytes(b"HEAD")
            key = V.codes_cache_key(audio, head)
            np.save(cache / f"song.{key}.codes.npy", codes(1024, seed=6).astype(np.int32))
            (cache / f"song.{key}.codes.json").write_text(json.dumps({"cache_key": key}))
            done = self.run_cli("--recordings", str(audio), "--out", str(tmp / "out"),
                                "--dialect", "score", "--codes-cache", str(cache),
                                "--head", str(head), "--plan-only")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("No trainable windows", done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
