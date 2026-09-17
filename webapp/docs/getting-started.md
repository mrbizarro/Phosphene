# Getting started

Phosphene makes video, images and sound on your Mac — nothing is uploaded, nothing is rented. You describe a shot, it goes into a queue, and the finished file lands in Outputs.

## The tabs {#tabs}

The row of tabs at the top of the left column is where you choose what to make. [[sc:tabs.switch]] switches between them from the keyboard.

| Tab | What it is for |
|---|---|
| **Video** | A clip from a prompt — or from an image, a first and last frame, keyframes, a clip to extend, or your own media (Remix). See [Video](#docs/video). |
| **One Shot** | One continuous shot of 30 seconds to 2 minutes that never cuts. See [One Shot](#docs/one-shot). |
| **Images** | Stills: edit a reference image, or put words on a picture with Ideogram 4. See [Images](#docs/images). |
| **Storyboard** | Plan a whole film from one idea, render every shot, grade them, cut them. See [Storyboard](#docs/storyboard). |
| **Editor** | A timeline: trim, split, move sound, add titles and music, render the film. See [Editor](#docs/editor). |
| **Audio** | A video driven by a sound file you bring — a voice, a song. See [Audio](#docs/audio). |
| **Train Character** | Teach Phosphene a face (and a voice) from your photos. See [Train Character](#docs/train-character). |

## The engine {#engine}

The switch at the top right of the header chooses which model renders: **LTX** or **Hailuo H3**. They are two different engines with different strengths, not a good one and a spare:

- **LTX** — every Video mode, LoRAs and trained characters.
- **Hailuo H3** — joint video, dialogue and sound. Text and Image modes.

The form changes with the engine: controls one engine does not use are hidden, and the Quality and Length chips show that engine's own sizes. The switch only appears when this Mac can run more than one engine. [How they compare](#docs/video/engines).

## The queue {#queue}

Everything you generate goes into one queue and renders one job at a time. The panel at the bottom has four tabs:

- **Now** — the render in progress, with a live preview of the shot as it forms. **Stop early** (on the preview) asks first, then stops it: *"nothing is saved"*, and the queue carries on with the next job.
- **Queue** — the jobs waiting. The × on a card removes it.
- **Recent** — what finished, filtered by All / Videos / Photos.
- **Logs** — the render log. See [Reading the log](#docs/troubleshooting/logs).

Under the Generate button: **⊞ Batch** pastes many prompts at once and queues them all; **Pause queue** holds the queue (the button then reads **Resume queue**); **Clear** removes every waiting job — the one running carries on.

## Outputs {#outputs}

The gallery on the right shows what you have made. Click a card to put it on the player.

- **All / Videos / Photos** filter it, and the search box finds outputs by prompt words, model, LoRA, size or seed ([[sc:search.focus]] jumps to it).
- **Show all** loads older renders than the newest 60.
- [[sc:outputs.step]] steps through the gallery, [[sc:player.toggle]] plays and pauses, [[sc:outputs.expand]] expands the player to full screen.
- Under the player, the action row offers what you can do next with that clip: **Extend** it, run **Upscale & Face Fix** on it, send it **To film**, **Animate** a still, see its **Params**.
- The trash button on a card moves the file to the macOS Trash after asking — restore it from Finder if you change your mind. [[sc:outputs.trash]] does the same for the selected output.

## Where files go {#files}

| What | Where |
|---|---|
| Your renders | the `mlx_outputs` folder inside the Phosphene install. The folder button in the Outputs header opens it in Finder; Pinokio's **Outputs** item opens it too. |
| Trained characters and downloaded LoRAs | `mlx_models/loras/` |
| Settings | `panel_settings.json` |

Every render also writes a small sidecar file beside it with its settings. That is what **Params** reads, and what lets Outputs search by LoRA or seed.

## Updating {#update}

- The version pill in the header says where you stand: **Up to date**, or **Update to** a newer version. When one is out, a banner offers **Update now** / **Later**.
- In Pinokio, the **Update** item in the Phosphene sidebar updates everything, including Python dependencies. Updating keeps your queue, settings and models.
- After an in-app update the banner reads *"Updated to … — restart to finish"*: click **Stop**, then **Start** in Pinokio. If the update touched dependencies, it tells you to use Pinokio's **Update** instead.
- If an update started from a very old version only seems to move the panel, click **Update** once more — an old version updates its updater first.

> **Tip** Press [[sc:docs.open]] anywhere to open these docs on the shortcuts page, and use the search box on the left to find any button by the word written on it.
