## About this fork

This fork adds audio-to-video mode for low memory pipeline.

Adds A2V Distilled Pipeline (a2vid_distilled.py) in phosphene root for
Q4-distilled audio-to-video generation. No ltx-2-mlx modifications
required — imports from stock ltx-pipelines-mlx package.

Key differences from the standard A2V path:
- a2vid_distilled.py lives in phosphene root (not inside ltx-2-mlx)
- mlx_warm_helper.py imports from the local a2vid_distilled module
- Distilled path (generate_a2v_distilled) passes audio_conditioning_scale
  (built into a2vid_distilled.py)
- Non-distilled path (generate_a2v) omits audio_conditioning_scale
  (upstream A2VidPipelineTwoStage does not accept it)
- patch_ltx_codec.py is unchanged (codec patch only)



<p align="center">
  <img src="assets/phosphene_banner.png" alt="Phosphene" width="100%">
</p>

<p align="center">
  <strong>Generative video, image, and character training on your Mac.</strong><br>
  MLX. No PyTorch, no CUDA, no cloud, no API key.<br>
  <a href="https://x.com/PhospheneAI">@PhospheneAI</a> on X · <a href="https://github.com/mrbizarro/phosphene">github.com/mrbizarro/phosphene</a>
</p>

<p align="center">
<img width="1920" height="993" alt="image" src="https://github.com/user-attachments/assets/0c504d54-1666-4a64-8872-7d471682c8f0" />

</p>

> **Current release: v3.0.6.** Latest on the v3.0 line — character training, Audio-to-Video, the Image Studio tab, hardware capability tiering, and a run of stabilization fixes since launch.

## Overview

Phosphene is a local generative-media panel for Apple Silicon. It runs [LTX-Video 2.3](https://github.com/Lightricks/LTX-Video) (MLX port) for joint audio-and-video synthesis, [Qwen-Image-Edit-2509](https://huggingface.co/Qwen/Qwen-Image-Edit-2509) (with a Lightning 4-step fast tier) for stills, and ships an in-panel LoRA training pipeline for character identity (face + optional voice from a single dataset). Everything runs on-device. No cloud, no API keys, no telemetry.

3.0 introduces in-panel character training (face + voice LoRA from one dataset), the Audio-to-Video workflow, the Image Studio tab, hardware capability tiering, and an agentic prompt enhancer driven by the same local Gemma 3 12B used for auto-captioning.

A 7-second character clip with synced audio renders in roughly 6 minutes on an M4 Max 64 GB. The delivered file is **1280×720 HD** after the built-in 2× upscale; clips are generated at 1024×576 internally and upscaled before mux. Voice + face LoRAs from a 50-image dataset finish in ~3 hours on the same hardware.

The interface adapts to the machine it runs on. Under 48 GB of unified memory, the panel exposes only what fits in that envelope (text-to-video, image-to-video, and the Image tab). At 48 GB and above, character mode, first/last-frame keyframing, clip extension, and the Q8 HQ pipelines become available. Tier detection runs once at boot and the unsupported surfaces are hidden rather than greyed out.

## Features

### Video
<img width="872" height="141" alt="image" src="https://github.com/user-attachments/assets/f8e6d839-0ef5-4310-8b83-4f0a76a58779" />


Text-to-video, image-to-video, and audio-to-video, all delivered as MP4 with joint audio (lip-sync, footsteps, ambience) in a single diffusion pass. Output is 1280×720 after the built-in 2× upscale. Character mode renders against the Q8 dev transformer with a fused character LoRA; the server-side validator refuses Q4 + character to prevent silent identity drift. First/last-frame keyframing and clip extension are available on the Q8 surface, with TeaCache wired through both.

### Image Studio
<img width="1920" height="843" alt="image" src="https://github.com/user-attachments/assets/8e2f52de-b34c-44cf-9283-df30c4079607" />


Qwen-Image-Edit (Lightning 4-step) is the default image engine. It handles instruction edits ("change the white jacket to red") and multi-subject composition with up to three reference images, generating four candidates per shot in seconds with the Lightning LoRA baked in. It's an optional one-click install (the panel falls back to a lighter mflux model if Qwen isn't installed). Results drop cards into a unified gallery, each with an Animate button that pre-fills the I2V form with the source still.

### Train Character

<img width="877" height="864" alt="image" src="https://github.com/user-attachments/assets/6e0564d9-6912-4d99-a9b0-1f2a2b01453e" />



End-to-end LoRA training inside the panel. The dataset uploader accepts 15 to 500 images per character. Captions are written by a local Gemma 3 12B (MLX, 4-bit) in roughly 90 seconds for a 37-image dataset, in the `[VISUAL]: <trigger>, <description>` format the LTX trainer expects. The default recipe is rank 32, alpha 32, 100 epochs, lr 1e-4, 512 px resolution, letterbox crop; total step count auto-scales with the dataset (e.g. 50 images → 5000 steps, 100 images → 10000 steps) so adding photos doesn't shift the trained-epochs target. Power users can override any of those in an advanced section. Optional voice LoRA from the same training run.

<img width="871" height="640" alt="image" src="https://github.com/user-attachments/assets/a25bfc78-671e-4972-bf51-231ae5f5cc04" />

The Train tab also exposes **Style** training (experimental in v3.0) — same end-to-end pipeline, different intent: a curated set of movie frames teaches the model an aesthetic (color grading, lighting, composition) rather than an identity. The trained style LoRA stacks with character LoRAs at render time. Lightly validated as of v3.0; please report rough edges via [GitHub Issues](https://github.com/mrbizarro/phosphene/issues).

### Audio-to-Video
<img width="876" height="557" alt="image" src="https://github.com/user-attachments/assets/e64b2a23-e3e2-4132-8ceb-838b0c089136" />

New workflow tab in 3.0. WAV or MP3 in, MP4 out — the audio drives motion in the generated video, and an optional reference image anchors frame zero. The pipeline runs in two stages: low-resolution with classifier-free guidance, then full-resolution with the distilled LoRA fused on top. The original input audio is muxed onto the final clip so the result is a single self-contained MP4. Requires Q8 + ≥64 GB unified memory.

### LoRAs

Drop `.safetensors` into `mlx_models/loras/` for immediate use, or browse and install LTX 2.3 LoRAs from CivitAI inside the panel (per-row rename, download, companion-aware delete). Character bundles live alongside style LoRAs and are filtered out of the regular picker so they don't show up twice.

### HTTP API

Everything the panel does is reachable over plain HTTP on `127.0.0.1:8198`. Queue video jobs, generate images, train characters, manage LoRAs, poll status, fetch outputs — all from `curl`, a Python script, or an external agent like Claude Code or Codex. The panel UI is just one client; nothing about the feature set is exclusive to it.

```bash
# Queue a text-to-video render with a character LoRA stacked on a style LoRA.
curl -s -X POST http://127.0.0.1:8198/queue/add \
  --data-urlencode "mode=t2v" \
  --data-urlencode "prompt=Cinematic close-up of bizarrotrn man in a wood-paneled study, golden hour" \
  --data-urlencode "width=1024" --data-urlencode "height=576" \
  --data-urlencode "frames=169" --data-urlencode "quality=high" \
  --data-urlencode 'loras=[{"path":"mlx_models/loras/bizarrotrn_v2.safetensors","strength":1.0}]'
# → {"ok": true, "id": "j-..."}
```

Endpoints cover the full lifecycle: `POST /queue/add` and `/run` for video, `POST /image/generate` for stills, `POST /train/start` for LoRA training, `POST /upload` for reference images and audio, `POST /characters/<id>/generate` for one-shot character renders, `POST /loras/refresh` and the CivitAI download endpoints for LoRA management, `GET /status` for queue and system state, `GET /outputs` for the gallery, `POST /queue/retry` / `/queue/remove` / `/queue/pause` / `/stop` for queue control. JSON in, JSON out, form-encoded POST bodies for the heavy endpoints. No auth — bound to loopback only.

Full reference with every field, every default, and copy-pasteable `curl` invocations: **[docs/API.md](docs/API.md)**. Notable uses: batch overnight render runs, IDE integrations, custom front-ends, and external agents that orchestrate Phosphene as a tool. The in-panel chat was retired in v3.0 — its replacement is your own agent talking to this API.

## Hardware

Apple Silicon only. MLX is Apple-only by design.

| RAM | Tier | What runs |
|---|---|---|
| Under 48 GB | Compact (Q4 surface) | Text and image-to-video at smaller sizes. Image tab works. Character, FFLF, Extend, and HQ are hidden. They need Q8. |
| 48 to 79 GB | Comfortable (Q8 surface) | The canonical tier, built on M4 Max 64 GB. Everything works. FFLF and Extend capped at 768 px long side. |
| 80 to 119 GB | Roomy | Most modes at full size. FFLF and Extend up to 1024 px. |
| 120 GB+ | Studio | No size limits. |

Working-memory footprint is non-negotiable: standard 1280×704 generation peaks at roughly 22 GiB resident, and HQ with the Q8 dev transformer at roughly 38 GiB. Tier is detected once at boot from RAM and exposed to the UI via `body[data-cap-tier="q4|q8"]`. Set `LTX_FORCE_CAP_TIER=q4` to preview the Compact surface from a higher-tier machine.

## Install

### Via Pinokio (recommended)

1. Install [Pinokio](https://pinokio.computer).
2. In Pinokio: **Discover** -> **Download from URL** -> paste `https://github.com/mrbizarro/phosphene`.
3. Click **Install**.
4. Click **Start** -> **Open Panel** -> http://127.0.0.1:8198.

Pinokio handles the hardware gate, the upstream `dgrauet/ltx-2-mlx` clone, the uv-managed Python 3.11 venv, the runtime patches, and the filtered model download (~28 GB: Q4 plus the Gemma encoder).

For the Q8 HQ tier (required for Character, FFLF, Extend), click **Download Q8** in the panel sidebar after first launch. About 37 GB, one time.

If you have a Hugging Face token, paste it under **Settings** in the panel. Downloads run roughly 10x faster, and the same token unlocks the gated LoRAs (HDR and Lightricks Control).

### Manual install

```bash
# 1. Clone Phosphene + the upstream MLX port (pinned to v0.14.8).
git clone https://github.com/mrbizarro/phosphene.git
cd phosphene
git clone https://github.com/dgrauet/ltx-2-mlx.git ltx-2-mlx
cd ltx-2-mlx && git checkout v0.14.8 && cd ..

# 2. Create the Python 3.11 venv inside ltx-2-mlx (uv-managed).
cd ltx-2-mlx
uv venv --python 3.11 --seed env

# 3. Install the MLX pipeline + trainer packages. Pin mlx to 0.31.1 —
#    0.31.2 attenuates the LTX vocoder by 22 dB.
./env/bin/uv pip install --python env/bin/python \
  'mlx==0.31.1' 'mlx-lm==0.31.1' 'mlx-metal==0.31.1'
./env/bin/uv pip install --python env/bin/python \
  ./packages/ltx-core-mlx ./packages/ltx-pipelines-mlx ./packages/ltx-trainer
./env/bin/uv pip install --python env/bin/python \
  pyyaml pydantic tqdm rich
# mlx-vlm powers Gemma 3 auto-caption. --no-deps so it doesn't drag mlx-lm past 0.31.1.
./env/bin/uv pip install --python env/bin/python --no-deps 'mlx-vlm==0.4.4'
# Agent + downloader + hub pin range.
./env/bin/pip install pillow numpy 'huggingface-hub>=1.5.0,<2.0' \
  'hf_transfer>=0.1.6' 'litellm>=1.83.14' 'smolagents>=1.24.0'
cd ..

# 4. Apply the runtime patches (idempotent, fail loud on upstream drift).
./ltx-2-mlx/env/bin/python3.11 patch_ltx_codec.py

# 5. Download the Q4 LTX weights + the Gemma 3 4-bit encoder (~28 GB total).
HF_HUB_ENABLE_HF_TRANSFER=1 ./ltx-2-mlx/env/bin/hf download \
  dgrauet/ltx-2.3-mlx-q4 --local-dir mlx_models/ltx-2.3-mlx-q4
HF_HUB_ENABLE_HF_TRANSFER=1 ./ltx-2-mlx/env/bin/hf download \
  mlx-community/gemma-3-12b-it-4bit --local-dir mlx_models/gemma-3-12b-it-4bit

# 6. (Optional) Image tab — install mflux + apply the FBCache patch.
./ltx-2-mlx/env/bin/pip install 'mflux==0.17.5'
./ltx-2-mlx/env/bin/pip install --force-reinstall --no-deps 'mflux==0.17.5'
./ltx-2-mlx/env/bin/python3.11 patch_mflux_fbcache.py

# 7. (Optional) HiDream — separate one-time clone for the photoreal engine.
#    Clone HIDREAM-O1-MLX-LAB-active into your home directory, or set
#    HIDREAM_LAB_DIR to point at it.
#    git clone <hidream-lab-repo> ~/HIDREAM-O1-MLX-LAB-active

# 8. Launch the panel.
./ltx-2-mlx/env/bin/python3.11 mlx_ltx_panel.py
```

About the version pins: `mlx 0.31.2` attenuates the LTX vocoder by 22 dB. Stay on 0.31.1. `ltx-2-mlx` is pinned to `v0.14.8` — we track a known-good tag, never upstream `main`. `mflux 0.17.5` is the version `patch_mflux_fbcache.py` is line-targeted against.

## Interface

Four workflow tabs at the top of the panel: Video, Images, Audio, Train Character. Each is a single page; the helper subprocess and model state persist across tab switches.

<table>
<tr>
<td width="50%"><img src="assets/screenshots/phos_05_character_mode.png" alt="Video tab · Character mode — compact avatar picker"></td>
<td width="50%"><img src="assets/screenshots/phos_02_images_tab.png" alt="Images tab — multi-reference subject composition"></td>
</tr>
<tr>
<td align="center"><sub><b>Video / Character mode</b> · round-avatar picker, voice indicator, manage modal</sub></td>
<td align="center"><sub><b>Images</b> · Qwen-Image-Edit, multi-ref composition</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/screenshots/phos_03_audio_tab.png" alt="Audio tab — audio drives the generation"></td>
<td width="50%"><img src="assets/screenshots/phos_04_train_tab.png" alt="Train Character tab — dataset + auto-caption + voice LoRA"></td>
</tr>
<tr>
<td align="center"><sub><b>Audio</b> · voice or music clip drives generation; optional reference image anchors frame 0</sub></td>
<td align="center"><sub><b>Train Character</b> · drop 15-50 photos, Gemma 3 auto-captions, optional voice LoRA</sub></td>
</tr>
</table>

Prompting notes:

- Video / text mode: describe sound the same way you describe scene; the audio path reads the same prompt as the visual.
- Video / image mode: prompt with motion beats rather than describing the still. Roughly one beat per 2–3 seconds of clip length.
- Video / character mode: select an avatar from the picker, include the trigger word in the prompt. Q8 Draft (736×416) for iteration, Q8 Pro (1024×576 → 1280×720 final) for delivery.
- Images: zero to three reference slots. Empty zone is text-to-image. Qwen-Image-Edit instructions are read literally — "change the white jacket to red" preserves the rest of the scene.
- Train Character: center crop for tight portraits, letterbox for wide-shot proportions. The default preset (rank 32, alpha 32, 100 epochs, lr 1e-4) is validated end-to-end and recommended as the starting point.

## Migrating from 2.0

Quit Pinokio (or the panel terminal), then click Update, then Start. Renders, settings, queue, models, and LoRAs all persist across the upgrade via Pinokio's `fs.link` persistent drive. The first update takes a few minutes.

> Stragglers note: a few very old v2.x installs once had to click Update twice (Pinokio ran the stale on-disk `update.js` before pulling the new one). The update script now force-reinstalls the changed packages, so a single Update is enough. If a v2.x install still boots to dependency errors, click Update once more.

Behavioral changes worth noting in 3.0:

- Character is a first-class mode pill on the Video tab, no longer a chip nested inside T2V.
- Q8 HQ is the default quality for character renders. The server-side validator rejects Q4 + character combinations to prevent identity-degraded output.
- TeaCache is wired through both Extend and Audio-to-Video stage 1.
- Vertical-player chrome is positioned outside the right edge so 9:16 clips are no longer occluded by controls.
- Training presets now scale step count by `epochs × image_count`. The 100-epoch "high" preset that produced the validated v2 LoRAs preserves its shape regardless of dataset size.

## What's in the repo

- `mlx_ltx_panel.py` is the panel HTTP server. One file, around 22k lines, with HTML, CSS, and JS inlined as the page string. Worker thread plus helper subprocess management plus capability tier detection.
- `mlx_warm_helper.py` is the long-running inference subprocess. Holds T2V, I2V, Extend, HQ, and Keyframe pipelines. Reads job specs from stdin, emits events to stdout.
- `image_engine.py` dispatches the Image tab. Backends `hidream`, `mflux`, `mock`. Each spawns its own subprocess with `start_new_session=True` so `/stop` kills the whole tree.
- `patch_ltx_codec.py` applies one idempotent runtime patch: lossless H.264 output (yuv444p). As of the v0.14.8 pin the memory-frees, VAE streaming, Metal-watchdog and frame_rate patches are all native upstream.
- `lora_lab/` is vendored from the [`lora-lab`](https://github.com/mrbizarro/lora-lab) authoring tree. Training works out of the box; set `LTX_LORA_LAB_ROOT` to iterate against an external clone.
- `mlx_models/` and `mlx_outputs/` both persist across Pinokio Reset via fs.link.

`image_engine.py` also carries a `hidream` backend (8B Qwen3-VL backbone, unified pixel-patch transformer) for photoreal stills. It is **hidden in the UI since v3.0.3** ([#15](https://github.com/mrbizarro/phosphene/issues/15)) and not installed by default — it lives in a separate lab repo you clone manually into `$HIDREAM_LAB_DIR` (see Manual install, step 7) and is loaded on demand.

## License and credits

Panel: MIT, see [LICENSE](LICENSE). LTX-Video 2.3 weights: Lightricks' license. MLX: Apache 2.0. Gemma 3 12B: Google's terms. PiperSR: AGPL-3.0.

Phosphene depends on the following projects:

- [Lightricks](https://github.com/Lightricks/LTX-Video) — LTX 2.3 and the joint audio + video architecture
- [@dgrauet](https://github.com/dgrauet/ltx-2-mlx) — MLX port of LTX-Video; the foundation everything else builds on
- [Apple ML team](https://github.com/ml-explore/mlx) — MLX
- [HiDream-ai](https://huggingface.co/HiDream-ai/HiDream-O1-Image-Dev) — HiDream-O1 weights and reference implementation
- [filipstrand/mflux](https://github.com/filipstrand/mflux) — MLX-native FLUX and Qwen-Edit family
- [mlx-community](https://huggingface.co/mlx-community) — Gemma 3 12B 4-bit
- [ModelPiper / PiperSR](https://github.com/ModelPiper/PiperSR) — optional 2× upscale on the Apple Neural Engine
- [@cocktailpeanut](https://twitter.com/cocktailpeanut) — Pinokio

What Phosphene adds on top of those: a persistent batch queue, a warm helper subprocess with capability-tier feature gating, lossless H.264 output with JSON sidecars, the in-panel character + voice LoRA training pipeline, the Image tab dispatch layer with adaptive wall-time estimates, the local Gemma 3 prompt-enhancer, and the Pinokio install + update lifecycle scripts.

## Roadmap

Upcoming work — three-aspect character LoRAs, scene/room LoRAs for
location continuity across clips, a real multi-character workflow,
and stacking-aware strength balance — is tracked in [ROADMAP.md](ROADMAP.md).
Contributors and feature requests welcome via GitHub Issues.

## Support development

Phosphene is free and open source.

- Follow [@PhospheneAI](https://x.com/PhospheneAI) on X for releases and clips
- Patreon: https://www.patreon.com/PhospheneAI
- Issues and PRs: https://github.com/mrbizarro/phosphene

## Network note

Phosphene runs locally. No telemetry. A clean production install checks GitHub every 30 minutes for an update badge, and only touches Hugging Face or CivitAI when you download models or LoRAs. Disable the update check with `PHOSPHENE_DISABLE_VERSION_CHECK=1`. The panel binds to `127.0.0.1` with no auth. It's not designed for LAN exposure or tunneling.
