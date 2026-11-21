#!/usr/bin/env python3
"""Speaker fingerprints for Re-voice's identity gate (runs in the Speech venv).

    speech-mlx/.venv/bin/python scripts/voice_identity_embed.py <base model dir> <out.json> <audio>...

Loads ONLY the speaker encoder (ECAPA-TDNN, ~76 tensors) from the Qwen3-TTS
Base checkpoint the Speech pack already installed: no download, no talker, a
few hundred MB at most. Writes {"<audio path>": [floats] | null}. A file too
short or silent maps to null; the gate then falls back to pitch alone.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RATE = 24000


def decode(path: str):
    import numpy as np
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', path, '-ac', '1', '-ar', str(RATE),
                          '-f', 'f32le', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def voiced(audio):
    """Drop the silences between words: the fingerprint is of the voice, not the room."""
    import numpy as np
    hop = RATE // 50
    if len(audio) < hop:
        return audio
    frames = audio[:len(audio) // hop * hop].reshape(-1, hop)
    energy = np.sqrt((frames ** 2).mean(axis=1))
    keep = energy > energy.max() * 10 ** (-30 / 20)
    return frames[keep].reshape(-1)


def encoder(model_dir: Path):
    import mlx.core as mx
    from mlx_audio.tts.models.qwen3_tts.config import Qwen3TTSSpeakerEncoderConfig
    from mlx_audio.tts.models.qwen3_tts.speaker_encoder import Qwen3TTSSpeakerEncoder
    from mlx_audio.tts.models.qwen3_tts.qwen3_tts import check_array_shape_qwen3
    config = json.loads((model_dir / 'config.json').read_text()).get('speaker_encoder_config') or {}
    fields = Qwen3TTSSpeakerEncoderConfig.__dataclass_fields__
    model = Qwen3TTSSpeakerEncoder(Qwen3TTSSpeakerEncoderConfig(**{k: v for k, v in config.items() if k in fields}))
    weights = []
    for file in sorted(model_dir.glob('*.safetensors')):
        for key, value in mx.load(str(file)).items():
            if not key.startswith('speaker_encoder.'):
                continue
            if 'weight' in key and value.ndim == 3 and ('conv' in key or 'speaker_encoder.fc' in key) \
                    and not check_array_shape_qwen3(value):
                value = mx.transpose(value, (0, 2, 1))
            weights.append((key[len('speaker_encoder.'):], value))
    if not weights:
        raise SystemExit('No speaker encoder in ' + str(model_dir))
    model.load_weights(weights, strict=True)
    model.eval()
    return model


def main(argv):
    import mlx.core as mx
    from mlx_audio.tts.models.qwen3_tts.qwen3_tts import mel_spectrogram
    model_dir, out, paths = Path(argv[1]), Path(argv[2]), argv[3:]
    model = encoder(model_dir)
    result = {}
    for path in paths:
        try:
            audio = voiced(decode(path))
        except subprocess.CalledProcessError:
            result[path] = None
            continue
        if len(audio) < RATE // 2:
            result[path] = None
            continue
        mels = mel_spectrogram(mx.array(audio), n_fft=1024, num_mels=128, sample_rate=RATE,
                               hop_size=256, win_size=1024, fmin=0, fmax=12000)
        vector = model(mels)
        mx.eval(vector)
        result[path] = [float(x) for x in vector.reshape(-1).tolist()]
    out.write_text(json.dumps(result))


if __name__ == '__main__':
    main(sys.argv)
