# Phosphene

AI video with sound, songs and images, made locally on your Mac.

<p align="center">
  <img src="assets/the_commuter_hero.gif" alt="The Commuter, a 75-second One Shot made in Phosphene" width="100%">
</p>

“The Commuter”: a 75-second One Shot rendered overnight on one Mac with Hailuo H3, from a single designed still. [More clips](https://phosphene.video/gallery/).

[Website](https://phosphene.video) · [Docs](https://phosphene.video/docs/) · [Benchmarks](https://phosphene.video/benchmarks/) · [Changelog](https://phosphene.video/changelog/) · [@PhospheneAI](https://x.com/PhospheneAI)

**Current release: v4.19.13.** See what's new in the [changelog](https://phosphene.video/changelog/).

Phosphene is a free, open-source app for Apple Silicon Macs, built on Apple's MLX by Mr Bizarro.
Renders run offline, and your prompts, pictures and videos stay on your Mac.
Optional extras download when a feature first needs them.

## What it does

- **Video with LTX-2.5:** turn text or a picture into video with sound from the same prompt.
  Set first and last keyframes, Extend a clip, Retake one moment, or lip-sync a face to your voice or song.
  Upscale & Face Fix and [Motion Control](docs/MOTION_CONTROL.md) are included. Adapts from 16 GB of memory.

- **Scenes with Hailuo H3:** make scenes with dialogue you can hear.
  Use keyframes, extend a shot, lip-sync, or add sound to a silent clip.
  About 75 GB, one click from the Pinokio sidebar. Needs 36 GB+ memory.
  Its licence excludes the EU, UK, South Korea and US.

- **Music Studio:** YuE2 makes songs with vocals from lyrics and a style.
  Make another take or work on the score. About 11 GB, installed inside the panel. Needs 24 GB+ memory.

- **Images:** Qwen-Image-Edit and FLUX.2 via mflux make images and edit them in words, like “make the jacket red”.
  Use up to three reference pictures, then send a still straight to video.

- **Storyboard, One Shot and the Editor:** build a film from one prompt or shot by shot.
  Cut it on the Editor timeline with music and room tone.

- **Train a character:** train a face and voice LoRA on your Mac from your own photos and recordings.

- **Scripts and agents:** queue renders through the [local HTTP API](docs/API.md).
  There's also an [image layout API for agents](docs/AGENT_API.md).

- **Prompt help:** Google's Gemma runs locally for Enhance, training captions and the storyboard planner.
  Enhance sharpens a prompt.

<p align="center">
  <img src="https://github.com/user-attachments/assets/0c504d54-1666-4a64-8872-7d471682c8f0" alt="The Phosphene panel" width="100%">
</p>

## Hardware

Apple Silicon only, M1 or newer. Intel Macs and Windows are not supported.

| Memory | What to expect |
| --- | --- |
| 16–24 GB | LTX-2.5 video from text or a picture at smaller sizes, and images. Songs from 24 GB. |
| 32–36 GB | Everything above. Hailuo H3 from 36 GB. |
| 48–64 GB | LTX keyframes, Extend, trained characters with Q8 weights, and the High tier. |
| 96 GB+ | Most modes at full size (available from 80 GB). Size limits come off at 120 GB. |

Phosphene is built on an M4 Max with 64 GB. See the [benchmarks](https://phosphene.video/benchmarks/).

**Disk:** a fresh install downloads 27.5 GB before Start, then about 10.7 GB in the background after the first start.
Hailuo H3 (~75 GB) and the music engine (~11 GB) are optional.

## Install

Today, install through [Pinokio](https://pinokio.computer):

1. Install Pinokio.
2. Open **Discover → Download from URL** and paste `https://github.com/mrbizarro/phosphene`.
3. Press **Install**.
4. Press **Start**, then open the panel.

To update, press **Update** in Pinokio, then restart Phosphene.
See the [full install guide](https://phosphene.video/docs/install/) for details, or the [manual install](docs/MANUAL_INSTALL.md) to do it by hand.

A free [native Mac app](https://phosphene.video/download/) is coming soon.
It can reuse models your Pinokio install already downloaded.

[Linux on Apple Silicon](docs/LINUX.md) is maintained by @joshuaswarren on a best-effort basis.
It is not installed by Pinokio.

## Where we're going

- A free native Mac app, so you won't need Pinokio.
- Pro, for people who use Phosphene every day or earn money with it.
- More models. Bringing new open models to the Mac first is the point. New ports reach everyone free; Pro may get them up to 24 hours early.
- Everything available today stays free.

## Pro

We love open source. Phosphene will always be open source and well maintained.
If you can afford it, Pro keeps the lights on: faster renders, priority support and more.

Pro is coming soon. Nothing is on sale yet. [Join the waitlist](https://phosphene.video/pricing/).

## Privacy

No prompt, picture, video or filename is uploaded; the panel listens on `127.0.0.1` only, with no login, so don't expose it to a network.
Phosphene checks GitHub for updates and contacts Hugging Face or CivitAI only when you download models or LoRAs.
Anonymous usage counts (version, hardware class such as “M4 Max / 64 GB”, and render stats) are on by default;
one click in Settings turns them off, and [every field is documented](docs/ANALYTICS.md).

## Contributing

[Bug reports](https://github.com/mrbizarro/phosphene/issues) and PRs are welcome.
A report with your chip, macOS version, Phosphene version and the error line is worth as much as a patch.
Contributors are credited in the [release notes](https://github.com/mrbizarro/phosphene/releases). See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

- Lightricks for LTX-Video 2.x, and @dgrauet for ltx-2-mlx, the MLX port Phosphene builds on.
- MiniMax for Hailuo H3, and Apple ML research for MLX.
- LightX2V for lightx2v v1.3, the adapter for Best on High; TaoLive (TaoMate-H3) and @kijai for the 3-step Fast adapter.
- @filipstrand for mflux; Multimodal Art Projection for YuE2 and @vanch007 for its MLX port.
- mlx-community for Gemma on MLX, and @cocktailpeanut for Pinokio.

Thanks to @blackest, @tanis2000, @joshuaswarren, @sahilkashyap64, @Morac2, @ybekocak,
@PhantombrainM, @Viktorminator, @Townesvz, @Sebartus, @mrapolo2020, @Techy2023,
and everyone who filed a bug.

## License

Phosphene's own code is [MIT](LICENSE). The AI models are not part of this repo and each has its own licence.
Phosphene shows a model's licence before download or first run when the licence requires it.

Ideogram 4, YuE2's weights, FLUX.1 Krea [dev] and Bria FIBO are non-commercial.
YuE2 lets individual creators sell songs they make. Hailuo H3's licence excludes the EU, UK, South Korea and US.
Check a model's licence before using its output commercially. [Model licence texts](LICENSES/).
