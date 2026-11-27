"""Pinned upstream/MLX weight manifest and canvas geometry for Ming-Image-0.2-Design."""
from __future__ import annotations

import math

MODEL_NAME = "Ming-Image 0.2 Design"
UPSTREAM_REPO = "inclusionAI/Ming-Image-0.2-Design"
UPSTREAM_REVISION = "3538f4c311cf8f5256d631ddb7f47793f741aa2e"
UPSTREAM_GITHUB = "https://github.com/inclusionAI/Ming-Image"

WEIGHTS_REPO = "amarck/Ming-Image-0.2-Design-MLX"
WEIGHTS_REVISION = "167212531758b9eabe443fdddc548c11d2281e22"

LICENSE_NAME = "MIT License"
CREDIT = (
    "Ming-Image-0.2-Design by inclusionAI (Ant Group), MLX weight conversion by amarck "
    "(MIT License; downloaded directly from Hugging Face, never re-hosted)."
)
NOTICE = (
    "Ming-Image-0.2-Design is licensed under the MIT License (Copyright 2026 inclusionAI). "
    "Connector, vision tower and VAE components are derived from Qwen2-1.5B, Qwen2.5-VL ViT "
    "and Qwen-Image VAE (Apache-2.0)."
)

DEFAULT_STEPS = 12
DEFAULT_CFG = 1.0
LATENT_SCALE = 16  # VAE downsample 8 * DiT patch 2

# Exact pinned manifest for the base MLX pack (18 files, 32,356,674,640 bytes = 32.36 GB).
PINNED_FILES: tuple[dict[str, object], ...] = (
    {
        "path": "LICENSE",
        "bytes": 1068,
        "sha256": "b772c54f1f150a680e7c083a92431c518cf62821a407f6cccdf53f6a07bcf702",
    },
    {
        "path": "config.json",
        "bytes": 6344,
        "sha256": "1b2e284feecd5e8bf7a9363dfc863bd8ccb22ae12cdbab4e600485229a34b561",
    },
    {
        "path": "index.json",
        "bytes": 116478,
        "sha256": "6562c982d59dba11023b4b5b2b7878d95df1895257407a24fe8ba9b9fe6eb5dd",
    },
    {
        "path": "quantization.json",
        "bytes": 54,
        "sha256": "4996a9a28c366dd41ca405d15f5e56d6427937464e18bd9e9559ade33cfcb4c1",
    },
    {
        "path": "tokenizer/config.json",
        "bytes": 5557,
        "sha256": "f797c39adcd1624e5bfc94f60d2a86095dc40978f8cf230fcd11ada877c597ea",
    },
    {
        "path": "tokenizer/preprocessor_config.json",
        "bytes": 450,
        "sha256": "e63066dde0329df1642c8dd9a269d613307804084d12e0787ccc887839339091",
    },
    {
        "path": "tokenizer/special_tokens_map.json",
        "bytes": 879,
        "sha256": "8c383d444e75b12f2643b4695bece7f3dd1d43725d8a26deeb019aa40a8f891d",
    },
    {
        "path": "tokenizer/tokenizer.json",
        "bytes": 12210709,
        "sha256": "e7ff01708d504f7bf4dbf7f5815adde57bab9a40e7f563ab6ad1acace4464917",
    },
    {
        "path": "tokenizer/tokenizer_config.json",
        "bytes": 58759,
        "sha256": "861c20bea04c2057362f226658ad9209978ff294b8a95591a0d32f69dbd3fc28",
    },
    {
        "path": "vae.safetensors",
        "bytes": 50596879,
        "sha256": "0e461aa8b9cad9fd7d8e5fa8c5dd2df66a0829b2b8baa16f214877dedc19e192",
    },
    {
        "path": "cond.safetensors",
        "bytes": 2683139985,
        "sha256": "1a2ee9d99502afdb418a7923a46d176a749a97c40f68f4d29235c64dcaac374f",
    },
    {
        "path": "dit-00001-of-00003.safetensors",
        "bytes": 4993702052,
        "sha256": "5100d03bc4b4b0fa22c7bda4bab7958bc097aa5e6165ff552aa148d7f6ec8d22",
    },
    {
        "path": "dit-00002-of-00003.safetensors",
        "bytes": 4986868726,
        "sha256": "560e78c3490f80f3a4f0e5a480c5f8020791ab7cc40e2d5681b1560e6c0a8531",
    },
    {
        "path": "dit-00003-of-00003.safetensors",
        "bytes": 2329287551,
        "sha256": "07853468afa6d139a79d8823b6ad26dd70d29d0176ebeedbc33caec2f2968ce0",
    },
    {
        "path": "llm-q8-00001-of-00004.safetensors",
        "bytes": 4736903729,
        "sha256": "1fd472b8541edf75e2e05914315b315da5892b55c28c304ca1663871974cfba6",
    },
    {
        "path": "llm-q8-00002-of-00004.safetensors",
        "bytes": 4960768234,
        "sha256": "c08686ffd26d2db98fe83f283207d41dbd8a06f95e652654fb528a4458a7ccc8",
    },
    {
        "path": "llm-q8-00003-of-00004.safetensors",
        "bytes": 4942077028,
        "sha256": "e6482ac6c90ffeb9cfc85d27b829ac0720de77c315847135c41254222ffe2f1e",
    },
    {
        "path": "llm-q8-00004-of-00004.safetensors",
        "bytes": 2660930158,
        "sha256": "e08bf09edf10fc44ccc6e2ae83d462abdc897d1f8040079fe54a11884466b329",
    },
)

# Pinned HTTP Range slices fetched directly from upstream inclusionAI/Ming-Image-0.2-Design
# at UPSTREAM_REVISION (never re-hosted) to supply the Qwen2.5-ViT vision tower, linear_proj,
# and 4-channel RGBA VAE encoder omitted from the text-to-image MLX pack (1.56 GB total fetch).
UPSTREAM_EDIT_SLICES: tuple[dict[str, object], ...] = (
    {
        "tag": "vision_raw",
        "remote_path": "mllm/model-00001-of-00007.safetensors",
        "header_len": 219016,
        "header_sha256": "4e2556cf77d963d295e3bc97c5dc7d58c9c547e1dbce90f6f10a6bf219c6f8b9",
        "data_start": 3589514128,
        "data_end": 4998661520,
        "data_bytes": 1409147392,
        "data_sha256": "20f0c274d3357db1bf6af2904b03a3d733634906a5b23df1908a19161ceb6c4a",
        "key_prefix": "vision.",
    },
    {
        "tag": "linproj_raw",
        "remote_path": "mllm/model-00007-of-00007.safetensors",
        "header_len": 208520,
        "header_sha256": "3b833d0836f54c376513c07682139cdf3981fc0b02ac0b0bfb6e9bed43c7110f",
        "data_start": 208528,
        "data_end": 42159760,
        "data_bytes": 41951232,
        "data_sha256": "05ce1a9208baf30605037080243b717160baa82b97cc992a8b36986882c9f175",
        "key_prefix": "linear_proj.",
    },
    {
        "tag": "vae_enc_raw",
        "remote_path": "vae/diffusion_pytorch_model.safetensors",
        "header_len": 21896,
        "header_sha256": "78fbb923cc0f28628aedfddfca0e4e775bbd1d7d103588142a1831f1b0fdef08",
        "data_start": 146617752,
        "data_end": 253817336,
        "data_bytes": 107199584,
        "data_sha256": "1fb4bcace2f8cce9c56114c17b06334bf8b2f5ae623d74523fd6874d4773bad5",
        "key_prefix": "encoder.",
    },
)

# Deterministic local safetensors files packed from UPSTREAM_EDIT_SLICES (1.49 GB on disk).
EDIT_OUTPUT_FILES: tuple[dict[str, object], ...] = (
    {
        "path": "vision.safetensors",
        "bytes": 1451140760,
        "sha256": "86ca519572badce147bb88611ba61d6a8102a0dac583dfdd28b75c2944c0ff83",
    },
    {
        "path": "vae_encoder.safetensors",
        "bytes": 38625328,
        "sha256": "8b3982e2b488e47353e4e3647467f34dfbe5da29f4c672321acff61d15ce8a95",
    },
)

BASE_TOTAL_BYTES: int = sum(int(item["bytes"]) for item in PINNED_FILES)
EDIT_FETCH_BYTES: int = sum(int(item["data_bytes"]) for item in UPSTREAM_EDIT_SLICES)
EDIT_DISK_BYTES: int = sum(int(item["bytes"]) for item in EDIT_OUTPUT_FILES)
TOTAL_BYTES: int = BASE_TOTAL_BYTES + EDIT_FETCH_BYTES
DISK_TOTAL_BYTES: int = BASE_TOTAL_BYTES + EDIT_DISK_BYTES
DOWNLOAD_GB: float = round(TOTAL_BYTES / 1e9, 2)  # 33.91 GB (32.36 GB base + 1.56 GB edit slices)
DISK_GB: float = round(DISK_TOTAL_BYTES / 1e9, 2)  # 33.85 GB kept on disk

# Trained aspect-ratio buckets (W, H) matching upstream bailingmm_utils.py
# for Standard (1024 bucket, ~1 MP) and High (2048 bucket, ~4 MP).
ASPECT_CANVAS: dict[str, dict[str, tuple[int, int]]] = {
    "standard": {
        "1:1": (1024, 1024),
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "4:3": (1152, 864),
        "3:4": (864, 1152),
        "3:2": (1248, 832),
        "2:3": (832, 1248),
        "21:9": (1536, 640),
    },
    "high": {
        "1:1": (2048, 2048),
        "16:9": (2560, 1440),
        "9:16": (1440, 2560),
        "4:3": (2432, 1824),
        "3:4": (1824, 2432),
        "3:2": (2496, 1664),
        "2:3": (1664, 2496),
        "21:9": (3072, 1280),
    },
}

# Trained 1024-bucket (H, W) lookup table matching upstream bailingmm_utils.process_ratio
# for preserving a reference picture's aspect ratio during image-edit.
EDIT_RATIO_1024: tuple[tuple[float, int, int], ...] = (
    (0.25, 512, 2048), (0.26, 512, 1984), (0.27, 512, 1920), (0.28, 512, 1856),
    (0.32, 576, 1792), (0.33, 576, 1728), (0.35, 576, 1664), (0.4, 640, 1600),
    (0.42, 640, 1536), (0.48, 704, 1472), (0.5, 704, 1408), (0.52, 704, 1344),
    (0.5581, 768, 1376), (0.5625, 720, 1280), (0.5647, 768, 1360), (0.57, 768, 1344),
    (0.6, 768, 1280), (0.622, 816, 1312), (0.625, 800, 1280), (0.65, 832, 1280),
    (0.6582, 832, 1264), (0.6667, 832, 1248), (0.6709, 848, 1264), (0.68, 832, 1216),
    (0.7013, 864, 1232), (0.72, 832, 1152), (0.7467, 896, 1200), (0.75, 864, 1152),
    (0.7568, 896, 1184), (0.78, 896, 1152), (0.8, 896, 1120), (0.8056, 928, 1152),
    (0.82, 896, 1088), (0.88, 960, 1088), (0.94, 960, 1024), (0.9846, 1024, 1040),
    (1.0, 1024, 1024), (1.07, 1024, 960), (1.13, 1088, 960), (1.21, 1088, 896),
    (1.2414, 1152, 928), (1.25, 1120, 896), (1.2807, 1168, 912), (1.29, 1152, 896),
    (1.3333, 1152, 864), (1.3393, 1200, 896), (1.38, 1152, 832), (1.46, 1216, 832),
    (1.4906, 1264, 848), (1.5, 1248, 832), (1.6, 1280, 800), (1.67, 1280, 768),
    (1.75, 1344, 768), (1.7708, 1360, 768), (1.7778, 1280, 720), (2.0, 1408, 704),
    (2.09, 1472, 704), (2.4, 1536, 640), (2.5, 1600, 640), (2.89, 1664, 576),
    (3.0, 1728, 576), (3.11, 1792, 576), (3.62, 1856, 512), (3.75, 1920, 512),
    (3.88, 1984, 512), (4.0, 2048, 512),
)

# Upstream trained prefixes for 4-channel RGBA transparent-background output
# (inclusionAI/Ming-Image README). Use at most one at the start of the prompt.
TRANSPARENT_PREFIX_DEFAULT = "RGBA, 4-channel, transparent background"
TRANSPARENT_PREFIXES: tuple[str, ...] = (
    "RGBA, 4-channel, transparent background",
    "isolated subject, alpha matte, no background",
    "cutout PNG, alpha=0 outside the object",
    "transparent canvas, not white, not checkerboard",
    "production RGBA layer for compositing",
    "带透明通道，4通道RGBA图像",
    "透明背景，alpha通道，无底图",
    "抠图素材，背景alpha=0",
    "孤立主体，透明PNG图层",
    "不要白底，不要棋盘格，只要透明通道",
)

# Upstream MIT system prompt (inclusionAI/Ming-Image assets/t2i_rewriter_system_prompt.txt)
# for rewriting a short idea into a structured Figma-style layout JSON prompt locally.
DESIGN_LAYOUT_SYSTEM_PROMPT = (
    "You are a senior visual designer and image-prompt engineer. Expand the user's request into one precise, "
    "high-resolution Figma-style caption. Return only one JSON object.\n\n"
    "Use exactly two top-level keys. `canvas_settings` contains exactly `aspect_ratio`, `ambient_lighting`, "
    "and `image_style`. `layers` lists visible groups from background to topmost overlay. Every layer contains "
    "exactly `description`, `coordinates`, `hierarchy_and_relation`, and `color_specs`; `color_specs` is an "
    "array of hex colors.\n\n"
    "`coordinates` MUST be one string, never an object or array, in exactly this form: "
    "`\"cx: 0.500, cy: 0.500, w: 1.000, h: 1.000\"`. Values are normalized; each bbox encloses its complete "
    "owned object and stays inside the canvas.\n\n"
    "A layer is one selectable visible semantic group: background, full person, coherent object, panel, card, "
    "row, or text block. Prefer the fewest groups that preserve the layout. Keep people and objects intact. "
    "Never create invisible parents, guides, placeholders, empty layers, duplicate summaries, or multiple "
    "owners for one element.\n\n"
    "Preserve every user-supplied rendered string character-for-character and as one contiguous string. "
    "Unless multiple visible copies are requested, it must occur exactly once across all `description` fields "
    "and zero times in `hierarchy_and_relation`. Quote it only where describing its visible rendering; refer "
    "to the related subject elsewhere with unquoted semantic wording. Enumerate intended copy, invent extra "
    "copy sparingly, and never hide content behind \"other text\", \"remaining labels\", or \"etc.\"\n\n"
    "Describe concrete composition, typography, materials, texture, lighting, pose, and camera treatment "
    "without literary filler. Use `hierarchy_and_relation` only for ownership, alignment, containment, "
    "stacking, and occlusion.\n\n"
    "Infer structured layouts first. Use one complete layer per card and state its row and column. A compact "
    "secondary table may be one layer only if every header and cell is listed; otherwise use a visible shared "
    "frame when present, one complete header, and one complete layer per body row, binding values to columns "
    "and stating blanks. Enumerate sequences, schedules, spans, gaps, and vacant tracks in visual order. "
    "Do not mistake ordinary alignment for a table.\n\n"
    "Silently verify schema, string coordinates, Z-order, exact-text counts, geometry, bbox validity, and completeness."
)


def apply_transparent_prefix(prompt: str) -> str:
    """Prepend the official RGBA prefix unless the prompt already begins with one."""
    text = prompt.strip()
    low = text.lower()
    for prefix in TRANSPARENT_PREFIXES:
        if low.startswith(prefix.lower()):
            return text
    return f"{TRANSPARENT_PREFIX_DEFAULT}. {text}" if text else TRANSPARENT_PREFIX_DEFAULT


def resolve_edit_canvas(ori_width: int, ori_height: int) -> tuple[int, int]:
    """Snap a reference image's aspect ratio to upstream's nearest trained 1024 bucket (W, H)."""
    if ori_width <= 0 or ori_height <= 0:
        return (1024, 1024)
    ratio = float(ori_height) / float(ori_width)
    _, h, w = min(EDIT_RATIO_1024, key=lambda item: abs(item[0] - ratio))
    return (int(w), int(h))


def resolve_canvas(
    tier: str = "standard",
    aspect: str = "1:1",
    width: int | None = None,
    height: int | None = None,
) -> tuple[int, int]:
    """Return a valid (width, height) pair divisible by 16."""
    if width is not None or height is not None:
        if width is None or height is None:
            raise ValueError("width and height must both be supplied")
        w, h = int(width), int(height)
        if w < 256 or h < 256 or w > 4096 or h > 4096:
            raise ValueError(f"canvas {w}x{h} outside supported range 256..4096")
        if not (0.25 <= (w / h) <= 4.0):
            raise ValueError(f"aspect ratio {w}/{h} must stay within 1:4 .. 4:1")
        w = max(LATENT_SCALE, (w // LATENT_SCALE) * LATENT_SCALE)
        h = max(LATENT_SCALE, (h // LATENT_SCALE) * LATENT_SCALE)
        return w, h

    tier_key = str(tier or "standard").strip().lower()
    if tier_key not in ASPECT_CANVAS:
        tier_key = "standard"
    aspect_key = str(aspect or "1:1").strip().lower().rstrip("s")
    table = ASPECT_CANVAS[tier_key]
    if aspect_key in table:
        return table[aspect_key]
    # Parse custom "W:H" aspect ratios into the chosen bucket's pixel budget.
    if ":" in aspect_key:
        parts = aspect_key.split(":", 1)
        try:
            aw, ah = float(parts[0]), float(parts[1])
            if aw > 0 and ah > 0:
                ratio = max(0.25, min(4.0, aw / ah))
                target_area = (2048 * 2048) if tier_key == "high" else (1024 * 1024)
                w = int(round(math.sqrt(target_area * ratio) / LATENT_SCALE)) * LATENT_SCALE
                h = int(round(math.sqrt(target_area / ratio) / LATENT_SCALE)) * LATENT_SCALE
                return max(LATENT_SCALE, w), max(LATENT_SCALE, h)
        except ValueError:
            pass
    return table["1:1"]


def file_url(rel_path: str) -> str:
    return f"https://huggingface.co/{WEIGHTS_REPO}/resolve/{WEIGHTS_REVISION}/{rel_path}"


def upstream_file_url(rel_path: str) -> str:
    return f"https://huggingface.co/{UPSTREAM_REPO}/resolve/{UPSTREAM_REVISION}/{rel_path}"
