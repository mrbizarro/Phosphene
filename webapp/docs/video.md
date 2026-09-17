# Video

The Video tab turns a prompt — and, if you like, an image or a clip — into a video with sound. Pick a mode, write the prompt, choose Quality and Length, press **Generate** ([[sc:prompt.generate]] from the prompt box).

## Modes {#modes}

The chips across the top of the form. Each one says what it takes.

| Mode | Takes | What you get |
|---|---|---|
| **Text** | a prompt | a clip from words alone |
| **Character** | a trained character + a prompt | your character's face (and voice) in a new shot — LTX only. See [Using a character](#docs/train-character/use-in-video) |
| **Image** | a reference image + a prompt | a clip that starts from, or is inspired by, your picture |
| **FFLF** | a start frame and an end frame | a clip that travels from one to the other |
| **Keyframes** | 3–8 frames | a clip that passes through each |
| **Extend** | a clip | more of that clip, after or before it |
| **Remix** | your own media | Ingredients, Motion Control, Colorize, Upscale & Face Fix — see [Remix](#docs/remix) |

Hailuo H3 renders **Text** and **Image**; the other modes are LTX's.

### Image: Anchor or Inspire {#anchor-inspire}

Drop a PNG, JPG or WEBP into **Reference image** — it is cover-cropped to the render size. On LTX-2.5 a **Reference use** row appears:

- **Anchor** — *animate this image*. Your picture is frame one and the clip moves it.
- **Inspire** — *new shot from it*. The clip takes the character or look into a new scene, and you will not see your picture as frame one. Good for "this character, new scene".

On Hailuo H3 the image is always the first frame.

### FFLF and Keyframes {#fflf}

- **FFLF** — **Start frame (frame 0)** opens the clip and its shape sets the output size; **End frame (last frame)** closes it. A close-up at the end holds a face through the clip.
- **Keyframes** — choose **3 keyframes** to **8 keyframes**. Start and End are fixed; the others are the beats in between.
- Both need the LTX High add-on (the Q8 model) and pick High quality on their own. If it is missing, Generate says so and tells you where to install it.

### Extend {#extend}

Pick the **Source video**, set **Extend by (seconds)** (0.5 to 10, default 2) and **Direction** (After or Before). Extend has its own two qualities:

- **Q8 Draft** — 12 steps, safe on a 64 GB Mac.
- **Q8 Pro** — 30 steps, wants 96 GB or more.

Stay on Q8 Draft unless the Mac has the memory.

## Writing the prompt {#prompt}

Describe the scene **and the sound**. Sound is generated with the picture; a prompt with no sound in it comes out near silent.

- **Enhance** (LTX) rewrites your prompt in the style the model was trained on.
- **No music** asks for voice, sound and ambience without a score.
- **No voice** appears when the selected character has a voice: the face still locks, the audio stays ambient.

### The Avoid box {#avoid}

**Avoid +** opens a second box for what the model should *not* make — *blurry hands, distorted fingers, warped text*.

> **When it works** Avoid applies on **High**, **High · 720p**, **Extend**, **FFLF** / **Keyframes** and **Audio**. **Quick**, **Balanced** and **Standard** run without guidance and ignore it. On Hailuo H3 there is no Avoid box: write refusals as plain sentences in the prompt, and only for what H3 adds unasked — camera drift and on-screen text.

## Quality and Length on LTX {#ltx-quality}

Each Quality chip shows its size and an estimate for this Mac. A chip reads *unavailable* when this Mac cannot run it, or names the download it needs.

| Quality | Size | What it is |
|---|---|---|
| **Quick** | 640×448 | the fastest look at the shot |
| **Balanced** | 1024×576 | 16:9 at the size most things are watched — the default |
| **Standard** | 1280×704 | the largest canvas the fast lane serves — bigger, not more detailed |
| **High** | 1024×576 | a second, larger pass over the first: sharper detail and steadier motion, for about twice the wait |
| **High · 720p** | 1280×704 | the High pass at 720p — the most detail LTX makes; measured peak 49.7 GB, so it wants a 64 GB Mac |

Quick, Balanced and Standard run on the fast (distilled) model; High and High · 720p need the High add-on (the Q8 model).

**Length**: 3s, 5s (default), 7s, 10s — and 20s, which is offered at Quick only.

On the fast qualities, a **Speed** row offers **Tuned** or **Fast draft** — a faster schedule that changes the take. High has a **STG — detail guidance** slider.

With a trained character selected the chips become **Q8 Draft** (704×384), **Q8 Pro** (1024×576, *best identity*) and, on LTX-2.5, High and High · 720p. Trained faces hold best on Q8 Pro.

## Quality and Length on Hailuo H3 {#h3-quality}

| Quality | Size | Notes |
|---|---|---|
| **Draft** | 640×384 | quick look — faces and fine detail only resolve at Standard and High |
| **Standard** | 768×448 | the workhorse |
| **High** | 1024×576 | true 16:9, the recommended delivery size |
| **Native** | 1344×768 | the most detail H3 can make — worth it with Turbo on, a long wait without |

**Length**: 3s, 5s (the longest single pass), **10s** and **15s**. 10 s and 15 s are chained from 5-second windows at render time, so choose the length before you render — there is no Extend afterwards.

### Turbo and Steps {#turbo}

- **Speed: Standard / Turbo.** Turbo is a 4-step adapter: much faster, and it overrules Steps. If it is not downloaded yet, the chip shows its size.
- **Steps: Auto / 12 / 16 / 20.** More steps, a longer wait (20 is the official reference recipe, about 2.4× Auto).
- On H3 installs whose runner has a single adapter slot, an **Adapter** row asks whether that slot goes to **Turbo** or **My LoRA** — they cannot both run. See [LoRAs on H3](#docs/loras/h3-stacking).

### Per-window prompts {#windows}

A 10 s or 15 s H3 clip asks every 5-second window for the same prompt, which can repeat the action. Open **Per-window prompts** and turn on **One line per window**: 10 s gives two boxes, 15 s three. A box left empty uses the main prompt.

### H3 first frame and orientation {#h3-image}

Image mode works on H3 — the picture is the first frame. Under **Customize**, **Orientation** turns the tier's canvas **Portrait** at the same cost.

## Engines side by side {#engines}

| | LTX | Hailuo H3 |
|---|---|---|
| Modes | all Video modes, Remix, Audio | Text, Image |
| Sound | joint with the picture | joint video, dialogue and sound |
| Characters and LoRAs | trained characters, LoRA stacks | its own H3 LoRA library |
| Avoid box | on High and the guided modes | none — say it in the prompt |
| Longest clip | 20s (Quick), 10s otherwise | 15s, chained windows |
| Memory | the Tier dialog (health chip → Tier) shows which qualities this Mac runs | 36 GB or more (see below) |

**H3 and memory.** The full H3 engine needs 60 GB. From 36 GB up, H3 runs on its compact Q8 engine, which **Install Hailuo H3** in Pinokio's Phosphene sidebar builds locally (about 5 minutes, ~22 GB, no extra download). Settings → **Hailuo H3 model** chooses between Automatic, Full and Compact. Below 36 GB, H3 is not offered — render on LTX. See [Troubleshooting](#docs/troubleshooting/memory).

## Upscale and export {#upscale}

After the render, the clip can be resized without cropping.

**On Hailuo H3** (under Customize → **Upscale**):

- **Native** — as rendered.
- **720p fit** / **1080p fit** — scaled and padded to fit. 720p fit is the default.
Below the sizes, **Also run Upscale & Face Fix after the draft** is optional and off by default. When it is ticked, the draft ships as rendered and a second job re-renders it at twice the size with LTX-2.5, keeping the face and the sound; the fixed clip lands next to the draft. It needs the 0.3 GB Upscale adapter, downloaded from the Models window, and takes about the draft's time again.

**On LTX** (under Customize → **Export**): **Native** (default), **720p fit**, or **2×**. When it is not Native, **Method** chooses **Fast** (instant) or **Sharp** (a sharper upscaler, +30–90 s).

Any finished clip can also go through [Upscale & Face Fix](#docs/remix/upscale-face-fix) later — one click under the player.

## More controls {#more}

- **Seed** — `-1` is random; reuse a seed to get the same take with a changed prompt.
- **LoRAs** — the add-ons picker. See [LoRAs](#docs/loras).
- **Orientation** (LTX) — 16:9 or 9:16.
- **Customize** — width × height, duration and frames, and **Open file when done**.
- **⊞ Batch** — paste many prompts and queue them all.
