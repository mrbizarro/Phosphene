"""Short-lived timing worker; Whisper is released before LTX loads."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from word_timing import Ear, load_audio

if __name__ == '__main__':
    from huggingface_hub import snapshot_download
    model = Path(sys.argv[3])
    if not (model / 'config.json').is_file() or not (model / 'weights.safetensors').is_file():
        snapshot_download('mlx-community/whisper-large-v3-turbo',
            revision='a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb', local_dir=str(model),
            allow_patterns=['config.json', 'weights.safetensors'])
    words, _ = Ear(str(model)).hear(load_audio(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(words))
