"""model_capabilities.py: what every engine and variant can do, in one table.

Owner, 31 Oct 2026: "see what features we are offering and gray out the
rest." Before this file the answer to "can H3 Flex render Keyframes?" lived in
three places that had to agree by hand: a Python constant (H3_FLEX_MODES), a
cell predicate (h3_cell_takes_flex) and a JS copy of both in engines.js. Every
port added a fourth. This table is the one place a port DECLARES a variant:

  * the page reads it (BOOT.capabilities, GET /capabilities) and greys out every
    mode, cell and option the picked variant does not declare, with the plain
    one-line reason written here (webapp/js/capabilities.js);
  * make_job asks check() the same question and falls back (or refuses) from
    the same data, so the browser and the server can never disagree;
  * test_model_capabilities.py binds every row to the panel's live constants,
    so a constant that moves without its row fails the build.

Vocabulary (the axes a variant declares; anything it leaves out it inherits
from its engine's base row, and anything absent from the base row is "not
offered"):

  modes      form modes the variant renders ("t2v", "i2v", "keyframe", ...).
  qualities  canvas keys (H3: draft, preview, fast_hd, standard, high, native).
             None = every canvas the engine has.
  lengths    length keys (H3: 3s, 5s, 10s, 15s, 10s_dense). None = all.
  cells      {"exclude_flags": [...]}: cell flags the variant never renders on
             ("dense" = the single long pass, "fast_hd" = the Fast HD recipe).
  inputs     what it takes in: first_frame, end_frame, keyframes, source_video,
             reference_image, audio_in, loras, character.
  outputs    what it gives back: video, audio, stems.
  options    form controls that still mean something under this variant:
             speed (Fast / Best), steps, clean_sound, upscale, window_prompts.
  pro        True = part of Phosphene Pro (gate: pro/ledger.json row `ledger`).
  status     "ready" | "preview" | "lab" (lab = never shown to users).
  fallback   variant id make_job renders instead when a request asks for
             something this variant does not declare (None = refuse).
  reasons    the one plain line the user sees per axis; written for a smart
             non-engineer, it names what to pick instead.

A new port adds one VARIANTS row (and, for a new engine, one ENGINES row) and
nothing else in this file. docs/CAPABILITIES.md walks through it.
"""
from __future__ import annotations

import copy

SCHEMA_VERSION = 1

AXES = ("modes", "qualities", "lengths")
INPUTS = ("first_frame", "end_frame", "keyframes", "source_video",
          "reference_image", "audio_in", "loras", "character")
OUTPUTS = ("video", "audio", "stems", "image", "song")
OPTIONS = ("speed", "steps", "clean_sound", "upscale", "window_prompts",
           "accel", "generation")
STATUSES = ("ready", "preview", "lab")

# Mode keys as the form names them, with the words the user sees.
MODE_LABELS = {
    "t2v": "Text", "i2v": "Image", "character": "Character",
    "keyframe": "Keyframes", "extend": "Extend", "a2v": "Lip-sync",
    "v2a": "Add sound", "i2v_clean_audio": "Image + your audio",
    "ingredients": "Ingredients", "control": "Motion control",
    "restore": "Restore", "upscale": "Upscale", "music": "Compose",
    "t2i": "Text to image", "edit": "Edit image",
}

# ---------------------------------------------------------------------------
# Engines: the base row every variant of that engine inherits from.
# ---------------------------------------------------------------------------
ENGINES: dict[str, dict] = {
    "ltx": {
        "label": "LTX",
        "surface": "video",
        "modes": ["t2v", "i2v", "character", "keyframe", "extend", "a2v",
                  "i2v_clean_audio", "ingredients", "control", "restore",
                  "upscale"],
        "qualities": ["quick", "balanced", "standard", "high", "high_720p"],
        "lengths": None,
        "cells": {"exclude_flags": []},
        "inputs": ["first_frame", "end_frame", "keyframes", "source_video",
                   "audio_in", "loras", "character"],
        "outputs": ["video", "audio"],
        "options": ["upscale", "accel", "generation"],
        "pro": False,
        "status": "ready",
        "default_variant": "ltx25",
    },
    "h3": {
        "label": "Hailuo H3",
        "surface": "video",
        "modes": ["t2v", "i2v", "keyframe", "extend", "a2v", "v2a"],
        "qualities": None,
        "lengths": None,
        "cells": {"exclude_flags": []},
        "inputs": ["first_frame", "end_frame", "keyframes", "source_video",
                   "reference_image", "audio_in", "loras"],
        "outputs": ["video", "audio"],
        "options": ["speed", "steps", "clean_sound", "upscale",
                    "window_prompts"],
        "pro": False,
        "status": "ready",
        "default_variant": "best",
    },
    "images": {
        # The Images lane (Image Studio). A model's row is `lab` until its bench has run.
        # One variant per image MODEL the Images tab's top picker lists (the panel's
        # ENGINES rows with surfaces=("image",) bind to `images.<their id>`).
        "label": "Images",
        "surface": "image",
        "modes": ["t2i", "edit"],
        "qualities": ["fast", "draft", "standard", "high"],
        "lengths": None,
        "cells": {"exclude_flags": []},
        "inputs": ["reference_image"],
        "outputs": ["image"],
        "options": [],
        "pro": False,
        "status": "preview",
        "default_variant": "p_h3_image",
    },
    "music": {
        "label": "YuE2",
        "surface": "music",
        "modes": ["music"],
        "qualities": ["draft", "final"],
        "lengths": None,
        "cells": {"exclude_flags": []},
        "inputs": ["loras"],
        "outputs": ["song", "audio"],
        "options": [],
        "pro": False,
        "status": "ready",
        "default_variant": "yue2",
    },
}

# ---------------------------------------------------------------------------
# Variants. `pick` says how the form selects it (which control, which value),
# so the page can tell which variant is the live one without a JS table.
# ---------------------------------------------------------------------------
_H3_SAMPLER_TIP = "Switch to H3 (Best) to pick Fast, Best or Steps."

VARIANTS: dict[str, dict] = {
    # ---- LTX: two generations, same capabilities -------------------------
    "ltx.ltx25": {
        "engine": "ltx", "label": "LTX-2.5", "pick": {"generation": "ltx25"},
    },
    "ltx.ltx23": {
        "engine": "ltx", "label": "LTX-2.3", "pick": {"generation": "ltx23"},
    },
    # ---- H3 ----------------------------------------------------------------
    "h3.best": {
        "engine": "h3", "label": "H3 (Best)", "pick": {"speed": "best"},
    },
    "h3.fast": {
        # TaoMate 3-step adapter (h3_cell_takes_tristep).
        "engine": "h3", "label": "H3 Fast", "pick": {"speed": "fast"},
        "qualities": ["draft", "preview", "fast_hd", "standard", "high"],
        "cells": {"exclude_flags": ["dense"]},
        "options": ["speed", "clean_sound", "upscale", "window_prompts"],
        "fallback": "h3.best",
        "reasons": {
            "qualities": "Fast runs on Draft, Standard and High. Pick Best for Native.",
            "cells": "Fast runs on 5-second windows. Pick Best for the single 10-second pass.",
            "options": "Fast runs its own 3 steps.",
        },
    },
    "h3.fast_hd": {
        # A recipe, not a sampler: Fast at Draft, then the x2 Upscale & Face Fix.
        "engine": "h3", "label": "H3 Fast HD", "pick": {"quality": "fast_hd"}, "rank": 1,
        "qualities": ["fast_hd"],
        "cells": {"exclude_flags": ["dense"]},
        "options": ["window_prompts"],
        "fallback": "h3.fast",
        "reasons": {
            "options": "Fast HD always renders Fast and finishes with Upscale & Face Fix.",
        },
    },
    "h3.flex": {
        # Pika H3 Flex (Pika Labs), FL2VA adapter, 6 forwards.
        "engine": "h3", "label": "H3 Flex", "pick": {"speed": "flex"}, "rank": 2,
        "status": "preview",
        "modes": ["t2v", "i2v"],
        "cells": {"exclude_flags": ["dense", "fast_hd"]},
        "inputs": ["first_frame", "loras"],
        "options": ["upscale", "window_prompts"],
        "steps": 6,
        "fallback": "h3.best",
        "reasons": {
            "modes": "H3 Flex makes videos from text or an image. Switch to H3 for this.",
            "cells": "H3 Flex runs on 5-second windows. Switch to H3 for this.",
            "options": "H3 Flex runs its own steps. " + _H3_SAMPLER_TIP,
            "inputs": "H3 Flex takes text or a first frame. Switch to H3 for this.",
        },
    },
    "h3.flex_turbo": {
        # akhaliq's Pika H3 Flex Turbo LoRA (lite r32), 4 forwards. Pro.
        "engine": "h3", "label": "H3 Flex Turbo", "pick": {"speed": "flex_turbo"}, "rank": 2,
        "inherits": "h3.flex",
        "status": "preview",
        "pro": True, "ledger": "h3-flex-turbo",
        "steps": 4,
        "fallback": "h3.flex",
        "reasons": {
            "modes": "H3 Flex Turbo makes videos from text or an image. Switch to H3 for this.",
            "cells": "H3 Flex Turbo runs on 5-second windows. Switch to H3 for this.",
            "options": "H3 Flex Turbo runs its own 4 steps. " + _H3_SAMPLER_TIP,
            "inputs": "H3 Flex Turbo takes text or a first frame. Switch to H3 for this.",
            "pro": "H3 Flex Turbo is part of Phosphene Pro. H3 Flex renders this for free.",
        },
    },
    # ---- Images ---------------------------------------------------------------
    "images.p_h3_image": {
        # Pruna's P-H3-Image on MiniMax H3 (R&D L18): one latent frame, 8 forwards, no guidance.
        # PREVIEW: the 6 Nov speed bench adds Fast 1.5 MP to Draft 2 / Standard 4 / High 8.
        # All five Fast prompts rendered under a minute. Edit, up to 8 reference images,
        # is second-wave and is NOT declared. Free for everyone (owner, card 296): no ledger row.
        "engine": "images", "label": "P-H3-Image", "pick": {},
        "status": "preview",
        "modes": ["t2i"],
        "qualities": ["fast", "draft", "standard", "high"],
        "inputs": [],
        "outputs": ["image"],
        "options": [],
        "steps": 8,
        "reasons": {
            "modes": "P-H3-Image makes images from text. Editing an image comes later.",
        },
    },
    "images.flux2": {
        # FLUX.2 [klein] 4B, the Images default ("auto"): text to image, and edits from references.
        "engine": "images", "label": "FLUX.2 [klein]", "pick": {"image_engine": "auto"}, "status": "ready",
        "qualities": None,
    },
    "images.qwen_edit": {
        # Qwen-Image-Edit (Reference Edit): changes a picture you give it; needs a reference.
        "engine": "images", "label": "Reference Edit", "pick": {"image_engine": "qwen_edit_inline"}, "status": "ready",
        "modes": ["edit"], "qualities": None,
        "reasons": {"modes": "Reference Edit changes a picture you give it. Drop a photo in first."},
    },
    "images.flux3": {
        # FLUX 3 Klein (Black Forest Labs): text to image, and up to the server's reference cap.
        "engine": "images", "label": "FLUX 3 Klein", "pick": {"image_engine": "flux3_distilled_q8"}, "status": "ready",
        "qualities": None,
    },
    "images.ideogram4": {
        # Ideogram 4: typography and layout; a reference is an optional bridge.
        "engine": "images", "label": "Ideogram 4", "pick": {"image_engine": "ideogram4_inline"}, "status": "ready",
        "qualities": None,
    },
    # ---- YuE2 ----------------------------------------------------------------
    "music.yue2": {
        "engine": "music", "label": "YuE2", "pick": {},
    },
}

_KEYS = ("label", "surface", "modes", "qualities", "lengths", "cells", "inputs",
         "outputs", "options", "pro", "ledger", "status", "steps", "fallback",
         "reasons", "pick", "rank")

# Reasons every variant gets unless it writes its own (the {label} is filled in).
_DEFAULT_REASONS = {
    "modes": "{label} does not make this kind of video.",
    "qualities": "{label} does not render this canvas.",
    "lengths": "{label} does not render this length.",
    "cells": "{label} does not render this shape.",
    "inputs": "{label} does not take this input.",
    "options": "{label} sets this itself.",
    "pro": "{label} is part of Phosphene Pro.",
}


class ManifestError(ValueError):
    """The table itself is wrong (a typo, an unknown axis value, a cycle)."""


def _resolve(vid: str, seen: tuple = ()) -> dict:
    if vid in seen:
        raise ManifestError(f"inherits cycle: {' -> '.join(seen + (vid,))}")
    if vid not in VARIANTS:
        raise ManifestError(f"unknown variant {vid!r}")
    row = VARIANTS[vid]
    eng = row.get("engine")
    if eng not in ENGINES:
        raise ManifestError(f"{vid}: unknown engine {eng!r}")
    base = (_resolve(row["inherits"], seen + (vid,)) if row.get("inherits")
            else copy.deepcopy(ENGINES[eng]))
    out = {k: copy.deepcopy(base.get(k)) for k in _KEYS}
    out["reasons"] = dict(base.get("reasons") or {})
    for k in _KEYS:
        if k == "reasons":
            out["reasons"].update(row.get("reasons") or {})
        elif k in row:
            out[k] = copy.deepcopy(row[k])
    out["surface"] = ENGINES[eng]["surface"]
    out["pro"] = bool(out.get("pro"))
    out["status"] = out.get("status") or "ready"
    out["engine"] = eng
    out["variant"] = vid.split(".", 1)[1]
    out["id"] = vid
    out["cells"] = {"exclude_flags": list((out.get("cells") or {}).get("exclude_flags") or [])}
    for k in ("inputs", "outputs", "options"):
        out[k] = list(out.get(k) or [])
    for axis, text in _DEFAULT_REASONS.items():
        out["reasons"].setdefault(axis, text.format(label=out["label"]))
    return out


def variant(vid: str) -> dict:
    """The resolved row for `engine.variant` (inheritance applied)."""
    return _resolve(vid)


def variants(engine: str | None = None) -> list[dict]:
    return [_resolve(v) for v in VARIANTS
            if engine is None or VARIANTS[v]["engine"] == engine]


def variant_for_pick(engine: str, pick: dict) -> dict:
    """Which variant the form is on, from what the form has picked
    ({"speed": "flex", "quality": "high", ...}). Every row whose `pick` matches
    is a candidate; the highest `rank` wins (Flex outranks a Fast HD cell
    because picking Flex moves the form off Fast HD; Fast HD outranks Fast and
    Best because make_job forces its recipe on that cell). No match: the
    engine's default variant."""
    best, score = None, None
    for row in variants(engine):
        want = row.get("pick") or {}
        if not want or not all(str(pick.get(k)) == str(v) for k, v in want.items()):
            continue
        s = (int(row.get("rank") or 0), len(want))
        if score is None or s > score:
            best, score = row, s
    if best is None:
        dv = ENGINES[engine].get("default_variant")
        return variant(f"{engine}.{dv}") if dv else None
    return best


def cell_allowed(row: dict, cell: dict) -> bool:
    """Would this variant render this cell? The same rule the strip greys from."""
    if not cell:
        return True
    q = row.get("qualities")
    if q is not None and cell.get("quality") not in q:
        return False
    ln = row.get("lengths")
    if ln is not None and cell.get("length") not in ln:
        return False
    return not any(bool(cell.get(f)) for f in row["cells"]["exclude_flags"])


def serves_mode(row: dict, mode: str) -> bool:
    modes = row.get("modes")
    if mode == "oneshot":
        mode = "t2v"
    return modes is None or mode in modes


def check(vid: str, *, mode: str | None = None, cell: dict | None = None,
          options: list | tuple = (), inputs: list | tuple = (),
          pro_active: bool = True) -> list[dict]:
    """Everything in this request the variant does not declare.

    Returns [] when it can render it. Otherwise one dict per problem:
    {"axis", "value", "reason", "fallback"}. make_job uses `fallback` (another
    variant id, or None = refuse) and prints `reason`; the page shows the same
    `reason` as the greyed control's tooltip, so both say the same sentence.
    """
    row = variant(vid)
    out: list[dict] = []

    def bad(axis: str, value) -> None:
        out.append({"axis": axis, "value": value, "reason": row["reasons"][axis],
                    "fallback": row.get("fallback")})

    if mode is not None and not serves_mode(row, mode):
        bad("modes", mode)
    if cell is not None and not cell_allowed(row, cell):
        q = row.get("qualities")
        ln = row.get("lengths")
        if q is not None and cell.get("quality") not in q:
            bad("qualities", cell.get("quality"))
        elif ln is not None and cell.get("length") not in ln:
            bad("lengths", cell.get("length"))
        else:
            bad("cells", cell.get("key") or cell.get("label"))
    for o in options or ():
        if o not in row["options"]:
            bad("options", o)
    for i in inputs or ():
        if i not in row["inputs"]:
            bad("inputs", i)
    if row["pro"] and not pro_active:
        bad("pro", True)
    return out


def payload(*, pro_active: bool | None = None) -> dict:
    """The table as the page reads it (JSON-safe, inheritance resolved)."""
    return {
        "schema": SCHEMA_VERSION,
        "mode_labels": dict(MODE_LABELS),
        "engines": {k: {"label": v["label"], "surface": v["surface"],
                        "default_variant": v.get("default_variant")}
                    for k, v in ENGINES.items()},
        "variants": {r["id"]: r for r in variants() if r["status"] != "lab"},
        "pro_active": pro_active,
    }


def validate() -> list[str]:
    """Structural problems in the table. The test suite asserts this is []."""
    errs: list[str] = []
    for eng, base in ENGINES.items():
        dv = base.get("default_variant")
        if dv and f"{eng}.{dv}" not in VARIANTS:
            errs.append(f"engine {eng}: default_variant {dv!r} has no row")
    for vid, raw in VARIANTS.items():
        if "." not in vid or vid.split(".", 1)[0] != raw.get("engine"):
            errs.append(f"{vid}: id must be '<engine>.<variant>'")
        unknown = set(raw) - set(_KEYS) - {"engine", "inherits"}
        if unknown:
            errs.append(f"{vid}: unknown keys {sorted(unknown)}")
        try:
            row = _resolve(vid)
        except ManifestError as e:
            errs.append(str(e))
            continue
        base = ENGINES[row["engine"]]
        for axis in AXES:
            vals, allowed = row.get(axis), base.get(axis)
            if vals is not None and allowed is not None:
                extra = set(vals) - set(allowed)
                if extra:
                    errs.append(f"{vid}: {axis} {sorted(extra)} not on engine {row['engine']}")
        for k, vocab in (("inputs", INPUTS), ("outputs", OUTPUTS), ("options", OPTIONS)):
            extra = set(row[k]) - set(vocab)
            if extra:
                errs.append(f"{vid}: unknown {k} {sorted(extra)}")
            beyond = set(row[k]) - set(base.get(k) or [])
            if beyond:
                errs.append(f"{vid}: {k} {sorted(beyond)} beyond engine {row['engine']}")
        for m in row.get("modes") or []:
            if m not in MODE_LABELS:
                errs.append(f"{vid}: mode {m!r} has no label")
        if row["status"] not in STATUSES:
            errs.append(f"{vid}: status {row['status']!r}")
        if row["pro"] and not row.get("ledger"):
            errs.append(f"{vid}: a Pro variant names its pro/ledger.json row (`ledger`)")
        fb = row.get("fallback")
        if fb is not None:
            if fb not in VARIANTS:
                errs.append(f"{vid}: fallback {fb!r} has no row")
            elif VARIANTS[fb]["engine"] != row["engine"]:
                errs.append(f"{vid}: fallback {fb!r} is another engine")
        for axis, text in (row.get("reasons") or {}).items():
            if len(text) > 140:
                errs.append(f"{vid}: reason for {axis} over 140 chars (one plain line)")
    return errs


if __name__ == "__main__":  # python3 model_capabilities.py -> table + check
    import json
    import sys
    problems = validate()
    for p in problems:
        print("FAIL", p)
    if "--json" in sys.argv:
        print(json.dumps(payload(), indent=2))
    else:
        for r in variants():
            print(f"{r['id']:<18} {r['status']:<8} {'Pro' if r['pro'] else 'Free':<5}"
                  f" modes={','.join(r['modes'] or ['*'])}"
                  f" cells-not={','.join(r['cells']['exclude_flags']) or '-'}"
                  f" options={','.join(r['options']) or '-'}")
    sys.exit(1 if problems else 0)
