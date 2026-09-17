# Remix

Remix is the last chip on the Video tab — *motion control · refs · color*. It holds four tools that start from **your own media**. Click it, then choose the tool in the row that opens; it remembers the last one you used.

## Ingredients {#ingredients}

*2–8 refs → one clip.* Several reference images — a face, a prop, a location — composed into one new clip.

1. Drop 2 to 8 images into **Reference images** (or click one from *Recent uploads*). They tile into one reference sheet.
2. The main prompt describes what is in the sheet; **Action / shot to generate** describes the shot.
3. Optional: a **Character** with **Identity strength** (0.8–1.8; about 1.3 is the sweet spot).

> **Needs LTX-2.3** Ingredients' reference adapter has no LTX-2.5 release yet. On LTX-2.5 the chip is greyed out and says so; use Image mode with **Inspire** instead, or install the 2.3 pack.

## Motion Control {#motion-control}

*Its motion → your scene.* The render copies the motion, camera move, composition and pose of a clip you supply, while your prompt repaints the subject and the scene.

1. Pick the clip in **Control video** (or paste its path). The output has that clip's size and length.
2. Write the new subject and scene. Leave the camera out of the prompt — the clip already carries it.

An ordinary video works. A pose, depth or edge sequence you already have follows more tightly — Phosphene does not make those sequences for you. It works best on shots whose content changes (camera moves, wide scenes) and worst on a static, high-contrast single subject. On LTX-2.5 the motion and framing still transfer, but the prompt has much weaker control over the new subject — the panel shows that warning.

## Colorize {#colorize}

*B&W clip → color.* Pick the clip in **Source video to colorize** and describe the colours to paint in. The output keeps the source's size and length.

## Upscale & Face Fix {#upscale-face-fix}

*Any clip → 2× sharper, face kept.* Re-renders a clip at twice its size with generated detail — not a filter — and keeps the face and the sound. Made for Hailuo H3 drafts, works on any clip. (Earlier versions called this **LTX Upscale** / **Upscale ×2**.)

**One click:** press **Upscale & Face Fix** under the player, on an Outputs card, on a finished row in the queue history, or on the Editor's clip bar. It queues the **Face Fix** recipe for that clip with the clip's own prompt and seed. The fixed clip is a **new file** next to the original, which is not changed. From the Editor, a line above the timeline offers to **swap it in** when it lands — same cut, same in and out points — or to keep the old one.

**With settings:** the small button beside **Upscale & Face Fix** under the player opens the clip here, in Remix.

1. Pick the clip in **Clip to fix**.
2. Choose a preset:

| Preset | What it does | Time for 5 s at 640×384 on an M4 Max |
|---|---|---|
| **Face Fix** | one refine step from the clip — sharper, the face is not redrawn (default, and what the one-click button runs) | about 2.5 min |
| **Faithful** | three refine steps — sharper detail, the face can shift a little | about 5.5 min |
| **Quick** | two refine steps, a touch softer | about 3.75 min |
| **Re-imagine** | a full re-render — the sharpest, and faces drift | about 7 min |

3. Generate. The sound, the length and the frame count are kept, and (if you leave it empty) the prompt comes from the source. The result is capped at this Mac's Image-to-Video size. A 5 s 1024×576 source takes much longer (about 20 min for Face Fix).

It needs the **LTX-2.5 Pixel Spatial Upscaler** adapter (0.3 GB) — download it from the Models window. A source already near the cap is refused, because it cannot grow enough to be worth the render.

Ingredients, Motion Control and Colorize run on the fast Q4 model — no Q8 download is needed.
