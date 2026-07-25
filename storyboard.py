#!/usr/bin/env python3.11
"""Storyboard — plan a run of shots that share a character, then shoot them.

Design doc: ~/AI/projects/phosphene/decisions/director_architecture.md

WHAT THIS IS, IN PHOSPHENE'S OWN TERMS
--------------------------------------
Phosphene's thesis is "your trained character, in any scene." A storyboard is simply that at
sequence scale: many scenes, one identity, planned before you spend the render time. It is
NOT a new studio bolted on the side — it is a layer ABOVE the existing modes that composes
them. A shot is just a normal panel job in one of the modes that already exist
(text / character / remix / keyframe / extend / a2v), so anything the panel can render, a
storyboard can schedule.

Naming follows the panel's plain voice — Text, Character, Extend, Remix, Train Character —
and names the artifact (the reviewable plan), because plan-before-you-render is the point.

WHAT IT REUSES RATHER THAN REBUILDS  (integration, not duplication)
-------------------------------------------------------------------
* Execution + crash-resume: the panel ALREADY has a persistent queue with crash-resume
  (`state/panel_queue.json`, `/queue/batch`). Shots are enqueued as ordinary jobs; we do NOT
  run a second scheduler and do NOT re-implement resume. storyboard.json holds the creative
  PLAN; panel_queue.json owns EXECUTION. Two files, two jobs, no overlap.
* Outputs: clips land in `mlx_outputs/` like every other render, so they appear in the normal
  gallery, carry the usual .json sidecar, and work with Params/Extend/Expand.
* Characters: `list_characters()` is the source of truth for casting.
* Tier limits: resolution is checked against the same cap-tier clamp the panel uses.

THE TWO HARDWARE FACTS THAT SHAPE THE ARCHITECTURE
---------------------------------------------------
1. `mlx_warm_helper` holds exactly ONE pipeline kind at a time — `_free_all_but(keep_kind)`
   nulls every other pipeline. A pipeline switch is a full reload, costing MINUTES.
2. Gemma occupies that same slot (`keep_kind != "gemma_lm"` frees it). On unified memory the
   planner and the renderer are mutually exclusive, not merely co-resident-but-tight.

Consequences, which are the whole architecture:
  * PLAN -> VALIDATE -> SHOOT run strictly sequentially. The LLM is evicted before any frame
    renders, and is never re-entered mid-shoot. The plan is fully materialized up front.
  * Shots are rendered GROUPED BY MODE, not in story order, so the helper reloads a pipeline
    once per kind instead of once per shot. Clips are re-sorted by `n` at assembly.

PLANNER MODEL: Qwen3.5, NOT Gemma  (decided 2026-07-24)
--------------------------------------------------------
Gemma 3 stays for `/prompt/enhance` — different task, already tuned, already loaded. But
planning is 100% structured output, and this project already documented that "Gemma 3 has no
native tool_calls". Gemma 4 has no clean official mlx-community 4-bit build (third-party /
uncensored / GGUF forks only), so it is not shippable.

  mlx-community/Qwen3.5-4B-4bit   3.06 GB  <- default planner
  mlx-community/Qwen3.5-9B-4bit   5.98 GB  <- optional upgrade
  gemma-3-12b-it-4bit             7.50 GB  <- stays, enhancement only

The planner is therefore better at the job AND smaller than the model it replaces. Fetched
lazily on first Storyboard use, so users who never touch the feature pay 0 bytes.

Qwen's "thinking" mode can leak reasoning into output, so validity is enforced in three
layers rather than hoped for: constrained decoding (mlx_lm.sample_utils.make_logits_processors,
present in the pinned mlx-lm 0.31.1), preamble stripping, then validate_storyboard() as the
final gate with a single repair retry.

A HARD-WON PANEL RULE THIS MODULE MUST HONOR
---------------------------------------------
`mlx_ltx_panel.py` submits character jobs with `enhance=false` and the comment
"CRITICAL: don't let Gemma strip the trigger". Gemma rewrites prompts and drops the trigger
token, which silently renders a stranger instead of the trained face. Therefore:
  * The planner emits FINAL prompt text. Shots are enqueued with enhance OFF.
  * Triggers are injected mechanically in Python, never left to the model.
  * `validate_storyboard()` fails a shot whose prompt lost its trigger — see the check below.

This module is deliberately pure-stdlib and side-effect-free with respect to models: nothing
here loads a pipeline. Phase 1 (plan) and Phase 3 (shoot) call OUT to the existing panel
machinery; everything in here is schema, validation, scheduling and durable state, so it can
be unit-tested on any machine with no GPU and no weights.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1

# Modes a shot may use. These MUST exist as real panel modes — the planner is never trusted
# to invent one. Kept as a plain tuple so validation errors can list the legal set.
VALID_MODES = ("text", "character", "remix", "keyframe", "extend", "a2v")

# Modes whose pipeline the warm helper caches independently. Used only for grouping/estimation;
# the authoritative mapping lives in the helper. Shots that share a bucket render back-to-back
# without paying a pipeline reload.
_PIPELINE_BUCKET = {
    "text": "t2v",
    "character": "t2v",   # character is a UI intent over t2v + fused LoRAs
    "remix": "remix",
    "keyframe": "keyframe",
    "extend": "extend",
    "a2v": "a2v",
}

# Rough per-second-of-video render cost, by quality, in seconds of wall clock. Deliberately
# pessimistic — the estimate exists to stop someone starting a 6-hour job unaware, not to be
# a benchmark. Tuned against observed two-stage 1536x896 ~11 min for a 5 s clip.
_SECS_PER_VIDEO_SEC = {"quick": 24.0, "balanced": 60.0, "standard": 96.0, "high": 132.0}
_PIPELINE_LOAD_SECS = 90.0


class StoryboardError(Exception):
    """Raised for malformed storyboards. Message is user-facing."""


# ---------------------------------------------------------------------------
# Durable state
# ---------------------------------------------------------------------------

def board_dir(state_dir: Path, board_id: str) -> Path:
    return Path(state_dir) / "storyboards" / board_id


def save_storyboard(state_dir: Path, board: dict) -> Path:
    """Write storyboard.json ATOMICALLY.

    Renders run for hours; the panel gets killed, Pinokio restarts, Macs sleep. A torn
    storyboard.json would lose the whole run, so we always write a temp file in the same directory
    and os.replace() it (atomic within a filesystem).
    """
    fid = board.get("id")
    if not fid:
        raise StoryboardError("storyboard has no id")
    d = board_dir(state_dir, fid)
    d.mkdir(parents=True, exist_ok=True)
    target = d / "storyboard.json"
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".sb-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(board, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def load_storyboard(state_dir: Path, board_id: str) -> dict:
    p = board_dir(state_dir, board_id) / "storyboard.json"
    if not p.is_file():
        raise StoryboardError(f"no such storyboard: {board_id}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StoryboardError(f"storyboard.json is corrupt: {e}") from e


def list_storyboards(state_dir: Path) -> list[dict]:
    """Newest first. Returns light summaries, not full specs."""
    root = Path(state_dir) / "storyboards"
    out: list[dict] = []
    if not root.is_dir():
        return out
    for d in root.iterdir():
        p = d / "storyboard.json"
        if not p.is_file():
            continue
        try:
            f = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        shots = f.get("shots") or []
        out.append({
            "id": f.get("id", d.name),
            "title": f.get("title", ""),
            "created_at": f.get("created_at", 0),
            "shots": len(shots),
            "done": sum(1 for s in shots if s.get("status") == "done"),
            "failed": sum(1 for s in shots if s.get("status") == "failed"),
        })
    out.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Phase 2 — VALIDATE (no models, sub-second, runs BEFORE anything renders)
# ---------------------------------------------------------------------------

def validate_storyboard(
    board: dict,
    *,
    known_character_ids: Iterable[str] = (),
    ref_root: Path | None = None,
    max_dim: int | None = None,
) -> list[str]:
    """Return a list of human-readable problems. Empty list == good to shoot.

    The whole point: a two-hour render must never begin on a plan that cannot succeed.
    Everything here is cheap string/path/int checking, so it costs ~nothing and can run on
    every save. Errors read as a fix-list, not a stack trace.
    """
    errs: list[str] = []
    chars = set(known_character_ids or ())

    if board.get("schema") != SCHEMA_VERSION:
        errs.append(
            f"schema version {board.get('schema')!r} — this build understands {SCHEMA_VERSION}"
        )
    if not str(board.get("id") or "").strip():
        errs.append("storyboard.id is empty")

    shots = board.get("shots")
    if not isinstance(shots, list) or not shots:
        errs.append("storyboard has no shots")
        return errs

    policy = board.get("policy") or {}
    seen_n: set[int] = set()

    for idx, s in enumerate(shots):
        where = f"shot {s.get('n', idx + 1)}"
        if not isinstance(s, dict):
            errs.append(f"{where}: not an object")
            continue

        n = s.get("n")
        if not isinstance(n, int) or n < 1:
            errs.append(f"{where}: 'n' must be a positive integer")
        elif n in seen_n:
            errs.append(f"{where}: duplicate shot number {n}")
        else:
            seen_n.add(n)

        mode = s.get("mode")
        if mode not in VALID_MODES:
            errs.append(f"{where}: mode {mode!r} is not one of {', '.join(VALID_MODES)}")

        prompt = (s.get("prompt") or "").strip()
        if not prompt:
            errs.append(f"{where}: empty prompt")

        cid = s.get("character_id")
        if cid:
            if chars and cid not in chars:
                errs.append(
                    f"{where}: character {cid!r} is not installed "
                    f"(have: {', '.join(sorted(chars)) or 'none'})"
                )
            # The trigger is injected mechanically, never trusted to the LLM — but if a plan
            # arrives with a character and the trigger is missing from the prompt, the LoRA
            # will not fire and the shot silently renders a stranger. Catch it here.
            trig = (s.get("trigger") or cid or "").strip()
            if trig and not re.search(rf"\b{re.escape(trig)}\b", prompt):
                errs.append(f"{where}: prompt is missing the character trigger {trig!r}")
        elif mode == "character":
            errs.append(f"{where}: mode 'character' requires a character_id")

        dur = s.get("duration_s")
        if not isinstance(dur, (int, float)) or not (0 < float(dur) <= 60):
            errs.append(f"{where}: duration_s must be between 0 and 60 (got {dur!r})")

        refs = s.get("refs") or []
        if not isinstance(refs, list):
            errs.append(f"{where}: refs must be a list")
        else:
            for r in refs:
                rp = Path(r)
                if ref_root is not None and not rp.is_absolute():
                    rp = Path(ref_root) / rp
                if not rp.is_file():
                    errs.append(f"{where}: reference image not found: {r}")
        if mode == "remix" and not refs:
            errs.append(f"{where}: mode 'remix' needs at least one reference image")

    # Resolution legality for the active tier. Q4 machines clamp; a plan that assumes 1536
    # on a 16 GB Mac would either clamp silently or swap, so surface it now.
    if max_dim:
        for key in ("draft", "final"):
            p = policy.get(key) or {}
            w, h = p.get("width"), p.get("height")
            if isinstance(w, int) and isinstance(h, int) and max(w, h) > max_dim:
                errs.append(
                    f"policy.{key}: {w}x{h} exceeds this machine's {max_dim}px cap — "
                    f"lower it or the render will clamp"
                )
    return errs


# ---------------------------------------------------------------------------
# Phase 3 — SHOOT: scheduling
# ---------------------------------------------------------------------------

def shooting_order(shots: list[dict]) -> list[dict]:
    """Group shots by pipeline bucket so the warm helper reloads once per KIND, not per shot.

    This is the single biggest wall-clock win on Apple Silicon and it exists purely because
    of `_free_all_but(keep_kind)`: rendering in story order across 3 interleaved modes costs
    one full pipeline load per switch (~90 s each), while grouped rendering costs one per
    bucket. 12 alternating shots: ~12 loads -> 3.

    Order within a bucket, and the order of buckets, is by first appearance in the story, so
    output stays deterministic and a resumed run reproduces the same sequence. Clips are
    re-sorted by `n` at assembly, so viewing order is unaffected.
    """
    pending = [s for s in shots if s.get("status") not in ("done", "skipped")]
    bucket_first: dict[str, int] = {}
    for s in pending:
        b = _PIPELINE_BUCKET.get(s.get("mode"), "t2v")
        n = s.get("n") or 0
        if b not in bucket_first or n < bucket_first[b]:
            bucket_first[b] = n
    return sorted(
        pending,
        key=lambda s: (
            bucket_first.get(_PIPELINE_BUCKET.get(s.get("mode"), "t2v"), 1 << 30),
            s.get("n") or 0,
        ),
    )


def estimate(board: dict, *, pass_name: str = "final") -> dict:
    """Wall-clock estimate for a pass, accounting for pipeline reloads.

    Deliberately pessimistic. Its job is to let someone see '2 h 40 m' BEFORE committing,
    and to show what grouped scheduling saves versus naive story order.
    """
    shots = [s for s in (board.get("shots") or []) if s.get("status") not in ("done", "skipped")]
    policy = (board.get("policy") or {}).get(pass_name) or {}
    quality = policy.get("quality", "balanced")
    per_sec = _SECS_PER_VIDEO_SEC.get(quality, 60.0)

    render = sum(float(s.get("duration_s") or 0) * per_sec for s in shots)

    grouped_loads = len({_PIPELINE_BUCKET.get(s.get("mode"), "t2v") for s in shots})
    naive_loads = 0
    prev = None
    for s in sorted(shots, key=lambda x: x.get("n") or 0):
        b = _PIPELINE_BUCKET.get(s.get("mode"), "t2v")
        if b != prev:
            naive_loads += 1
            prev = b

    return {
        "pass": pass_name,
        "shots": len(shots),
        "render_secs": round(render),
        "pipeline_loads": grouped_loads,
        "total_secs": round(render + grouped_loads * _PIPELINE_LOAD_SECS),
        "naive_total_secs": round(render + naive_loads * _PIPELINE_LOAD_SECS),
        "saved_secs": round((naive_loads - grouped_loads) * _PIPELINE_LOAD_SECS),
    }


def ensure_trigger(prompt: str, trigger: str) -> str:
    """Guarantee the character trigger is present, mechanically.

    The panel already learned this the hard way — character jobs are submitted with
    `enhance=false` under the comment "CRITICAL: don't let Gemma strip the trigger", because
    a rewritten prompt that loses the token renders a stranger's face and reads as a model
    bug. So we never rely on the planner to keep it: we put it there in Python.

    Idempotent, and word-boundary aware so "bizarrotrn" is not considered present merely
    because "bizarrotrnx" appears.
    """
    p = (prompt or "").strip()
    t = (trigger or "").strip()
    if not t:
        return p
    if re.search(rf"\b{re.escape(t)}\b", p):
        return p
    return f"{t} {p}" if p else t


def shot_to_job(shot: dict, policy_pass: dict) -> dict:
    """Translate one storyboard shot into the panel's ORDINARY job form fields.

    Deliberately produces the same shape a human clicking Generate would produce, so shots
    flow through `/queue/add` -> `make_job` -> the normal worker, land in `mlx_outputs/`, and
    show up in the usual gallery with a usual sidecar. No private execution path.

    NOTE `enhance: "off"` is not optional — see ensure_trigger() above.
    """
    trigger = (shot.get("trigger") or shot.get("character_id") or "").strip()
    prompt = shot.get("prompt") or ""
    if trigger:
        prompt = ensure_trigger(prompt, trigger)
    job = {
        "mode": shot.get("mode") or "text",
        "prompt": prompt,
        "quality": policy_pass.get("quality", "balanced"),
        "width": policy_pass.get("width"),
        "height": policy_pass.get("height"),
        "frames": policy_pass.get("frames"),
        "enhance": "off",          # never let Gemma touch a planned prompt
        "auto_open": "off",        # batches must not steal the viewer
    }
    if shot.get("character_id"):
        job["character_id"] = shot["character_id"]
    if shot.get("seed") is not None:
        job["seed"] = shot["seed"]
    return {k: v for k, v in job.items() if v is not None}


def new_storyboard(board_id: str, title: str, *, shots: list[dict] | None = None,
             cast: list[dict] | None = None, policy: dict | None = None) -> dict:
    """Build an empty, schema-correct storyboard. Kept here so the planner, the tests and any
    future importer all produce the identical shape."""
    return {
        "schema": SCHEMA_VERSION,
        "id": board_id,
        "title": title,
        "created_at": int(time.time()),
        "cast": cast or [],
        "policy": policy or {
            "draft": {"quality": "quick", "width": 640, "height": 480, "frames": 49},
            "final": {"quality": "balanced", "width": 1024, "height": 576, "frames": 121},
        },
        "shots": shots or [],
    }
