"""The real-audio tokenizer head: the rules a recording has to pass through.

CPU only, no YuE2 weights, no 2.5 GB MERT checkpoint, no audio decoding. What
is pinned here is everything that can be wrong in silence — every item below
has identical shapes whether it is right or wrong, which is exactly why it gets
a test rather than a comment:

  * the checkpoint's tensor names map onto this module's parameter tree with
    nothing missing and nothing extra, and a renamed or truncated file is
    refused by name rather than loaded half-way;
  * the encoder computes what torch's `TransformerEncoderLayer(norm_first=True,
    activation="gelu")` computes — checked against a numpy reference written
    out longhand on random weights, including the fused q,k,v row order, which
    every permutation would load without complaint;
  * `pos` is sliced for a short window, never interpolated;
  * instance norm is per TRACK and uses the population standard deviation
    (numpy's default, not torch's) — the original reads the array as numpy;
  * the time resampler is `align_corners=False` half-pixel, the convention that
    decides which frame every code lands on;
  * the tiling is `joint_v6.predict`'s: hop 256, a pulled-back final window,
    128 frames trimmed off every interior edge, and no frame left unwritten;
  * the MERT mixer one-hot sits at `layer + 1`, because transformers collects
    hidden states only after each block while `lyra.mert`'s vector begins with
    the pre-layer embedding;
  * the pack keeps the head out of the LoRA picker's reach;
  * the runner's argv round trip carries the prompt and refuses nonsense.

The numbers in `docs/MUSIC_ENGINE.md` come from the real head against a real
torch reference; this file is the part that runs on every release.
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
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "music"))
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="yue2-tok-state-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"

import yue2_tokenizer as T                                       # noqa: E402
from scripts.pinokio import music_lora_fetch as P                # noqa: E402


# ------------------------------------------------------------ numpy reference

def _layer_norm(x, weight, bias, eps=1e-5):
    mean = x.mean(-1, keepdims=True)
    variance = ((x - mean) ** 2).mean(-1, keepdims=True)
    return (x - mean) / np.sqrt(variance + eps) * weight + bias


def _gelu(x):
    from math import erf

    return 0.5 * x * (1.0 + np.vectorize(erf)(x / np.sqrt(2.0)))


def _attention(x, in_weight, in_bias, out_weight, out_bias, heads):
    """torch's `MultiheadAttention` written out: q,k,v are the row blocks of
    `in_proj_weight`, in that order, and q is scaled by 1/sqrt(head_dim)."""
    length, dim = x.shape
    head_dim = dim // heads
    fused = x @ in_weight.T + in_bias
    query, key, value = fused[:, :dim], fused[:, dim:2 * dim], fused[:, 2 * dim:]
    out = np.zeros_like(x)
    for head in range(heads):
        span = slice(head * head_dim, (head + 1) * head_dim)
        scores = (query[:, span] @ key[:, span].T) / np.sqrt(head_dim)
        scores = scores - scores.max(-1, keepdims=True)
        weights = np.exp(scores)
        weights /= weights.sum(-1, keepdims=True)
        out[:, span] = weights @ value[:, span]
    return out @ out_weight.T + out_bias


def _reference_head(weights, features, layers=2, heads=T.NUM_HEADS):
    """`Tok.forward` from `joint_v6.py`, in numpy, on the given weights."""
    frames = len(features)
    x = features @ weights["inp.weight"].T + weights["inp.bias"]
    x = x + weights["pos"][0, :frames]
    for index in range(layers):
        prefix = f"enc.layers.{index}."
        normed = _layer_norm(x, weights[prefix + "norm1.weight"], weights[prefix + "norm1.bias"])
        x = x + _attention(normed, weights[prefix + "self_attn.in_proj_weight"],
                           weights[prefix + "self_attn.in_proj_bias"],
                           weights[prefix + "self_attn.out_proj.weight"],
                           weights[prefix + "self_attn.out_proj.bias"], heads)
        normed = _layer_norm(x, weights[prefix + "norm2.weight"], weights[prefix + "norm2.bias"])
        hidden = _gelu(normed @ weights[prefix + "linear1.weight"].T + weights[prefix + "linear1.bias"])
        x = x + hidden @ weights[prefix + "linear2.weight"].T + weights[prefix + "linear2.bias"]
    x = _layer_norm(x, weights["norm.weight"], weights["norm.bias"])
    return x @ weights["head.weight"].T + weights["head.bias"]


def _random_weights(rng, *, layers=2, dim=T.MODEL_DIM, vocab=64, window=T.WINDOW,
                    feature_dim=T.FEATURE_DIM, ffn=T.FFN_DIM):
    def draw(*shape, scale=0.05):
        return rng.standard_normal(shape).astype(np.float32) * scale

    weights = {
        "inp.weight": draw(dim, feature_dim), "inp.bias": draw(dim),
        "pos": draw(1, window, dim),
        "norm.weight": 1.0 + draw(dim), "norm.bias": draw(dim),
        "head.weight": draw(vocab, dim), "head.bias": draw(vocab),
    }
    for index in range(layers):
        prefix = f"enc.layers.{index}."
        weights.update({
            prefix + "self_attn.in_proj_weight": draw(3 * dim, dim),
            prefix + "self_attn.in_proj_bias": draw(3 * dim),
            prefix + "self_attn.out_proj.weight": draw(dim, dim),
            prefix + "self_attn.out_proj.bias": draw(dim),
            prefix + "linear1.weight": draw(ffn, dim), prefix + "linear1.bias": draw(ffn),
            prefix + "linear2.weight": draw(dim, ffn), prefix + "linear2.bias": draw(dim),
            prefix + "norm1.weight": 1.0 + draw(dim), prefix + "norm1.bias": draw(dim),
            prefix + "norm2.weight": 1.0 + draw(dim), prefix + "norm2.bias": draw(dim),
        })
    return weights


def _small_head(weights, *, layers=2, vocab=64):
    head = T.TokenizerHead()
    head.enc = T.Encoder(layers)
    head.head = type(head.head)(T.MODEL_DIM, vocab)
    head.update(_tree(weights))
    mx.eval(head.parameters())
    return head


def _tree(weights):
    from mlx.utils import tree_unflatten

    return tree_unflatten([(key, mx.array(value)) for key, value in weights.items()])


# ------------------------------------------------------------------- the tests

class KeyMapping(unittest.TestCase):
    def test_expected_keys_match_a_fresh_module(self):
        from mlx.utils import tree_flatten

        head = T.TokenizerHead()
        names = sorted(key for key, _ in tree_flatten(head.parameters()))
        self.assertEqual(names, list(T.EXPECTED_KEYS))
        self.assertEqual(len(names), 103, "the checkpoint carries 103 tensors")

    def test_shapes_are_the_published_architecture(self):
        from mlx.utils import tree_flatten

        shapes = {key: tuple(value.shape) for key, value in tree_flatten(T.TokenizerHead().parameters())}
        self.assertEqual(shapes["inp.weight"], (512, 1024))
        self.assertEqual(shapes["pos"], (1, 512, 512))
        self.assertEqual(shapes["head.weight"], (32768, 512))
        self.assertEqual(shapes["enc.layers.7.self_attn.in_proj_weight"], (1536, 512))
        self.assertEqual(shapes["enc.layers.0.linear1.weight"], (2048, 512))

    def test_a_missing_tensor_is_refused_by_name(self):
        weights = {key: np.zeros((1,), np.float32) for key in T.EXPECTED_KEYS}
        weights.pop("head.bias")
        with self.assertRaises(ValueError) as caught:
            T.convert_head_weights(weights)
        self.assertIn("missing", str(caught.exception))
        self.assertIn("tokenizer_head_joint_v9", str(caught.exception))

    def test_an_extra_tensor_is_refused(self):
        weights = {key: np.zeros((1,), np.float32) for key in T.EXPECTED_KEYS}
        weights["enc.layers.8.norm1.weight"] = np.zeros((1,), np.float32)
        with self.assertRaises(ValueError) as caught:
            T.convert_head_weights(weights)
        self.assertIn("unexpected", str(caught.exception))

    def test_a_missing_file_names_the_fetch_command(self):
        with self.assertRaises(FileNotFoundError) as caught:
            T.load_head(Path(tempfile.mkdtemp()) / "nothing.safetensors")
        self.assertIn("music_lora_fetch", str(caught.exception))


class EncoderMath(unittest.TestCase):
    """The encoder against a numpy reference on random weights."""

    def setUp(self):
        self.rng = np.random.default_rng(20260921)
        self.weights = _random_weights(self.rng)
        self.head = _small_head(self.weights)

    def _run(self, frames):
        features = self.rng.standard_normal((frames, T.FEATURE_DIM)).astype(np.float32)
        want = _reference_head(self.weights, features)
        got = np.array(self.head(mx.array(features[None])).astype(mx.float32))[0]
        return want, got

    def test_full_window_matches_the_reference(self):
        want, got = self._run(T.WINDOW)
        self.assertLess(np.abs(want - got).max(), 2e-4)
        self.assertTrue((want.argmax(-1) == got.argmax(-1)).all())

    def test_short_window_slices_pos_rather_than_interpolating(self):
        want, got = self._run(97)
        self.assertLess(np.abs(want - got).max(), 2e-4)

    def test_qkv_row_order_is_q_then_k_then_v(self):
        """Every permutation has identical shapes and loads without complaint.
        Only one of them is what the checkpoint means."""
        features = self.rng.standard_normal((64, T.FEATURE_DIM)).astype(np.float32)
        want = _reference_head(self.weights, features)
        import itertools

        wrong = 0
        for order in itertools.permutations(range(3)):
            weights = dict(self.weights)
            for index in range(2):
                prefix = f"enc.layers.{index}.self_attn."
                blocks = self.weights[prefix + "in_proj_weight"].reshape(3, T.MODEL_DIM, T.MODEL_DIM)
                biases = self.weights[prefix + "in_proj_bias"].reshape(3, T.MODEL_DIM)
                weights[prefix + "in_proj_weight"] = np.concatenate(
                    [blocks[i] for i in order], axis=0)
                weights[prefix + "in_proj_bias"] = np.concatenate([biases[i] for i in order])
            got = np.array(_small_head(weights)(mx.array(features[None])).astype(mx.float32))[0]
            difference = np.abs(want - got).max()
            if order == (0, 1, 2):
                self.assertLess(difference, 2e-4, "q,k,v must be the identity")
            else:
                self.assertGreater(difference, 1e-3, f"permutation {order} looked identical")
                wrong += 1
        self.assertEqual(wrong, 5)

    def test_more_frames_than_a_window_is_refused(self):
        features = np.zeros((T.WINDOW + 1, T.FEATURE_DIM), np.float32)
        with self.assertRaises(ValueError):
            self.head(mx.array(features[None]))

    def test_the_wrong_feature_width_is_refused(self):
        with self.assertRaises(ValueError):
            self.head(mx.array(np.zeros((1, 8, 512), np.float32)))


class InstanceNorm(unittest.TestCase):
    def test_per_channel_over_the_whole_track(self):
        rng = np.random.default_rng(3)
        values = rng.standard_normal((400, 16)).astype(np.float32) * 7.0 + 3.0
        normed = T.instance_norm(values)
        self.assertTrue(np.allclose(normed.mean(0), 0.0, atol=1e-5))
        self.assertTrue(np.allclose(normed.std(0), 1.0, atol=1e-3))

    def test_population_std_not_bessel_corrected(self):
        """numpy's default is ddof=0 and torch's is ddof=1. The original reads
        the array as numpy, so a Bessel-corrected port is quietly wrong."""
        values = np.arange(8, dtype=np.float32)[:, None]
        expected = (values - values.mean(0)) / (values.std(0) + T.INSTNORM_EPS)
        self.assertTrue(np.allclose(T.instance_norm(values), expected))
        bessel = (values - values.mean(0)) / (values.std(0, ddof=1) + T.INSTNORM_EPS)
        self.assertFalse(np.allclose(T.instance_norm(values), bessel))

    def test_a_constant_channel_survives_the_epsilon(self):
        values = np.ones((32, 4), np.float32)
        self.assertTrue(np.isfinite(T.instance_norm(values)).all())

    def test_a_window_is_not_normalised_on_its_own(self):
        """Normalising per window instead of per track would make the same
        second of music decode differently depending on where it was cut."""
        rng = np.random.default_rng(5)
        track = rng.standard_normal((1000, 8)).astype(np.float32)
        track[:500] *= 10.0
        whole = T.instance_norm(track)[500:]
        piece = T.instance_norm(track[500:])
        self.assertFalse(np.allclose(whole, piece, atol=1e-3))

    def test_rank_is_checked(self):
        with self.assertRaises(ValueError):
            T.instance_norm(np.zeros((4, 4, 4), np.float32))


class TimeResampling(unittest.TestCase):
    def test_identity_when_the_length_already_matches(self):
        values = np.arange(20, dtype=np.float32).reshape(10, 2)
        self.assertIs(T.resample_time(values, 10), values)

    def test_half_pixel_convention(self):
        """`align_corners=False`: output i reads source (i + 0.5) * scale - 0.5,
        clamped at 0. Getting this wrong shifts every code by a frame."""
        values = np.arange(10, dtype=np.float32)[:, None]
        self.assertTrue(np.allclose(T.resample_time(values, 5).ravel(),
                                    [0.5, 2.5, 4.5, 6.5, 8.5]))

    def test_upsampling_clamps_at_both_edges(self):
        values = np.arange(4, dtype=np.float32)[:, None]
        out = T.resample_time(values, 8).ravel()
        self.assertAlmostEqual(float(out[0]), 0.0, places=5)
        self.assertAlmostEqual(float(out[-1]), 3.0, places=5)
        self.assertTrue(np.all(np.diff(out) >= -1e-6))

    def test_zero_frames_is_refused(self):
        with self.assertRaises(ValueError):
            T.resample_time(np.zeros((4, 1), np.float32), 0)

    def test_twenty_five_hertz_is_a_whole_number_of_frames(self):
        """MERT-v2-FullSong runs at 25 Hz natively, so a track whose length is a
        multiple of 960 samples resamples by the identity."""
        self.assertEqual(T.MERT_RATE // T.FRAME_RATE, 960)
        for seconds in (1, 7, 30):
            samples = seconds * T.MERT_RATE
            self.assertEqual(int(round(samples / T.MERT_RATE * T.FRAME_RATE)),
                             samples // 960)


class Tiling(unittest.TestCase):
    def test_short_track_is_one_window(self):
        self.assertEqual(T.tile_starts(300), [0])

    def test_hop_is_half_a_window_with_a_pulled_back_tail(self):
        self.assertEqual(T.tile_starts(1200), [0, 256, 512, 688])
        self.assertEqual(T.tile_starts(750), [0, 238])

    def test_exact_multiple_needs_no_pull_back(self):
        self.assertEqual(T.tile_starts(T.WINDOW), [0])
        self.assertEqual(T.tile_starts(T.WINDOW + T.HOP), [0, 256])

    def test_zero_frames_is_refused(self):
        with self.assertRaises(ValueError):
            T.tile_starts(0)

    def test_no_frame_is_left_unwritten(self):
        """A gap would leave a frame at code 0 — silence spliced into the middle
        of a recording. Only the pulled-back final window may overlap what came
        before it, and it wins, which is `joint_v6.predict`'s own behaviour."""
        for frames in (1, 100, 512, 513, 750, 1200, 2049, 9000):
            starts = T.tile_starts(frames)
            written = np.zeros(frames, np.int64)
            for start in starts:
                length = min(T.WINDOW, frames - start)
                low = start + (0 if start == 0 else T.TRIM)
                high = start + length - (0 if start + length >= frames else T.TRIM)
                written[low:high] += 1
            self.assertTrue((written >= 1).all(), f"{frames} frames: a gap at "
                                                  f"{int(np.argmin(written))}")
            overlap = np.flatnonzero(written > 1)
            if overlap.size:
                self.assertGreaterEqual(int(overlap.min()), starts[-1],
                                        f"{frames} frames: an overlap before the final window")
                self.assertNotIn(starts[-1], starts[:-1])


class Windowing(unittest.TestCase):
    """`predict_codes` end to end on a tiny head with known weights."""

    def setUp(self):
        self.rng = np.random.default_rng(11)
        self.weights = _random_weights(self.rng)
        self.head = _small_head(self.weights)

    def test_every_code_comes_from_a_windows_centre(self):
        """Two properties at once: the code is the argmax of the window that
        claimed the frame, and no frame is decided within 128 frames of an
        interior window edge — the reason for the trim in the first place."""
        frames = 1200
        features = self.rng.standard_normal((frames, T.FEATURE_DIM)).astype(np.float32)
        codes = T.predict_codes(self.head, features)
        self.assertEqual(codes.shape, (frames,))
        owner = np.full(frames, -1, np.int64)
        argmax = {}
        for start in T.tile_starts(frames):
            length = min(T.WINDOW, frames - start)
            window = features[start:start + length]
            if length < T.WINDOW:
                window = np.pad(window, ((0, T.WINDOW - length), (0, 0)))
            argmax[start] = np.array(
                mx.argmax(self.head(mx.array(window[None])), axis=-1))[0, :length]
            low = start + (0 if start == 0 else T.TRIM)
            high = start + length - (0 if start + length >= frames else T.TRIM)
            owner[low:high] = start                              # a later window wins
        self.assertTrue((owner >= 0).all())
        for frame in range(frames):
            start = int(owner[frame])
            length = len(argmax[start])
            self.assertEqual(int(codes[frame]), int(argmax[start][frame - start]))
            if start:
                self.assertGreaterEqual(frame - start, T.TRIM)
            if start + length < frames:
                self.assertGreaterEqual(start + length - frame, T.TRIM)

    def test_a_short_track_is_padded_not_stretched(self):
        features = self.rng.standard_normal((90, T.FEATURE_DIM)).astype(np.float32)
        codes = T.predict_codes(self.head, features)
        self.assertEqual(len(codes), 90)

    def test_progress_reports_every_window(self):
        seen = []
        features = np.zeros((1200, T.FEATURE_DIM), np.float32)
        T.predict_codes(self.head, features, progress=lambda k, n: seen.append((k, n)))
        self.assertEqual(seen, [(index + 1, 4) for index in range(4)])

    def test_cancellation_is_honoured(self):
        features = np.zeros((1200, T.FEATURE_DIM), np.float32)
        with self.assertRaises(InterruptedError):
            T.predict_codes(self.head, features, cancelled=lambda: True)

    def test_the_wrong_feature_width_is_refused(self):
        with self.assertRaises(ValueError):
            T.predict_codes(self.head, np.zeros((100, 64), np.float32))


class MertMixer(unittest.TestCase):
    def test_the_one_hot_sits_one_past_the_transformers_index(self):
        """transformers collects a hidden state only AFTER each conformer block,
        so hidden_states[k] is the output of layers[k]; lyra.mert's vector is one
        longer because index 0 is the pre-layer subsampled embedding. Verified
        against the real HF model: index 21 reproduces hidden_states[20] to
        1.135e-04, while 19, 20 and 22 are off by 7.3, 12.2 and 19.8."""
        weights = np.array(T._mixer_weights(24, 20))
        self.assertEqual(weights.shape, (25,))
        self.assertEqual(float(weights[21]), 1.0)
        self.assertEqual(float(weights.sum()), 1.0)

    def test_the_default_layer_is_the_head_metadata_layer(self):
        self.assertEqual(T.MERT_LAYER, 20)

    def test_a_layer_outside_the_stack_is_refused(self):
        for layer in (-1, 24, 99):
            with self.assertRaises(ValueError):
                T._mixer_weights(24, layer)


class CodeStatistics(unittest.TestCase):
    def test_a_collapsed_stream_reads_as_collapsed(self):
        stats = T.code_statistics(np.zeros(500, np.int64))
        self.assertEqual(stats["unique"], 1)
        self.assertEqual(stats["repeat"], 1.0)
        self.assertEqual(stats["run_max"], 500)
        self.assertEqual(stats["seconds"], 20.0)

    def test_a_healthy_stream_reads_as_healthy(self):
        codes = np.arange(750, dtype=np.int64) % 600
        stats = T.code_statistics(codes)
        self.assertEqual(stats["unique"], 600)
        self.assertEqual(stats["repeat"], 0.0)
        self.assertEqual(stats["run_mean"], 1.0)

    def test_frames_convert_at_twenty_five_per_second(self):
        self.assertEqual(T.code_statistics(np.zeros(250, np.int64))["seconds"], 10.0)

    def test_empty_is_refused(self):
        with self.assertRaises(ValueError):
            T.code_statistics(np.zeros(0, np.int64))


class Measurements(unittest.TestCase):
    def test_a_recording_measured_against_itself_is_zero(self):
        rng = np.random.default_rng(1)
        audio = rng.standard_normal(48000).astype(np.float32) * 0.1
        self.assertEqual(T.spectral_distance(audio, audio), 0.0)
        self.assertAlmostEqual(T.chroma_correlation(audio, audio), 1.0, places=5)

    def test_a_different_note_is_further_away_than_the_same_note(self):
        """Both measures have to rank a re-performance of the same note above a
        different note. Everything is mixed with the same noise floor: a log-mel
        distance between a bare tone and anything else is dominated by the empty
        bins, where the log of 1e-5 is not a musical fact."""
        rng = np.random.default_rng(2)
        time = np.arange(96000, dtype=np.float64) / 48000
        floor = lambda seed: 0.05 * np.random.default_rng(seed).standard_normal(len(time))  # noqa: E731
        tone = lambda hz, seed: (np.sin(2 * np.pi * hz * time) + floor(seed)).astype(np.float32)  # noqa: E731
        reference = tone(440, 1)
        same = tone(440, 2)                                      # a second take
        other = tone(880, 2)                                     # an octave up
        self.assertLess(T.spectral_distance(reference, same),
                        T.spectral_distance(reference, other))
        self.assertGreater(T.chroma_correlation(reference, same),
                           T.chroma_correlation(reference, other))
        del rng

    def test_a_non_finite_recording_is_refused(self):
        audio = np.zeros(48000, np.float32)
        audio[10] = np.nan
        with self.assertRaises(ValueError):
            T.log_mel(audio)


def pinned_stand_in(path: Path, source: dict) -> None:
    """A file of the PINNED size. Readiness is size-checked (Codex review,
    2026-09-22), so a one-byte placeholder IS a truncated download now.
    Sparse — instant, and it costs no disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(int(source["bytes"]))


class ThePack(unittest.TestCase):
    def test_the_head_is_not_offered_as_a_lora(self):
        """`pack_adapters()` globs the root and `user/`. A head in the root
        would appear in the studio's picker as if it were an adapter."""
        root = Path(tempfile.mkdtemp(prefix="yue2-pack-"))
        pinned_stand_in(root / P.HEAD_SUBDIR / P.TOKENIZER_HEAD,
                        P.HEAD_SOURCES[P.TOKENIZER_HEAD])
        pinned_stand_in(root / P.JOINT_NAR_ADAPTER, P.SOURCES[P.JOINT_NAR_ADAPTER])
        names = [entry["file"] for entry in P.pack_adapters(root)]
        self.assertIn(P.JOINT_NAR_ADAPTER, names)
        self.assertNotIn(P.TOKENIZER_HEAD, names)

    def test_adapter_readiness_does_not_depend_on_the_head(self):
        """An installed pack must not be declared broken by a new download."""
        root = Path(tempfile.mkdtemp(prefix="yue2-pack-"))
        for name, source in P.SOURCES.items():
            pinned_stand_in(root / name, source)
        self.assertEqual(P.lora_problems(root), [])
        self.assertEqual(P.head_problems(root), [P.TOKENIZER_HEAD])
        # …but the PACK is not whole until the head lands, which is the
        # question the Download button asks (Codex review, 2026-09-22).
        self.assertEqual(P.pack_problems(root),
                         [f"{P.HEAD_SUBDIR}/{P.TOKENIZER_HEAD}"])

    def test_the_optional_assets_are_not_a_gate(self):
        root = Path(tempfile.mkdtemp(prefix="yue2-pack-"))
        pinned_stand_in(root / P.HEAD_SUBDIR / P.TOKENIZER_HEAD,
                        P.HEAD_SOURCES[P.TOKENIZER_HEAD])
        self.assertEqual(P.head_problems(root), [])

    def test_the_head_and_the_adapter_come_from_one_revision(self):
        head = P.HEAD_SOURCES[P.TOKENIZER_HEAD]
        adapter = P.SOURCES[P.JOINT_NAR_ADAPTER]
        self.assertEqual(head["repo"], adapter["repo"])
        self.assertEqual(head["revision"], adapter["revision"])

    def test_every_pinned_part_carries_a_sha256_and_a_size(self):
        for name, source in {**P.SOURCES, **P.HEAD_SOURCES}.items():
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$", name)
            self.assertGreater(source["bytes"], 0, name)

    def test_the_module_agrees_with_the_pack_on_the_pinned_head(self):
        self.assertEqual(T.HEAD_SHA256, P.HEAD_SOURCES[P.TOKENIZER_HEAD]["sha256"])
        self.assertIn(T.HEAD_VERSION, P.TOKENIZER_HEAD)

    def test_head_path_lives_under_the_subdirectory(self):
        self.assertEqual(P.head_path("/tmp/pack"),
                         Path("/tmp/pack") / P.HEAD_SUBDIR / P.TOKENIZER_HEAD)


class RunnerArguments(unittest.TestCase):
    """The argv contract the panel will build."""

    def setUp(self):
        import yue2_run

        self.run = yue2_run

    def _args(self, *extra):
        return self.run.parse_args([
            "--model-dir", "/tmp/generator", "--vae-dir", "/tmp/vae",
            "--output", "/tmp/song.wav", *extra])

    def test_the_prompt_round_trips_through_argv(self):
        args = self._args("--audio-prompt", "/tmp/take.wav",
                          "--audio-prompt-seconds", "12.5",
                          "--audio-prompt-start", "30")
        self.assertEqual(args.audio_prompt, Path("/tmp/take.wav"))
        self.assertEqual(args.audio_prompt_seconds, 12.5)
        self.assertEqual(args.audio_prompt_start, 30.0)

    def test_the_default_listen_is_ten_seconds(self):
        self.assertEqual(self._args().audio_prompt_seconds, 10.0)
        self.assertIsNone(self._args().audio_prompt)

    def test_a_missing_recording_is_refused_before_anything_loads(self):
        missing = Path(tempfile.mkdtemp()) / "nope.wav"
        self.assertEqual(self.run.main([
            "--model-dir", "/tmp/generator", "--vae-dir", "/tmp/vae",
            "--output", "/tmp/song.wav", "--style", "a test",
            "--audio-prompt", str(missing)]), 2)

    def test_an_absurd_duration_is_refused(self):
        real = Path(tempfile.mkdtemp()) / "take.wav"
        real.write_bytes(b"not really a wav")
        for seconds in ("0", "600"):
            self.assertEqual(self.run.main([
                "--model-dir", "/tmp/generator", "--vae-dir", "/tmp/vae",
                "--output", "/tmp/song.wav", "--style", "a test",
                "--audio-prompt", str(real), "--audio-prompt-seconds", seconds]), 2)

    def test_a_prompt_and_a_variation_do_not_mix(self):
        real = Path(tempfile.mkdtemp()) / "take.wav"
        real.write_bytes(b"not really a wav")
        artifacts = Path(tempfile.mkdtemp())
        (artifacts / "result.json").write_text("{}")
        self.assertEqual(self.run.main([
            "--model-dir", "/tmp/generator", "--vae-dir", "/tmp/vae",
            "--output", "/tmp/song.wav", "--audio-prompt", str(real),
            "--from-artifacts", str(artifacts), "--variation", "take"]), 2)


class TheCommandLine(unittest.TestCase):
    def test_encode_refuses_a_round_trip_without_the_models(self):
        code = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "music" / "yue2_tokenizer.py"), "encode",
             "/tmp/nothing.wav", "--out", "/tmp/codes.npy", "--roundtrip", "/tmp/out.wav"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        self.assertEqual(code.returncode, 2)
        self.assertIn(b"--model-dir", code.stdout)

    def test_check_reports_the_head_separately(self):
        root = Path(tempfile.mkdtemp(prefix="yue2-pack-"))
        for name, source in P.SOURCES.items():
            pinned_stand_in(root / name, source)
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pinokio" / "music_lora_fetch.py"),
             "--root", str(root), "--check"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        payload = json.loads(done.stdout.decode())
        self.assertTrue(payload["ready"])
        self.assertFalse(payload["head_ready"])
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["head_missing"], [P.TOKENIZER_HEAD])


if __name__ == "__main__":
    unittest.main(verbosity=2)
