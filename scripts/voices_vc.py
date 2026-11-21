#!/usr/bin/env python3
"""Convert speech tokens to a saved voice; preserve the picture and audio clock.

Runs in the pinned Speech environment, after the video engine has exited.
The panel supplies the separated dialogue. The residual soundtrack is retained.
Weights are local, pinned, downloaded only by --setup; no global HF cache.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

PACKS = {
    "chatterbox": ("mlx-community/chatterbox-fp16", "4923fcca09086356aeab5191a2348c5a17a23694"),
    "tokenizer": ("mlx-community/S3TokenizerV2", "e0c9886f0e1c35ae85b1f27277416fb19fc72bec"),
}
REQUIRED = {"chatterbox": ("config.json", "model.safetensors", "tokenizer.json"),
            "tokenizer": ("config.json", "model.safetensors")}


def ready(root: Path) -> bool:
    try:
        receipt = json.loads((root / "ready.json").read_text())
        return (receipt == {key: list(value) for key, value in PACKS.items()}
                and all((root / key / filename).is_file()
                        and (root / key / filename).stat().st_size > 0
                        for key, files in REQUIRED.items() for filename in files))
    except (OSError, ValueError):
        return False


def setup(root: Path):
    from huggingface_hub import snapshot_download
    for key, (repo, revision) in PACKS.items():
        snapshot_download(repo, revision=revision, local_dir=str(root / key),
                          allow_patterns=["*.json", "*.safetensors", "LICENSE*"])
    (root / "ready.json").write_text(json.dumps(PACKS))


def read_pcm(path: Path, rate: int, channels: int = 1):
    import numpy as np
    result = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
        "-ac", str(channels), "-ar", str(rate), "-f", "f32le", "-"],
        check=True, capture_output=True, timeout=120)
    return np.frombuffer(result.stdout, np.float32).reshape(-1, channels).T.copy()


def write_pcm(path: Path, data, rate: int):
    import numpy as np
    data = np.atleast_2d(data).astype(np.float32)
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "f32le",
        "-ar", str(rate), "-ac", str(data.shape[0]), "-i", "pipe:0", str(path)],
        input=data.T.tobytes(), check=True, capture_output=True, timeout=120)


def fit_length(wave, count):
    import numpy as np
    return np.pad(wave[:count], (0, max(0, count - len(wave))))


def mux_picture(source: Path, audio: Path, output: Path):
    # Decoding AAC exposes its padded final frame. Without an explicit end
    # time, re-encoding that PCM length grows every converted clip by ~20 ms.
    duration = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
        text=True, timeout=30).strip()
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(source),
        "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", duration, "-movflags", "+faststart",
        str(output)], check=True, capture_output=True, timeout=120)


def convert(source: Path, stem: Path, voice: Path, output: Path, models: Path):
    import numpy as np
    import mlx.core as mx
    import huggingface_hub
    from mlx_audio.tts.utils import load_model
    from mlx_audio.tts.models.chatterbox.s3tokenizer import log_mel_spectrogram

    # mlx-audio's post-load hook normally fetches this tokenizer by repo ID.
    # Bind that one read to our pinned local copy in this subprocess only.
    download = huggingface_hub.snapshot_download
    def local_tokenizer(repo_id, **kwargs):
        if repo_id != PACKS["tokenizer"][0]:
            raise RuntimeError("Unexpected voice conversion model request")
        return str(models / "tokenizer")
    huggingface_hub.snapshot_download = local_tokenizer
    try:
        model = load_model(str(models / "chatterbox"))
    finally:
        huggingface_hub.snapshot_download = download

    def tokens(wave):
        mel = mx.expand_dims(log_mel_spectrogram(mx.array(wave)), 0)
        return model._s3_tokenizer(mel, mx.array([mel.shape[2]]))

    ref24 = read_pcm(voice, 24000)[0][:240000]
    ref16 = read_pcm(voice, 16000)[0][:160000]
    ref_tokens, ref_lengths = tokens(ref16)
    conditioning = model.s3gen.embed_ref(ref_wav=mx.array(ref24)[None], ref_sr=24000,
        ref_speech_tokens=ref_tokens, ref_speech_token_lens=ref_lengths)
    speech = read_pcm(stem, 16000)[0]
    # Fixed windows bound memory for long films. Source tokens retain their
    # 25 Hz clock; no text generation, tempo change or word re-timing occurs.
    converted = []
    for offset in range(0, len(speech), 10 * 16000):
        chunk = speech[offset:offset + 10 * 16000]
        if float(np.sqrt(np.mean(chunk ** 2))) < 1e-5:
            converted.append(np.zeros(round(len(chunk) * 1.5), np.float32))
            continue
        speech_tokens, _ = tokens(chunk)
        wave = np.array(model.s3gen(speech_tokens=speech_tokens,
                                   ref_dict=conditioning, finalize=True)).reshape(-1)
        converted.append(fit_length(wave, round(len(chunk) * 1.5)))
    with tempfile.TemporaryDirectory(prefix="voice-convert-") as tmp:
        tmp = Path(tmp)
        write_pcm(tmp / "converted.wav", np.concatenate(converted), 24000)
        original = read_pcm(source, 44100, 2)
        vocals = read_pcm(stem, 44100, 2)
        count = original.shape[1]
        vocals = np.stack([fit_length(channel, count) for channel in vocals])
        replacement = fit_length(read_pcm(tmp / "converted.wav", 44100)[0], count)
        # Retain the separated ambient/music residual and match speech level.
        gain = min(10.0, float(np.sqrt(np.mean(vocals ** 2))) /
                   max(1e-6, float(np.sqrt(np.mean(replacement ** 2)))))
        mixed = np.clip(original - vocals + replacement[None] * gain, -1, 1)
        write_pcm(tmp / "mix.wav", mixed, 44100)
        mux_picture(source, tmp / "mix.wav", output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--setup", action="store_true")
    for name in ("source", "stem", "voice", "output"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.setup:
        setup(args.models)
    else:
        if not all((args.source, args.stem, args.voice, args.output)):
            parser.error("source, stem, voice and output are required")
        convert(args.source, args.stem, args.voice, args.output, args.models)


if __name__ == "__main__":
    main()
