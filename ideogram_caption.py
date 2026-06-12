"""Agent-facing Ideogram 4 caption builder + validator (pure stdlib).

WHY THIS EXISTS
---------------
Ideogram 4 (via mflux) is trained on a finicky, order-sensitive structured
JSON "caption" — not a free-text prompt. The browser panel hides that schema
behind a drag-on-canvas editor whose serializer lives in JS
(`ideoBuildCaption` / `ideoSynthDesc` / `ideoRectToBbox` inside
mlx_ltx_panel.py). An LLM agent has no canvas, so this module is a FAITHFUL
Python port of that serializer: the agent describes a composition in simple,
human terms (a scene + a list of fractional boxes), and `build_caption`
emits the exact strict caption the upstream verifier
(mflux .../ideogram4_text_encoder/caption.py — `Ideogram4CaptionVerifier`)
expects.

THE AGENT-FACING SPEC
---------------------
A spec is a plain dict::

    {
      "scene":  "<overall background description>",   # required, str
      "boxes":  [ <box>, ... ],                        # required list (may be empty)
      "render": "design" | "photo",                    # default "design"
    }

Each box is::

    {
      "type":  "text" | "object",     # required
      "x": 0..1, "y": 0..1,           # top-left corner, FRACTIONS of the frame
      "w": 0..1, "h": 0..1,           # size, fractions of the frame
      "text":  "...",                 # REQUIRED for type=text (the literal words)
      "desc":  "...",                 # optional refinement; REQUIRED for type=object
      "style": "headline|subhead|body|caps|script|serif",  # text only, default headline
      "align": "left|center|right",   # text only, default center
      "color": "#RRGGBB",             # text only, default #FFFFFF
    }

Coordinates are aspect-INDEPENDENT fractions with a top-left origin, exactly
like the canvas: (x=0,y=0) is the top-left of the frame, (x=1,y=1) the
bottom-right. The caption stores each box as a bbox ``[y_min, x_min, y_max,
x_max]`` of ROW-FIRST integers in 0..1000 (the single source of bbox truth is
`rect_to_bbox`, a 1:1 port of the JS `ideoRectToBbox`).

PORT FIDELITY
-------------
`build_caption` matches the JS `ideoBuildCaption` key-for-key, with ONE
deliberate difference: it OMITS the optional `style_description` block. The
upstream verifier treats `style_description` as optional (it only validates it
``if "style_description" in caption``), and the block is brittle (its strict
key-order / photo-XOR-art_style rules are a common cause of WARN noise). The
render-mode distinction the JS encodes in `style_description` is preserved
where it actually matters for the agent: in `high_level_description`
("A graphic design ..." for design vs "A photographic image ..." for photo).
Everything else — synthesized `desc` wording, bbox math, per-element color
palette (UPPERCASE #RRGGBB, ≤5), the overall scene folded into
`compositional_deconstruction.background`, dropping empty text boxes,
insertion/key order — is a faithful port.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Constants (ported from the JS panel state)
# ---------------------------------------------------------------------------
MAX_TEXT_BOXES = 6                       # IDEO_MAX_TEXT_BOXES — cap on text boxes
VALID_RENDER = ("design", "photo")       # agent-facing render modes
VALID_TYPES = ("text", "object")         # agent-facing box types
VALID_STYLES = ("headline", "subhead", "body", "caps", "script", "serif")
VALID_ALIGN = ("left", "center", "right")
VALID_ASPECT = ("16:9", "1:1", "9:16", "4:3", "3:4", "21:9")
VALID_QUALITY = ("turbo", "default", "quality")

# quality -> mflux Ideogram sampler preset (mirrors the panel's ideo_preset).
QUALITY_PRESET = {
    "turbo": "V4_TURBO_12",
    "default": "V4_DEFAULT_20",
    "quality": "V4_QUALITY_48",
}

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")
_STRICT_HEX_RE = re.compile(r"^#[0-9A-F]{6}$")

# ideoColorName map — exact UPPERCASE-keyed table from the JS.
_COLOR_NAMES = {
    "#FFFFFF": "white", "#0A0A0A": "near-black", "#000000": "black",
    "#F5C518": "gold", "#E63946": "red", "#2F81F7": "blue",
    "#3FB950": "green", "#8B5E3C": "brown", "#5A6B3A": "olive green",
}

# ideoSynthDesc style/align word tables — exact wording from the JS.
_STYLE_WORD = {
    "headline": "a bold headline",
    "subhead": "a medium-weight subheading",
    "body": "clean body text",
    "caps": "small-caps lettering",
    "script": "a flowing script",
    "serif": "an elegant serif",
}
_ALIGN_WORD = {"left": "left-aligned", "center": "centered", "right": "right-aligned"}


# ---------------------------------------------------------------------------
# Low-level helpers — 1:1 ports of the JS primitives
# ---------------------------------------------------------------------------
def norm_hex(s: Any) -> str:
    """Port of `ideoNormHex`: accept '#RRGGBB' or 'RRGGBB' (any case),
    return UPPERCASE '#RRGGBB'; anything else → '#FFFFFF'."""
    s = str(s if s is not None else "").strip()
    if _HEX_RE.match(s):
        if not s.startswith("#"):
            s = "#" + s
        return s.upper()
    return "#FFFFFF"


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def rect_to_bbox(x: float, y: float, w: float, h: float) -> list[int]:
    """Port of `ideoRectToBbox`: {x,y,w,h} fractions → [y_min, x_min, y_max,
    x_max] ROW-FIRST integers in 0..1000, corners clamped to [0,1] first then
    min/max-ordered. This is the ONE place bbox math lives, matching the JS."""
    x0 = _clamp01(float(x)); y0 = _clamp01(float(y))
    x1 = _clamp01(float(x) + float(w)); y1 = _clamp01(float(y) + float(h))
    Y0 = round(y0 * 1000); X0 = round(x0 * 1000)
    Y1 = round(y1 * 1000); X1 = round(x1 * 1000)
    return [min(Y0, Y1), min(X0, X1), max(Y0, Y1), max(X0, X1)]


def color_name(hex_in: Any) -> str:
    """Port of `ideoColorName`: named color for the swatch table, else
    'the color #RRGGBB'."""
    h = norm_hex(hex_in)
    return _COLOR_NAMES.get(h, "the color " + h)


def _synth_desc(box: dict) -> str:
    """Port of `ideoSynthDesc`. A manual `desc` (agent's box["desc"]) wins
    verbatim (trimmed). Objects with no desc fall back to the JS default.
    Text boxes synthesize "<Style>, <align>, in <color>." exactly like the JS."""
    manual = str(box.get("desc") or "").strip()
    if manual:
        return manual
    if box.get("type") == "object":
        return "An object in the scene."
    style = box.get("style") or "headline"
    align = box.get("align") or "center"
    style_word = _STYLE_WORD.get(style, "text")
    align_word = _ALIGN_WORD.get(align, "centered")
    cname = color_name(box.get("color"))
    # JS: `${styleWord[0].toUpperCase()}${styleWord.slice(1)}, ${alignWord}, in ${colorName}.`
    cap_style = style_word[0].upper() + style_word[1:]
    return f"{cap_style}, {align_word}, in {cname}."


# ---------------------------------------------------------------------------
# build_caption — the serializer (agent spec -> strict caption dict)
# ---------------------------------------------------------------------------
def build_caption(spec: dict) -> dict:
    """Build the strict Ideogram 4 caption dict from an agent-facing spec.

    Faithful port of the JS `ideoBuildCaption`, OMITTING the optional
    `style_description` block (see module docstring). Key/insertion order is
    significant and matches the upstream verifier: root keys are
    ``high_level_description`` then ``compositional_deconstruction``; each
    element is ``type, bbox, [text], desc, [color_palette]``.

    Does NOT validate — call `validate_spec` first for human-readable issues.
    Tolerant by construction (clamps, normalizes hex, drops empty text) so it
    never raises on a well-typed dict.
    """
    if not isinstance(spec, dict):
        raise TypeError("spec must be a dict")

    render = (spec.get("render") or "design")
    render = render if render in VALID_RENDER else "design"
    background = str(spec.get("scene") or "").strip() or "A clean, simple background."
    boxes = spec.get("boxes") or []
    if not isinstance(boxes, list):
        boxes = []

    # high_level_description — fold up to 4 filled text strings, mirroring JS.
    text_boxes = [b for b in boxes if isinstance(b, dict) and b.get("type") == "text"]
    filled = [str(b.get("text") or "").strip() for b in text_boxes if str(b.get("text") or "").strip()]
    hl_bits: list[str] = []
    if filled:
        quoted = ", ".join('"' + t + '"' for t in filled[:4])
        hl_bits.append("text reading " + quoted)
    feat = (" featuring " + "; ".join(hl_bits)) if hl_bits else ""
    if render == "photo":
        high_level = f"A photographic image{feat}."
    else:
        high_level = f"A graphic design{feat}."

    cap: dict[str, Any] = {}
    cap["high_level_description"] = high_level

    # compositional_deconstruction — order: background, elements.
    elements: list[dict[str, Any]] = []
    for box in boxes:
        if not isinstance(box, dict):
            continue
        btype = box.get("type")
        if btype not in VALID_TYPES:
            continue
        if btype == "text" and not str(box.get("text") or "").strip():
            continue  # drop empty text boxes (JS parity)
        el: dict[str, Any] = {}
        el["type"] = "obj" if btype == "object" else "text"   # type first
        el["bbox"] = rect_to_bbox(                            # then bbox
            box.get("x", 0), box.get("y", 0), box.get("w", 0), box.get("h", 0)
        )
        if el["type"] == "text":
            el["text"] = str(box.get("text") or "").strip()   # text (text only)
        el["desc"] = _synth_desc(box)                         # desc
        if btype == "text":                                   # per-element palette ≤5, text only
            el["color_palette"] = [norm_hex(box.get("color"))][:5]
        elements.append(el)

    cap["compositional_deconstruction"] = {"background": background, "elements": elements}
    return cap


# ---------------------------------------------------------------------------
# validate_spec — human-readable issues (warnings, not hard errors)
# ---------------------------------------------------------------------------
def _overlap_fraction(a: dict, b: dict) -> float:
    """Intersection area / smaller box area, in fractional coords. Used to
    warn (not block) when two boxes overlap heavily."""
    try:
        ax0, ay0 = float(a.get("x", 0)), float(a.get("y", 0))
        ax1, ay1 = ax0 + float(a.get("w", 0)), ay0 + float(a.get("h", 0))
        bx0, by0 = float(b.get("x", 0)), float(b.get("y", 0))
        bx1, by1 = bx0 + float(b.get("w", 0)), by0 + float(b.get("h", 0))
    except (TypeError, ValueError):
        return 0.0
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    smaller = min(area_a, area_b)
    if smaller <= 0:
        return 0.0
    return inter / smaller


def validate_spec(spec: dict) -> list[str]:
    """Return a list of human-readable issues with an agent-facing spec.

    These are WARNINGS where the model would still render (overlaps, missing
    refinement desc on objects degrade quality but don't crash) and harder
    problems where it would not (no text in a text box, bbox out of range,
    bad hex). The HTTP layer decides which subset is fatal; this function just
    reports. Empty list = clean.
    """
    issues: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]

    # scene
    scene = spec.get("scene")
    if scene is None or not str(scene).strip():
        issues.append("scene: required — describe the overall background/composition")
    elif not isinstance(scene, str):
        issues.append("scene: must be a string")

    # render
    render = spec.get("render", "design")
    if render not in VALID_RENDER:
        issues.append(f"render: {render!r} unknown — use one of {list(VALID_RENDER)}")

    # boxes
    boxes = spec.get("boxes")
    if boxes is None:
        issues.append("boxes: required — pass a list (may be empty for a pure-scene render)")
        boxes = []
    elif not isinstance(boxes, list):
        issues.append("boxes: must be a list")
        boxes = []

    text_count = 0
    valid_boxes_for_overlap: list[tuple[int, dict]] = []
    for i, box in enumerate(boxes):
        p = f"boxes[{i}]"
        if not isinstance(box, dict):
            issues.append(f"{p}: must be an object")
            continue
        btype = box.get("type")
        if btype not in VALID_TYPES:
            issues.append(f"{p}.type: must be 'text' or 'object' (got {btype!r})")
            continue
        if btype == "text":
            text_count += 1
            if not str(box.get("text") or "").strip():
                issues.append(f"{p}.text: text box has no text (it will be dropped from the caption)")
            style = box.get("style", "headline")
            if style not in VALID_STYLES:
                issues.append(f"{p}.style: {style!r} unknown — use one of {list(VALID_STYLES)}")
            align = box.get("align", "center")
            if align not in VALID_ALIGN:
                issues.append(f"{p}.align: {align!r} unknown — use one of {list(VALID_ALIGN)}")
            color = box.get("color")
            # norm_hex silently falls back to white on a bad color — surface that.
            if color is not None and not _HEX_RE.match(str(color)):
                issues.append(f"{p}.color: {color!r} is not a #RRGGBB hex (will default to #FFFFFF)")
        else:  # object
            if not str(box.get("desc") or "").strip():
                issues.append(f"{p}.desc: object box needs a 'desc' describing what to render there")

        # geometry: x,y,w,h present and in range
        geom_ok = True
        for k in ("x", "y", "w", "h"):
            if k not in box:
                issues.append(f"{p}.{k}: missing (fraction 0..1, top-left origin)")
                geom_ok = False
                continue
            try:
                val = float(box[k])
            except (TypeError, ValueError):
                issues.append(f"{p}.{k}: must be a number (fraction 0..1)")
                geom_ok = False
                continue
            if val < 0 or val > 1:
                issues.append(f"{p}.{k}: {val} out of range — must be a fraction in [0,1]")
        # off-frame: top-left + size must stay within the unit frame, else the
        # bbox silently clamps and the box ends up a different size than asked.
        if geom_ok:
            try:
                if float(box["x"]) + float(box["w"]) > 1.0:
                    issues.append(f"{p}: x+w = {round(float(box['x']) + float(box['w']), 3)} extends past the "
                                  f"right edge (>1) — the bbox will be clamped to the frame")
                if float(box["y"]) + float(box["h"]) > 1.0:
                    issues.append(f"{p}: y+h = {round(float(box['y']) + float(box['h']), 3)} extends past the "
                                  f"bottom edge (>1) — the bbox will be clamped to the frame")
            except (TypeError, ValueError, KeyError):
                pass
        # zero-area / off-frame bbox check via the real bbox math
        try:
            bb = rect_to_bbox(box.get("x", 0), box.get("y", 0), box.get("w", 0), box.get("h", 0))
            if bb[0] == bb[2] or bb[1] == bb[3]:
                issues.append(f"{p}: box has zero width or height after clamping — give it a visible w/h")
            valid_boxes_for_overlap.append((i, box))
        except (TypeError, ValueError):
            pass

    if text_count > MAX_TEXT_BOXES:
        issues.append(f"boxes: {text_count} text boxes exceeds the {MAX_TEXT_BOXES}-box cap "
                      f"(extra text boxes confuse the layout)")

    # overlap warnings (>40% of the smaller box)
    for a in range(len(valid_boxes_for_overlap)):
        for b in range(a + 1, len(valid_boxes_for_overlap)):
            ia, ba = valid_boxes_for_overlap[a]
            ib, bb = valid_boxes_for_overlap[b]
            ov = _overlap_fraction(ba, bb)
            if ov > 0.40:
                issues.append(f"boxes[{ia}] and boxes[{ib}] overlap ~{round(ov * 100)}% — "
                              f"text/objects may collide")

    return issues


# Convenience: caption-level validation reusing the upstream verifier's rules
# in pure Python (mirror of the JS ideoValidateCaption), so callers can assert
# the built caption itself is schema-clean without importing mflux.
def validate_caption(cap: Any) -> list[str]:
    """Validate a built caption dict against the Ideogram 4 schema (the same
    rules the upstream `Ideogram4CaptionVerifier` enforces, minus the
    ASCII-escape check). Returns human-readable problems; empty = valid."""
    out: list[str] = []

    def is_str(v: Any) -> bool:
        return isinstance(v, str)

    if not isinstance(cap, dict):
        out.append("root must be an object")
        return out
    root_known = ("high_level_description", "style_description", "compositional_deconstruction")
    for k in cap:
        if k not in root_known:
            out.append("root: unknown key " + str(k))
    if "high_level_description" in cap and not is_str(cap["high_level_description"]):
        out.append("high_level_description must be a string")

    if "style_description" in cap:
        sd = cap["style_description"]
        if not isinstance(sd, dict):
            out.append("style_description must be an object")
        else:
            has_photo = "photo" in sd
            has_art = "art_style" in sd
            if has_photo == has_art:
                out.append("style_description: exactly one of 'photo' or 'art_style'")
            order = (["aesthetics", "lighting", "photo", "medium", "color_palette"]
                     if has_photo else
                     ["aesthetics", "lighting", "medium", "art_style", "color_palette"])
            present = [k for k in sd if k in order]
            want = [k for k in order if k != "color_palette" or "color_palette" in sd]
            if present != want:
                out.append("style_description: key order " + ",".join(present) + " != " + ",".join(want))
            for k in sd:
                if k not in order:
                    out.append("style_description: key " + str(k) + " not allowed")
            for k in order:
                if k != "color_palette" and k not in sd:
                    out.append("style_description: missing " + k)
            if "color_palette" in sd:
                cp = sd["color_palette"]
                if not isinstance(cp, list):
                    out.append("style_description.color_palette must be a list")
                else:
                    if len(cp) > 16:
                        out.append("style_description.color_palette: >16 colors")
                    for c in cp:
                        if not (isinstance(c, str) and _STRICT_HEX_RE.match(c)):
                            out.append("style_description.color_palette: " + str(c) + " not uppercase #RRGGBB")

    if "compositional_deconstruction" not in cap:
        out.append("root: 'compositional_deconstruction' should exist")
        return out
    cd = cap["compositional_deconstruction"]
    if not isinstance(cd, dict):
        out.append("compositional_deconstruction must be an object")
        return out
    cd_present = [k for k in cd if k in ("background", "elements")]
    cd_want = [k for k in ("background", "elements") if k in cd]
    if cd_present != cd_want:
        out.append("compositional_deconstruction: key order")
    for k in cd:
        if k not in ("background", "elements"):
            out.append("compositional_deconstruction: key " + str(k) + " not allowed")
    if "background" not in cd:
        out.append("compositional_deconstruction: 'background' should exist")
    elif not is_str(cd["background"]):
        out.append("compositional_deconstruction.background must be a string")
    if "elements" not in cd:
        out.append("compositional_deconstruction: 'elements' should exist")
        return out
    if not isinstance(cd["elements"], list):
        out.append("compositional_deconstruction.elements must be a list")
        return out
    for i, el in enumerate(cd["elements"]):
        p = "elements[" + str(i) + "]"
        if not isinstance(el, dict):
            out.append(p + " must be an object")
            continue
        el_known = ("type", "bbox", "text", "desc", "color_palette")
        for k in el:
            if k not in el_known:
                out.append(p + ": unknown key " + str(k))
        if el.get("type") not in ("obj", "text"):
            out.append(p + ": type must be 'obj' or 'text'")
            continue
        order = (["type", "bbox", "text", "desc", "color_palette"]
                 if el["type"] == "text" else ["type", "bbox", "desc", "color_palette"])
        present = [k for k in el if k in order]
        want = [k for k in order if (k not in ("bbox", "color_palette")) or k in el]
        if present != want:
            out.append(p + ": key order " + ",".join(present) + " != " + ",".join(want))
        if "desc" not in el:
            out.append(p + ": 'desc' should exist")
        elif not is_str(el["desc"]):
            out.append(p + ".desc must be a string")
        if el["type"] == "text":
            if "text" not in el:
                out.append(p + ": text elements should include 'text'")
            elif not is_str(el["text"]):
                out.append(p + ".text must be a string")
        if "bbox" in el:
            bb = el["bbox"]
            if not (isinstance(bb, list) and len(bb) == 4):
                out.append(p + ".bbox: expected [y_min,x_min,y_max,x_max]")
            elif not all(type(v) is int for v in bb):
                out.append(p + ".bbox: all values must be integers")
            else:
                if not all(0 <= v <= 1000 for v in bb):
                    out.append(p + ".bbox: values must be in [0,1000]")
                if bb[0] > bb[2]:
                    out.append(p + ".bbox: y_min > y_max")
                if bb[1] > bb[3]:
                    out.append(p + ".bbox: x_min > x_max")
        if "color_palette" in el:
            cp = el["color_palette"]
            if not isinstance(cp, list):
                out.append(p + ".color_palette must be a list")
            else:
                if len(cp) > 5:
                    out.append(p + ".color_palette: >5 colors")
                for c in cp:
                    if not (isinstance(c, str) and _STRICT_HEX_RE.match(c)):
                        out.append(p + ".color_palette: " + str(c) + " not uppercase #RRGGBB")
    return out
