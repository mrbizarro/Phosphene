# Images

The Images tab makes stills. Pick the model first: **Model**, at the top of the column, is the same picker the Video tab uses. Models here: **FLUX.2 [klein]**, the recommended first model, **FLUX 3 Klein**, **Reference Edit**, which reworks an image you give it, **Ideogram 4**, which puts words on a picture, **P-H3-Image**, which makes large photographs and posters from words alone, and **MiniMax-H3-Image**, MiniMax's own image model for detailed scenes from words, which also edits your pictures.

## Choosing the model {#engines}

| Model | Use it for | Versions (under the prompt) |
|---|---|---|
| **FLUX.2 [klein]** | pictures from words, edits from your photos; a ~4.3 GB first download. The default | one |
| **FLUX 3 Klein** | sharper pictures from words or references | Q8, Q6, Q4, each at Standard or Base Quality |
| **Reference Edit** | changing a picture you give it: a background, an outfit, several references combined | Fast (4 steps, about 1:20), Standard (8 steps, about 2:05, no LoRA), Quality (40 steps, about 3:50, for final renders) |
| **Ideogram 4** | text in the image, placed where you want it, with an optional reference | one |
| **P-H3-Image** | big photographs, film frames and posters from text, words spelled right; about 2.5 min at 4 MP | Draft, Standard, High |
| **MiniMax-H3-Image** | detailed pictures from a long, descriptive prompt, 4 to 16 megapixels, and edits of your pictures; preview | Draft (4 MP), Standard (8 MP), High (16 MP) |

The model's versions sit in **Version**, under the prompt; a model with one version just names it there. Phosphene remembers the version you last used for each model.

The pill next to **Version** says whether that version is downloaded. A version that needs more memory than this Mac has is greyed out and labelled *needs a N GB Mac*; a model none of whose versions fit is dimmed in the **Model** menu with the memory it needs.

On Macs with 16 GB of memory or less, **Auto** uses FLUX.2 [klein] 4B at 4-bit for a render without references if the engine chosen in Settings needs a bigger Mac; your Settings stay the same. Renders with reference images keep the Settings engine because Klein does not use references. If that engine cannot fit, remove the references so Auto can use Klein, or choose a lighter engine in Settings.

## Reference images {#references}

Reference Edit needs at least one picture: drop it into **Primary**, and up to two more into the **Multi-ref** slots (or click one from *Recent uploads*). With two or more, name them in the prompt — *the jacket from reference 1 and the street from reference 2*.

Then set **Aspect** (16:9 1280×720 by default, down to smaller, faster sizes), **Candidates (n)** — 1 to 8 images per run — and **Seed**, and press **Generate** ([[sc:prompt.generate]] from the prompt).

## Ideogram 4 — words on the picture {#ideogram}

The first render shows Ideogram 4's licence screen (non-commercial use only). After you accept, it downloads about 28 GB, once.

- **Simple** — a plain prompt. **Layout** — the visual canvas (below).
- **Render**: **Design** (illustration, graphic) or **Photographic** (camera realism).
- **Quality**: Default (20 steps), Turbo (12, faster), Quality (48, slower). **⚡ Fast mode** is lighter on memory for M1/M2 and smaller Macs.
- **Use reference** appears when a reference is loaded: Ideogram redraws it from a description rather than copying pixels. Leave it off and the reference is ignored — the panel warns you.
- **Image palette** sets up to 16 colours.

### The Layout canvas {#layout}

1. Choose **Layout**. Drag on the frame to draw a box, or use **Insert text** / **Insert object**. **Examples…** loads a poster, logo, label or meme layout.
2. With a box selected, set its **Region** (Text or Object), the **Text (literal words)** to render, an optional description, a **Style**, **Align** and **Color**.
3. Boxes snap to thirds, the centre and each other while **Snap** is on.

Keys on the canvas, with a box selected:

[[shortcuts:canvas]]

A Layout render needs at least one box.

## FLUX 3 Klein

Choose **FLUX 3 Klein** under **Model** and **FLUX 3 Q8 / Standard** in **Version** to make an image from text, or add reference images to edit them. Describe the complete scene you want. Standard uses 16 steps. **Base Quality** uses a separate model at 50 steps and takes longer.

Q8 is the recommended quality. Q6 uses less memory; Q4 supports 24 GB Macs with a small image size and one reference. 32 GB or more is recommended. The first run downloads about 9.9 GB for Q8, 7.6 GB for Q6 or 5.3 GB for Q4, plus the image engine. Stop cancels setup or rendering; a later run resumes downloads. After that, an image takes about 50 seconds on an M4 Max with 64 GB.

The model uses the FLUX Kommunity License, separate from the app's MIT licence. Read and accept it on first use. Commercial output generation is permitted for users below USD 5 million gross annualized revenue including affiliates, subject to the full terms. Review images before sharing them and make any legally required AI disclosures.

Your images appear in the gallery. Choose **Animate** to send one to image-to-video. FLUX.1 and Qwen LoRAs cannot be used with this model.

## P-H3-Image — large images from text, on MiniMax H3 {#p-h3-image}

P-H3-Image is Pruna AI's image model, built on MiniMax H3, the same model behind Hailuo H3 video. It makes one picture from a text prompt in 8 steps, with nothing to tune. It is strongest at photographs, film frames, crowds and posters whose quoted words must be spelled exactly. Hands and very small faces in the background can still go wrong. It does not use reference images yet: anything in the slots is ignored.

Choose **P-H3-Image** under **Model**, then a size in **Version**:

| Choice | Size | Time on an M4 Max, 64 GB |
|---|---|---|
| **P-H3-Image** (Standard) | 4 megapixels, e.g. 2368×1760 | about 2.5 min |
| **P-H3-Image Draft** | 2 megapixels | about 1.3 min |
| **P-H3-Image High** | 8 megapixels, e.g. 3360×2496 | about 5 min |

**Aspect** sets the shape; the size comes from the choice. It needs about 26 GB of free memory, so a Mac with 48 GB or more.

**First use.** It runs on the Hailuo H3 video pack, so install Hailuo H3 first. Then the chip next to **Version** reads *download ~18 GB*: click it. Phosphene shows the MiniMax H3 licence, then downloads P-H3-Image's own parts (adapter, text encoder, image decoder) once, from Pruna's repository at a pinned version, and checks every file. It needs about 24 GB free on the disk while it prepares, and keeps about 15 GB. The chip says *ready* when it is done. If it reads *update the H3 runner*, update Hailuo H3 first (your weights stay).

*P-H3-Image by Pruna AI, based on MiniMax-H3 by MiniMax, under the MiniMax H3 Community License; its text encoder is based on Qwen3-VL-4B (Apache-2.0). Powered by MiniMax H3. Fetched from Pruna's repository, never re-hosted; the only change made on your Mac is re-arranging the image decoder's tensors for the MLX runner (renamed and re-fused, values unchanged), noted inside the file. MiniMax H3 is licensed under the MiniMax H3 Community License Agreement, Copyright (c) 2026 MiniMax. All Rights Reserved.*

## MiniMax-H3-Image — MiniMax's own image model {#minimax-h3-image}

MiniMax-H3-Image is the image model of the H3 family, made by MiniMax. It makes one picture from a text prompt in 6 steps, with nothing to tune: no guidance setting and no negative prompt. It follows a long, detailed English description of the whole picture best (who, where, light, lens, any quoted words), so write the prompt as a paragraph rather than a list of tags. It is a preview. It also edits pictures you give it: see [Edit a picture](#minimax-h3-image-edit) below.

Choose **MiniMax-H3-Image** under **Model**, then a size in **Version**:

| Choice | Size |
|---|---|
| **Draft** | 4 megapixels, e.g. 2048×2048 |
| **Standard** | 8 megapixels |
| **High** | 16 megapixels, the size it works best at |

**Aspect** sets the shape; the size comes from the choice. A request below 4 megapixels is made on a 4 megapixel canvas and scaled back down, so it costs the same as Draft. The time for each choice is shown in the **Version** list; those are estimates until they have been measured on your Mac, and they follow your own renders after two. It needs a Mac with plenty of memory; Phosphene tells you if yours is too small.

**First use.** It shares the text encoder of the Hailuo H3 video pack, so install Hailuo H3 first. Then the chip next to **Version** reads *download ~66 GB*: click it. Phosphene shows the MiniMax-H3-Image licence, then downloads the original files from MiniMaxAI at a pinned version, checks every file, and converts them on your Mac one piece at a time. It needs about 30 GB free on the disk while it prepares, and keeps about 21 GB. The chip says *ready* when it is done. If it reads *update the H3 runner*, update Hailuo H3 first (your weights stay).

### Edit a picture with MiniMax-H3-Image {#minimax-h3-image-edit}

Drop a picture into **Picture 1** and say what to change: *"Make the clay in Picture 1 bright blue. Keep the hands, the light and the framing the same."* Picture 1 is the one that changes. Say what should stay as well as what should change; the model follows long, plain sentences best.

- **More pictures.** When Picture 1 is filled, another slot appears, up to **Picture 6**. Name them in the prompt: *"Put the hat from Picture 2 on the man in Picture 1."*
- **Shape.** While editing, **Aspect** starts on **Same as picture**: the result keeps the shape of Picture 1. Pick any other shape to reframe it; the model fills the new area.
- **Edit this picture.** On any finished picture, **More**, then **Edit this picture**, opens it here as Picture 1, so you can keep changing it.
- **Time.** Each picture adds to every step. One picture at Draft took about 5 minutes on an M4 Max with 64 GB (text alone takes about 2); the estimate next to **Version** counts your pictures.

Editing is free: one picture, several pictures combined, and changing the shape while editing.

*Powered by MiniMax-H3-Image (MiniMax). Fetched from MiniMaxAI, never re-hosted; the only change made on your Mac is quantising the model to 8 bit and precomputing its timestep tables for the MLX runner, noted inside the files. MiniMax-H3-Image is licensed under the MiniMax-H3-Image Community License Agreement, Copyright (c) 2026 MiniMax. All Rights Reserved.*
