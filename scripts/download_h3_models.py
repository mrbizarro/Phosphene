"""Download the reviewed H3 files from immutable Hugging Face revisions."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


SELECTIONS = (
    {
        "repo": "DeepBeepMeep/MiniMax-H3",
        "revision": "c4eddf2c7df0bb6e02f8beccd233747ead79d797",
        "files": ("MiniMax-H3-FL2VA-pruned_bf16.safetensors",),
        "destination": "deepbeep-pruned-bf16",
    },
    {
        "repo": "ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit",
        "revision": "32bfc37f1dc8bd331394573859a627bc0aa9822b",
        "files": (
            "text_encoder.safetensors",
            "video_vae.safetensors",
            "audio_vae.safetensors",
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.json",
            "merges.txt",
            "LICENSE",
            "NOTICE",
            "MODIFICATIONS.md",
        ),
        "destination": "ddalcu-q8",
    },
    {
        "repo": "MiniMaxAI/MiniMax-H3",
        "revision": "5d9b308a59ab12e67147f191e184baf704185bd1",
        "files": ("FL2VA/text_encoder/config.json", "FL2VA/model_index.json"),
        "destination": "upstream-meta",
    },
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="H3 model root; files are placed below its models/ directory",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    os.environ.setdefault("HF_HOME", str(root / "hf_home"))
    os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

    from huggingface_hub import hf_hub_download

    for selection in SELECTIONS:
        destination = root / "models" / selection["destination"]
        destination.mkdir(parents=True, exist_ok=True)
        for filename in selection["files"]:
            print(
                f"{selection['repo']}@{selection['revision']}: {filename}",
                flush=True,
            )
            hf_hub_download(
                repo_id=selection["repo"],
                filename=filename,
                revision=selection["revision"],
                local_dir=destination,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
