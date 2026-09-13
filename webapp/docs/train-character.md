# Train Character

Train Phosphene on one person's face — and, optionally, their voice — from your own photos. The result is a character you can put in any LTX shot. Training needs at least 24 GB of memory and runs on LTX-2.3 (the tab downloads what it needs).

## The steps {#steps}

1. **Train type** — **Character** (the face, and voice, of one person) or **Style** (a cinematic look, colour, lighting; experimental).
2. **Dataset** — drop **15 to 50 photos** (up to 500) — PNG, JPG or WEBP, with matching `.txt` captions if you have them — or a ZIP of paired images and captions. Each preview shows the square crop the trainer sees. *How to train a character well* covers the mix of shots, light and backgrounds that works.
3. **Captions** — see below.
4. **Trigger word** — a rare, letters-only word the model ties to this character. **↻ Suggest** makes one. Avoid digits: they split into common tokens.
5. **Quality preset** — see below.
6. **Crop strategy** — **Center crop** for tight portraits, **Letterbox** to keep wide-shot proportions.
7. **Voice** (optional) — see below.
8. Check the estimate — **Estimated wall time**, **Peak RAM** — and press **Train Character**.

## Captions {#captions}

Each image needs a caption in the form `[VISUAL]: trigger, 50–80 words` followed by `[TEXT]: None`; missing tags are added for you. **Auto-caption with Gemma 3** writes one for every image (2–3 seconds each) and overwrites existing captions. A thumbnail marked **no cap** falls back to a caption made from the trigger word alone.

Captions cannot be edited inside the tab: to change one, upload a corrected `.txt` (or ZIP). Training asks before it starts if captions are missing or very short.

## Presets, and what "identity graded" means {#presets}

| Preset | On a Mac with 64 GB or more |
|---|---|
| **Quick** | ~30 epochs, rank 8, 512px — a look, not a face · identity ungraded |
| **Medium** | ~60 epochs, rank 16, 576px — more capacity · identity ungraded |
| **High** | ~100 epochs, rank 32, 512px — validated for identity (recommended) |

**Identity graded** means the recipe has actually been measured on real faces and holds them. Only **High** on a 64 GB+ Mac is. Quick and Medium are for a fast look or a style, not a person.

On Macs under 64 GB, training runs a compact profile (fewer steps, lower rank), and even "High" there is **not** the graded recipe — the note under the presets says so. **Advanced** exposes rank, steps, learning rate, resolution and caption strategy.

When a run finishes, Phosphene measures the result. A character that came out weak or carries nothing gets a **WEAK** or **DEAD** badge and a banner with advice — usually, train again on High.

## Voice {#voice}

Upload one clean clip — 10 to 25 seconds of a single speaker, MP3, WAV, M4A or FLAC, up to 50 MB — and tick **Train voice LoRA**. **Audio steps**: Smoke 100, Standard 250 (default) or Long 500. The button becomes **Train Character + Voice**.

## Using a character in Video {#use-in-video}

Trained characters land in `mlx_models/loras/` and appear in two places:

- **The Character mode** on the Video tab (LTX): pick the face from the strip. Voiced characters carry a music-note badge. Set **strength** (0–2, default 1.0), or split it into separate **face** and **voice** strengths. **No voice** keeps the face and drops the speech.
- **The LoRA picker**, marked **Trained**, like any other LoRA.

Use the trigger word in the prompt — *mrztrn man walking on the beach*. A trained character needs the full model: with one active, **Quick** and **Standard** are greyed out and the render uses High. The pencil on the strip renames or deletes a character; the trigger word itself cannot change.

Clicking a character chip under *Use your trained characters* on the Train tab switches to the Video tab in Text mode with that character's LoRA on at strength 1.0, and fills an empty prompt with its trigger word.
