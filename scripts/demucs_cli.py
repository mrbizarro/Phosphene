"""Relocatable Demucs CLI with FFmpeg I/O, independent of torchcodec."""
import subprocess


def save_audio(wav, path, samplerate, bitrate=320, clip='rescale', bits_per_sample=16,
               as_float=False, preset=2):
    import numpy as np
    from demucs.audio import prevent_clip
    samples = prevent_clip(wav, mode=clip).detach().cpu().numpy().T.astype(np.float32)
    suffix = str(path).lower()
    codec = (['-c:a', 'libmp3lame', '-b:a', f'{bitrate}k'] if suffix.endswith('.mp3') else
             ['-c:a', 'flac', '-sample_fmt', 's32' if bits_per_sample == 24 else 's16'] if suffix.endswith('.flac') else
             ['-c:a', 'pcm_f32le' if as_float else 'pcm_s24le' if bits_per_sample == 24 else 'pcm_s16le'])
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'f32le', '-ar', str(samplerate),
                    '-ac', str(samples.shape[1]), '-i', '-', *codec, str(path)],
                   input=samples.tobytes(), check=True)


if __name__ == '__main__':
    import demucs.separate
    demucs.separate.save_audio = save_audio
    demucs.separate.main()
