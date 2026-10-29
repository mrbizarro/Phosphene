# Images

The Images tab makes stills. Two kinds of engine live here: **Reference Edit**, which reworks an image you give it, and **Ideogram 4**, which puts words on a picture.

## Choosing the engine {#engines}

| Engine | Use it for |
|---|---|
| **Auto (use Settings)** | whatever Settings picks for this Mac |
| **Reference Edit — Fast** | image-to-image in 4 steps, about 1:20, several references — the default |
| **Reference Edit — Standard** | 8 steps, about 2:05, no LoRA |
| **Reference Edit — Quality** | 40 steps, about 3:50 — for final renders |
| **Ideogram 4 — typography & layout** | text in the image, with an optional reference |

The pill next to the menu says whether that engine is downloaded. An engine that needs more memory than this Mac has is greyed out and labelled *needs a N GB Mac*.

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

Choose **FLUX 3 Q8 / Standard** to make an image from text, or add reference images to edit them. Describe the complete scene you want. Standard uses 16 steps. **Base Quality** uses a separate model at 50 steps and takes longer.

Q8 is the recommended quality. Q6 uses less memory; Q4 supports 24 GB Macs with a small image size and one reference. 32 GB or more is recommended. The first run downloads about 9.9 GB for Q8, 7.6 GB for Q6 or 5.3 GB for Q4, plus the image engine. Stop cancels setup or rendering; a later run resumes downloads. After that, an image takes about 50 seconds on an M4 Max with 64 GB.

The model uses the FLUX Kommunity License, separate from the app's MIT licence. Read and accept it on first use. Commercial output generation is permitted for users below USD 5 million gross annualized revenue including affiliates, subject to the full terms. Review images before sharing them and make any legally required AI disclosures.

Your images appear in the gallery. Choose **Animate** to send one to image-to-video. FLUX.1 and Qwen LoRAs cannot be used with this model.
