# FLUX 3 Klein

Local, free image generation and reference editing with **FLUX 3 Klein by Black Forest Labs**. Added in Phosphene 4.19.14.

In Images, choose FLUX 3 Q8 / Standard. With empty reference slots it makes an image from text; with references it edits or combines them. Describe the whole target scene, including the change. Standard uses the distilled checkpoint at 16 steps. Base Quality is a separate checkpoint with 50 steps and guidance 3.5; it takes longer and is not guaranteed to improve every prompt.

Q8 is the default quality. Q6 reduces the download and memory use; Q4 is for smaller Macs. The first Generate installs the worker and selected weights, showing progress in the job log and Now card. Stop cancels setup, downloads or generation. A later Generate resumes missing downloads. Completed images use the existing gallery sidecar and Animate action for image-to-video.

## Requirements and size

Apple Silicon, macOS 14 or later. Use 32 GB RAM or more for Q8/Q6. A 24 GB Mac can use Q4 at a small size with at most one reference. The UI has three reference slots; the API accepts up to ten on larger Macs. Canvases are limited to one megapixel, with dimensions divisible by 16. Close other memory-heavy apps before rendering.

| Weights | Standard download | Base download | Upstream peak RAM at 1024 square |
|---|---:|---:|---:|
| Q8 | 9.92 GB | 9.91 GB | 22.8 GB |
| Q6 | 7.63 GB | 7.62 GB | 20.6 GB |
| Q4 | 5.33 GB | 5.33 GB | 18.5 GB |

GB in this table means decimal GB. RAM figures are upstream measurements for distilled generation on a 64 GB M4 Pro, not Phosphene measurements. References add memory pressure. While installing or downloading, the worker keeps 5 GiB of the disk free, checks each destination volume and budgets another 3 GiB for its environment. Once installed, rendering only needs room for the new image. Pinned package wheels total about 264 MB to download, excluding Python itself. The environment's actual installed size still needs a fresh-install measurement.

## Isolation and downloads

The studio's existing dependency pins and FLUX.1/Qwen workers are unchanged. FLUX 3 uses Python 3.12, mflux 0.23.0 and MLX 0.32.4 in `flux3-worker/env`. Its complete dependency set is pinned in `scripts/pinokio/flux3_requirements.txt`. The panel reuses its existing installer PATH preparation, scrubs inherited Python/MPS settings and calls `scripts/pinokio/flux3_env.py`. Installation is locked across processes and every first use checks package versions without importing MLX into the studio.

The six mflux-community conversions are public, ungated derivatives of the official Klein models. Repository revisions, file sizes and weight hashes are recorded in `data/flux3_models.json`. Only that pinned file list is fetched. Downloads do not send a Hugging Face token. The worker verifies weight hashes before loading and passes the Base identity explicitly, so a snapshot's hash directory cannot be mistaken for the distilled model.

The worker follows `HF_HUB_CACHE`, then `HF_HOME`, then the normal Hugging Face cache. `PHOSPHENE_FLUX3_ROOT` can place its environment on another volume. No existing weights or outputs are removed. Library images record the checkpoint revision, precision, credit, elapsed render time, MLX peak and process RSS peak. Multi-candidate batches reload the model per image to keep memory bounded; RSS is the process high-water mark to that point.

FLUX.1 and Qwen LoRAs do not apply to FLUX 3. LoRA controls are unavailable for this worker.

## Model licence

The app remains MIT. The model weights use the **FLUX Kommunity License v1.0**, accepted separately before download or use. Commercial output generation requires gross annualized revenue below USD 5 million including affiliates, subject to the full terms. Other commercial terms come directly from Black Forest Labs.

Review outputs before sharing or displaying them, and disclose AI generation where required by law. The full licence and the required attribution notice are kept in [licenses/FLUX-Kommunity-1.0.txt](licenses/FLUX-Kommunity-1.0.txt) and [licenses/FLUX3-NOTICE.txt](licenses/FLUX3-NOTICE.txt), and copied next to downloaded weights. Recipients receive model rights directly from Black Forest Labs. The conversions are modified community models, not official Black Forest Labs products.

## Validation status

Measured on an M4 Max with 64 GB through the studio's own Generate path, launched with Pinokio's environment (2026-10-29), Standard Q6, 16 steps:

| Step | Measured |
|---|---|
| First Generate: engine install, 7.63 GB download, render | 178 s wall |
| Worker environment on disk | 1.1 GB |
| 1280 x 720 image, model loaded per image | 51-52 s (MLX peak 12.1 GB) |
| 1024 x 768 or 768 x 1024 image | 43-46 s (MLX peak 10.3 GB) |
| Reference edit, one reference, 1280 x 720 | 110 s (MLX peak 12.1 GB) |

The worker process stayed under 5.5 GB resident; the MLX peak is the figure to plan memory by. Q8 and Base Quality use the same code path but were not rendered on the test Mac for lack of disk space.

## Sources

- [mflux 0.23.0 FLUX 3 guide](https://github.com/mflux-community/mflux/blob/v.0.23.0/src/mflux/models/flux3/README.md)
- [Pinned mflux dependency manifest](https://github.com/mflux-community/mflux/blob/v.0.23.0/pyproject.toml)
- [mflux-community Q8 conversion](https://huggingface.co/mflux-community/flux-3-image-klein-mflux-q8)
- [Black Forest Labs licence](https://huggingface.co/black-forest-labs/flux-3-image-klein/blob/main/LICENSE.md)
