#!/usr/bin/env python3.11
"""Director — plan a whole short film / music video, then shoot it.

Design doc: ~/AI/projects/phosphene/decisions/director_architecture.md

THE TWO HARDWARE FACTS THAT SHAPE THIS MODULE
---------------------------------------------
1. `mlx_warm_helper` holds exactly ONE pipeline kind at a time — `_free_all_but(keep_kind)`
   nulls every other pipeline. A pipeline switch is a full reload, costing MINUTES.
2. Gemma occupies that same slot (`keep_kind != "gemma_lm"` frees it). On unified memory the
   planner and the renderer are mutually exclusive, not merely co-resident-but-tight.

Consequences, which are the whole architecture:
  * PLAN -> VALIDATE -> SHOOT run strictly sequentially. The LLM is evicted before any frame
    renders, and is never re-entered mid-shoot. The plan is fully materialized up front.
  * Shots are rendered GROUPED BY MODE, not in story order, so the helper reloads a pipeline
    once per kind instead of once per shot. Clips are re-sorted by `n` at assembly.

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


class DirectorError(Exception):
    """Raised for malformed film specs. Message is user-facing."""


# ---------------------------------------------------------------------------
# Durable state
# ---------------------------------------------------------------------------

def film_dir(state_dir: Path, film_id: str) -> Path:
    return Path(state_dir) / "films" / film_id


def save_film(state_dir: Path, film: dict) -> Path:
    """Write film.json ATOMICALLY.

    Renders run for hours; the panel gets killed, Pinokio restarts, Macs sleep. A torn
    film.json would lose the whole run, so we always write a temp file in the same directory
    and os.replace() it (atomic within a filesystem).
    """
    fid = film.get("id")
    if not fid:
        raise DirectorError("film has no id")
    d = film_dir(state_dir, fid)
    d.mkdir(parents=True, exist_ok=True)
    target = d / "film.json"
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".film-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(film, fh, indent=2, ensure_ascii=False)
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


def load_film(state_dir: Path, film_id: str) -> dict:
    p = film_dir(state_dir, film_id) / "film.json"
    if not p.is_file():
        raise DirectorError(f"no such film: {film_id}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise DirectorError(f"film.json is corrupt: {e}") from e


def list_films(state_dir: Path) -> list[dict]:
    """Newest first. Returns light summaries, not full specs."""
    root = Path(state_dir) / "films"
    out: list[dict] = []
    if not root.is_dir():
        return out
    for d in root.iterdir():
        p = d / "film.json"
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

def validate_film(
    film: dict,
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

    if film.get("schema") != SCHEMA_VERSION:
        errs.append(
            f"schema version {film.get('schema')!r} — this build understands {SCHEMA_VERSION}"
        )
    if not str(film.get("id") or "").strip():
        errs.append("film.id is empty")

    shots = film.get("shots")
    if not isinstance(shots, list) or not shots:
        errs.append("film has no shots")
        return errs

    policy = film.get("policy") or {}
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


def estimate(film: dict, *, pass_name: str = "final") -> dict:
    """Wall-clock estimate for a pass, accounting for pipeline reloads.

    Deliberately pessimistic. Its job is to let someone see '2 h 40 m' BEFORE committing,
    and to show what grouped scheduling saves versus naive story order.
    """
    shots = [s for s in (film.get("shots") or []) if s.get("status") not in ("done", "skipped")]
    policy = (film.get("policy") or {}).get(pass_name) or {}
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


def new_film(film_id: str, title: str, *, shots: list[dict] | None = None,
             cast: list[dict] | None = None, policy: dict | None = None) -> dict:
    """Build an empty, schema-correct film spec. Kept here so the planner, the tests and any
    future importer all produce the identical shape."""
    return {
        "schema": SCHEMA_VERSION,
        "id": film_id,
        "title": title,
        "created_at": int(time.time()),
        "cast": cast or [],
        "policy": policy or {
            "draft": {"quality": "quick", "width": 640, "height": 480, "frames": 49},
            "final": {"quality": "balanced", "width": 1024, "height": 576, "frames": 121},
        },
        "shots": shots or [],
    }
