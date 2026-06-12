# Phosphene Agent Image API — Ideogram 4 layout

A clean JSON surface that lets an LLM agent (Claude, Codex, anything that speaks HTTP) **fully compose an Ideogram 4 image** — scene, on-image text, object regions, typographic styles, colors — without ever touching the model's finicky internal caption schema.

You describe a picture in plain terms: an overall **scene**, plus a list of **boxes** placed by fraction of the frame. Phosphene translates that into the strict Ideogram 4 caption for you, renders it, and hands back the image paths.

This complements the general [`docs/API.md`](API.md). The same panel process serves both.

## Server

- **Base URL:** `http://127.0.0.1:8199`
- **Process:** `mlx_ltx_panel.py` (repo root). Caption logic: `ideogram_caption.py`.
- **Auth:** none — loopback only.
- **Two endpoints:** `GET /image/agent/schema` (the contract) and `POST /image/agent` (validate / render).

> **Agents: read the schema first.** `GET /image/agent/schema` returns the full field-by-field spec, the caption rules, and two complete worked examples. An agent that reads only that response can compose a correct request. The summary below is for humans.

---

## The box model

Coordinates are **fractions of the frame** with a **top-left origin** — aspect-independent, so the same box lands in the same relative spot at any aspect ratio.

- `x`, `y` — the **top-left corner** of the box (0 = left / top, 1 = right / bottom).
- `w`, `h` — the box's **width / height** as a fraction of the frame.
- `(0,0)` is the top-left of the image; `(1,1)` is the bottom-right.
- Keep `x + w ≤ 1` and `y + h ≤ 1`; boxes that spill past an edge are clamped (you get a warning, not an error).

Internally each box becomes a bbox `[y_min, x_min, y_max, x_max]` of **row-first integers in 0..1000**. You never write that — the server does it.

### Request fields (`POST /image/agent`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `scene` | string | — (required) | The overall background / setting / mood. Describe the *world*, not the text. |
| `boxes` | array | — (required) | List of placed elements. May be `[]` for a pure-scene render. |
| `render` | `design` \| `photo` | `design` | `design` = graphic/poster/vector look; `photo` = photographic. |
| `aspect` | `16:9` \| `1:1` \| `9:16` \| `4:3` \| `3:4` \| `21:9` | `16:9` | 16:9=1280×720, 1:1=1024×1024, 9:16=720×1280, 4:3=1024×768, 3:4=768×1024, 21:9=1280×544. |
| `quality` | `turbo` \| `default` \| `quality` | `turbo` | Sampler effort → `V4_TURBO_12` / `V4_DEFAULT_20` / `V4_QUALITY_48`. |
| `n` | int 1–4 | `1` | Candidate images to render. |
| `seed` | int | `-1` | Fixed seed for reproducibility; `-1` = random. For `n>1`: seed, seed+1, … |
| `validate_only` | bool | `false` | If true: don't render — return the built caption + warnings. |
| `wait` | bool | `true` | If true: block until the render finishes and return image paths. If false: enqueue and return immediately. |

### Box fields

| Field | Type | Applies to | Notes |
|---|---|---|---|
| `type` | `text` \| `object` | both | `text` renders literal words; `object` renders a described thing. |
| `x`, `y`, `w`, `h` | number 0–1 | both | Top-left origin, fractions of the frame (see above). |
| `text` | string | **text (required)** | The literal words to render. Empty text boxes are dropped. |
| `desc` | string | **object (required)**, text (optional) | What to render. For text boxes, overrides the auto style/align/color description. |
| `style` | `headline` \| `subhead` \| `body` \| `caps` \| `script` \| `serif` | text | Typographic feel. Default `headline`. |
| `align` | `left` \| `center` \| `right` | text | Default `center`. |
| `color` | `#RRGGBB` | text | Any case; normalized to uppercase. Bad values fall back to `#FFFFFF`. |

### Responses

- **`validate_only:true`** → `200 { "ok": true, "caption": {…}, "issues": [warnings]}`.
- **`wait:true`** (default) → `200 { "ok": true, "caption": {…}, "images": [{"path","url"}], "seconds": N, "job_id": "…"}`. Open each `url` on this same host to view the PNG.
- **`wait:false`** → `202 { "ok": true, "queued": true, "job_id": "…", "caption": {…}, "where_results_land": "…"}`.
- **Bad request** → `400 {"ok": false, "issues": […]}` (missing scene, object without `desc`, `n` out of range, unknown enum, a box that renders nothing, …).
- **GPU busy / render in flight** → `503` (transient — retry once it frees up). A `wait:true` request otherwise simply waits its turn in the queue.
- **Timeout** → `504` after 25 minutes (the job may still finish on the queue).

`issues` are human-readable. **Warnings** (off-frame clamp, heavy overlap, bad-color fallback, dropped empty text box) still render; the **fatal** subset returns `400`.

---

## Curl examples

### 1. Validate a layout without rendering (free, instant)

```bash
curl -s http://127.0.0.1:8199/image/agent \
  -H 'Content-Type: application/json' \
  -d '{
    "validate_only": true,
    "scene": "A moody vintage travel poster of a mountain lake at golden hour, warm muted palette",
    "render": "design",
    "aspect": "16:9",
    "boxes": [
      {"type":"text","x":0.08,"y":0.08,"w":0.84,"h":0.18,"text":"LAKE DISTRICT","style":"headline","align":"center","color":"#F5C518"},
      {"type":"text","x":0.30,"y":0.80,"w":0.40,"h":0.10,"text":"Est. 1951","style":"serif","align":"center","color":"#FFFFFF"},
      {"type":"object","x":0.55,"y":0.32,"w":0.35,"h":0.42,"desc":"A small wooden canoe drifting on the still lake"}
    ]
  }'
```

### 2. Render a full 16:9 poster and wait for the images

```bash
curl -s http://127.0.0.1:8199/image/agent \
  -H 'Content-Type: application/json' \
  -d '{
    "scene": "A moody vintage travel poster of a mountain lake at golden hour, warm muted palette",
    "render": "design",
    "aspect": "16:9",
    "quality": "turbo",
    "n": 1,
    "wait": true,
    "boxes": [
      {"type":"text","x":0.08,"y":0.08,"w":0.84,"h":0.18,"text":"LAKE DISTRICT","style":"headline","align":"center","color":"#F5C518"},
      {"type":"text","x":0.30,"y":0.80,"w":0.40,"h":0.10,"text":"Est. 1951","style":"serif","align":"center","color":"#FFFFFF"},
      {"type":"object","x":0.55,"y":0.32,"w":0.35,"h":0.42,"desc":"A small wooden canoe drifting on the still lake"}
    ]
  }'
# -> {"ok":true,"job_id":"...","caption":{...},"images":[{"path":".../cand_..._mflux.png","url":"/file?path=..."}],"seconds":...}
```

### 3. Submit a 9:16 label and return immediately (don't block)

```bash
curl -s http://127.0.0.1:8199/image/agent \
  -H 'Content-Type: application/json' \
  -d '{
    "scene": "A minimalist product label, soft off-white paper texture, centered composition",
    "render": "design",
    "aspect": "9:16",
    "quality": "default",
    "wait": false,
    "boxes": [
      {"type":"text","x":0.10,"y":0.42,"w":0.80,"h":0.16,"text":"COLD BREW","style":"caps","align":"center","color":"#0A0A0A"}
    ]
  }'
# -> 202 {"ok":true,"queued":true,"job_id":"...","caption":{...},"where_results_land":"..."}
```

### Read the contract

```bash
curl -s http://127.0.0.1:8199/image/agent/schema | python3 -m json.tool
```

---

## Render-time expectations

Wall time ≈ **one-time model cold-load + n × per-image sampler time**. The *first* Ideogram render after the panel starts is slower because the weights load once.

| `quality` | Sampler | Speed |
|---|---|---|
| `turbo` | `V4_TURBO_12` (~12 steps) | fastest |
| `default` | `V4_DEFAULT_20` (~20 steps) | moderate |
| `quality` | `V4_QUALITY_48` (~48 steps) | slowest, highest fidelity |

Renders are **serialized** with the panel's other GPU work (video, training, other image jobs). A `wait:true` request waits behind any in-flight job; if you'd rather not hold the connection, use `wait:false` and poll `GET /state` for your `job_id`. The `wait` timeout is 25 minutes.

> **Note (Ideogram weights gate):** Ideogram 4 downloads gated weights from Hugging Face on first use. If you get a 5xx about denied access, the panel operator needs a HF **Read** token set in Settings *and* the same account must have accepted the license at `huggingface.co/ideogram-ai/ideogram-4-fp8`. This is one-time, operator-side.

---

## System-prompt snippet for an agent author

Paste this into your agent's system prompt so it drives the endpoint correctly:

```text
You can generate composed images (posters, labels, text-on-image graphics) with Phosphene's Ideogram 4 agent API at http://127.0.0.1:8199.

ALWAYS start by calling GET /image/agent/schema once — it returns the exact field schema, the rules, and two worked examples. Compose against that.

To make an image, POST JSON to /image/agent with:
  - scene: a plain-language description of the overall background/mood (required).
  - boxes: a list of placed elements (required; [] for a pure-scene image). Each box positions itself by FRACTIONS of the frame with a TOP-LEFT origin: x,y is the top-left corner and w,h the size, each in [0,1]. (0,0)=top-left, (1,1)=bottom-right. Keep x+w<=1 and y+h<=1.
      * text box:   {"type":"text","x","y","w","h","text": "<the literal words>", "style":"headline|subhead|body|caps|script|serif","align":"left|center|right","color":"#RRGGBB"}
      * object box: {"type":"object","x","y","w","h","desc":"<what to render there>"}   (desc is required for objects)
  - render: "design" (graphic/poster) or "photo". aspect: "16:9"|"1:1"|"9:16"|"4:3"|"3:4"|"21:9". quality: "turbo"|"default"|"quality". n: 1-4.

Iterate cheaply with "validate_only": true — it returns the built caption plus warnings without rendering. When the layout looks right, POST again with "wait": true to render and receive image urls. Do NOT hand-write Ideogram's internal caption JSON — the server builds it from your scene+boxes. Max 6 text boxes.
```
