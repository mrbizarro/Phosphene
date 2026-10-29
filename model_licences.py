"""Per-model licence acceptance - which models need it, and what was accepted.

Some of the models Phosphene downloads come with licences that bind whoever
downloads or runs them, and that ask for more than a line in the README:

  * MiniMax H3 (Hailuo H3) - the MiniMax H3 Community License. It covers only
    its "Applicable Territory" (worldwide except the EU, the UK, South Korea
    and the US), says a product must bind each user to its use restrictions
    (s. V.2), show "MiniMax H3" in the UI (s. IV.2) and keep a way to report
    suspected misuse (s. V.5). Its Turbo / Fast adapters are H3 Model
    Derivatives, so the same licence covers them.
  * Ideogram 4, YuE2 (+ the helpers Music Studio uses), FLUX.1 Krea [dev] and
    Bria FIBO - non-commercial licences.

Before the panel downloads or first runs one of them, the user sees that
model's licence screen (name, link, the restrictions that matter, and an
explicit "I accept"). The acceptance is stored on this Mac in
`<state>/model_licences.json` - model, licence version, time, app version -
and asked again when the licence version changes. A marker file
`<state>/licence-<key>-<version>.accepted` is written alongside, because the
Pinokio installer checks the same matching pair through this module from bash.
Missing, corrupt or mismatched records require explicit acceptance again.

Territory: Phosphene shows the H3 territory notice and the user confirms it in
the accept box. It does not look up the user's location - the licence asks
licensees not to use H3 outside its territory and asks products to bind and
notify users (s. V.2, V.4); it does not ask for geo-blocking.

Apache-2.0 models (FLUX.2 klein, Qwen-Image-Edit, Z-Image-Turbo, Gemma 4) and
the LTX packs (whose licence binds on download/use and is shipped as a file
next to the weights) need no screen.

Stdlib only; no import of the panel (the panel imports this).
"""
from __future__ import annotations

import json
import fcntl
import uuid
import os
import threading
import time
from pathlib import Path

REPORT_MISUSE_URL = ("https://github.com/mrbizarro/Phosphene/issues/new"
                     "?title=Misuse%20report%3A%20")

# Static facts per model. `version` is the licence's own date: change it when
# the licence text changes and everyone is asked again.
LICENCES: dict[str, dict] = {
    "flux3": {
        "model": "FLUX 3 Klein by Black Forest Labs",
        "licence": "FLUX Kommunity License v1.0",
        "licensor": "Black Forest Labs Inc.",
        "version": "flux-kommunity-1.0",
        "url": "https://huggingface.co/black-forest-labs/flux-3-image-klein/blob/main/LICENSE.md",
        "non_commercial": False,
        "covers": "FLUX 3 Image Klein and Klein Base, including quantized weights",
    },
    "h3": {
        "model": "MiniMax H3 (Hailuo H3)",
        "licence": "MiniMax H3 Community License Agreement",
        "licensor": "MiniMax (Nanonoble Pte. Ltd.)",
        "version": "2026-08-02",
        "url": "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE",
        "non_commercial": False,
        "covers": "the Hailuo H3 engine and its Turbo / Fast speed adapters",
        "report_url": REPORT_MISUSE_URL,
        "excluded_territories": ["European Union", "United Kingdom",
                                 "South Korea", "United States"],
    },
    "ideogram4": {
        "model": "Ideogram 4",
        "licence": "Ideogram Non-Commercial Model Agreement",
        "licensor": "Ideogram, Inc.",
        "version": "2026-06-03",
        "url": "https://huggingface.co/ideogram-ai/ideogram-4-fp8/blob/main/LICENSE.md",
        "non_commercial": True,
        "covers": "the Ideogram 4 image engine",
    },
    "yue2": {
        "model": "YuE2 (Music Studio)",
        "licence": "CC BY-NC 4.0 plus the YuE2 individual-creator permission",
        "licensor": "Multimodal Art Projection (m-a-p)",
        "version": "2026-09-16",
        "url": "https://github.com/multimodal-art-projection/YuE/blob/main/MODEL_LICENSE",
        "non_commercial": True,
        "covers": "YuE2-3B and the helper models Music Studio downloads with it",
    },
    "flux1_dev": {
        "model": "FLUX.1 Krea [dev]",
        "licence": "FLUX.1 [dev] Non-Commercial License",
        "licensor": "Black Forest Labs Inc.",
        "version": "flux-1-dev-non-commercial",
        "url": "https://huggingface.co/black-forest-labs/FLUX.1-Krea-dev/blob/main/LICENSE.md",
        "non_commercial": True,
        "covers": "the FLUX.1 Krea [dev] image engine",
    },
    "fibo": {
        "model": "Bria FIBO",
        "licence": "Bria FIBO licence (CC BY-NC 4.0)",
        "licensor": "Bria AI",
        "version": "bria-fibo",
        "url": "https://huggingface.co/briaai/FIBO",
        "non_commercial": True,
        "covers": "the Bria FIBO image engine",
    },
}

# User-facing words (drafted by Codex, fact-checked against each licence text).
COPY: dict = {'modal_title': 'Model licence',
 'decline_button': 'Cancel',
 'accept_button': 'Accept and continue',
 'full_licence_link': 'Read full licence',
 'install_gate_h3': 'FATAL error: Open Phosphene, click Hailuo H3 in the engine '
                    'switcher, read the MiniMax H3 licence and tick I accept, then run '
                    'Install Hailuo H3 again.',
 'render_refusal': 'The licence for {title} has not been accepted on this Mac yet. '
                   'Open Phosphene and accept the licence once to continue.',
 'h3': {'title': 'MiniMax H3 licence',
        'summary': 'You agree to the MiniMax H3 licence, including its territory '
                   'limits and Acceptable Use Policy, for H3 and its Turbo / Fast '
                   'adapters.',
        'terms': ['Use, copy, share or display H3 and its outputs only within the '
                  "licence's territory.",
                  'Follow the Acceptable Use Policy: no unauthorized impersonation, '
                  'harm to minors, election disinformation, harassment or military '
                  'use.',
                  'Clearly and prominently label content as machine-generated when '
                  'posting it publicly.',
                  'Do not use H3 or its outputs to improve AI models other than H3 or '
                  'its Model Derivatives.',
                  'If your commercial products and services generate over USD 20M in '
                  "yearly revenue, get MiniMax's prior written authorization."],
        'badge': 'Territory restrictions',
        'accept_label': 'I have read and accept the MiniMax H3 licence and will only '
                        "use H3 and its outputs inside the licence's territory.",
        'territory_notice': 'This licence does not cover use of H3 or its outputs in '
                            'the European Union, United Kingdom, South Korea or United '
                            'States. Phosphene does not check your location. Contact '
                            'MiniMax about a licence for these regions.',
        'report_line': 'Report suspected misuse of H3 outputs made with Phosphene '
                       'through a GitHub issue on the Phosphene repo.',
        'credit_line': 'MiniMax H3'},
 'ideogram4': {'title': 'Ideogram 4 licence',
               'summary': 'You agree to use Ideogram 4 only for non-commercial '
                          'purposes under the Ideogram Non-Commercial Model Agreement.',
               'terms': ['No use that directly or indirectly generates revenue or '
                         'seeks commercial advantage.',
                         'Do not make images for revenue-generating products or '
                         'services, or to advertise or promote them.',
                         "Follow Ideogram's Acceptable Use Policy and respect other "
                         "people's rights.",
                         'No military, surveillance or biometric use, and nothing that '
                         "violates other people's rights, including rights of "
                         'publicity.',
                         'Do not bypass content filters, watermarks or other safety '
                         'measures.'],
               'badge': 'Non-commercial use only',
               'accept_label': 'I have read and accept the Ideogram Non-Commercial '
                               'Model Agreement for non-commercial use only.'},
 'yue2': {'title': 'YuE2 model licence',
          'summary': "Music Studio's YuE2 weights and helper models are CC BY-NC 4.0. "
                     'YuE2 adds a permission for individual creators to make and '
                     'monetize songs.',
          'terms': ['Personal users, content creators and musicians acting as '
                    'individuals may use the weights free of charge to make songs and '
                    'sell or monetize them.',
                    "This extra permission excludes companies' commercial use of the "
                    'weights, which needs a licence from the YuE2 authors.',
                    'The extra permission covers songs, not selling or commercially '
                    'redistributing the weights. Other CC BY-NC 4.0 conditions still '
                    'apply to weights.',
                    'This permission forbids illegal, harmful, deceptive or unethical '
                    'use of weights or outputs, including fraud, harassment or '
                    'deceptive impersonation.',
                    'When sharing weights or adapted weights, credit YuE2, the model '
                    'name and its source repository. Credit on generated songs is '
                    'optional.'],
          'badge': 'Non-commercial use only',
          'accept_label': 'I accept the weight licence: non-commercial use only, with '
                          'the stated extra permission for individual creators and its '
                          'conditions.'},
 'flux1_dev': {'title': 'FLUX.1 Krea [dev] licence',
               'summary': 'You agree to use FLUX.1 Krea [dev] only for non-commercial, '
                          'non-production purposes under the FLUX.1 [dev] '
                          'Non-Commercial License.',
               'terms': ['No model use for revenue-generating activity or for direct '
                         'interactions with, or impact on, end users.',
                         'No model use for military purposes, surveillance or '
                         'biometric processing.',
                         'Maintain content filters or review outputs for unlawful or '
                         'infringing content before sharing or displaying them.',
                         'Disclose AI generation or modification where required by '
                         'law.',
                         'Do not use outputs to train, fine-tune or distill models '
                         'that compete with FLUX.1 [dev] or FLUX.1 Kontext [dev].'],
               'badge': 'Non-commercial use only',
               'accept_label': 'I have read and accept the FLUX.1 [dev] Non-Commercial '
                               'License for non-commercial use only.'},
 'fibo': {'title': 'Bria FIBO licence',
          'summary': 'You agree to use Bria FIBO for non-commercial purposes under its '
                     'licence based on CC BY-NC 4.0.',
          'terms': ['Use the model only for non-commercial purposes.',
                    'Commercial use is not covered by this licence.',
                    'Give credit to Bria when sharing the model or adaptations.'],
          'badge': 'Non-commercial use only',
          'accept_label': 'I have read and accept the Bria FIBO licence for '
                          'non-commercial use only.'}}

COPY["flux3"] = {
    "title": "FLUX 3 Klein licence",
    "summary": "The app is MIT. These model weights have a separate licence from Black Forest Labs.",
    "badge": "Commercial outputs below USD 5M revenue",
    "terms": [
        "Commercial output generation is permitted only while your gross annualized revenue, including affiliates, is below USD 5 million. Otherwise obtain commercial terms from Black Forest Labs.",
        "Review outputs for unlawful or infringing content before sharing or displaying them. Disclose AI generation or modification where the law requires it.",
        "Follow the full licence, including its use restrictions. Rights to the model are granted directly to you by Black Forest Labs, not by Phosphene.",
        "When sharing model weights, include the full licence and the Black Forest Labs attribution notice. The community quantizations are modified models, not official Black Forest Labs products.",
    ],
    "accept_label": "I have read and accept the FLUX Kommunity License and will review outputs before sharing them.",
}

STORE_NAME = "model_licences.json"
_LOCK = threading.Lock()


def _store(state_dir) -> Path:
    return Path(state_dir) / STORE_NAME


def marker_path(state_dir, key: str) -> Path:
    return Path(state_dir) / f"licence-{key}-{LICENCES[key]['version']}.accepted"


def load(state_dir) -> dict:
    try:
        data = json.loads(_store(state_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_accepted(state_dir, key: str) -> bool:
    if key not in LICENCES:
        return True
    rec = load(state_dir).get(key)
    if not isinstance(rec, dict) or rec.get("version") != LICENCES[key]["version"]:
        return False
    try:
        marker = json.loads(marker_path(state_dir, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    # Both representations must describe the same explicit acceptance. Never
    # repair either from weights or from an unpaired/stale record.
    return bool(marker == rec and rec.get("accepted_at")
                and rec.get("licence") == LICENCES[key]["licence"]
                and rec.get("statement") == COPY[key]["accept_label"])


def accept(state_dir, key: str, app_version: str | None = None) -> dict:
    """Record that the user accepted `key`'s licence (current version)."""
    if key not in LICENCES:
        raise KeyError(f"unknown licence {key!r}")
    rec = {
        "acceptance_id": uuid.uuid4().hex,
        "licence": LICENCES[key]["licence"],
        "version": LICENCES[key]["version"],
        "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app_version": app_version or "",
        "statement": (COPY.get(key) or {}).get("accept_label", ""),
    }
    state = Path(state_dir)
    with _LOCK:
        state.mkdir(parents=True, exist_ok=True)
        # Two panels can share one state dir: lock the read-modify-write
        # across processes too, and give every writer its own temp file.
        with open(state / (STORE_NAME + ".lock"), "a") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            data = load(state)
            data[key] = rec
            # Publish the marker first and JSON last. A partial save cannot
            # authorize: readers require an identical pair. Unique IDs keep
            # same-second attempts distinct. Lock failure must fail closed.
            _atomic_write(marker_path(state, key), rec)
            _atomic_write(_store(state), data)
    return rec


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def describe(state_dir, key: str) -> dict:
    """Everything the licence screen shows for one model."""
    data = load(state_dir)
    rec = data.get(key) if isinstance(data.get(key), dict) else None
    out = dict(LICENCES[key])
    out.update(COPY.get(key) or {})
    out["key"] = key
    out["accepted"] = is_accepted(state_dir, key)
    out["accepted_at"] = (rec or {}).get("accepted_at") if out["accepted"] else None
    return out


def describe_all(state_dir) -> dict:
    ui = {k: v for k, v in COPY.items() if k not in LICENCES}
    return {"licences": [describe(state_dir, k) for k in LICENCES], "ui": ui}


# ---- which licence a piece of work needs --------------------------------------
def licence_for_image_cfg(cfg) -> str | None:
    """The licence an image-engine config needs, from its mflux family/model."""
    if cfg is not None and getattr(cfg, "kind", "") == "flux3":
        return "flux3"
    if cfg is None or getattr(cfg, "kind", "") != "mflux":
        return None
    family = str(getattr(cfg, "mflux_family", "") or "").lower()
    model = str(getattr(cfg, "mflux_model", "") or "").lower()
    if family == "ideogram" or "ideogram" in model:
        return "ideogram4"
    if family == "fibo" or "fibo" in model:
        return "fibo"
    # "auto" is the engine's own family default (image_engine resolves the
    # family from the model name), so it must not let Krea through.
    if "krea" in model or "flux.1-dev" in model or (
            family in ("", "flux1", "auto") and model in ("dev", "dev-fill")):
        return "flux1_dev"
    return None


def licence_for_job(params: dict, image_cfg_for=None) -> str | None:
    """The licence a queued job needs before it may run, or None.

    `image_cfg_for(engine_override, params)` resolves an image job's engine
    config (only consulted for mode == "image")."""
    p = params or {}
    mode = str(p.get("mode") or "").strip().lower()
    engine = str(p.get("engine") or "").strip().lower()
    if mode == "music" or engine == "music":
        return "yue2"
    if engine == "h3":
        return "h3"
    if mode == "image":
        override = str(p.get("engine_override") or "auto").strip().lower()
        if override == "ideogram4_inline":
            return "ideogram4"
        if image_cfg_for is not None:
            try:
                return licence_for_image_cfg(image_cfg_for(override, p))
            except Exception:  # noqa: BLE001 - the worker re-checks with the real cfg
                return None
    return None


def refusal(state_dir, key: str | None) -> dict | None:
    """The 451 payload when `key` is set and not yet accepted; else None."""
    if not key or is_accepted(state_dir, key):
        return None
    info = describe(state_dir, key)
    tmpl = COPY.get("render_refusal") or (
        "The licence for {title} has not been accepted on this Mac yet. "
        "Open Phosphene and accept the licence once to continue.")
    return {"ok": False, "code": "licence_required", "licence": key,
            "error": tmpl.replace("{title}", info["model"]),
            "url": info["url"]}


if __name__ == "__main__":
    import sys
    # Read-only shell gate. Unknown keys or interpreter/read failures refuse.
    if len(sys.argv) != 4 or sys.argv[2] not in LICENCES:
        sys.exit(1)
    sys.exit(0 if (sys.argv[3] == LICENCES[sys.argv[2]]["version"]
                   and is_accepted(sys.argv[1], sys.argv[2])) else 1)
