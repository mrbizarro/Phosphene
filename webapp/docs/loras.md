# LoRAs

A LoRA is a small add-on that teaches a model a face, a style, a motion or a speed-up. The **LoRAs** section of the Video form (it moves into Images too) is where you turn them on.

## The picker {#picker}

- The summary reads *none active*, or how many are installed and active.
- Click a row to turn a LoRA on or off. Turning one on adds its first trigger word to your prompt; the trigger chips on an active row add them again.
- **strength** — a slider from -2 to 2. It starts at the LoRA's recommended strength, or 1.0.
- With five or more LoRAs, a filter box finds them by name or trigger word.
- The row buttons rename it (display name only), download the file, open it on CivitAI, or delete it from disk (asks first — it is permanent).
- **Write a guide** has the planner model write what the LoRA does, how to prompt it, and a strength to start from.
- **?** means its family is unknown — it may or may not work on this engine. **Update** means CivitAI has a newer version (**Check for updates** asks).

LoRAs are per engine: an LTX LoRA cannot load on Hailuo H3 and an H3 LoRA cannot load on LTX. The picker shows the active engine's library and offers *Show N from other modes*.

## Browse CivitAI and Hugging Face {#browse}

**Browse CivitAI** opens the browser.

1. Pick the source — **CivitAI** or **Hugging Face** — and the engine: **All**, **LTX** or **Hailuo H3**.
2. Search (a name, a style, a creator; on Hugging Face also `author:someone` or `owner/repo`) and press Enter.
3. **Install** on a result. It lands in `mlx_models/loras/` and turns on in the picker — unless it is for the other engine, in which case it says which engine to switch to.

CivitAI needs an API key to download: paste it in the browser's banner or in Settings → **API tokens**. Hugging Face results can be filtered by kind (Characters, Styles, Motion, Speed) and **With example**; read the repo before you install.

## Adding a file yourself {#import}

- **LTX** — copy `.safetensors` files into `mlx_models/loras/` and press the rescan button.
- **Hailuo H3** — **Import H3 LoRA** takes a `.safetensors` file (up to 4 GB) and reports how it was read and its recommended strength.

## Stacking LoRAs on Hailuo H3 {#h3-stacking}

- On an H3 install that stacks, up to **4 LoRAs** per render, each with its own strength, and Turbo rides along. Keep the strengths' total near 1.5 or under, and avoid two LoRAs that pull the same thing (two faces, two styles).
- On an older H3 install there is **one adapter slot**: the **Adapter** row chooses **Turbo** or **My LoRA** for this render. Picking more than the install allows stops the job with a message saying how many to un-pick.
