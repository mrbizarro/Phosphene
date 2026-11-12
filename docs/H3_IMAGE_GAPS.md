# MiniMax-H3-Image: what the model can do vs what Phosphene ships

Sources: the official MiniMax-H3-Image model card and `model_index.json` (tasks `t2i` and `edit`),
MiniMax's recommended server code (SGLang `minimax_h3_image` pipeline, presentation, reference
encoding, image noise) and the ComfyUI MiniMax-H3 nodes, read in `ports/minimax-h3-image/study.md`.
Our MLX 8-bit port: `Mrbizarro/MiniMax-H3-Image-MLX-8bit`, engine `minimax_h3_mlx/h3_image.py`.

Studio 5.0.18 baseline = before this work. "Now" = this branch.

| Capability (model) | Upstream | Studio 5.0.18 | Now | Notes |
|---|---|---|---|---|
| Text to image, 4 to 16 MP | yes | yes (Draft 4, Standard 8, High 16 MP) | yes | unchanged |
| Edit a picture by instruction (Picture 1) | yes (`edit`, 1 reference) | no: references ignored with a log line | **yes** | engine: reference rows between text and target, held at the 0.999 level; Qwen sees the picture at a 1024 px short edge, the VAE at 1024-2048 px |
| Several reference pictures in one image | yes (up to 16) | no | **yes, up to 6 in the Studio** | engine takes 16; the Studio caps at 6 to keep a render near its measured time and memory. Pictures are named "Picture 1", "Picture 2", ... in the prompt |
| Output in the shape of Picture 1 | yes (any 32 px size) | no | **yes ("Same as picture")** | the default shape while editing |
| Change the shape while editing (reframe, fill the new area) | yes (target size is free) | no | **yes** | pick any other shape with a reference loaded |
| 3:2 and 2:3 shapes | yes | runner yes, Studio menu no | **yes** | added to the Studio shape menu for every engine (1248x832, 832x1248) |
| Edit the result again | n/a (workflow) | no | **yes (More, Edit this picture)** | any finished picture loads into Picture 1 |
| Seeds, several candidates | yes | yes | yes | unchanged |
| Exact text on posters | yes | yes (prompt) | yes | unchanged; no extra control needed |
| Negative prompt, guidance, step count | no (fixed 6 steps, no guidance) | correctly absent | absent | nothing to add |
| Prompt rewriter (MiniMax's PE model) | optional, CUDA-only weights | no | no | not portable today; the Studio says longer English descriptions work best |
| Masks / inpainting regions | no (not in the release) | no | no | edit by words covers it |
| ControlNet, training adapters | third party only | no | no | out of scope |
| LoRA | not released | no | no | |

## Free or Pro

Everything here is Free (owner, 12 Nov 2026): editing one picture, combining several pictures and
changing the shape while editing, as well as text to image.
