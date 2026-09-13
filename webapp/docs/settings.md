# Settings

The gear in the header opens Settings. Changes are saved to `panel_settings.json` when you press **Apply** and affect every new render; files already in the gallery are not re-encoded.

## Output format {#output-format}

How finished clips are encoded.

| Choice | One line |
|---|---|
| **Standard** | visually lossless to almost everyone, about 7 MB per 5 s clip |
| **Video production (lossless)** | for grading and editing elsewhere, about 50 MB per 5 s clip |
| **Web / social** | the smallest files, about 3 MB per 5 s clip |
| **Custom** | set the pixel format and CRF yourself: 0 is lossless, 18 visually lossless, 23 the web default, 28 and up visibly lossy |

## Memory / speed {#memory-speed}

- **Live preview** — *On* shows the shot as it forms in the Now card. It never changes the result (the file is byte-for-byte the same either way); turning it off also removes the **Stop early** button.
- **Hailuo H3 model** (when H3 is installed) — **Automatic** picks the best this Mac can hold; **Full (bf16)** peaks around 42 GiB; **Compact (Q8)** peaks around 21 GiB, about half the memory for a little more time. On a Mac too small for Full, that option says so and is disabled.

## Model files {#model-files}

**Verify model files (checksum)** checks every installed weight file against its published checksum and offers a one-click re-download of any that are damaged. It takes 1–2 minutes.

## Storage {#storage}

Shown when this disk holds weights this version does not render with by default. Each pack lists its size and a **Remove** button, which asks first. Removing a pack never touches your renders in `mlx_outputs`.

## API tokens {#tokens}

Saved on this Mac only, and sent nowhere except as sign-in to the site they belong to.

- **CivitAI API key** — needed to install LoRAs from CivitAI. **save & test** checks it.
- **Hugging Face token** — needed for gated models; read access is enough.

## Spicy mode (adult content) {#spicy}

Off by default: the CivitAI browser hides NSFW results and the **Show NSFW** switch. **Enable Spicy mode** asks for a second click, then lets you choose per search.

## Appearance {#appearance}

**Dark**, **Light** or **System** (follows the Mac, live). Saved in this browser only.

## Completion alerts {#alerts}

A chime when a render finishes, and a browser notification when the tab is in the background once you allow it. **Send a test** tries it.

## Anonymous usage analytics {#analytics}

Sends anonymous counts — version, hardware class, engine, tier, resolution, the first line of an error. Never your prompts, file names, paths, images or video; a copy of what is sent is kept in `state/usage-log.jsonl`. **Turn off** stops it.
