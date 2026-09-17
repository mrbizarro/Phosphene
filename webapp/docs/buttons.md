# Buttons and icons

The buttons that show a picture instead of a word. Hover any of them in the panel for its tooltip.

## The header {#header}

| Button | What it does |
|---|---|
| **LTX · 2.5** (engine switch) | chooses the engine that renders — LTX or Hailuo H3 |
| the health chip (a coloured dot and a memory reading) | opens the machine's state: Tier, Memory, Helper, Models, Queue, Render. Green is fine, yellow needs a look, red is blocking |
| the version pill | **Up to date**, **Update to …** (click to update), or **Restart Phosphene** (click to restart onto the new code) |
| the bug | **Report a bug** — a GitHub issue pre-filled with your version and log |
| [[icon:ph-gear-six]] | Settings |
| [[icon:ph-book-open]] **Docs** | these docs ([[sc:docs.open]] opens them on the shortcuts page) |
| [[icon:ph-star]] | star Phosphene on GitHub |
| the X mark | the official @PhospheneAI account on X |

## The player {#player}

| Button | What it does |
|---|---|
| **Params** | the settings the selected output was made with |
| **Upscale & Face Fix** | queues a 2× re-render of the clip that keeps the face and the sound; the fixed clip lands next to it. The small button beside it opens the [settings](#docs/remix/upscale-face-fix) |
| **Extend** · **To film** · **Animate** | use the selected output as the start of something new |
| the crossed-out eye (red) | hides this output from the gallery — the file is not deleted |
| **Expand** | full screen ([[sc:outputs.expand]]; Esc closes it) |

## Output cards {#cards}

| Button | What it does |
|---|---|
| **ⓘ** | how this output was made — prompt, engine, quality, seed, LoRAs |
| **Upscale & Face Fix** (on a video card, on hover) | queues the face-safe 2× of that clip — same as the player button |
| [[icon:ph-trash-simple]] | moves the file to the macOS Trash, after asking ([[sc:outputs.trash]] on the selected output) |
| [[icon:ph-folder-simple]] (Outputs header) | reveals the outputs folder in Finder |

## The queue and the Now card {#queue}

| Button | What it does |
|---|---|
| **×** on a queued job | removes it from the queue |
| **Face Fix** on a finished video in the history | queues Upscale & Face Fix for that clip |
| **×** on a failed or stopped card | dismisses the message |
| **Stop** (under Generate) | stops the render in progress |
| **Stop early** (on the live preview) | stops it after asking — nothing is saved, the queue carries on |

## The Editor {#editor}

| Button | What it does |
|---|---|
| 🔊 | mutes the preview only — the film is not changed ([[sc:editor.mute]]) |
| **▾** beside Render | Deliver as (format, size, finish) and **Export for Premiere / Resolve / AE** |
| **⋯** | Drafts and versions, Media pool, Auto-edit, Storyboard, Close |
| **i** | the preview is approximate at cuts; the render is exact |
| **Keys** | every key and gesture the timeline answers to |
| **+** on a media pool row | puts the clip at the end of the sequence |
| **▣** on a still | lays it over the picture at the playhead (the overlay lane) |
| the +0.25s label on a sound strip | the sound is that far out of sync — click it to put it back |

## Characters and LoRAs {#loras}

| Button | What it does |
|---|---|
| [[icon:ph-pencil-simple]] on the character strip | rename or delete characters |
| [[icon:ph-arrow-clockwise-bold]] on the character strip | rescan for new characters |
| [[icon:ph-pencil-simple]] on a LoRA | rename it (display name only) |
| [[icon:ph-download-simple]] on a LoRA | download the file — or, when marked **Update**, its newer version |
| [[icon:ph-arrow-square-out]] on a LoRA | open its page on CivitAI |
| [[icon:ph-x-bold]] on a LoRA | delete it from disk (asks first) |

## Storyboard cards {#storyboard}

| Button | What it does |
|---|---|
| **↑ / ↓** | move the shot earlier or later |
| the dice | a new seed for this shot |
| **✕** | delete the shot |
