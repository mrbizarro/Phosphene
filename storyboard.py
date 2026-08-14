#!/usr/bin/env python3.11
"""Storyboard — plan a run of shots that share a character, then shoot them.


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
WHAT ACTUALLY SHIPS IS GEMMA. Read this section as the decision record it is, not as a
description of the running system: `storyboard_planner.py` plans on
`mlx_models/gemma-3-12b-it-4bit` — the weights the panel already downloads for
`/prompt/enhance` — so the Storyboard tab costs a user zero new bytes. Qwen3.5 remains the
target and is a one-line switch (`LTX_STORYBOARD_PLANNER`); the reasoning below is why.
See the MODEL section at the top of `storyboard_planner.py` for the shipped arrangement.

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

# Storyboard mode -> the mode string the PANEL'S job form actually speaks.
#
# This is load-bearing and it is not cosmetic. `mlx_ltx_panel.py` has exactly one backend
# video mode for both of v1's shot types: t2v. "Character" is a UI INTENT there, not a mode —
# the panel's own setMode('character') sets the hidden #mode field to 't2v' and lets
# `character_id` drive the LoRA stack ("Mode hidden field still 't2v' — backend doesn't know
# 'character' and doesn't need to"). Posting mode="character" or mode="text" to /queue/add
# reaches run_job_inner with a mode nothing dispatches on.
_PANEL_MODE = {
    "text": "t2v",
    "character": "t2v",
    "remix": "t2v",        # remix rides t2v + an IC-LoRA ref; not enqueued in v1 (no refs)
    "keyframe": "keyframe",
    "extend": "extend",
    "a2v": "a2v",
}

# Engines a shot may name. Anything else falls back to the built-in one.
VALID_ENGINES = ("ltx", "h3")
DEFAULT_ENGINE = "ltx"

# The FILM-level engine choice, which the user makes once in the brief.
#   "auto" — the plan decides per shot: a cast shot goes to LTX (that is where
#            character LoRAs load), everything else to H3.
#   "h3"   — every shot on Hailuo H3. The planner writes the whole film in H3's
#            three-field dialect. A trained character cannot come along: H3
#            stacks no LoRAs, so a cast shot would render a stranger.
#   "ltx"  — every shot on LTX-2.3. The planner drops the H3 dialect entirely.
# It is a PLANNING input, not a post-hoc filter: the two engines want prompts
# written differently (`_assemble_h3_prompt` vs `_assemble_ltx_prompt`), so
# changing it on an existing film means re-planning, not re-labelling.
ENGINE_MODES = ("auto", "h3", "ltx")
DEFAULT_ENGINE_MODE = "auto"

# A film is shot TWICE: a cheap draft pass you watch, then a delivery pass of
# only the shots you kept. Which pass a shot has already been through is a
# property of that PASS, and the board records it as the pass's own output key —
# never as the shot's single `status`, which only ever describes the most recent
# thing that happened to it.
#
# That distinction is load-bearing and it was once got wrong: the scheduler
# filtered on `status not in ("done", "skipped")` while the reconciler set
# `status = "done"` the moment the DRAFT landed, so by the time the delivery
# pass asked for work every shot looked finished. `/storyboard/render pass=final`
# answered 202 and enqueued nothing, for every film, forever.
PASS_NAMES = ("draft", "final")
_PASS_OUTPUT_KEY = {"draft": "draft_output", "final": "final_output"}
_PASS_JOB_KEY = {"draft": "draft_job_id", "final": "final_job_id"}


def pass_output_key(pass_name: str = "draft") -> str:
    """The board field holding this pass's rendered clip."""
    return _PASS_OUTPUT_KEY.get((pass_name or "draft").strip().lower(), "draft_output")


def pass_job_key(pass_name: str = "draft") -> str:
    """The board field holding this pass's job id."""
    return _PASS_JOB_KEY.get((pass_name or "draft").strip().lower(), "draft_job_id")


def shot_pass_done(shot: dict, pass_name: str = "draft") -> bool:
    """Has THIS shot already been through THIS pass? The only correct test."""
    return bool(shot.get(pass_output_key(pass_name)))


def shots_pending(shots: Iterable[dict], pass_name: str = "draft") -> list[dict]:
    """Shots this pass still has to render, in board order.

    A cut shot is out of every pass. Everything else is in until it has that
    pass's own output — a finished draft does NOT excuse a shot from delivery.
    """
    return [s for s in (shots or [])
            if isinstance(s, dict)
            and s.get("status") != "skipped"
            and not shot_pass_done(s, pass_name)]


def resolve_engine(shot: dict, *, engine_mode: str = DEFAULT_ENGINE_MODE,
                   h3_available: bool = True) -> str:
    """The engine this shot will ACTUALLY render on. One function, every caller.

    Precedence, strongest first:
      1. no H3 pack on this machine -> LTX, always;
      2. the shot is cast -> LTX, always (H3 stacks no LoRAs, so a cast shot on
         H3 renders a stranger — the exact failure `ensure_trigger` exists for);
      3. the film's engine mode, when it is not "auto";
      4. what the plan wrote on the shot.
    """
    if not h3_available:
        return "ltx"
    if shot.get("character_id"):
        return "ltx"
    mode = (engine_mode or DEFAULT_ENGINE_MODE).strip().lower()
    if mode in ("h3", "ltx"):
        return mode
    return shot_engine(shot)

# LTX renders at 24 fps and the sampler requires frames % 8 == 1.
LTX_FPS = 24

# H3's duration axis. The panel owns the canonical table (H3_TIERS, built from a quality axis
# and a length axis); these are the LENGTH keys and the frame count each one delivers, on the
# 17n+5 grid the runner snaps to. Duplicated here — as data, not as a second cost model — so
# `shot_to_job()` emits a self-consistent job dict without importing the panel (this module is
# deliberately pure-stdlib so it unit-tests with no GPU and no weights). make_job re-stamps
# width/height/frames from the real cell, so the panel is still the authority; a drift here
# can only make the pre-flight job dict look wrong, never make a render come out wrong.
H3_LENGTHS = ("3s", "5s", "10s", "15s")
_H3_LENGTH_SECONDS = {"3s": 3.0, "5s": 5.0, "10s": 10.0, "15s": 15.0}
_H3_LENGTH_FRAMES = {"3s": 73, "5s": 124, "10s": 243, "15s": 362}
# How many chained 5 s windows each length renders as. Anything past 5 s is
# N windows stitched by the runner, and every window is asked for a prompt.
_H3_LENGTH_WINDOWS = {"3s": 1, "5s": 1, "10s": 2, "15s": 3}

# Does this shot have spoken lines? The planner writes dialogue in explicit
# <d>…</d> tags, so this is a DERIVATION rather than a guess — which is the only
# reason the storyboard lane is allowed to decide `no_voice` automatically at
# all.
#
# The lookahead is load-bearing: a bare `<d>\s*\S` matches `<d></d>`, because
# the `<` of the CLOSING tag is itself non-whitespace. An empty tag is a planner
# artefact and means silence, so it must not load the voice.
_HAS_DIALOGUE = re.compile(r"<d>\s*(?!</\s*d\s*>)\S", re.I)

# A pass's quality (the LTX vocabulary the policy speaks) -> H3's canvas axis.
# quick 640x384 · standard 768x448 · high 1024x576 are the three offered canvases;
# "native" (1344x768) is deliberately unreachable from a storyboard pass — it is a ~20 min
# clip with Turbo and ~45 without, which is not a cost any pass-level choice should be able
# to opt someone into silently.
_H3_QUALITY_FOR_PASS = {
    "quick": "draft",
    "balanced": "standard",
    "standard": "high",
    "high": "high",
}

# Rough per-second-of-video render cost, by quality, in seconds of wall clock. Deliberately
# pessimistic — the estimate exists to stop someone starting a 6-hour job unaware, not to be
# a benchmark. Tuned against observed two-stage 1536x896 ~11 min for a 5 s clip.
_SECS_PER_VIDEO_SEC = {"quick": 24.0, "balanced": 60.0, "standard": 96.0, "high": 132.0}
_PIPELINE_LOAD_SECS = 90.0

# H3 FALLBACK cost, per second of video, by canvas. The panel passes `h3_cost=` into
# estimate() and that hook wins every time — it reads H3_TIERS[cell]["eta_min"], which is
# MEASURED wall clock per cell and is the only number allowed on screen. This table exists so
# storyboard.py keeps working (and keeps unit-testing) standalone, and it is derived from the
# same measurements rather than invented:
#   draft 3s     3.0 min / 3 s  =  60 s per video-second
#   standard 5s  9.1 min / 5 s  = 109
#   high 5s     18.8 min / 5 s  = 226   (no Turbo)
#   native 5s   44.9 min / 5 s  = 538   (no Turbo, never selected by a pass)
# An H3 clip is a fresh subprocess that loads its own weights, so this per-clip number is
# END TO END — which is why an H3 bucket adds no separate pipeline-load charge below.
_H3_SECS_PER_VIDEO_SEC = {"draft": 60.0, "standard": 109.0, "high": 226.0, "native": 538.0}


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

def validate_storyboard_detail(
    board: dict,
    *,
    known_character_ids: Iterable[str] = (),
    ref_root: Path | None = None,
    max_dim: int | None = None,
) -> list[dict]:
    """Every check `validate_storyboard()` makes, STRUCTURED. Empty list == good to shoot.

    Each entry is::

        {"code": "missing_trigger",         # stable machine name, never translated
         "n": 3 | None,                     # which shot, when it is about one
         "field": "prompt" | None,          # which control to focus / flag
         "message": "<the exact string validate_storyboard() has always returned>",
         "data": {...}}                     # whatever a fix button needs

    This exists so the UI can render its own human copy per `code` and still put a Fix button
    beside the right control, WITHOUT regexing English out of `message`. `validate_storyboard()`
    below is now a formatter over this, so the strings stay byte-identical by construction and
    the panel's copy can never drift from the check that produced it.

    The whole point of both: a two-hour render must never begin on a plan that cannot succeed.
    Everything here is cheap string/path/int checking, so it costs ~nothing and can run on
    every save. Errors read as a fix-list, not a stack trace.
    """
    errs: list[dict] = []
    chars = set(known_character_ids or ())

    def add(code: str, message: str, *, n: int | None = None,
            field: str | None = None, **data: Any) -> None:
        errs.append({"code": code, "n": n, "field": field,
                     "message": message, "data": data})

    if board.get("schema") != SCHEMA_VERSION:
        add("schema_version",
            f"schema version {board.get('schema')!r} — this build understands {SCHEMA_VERSION}",
            got=board.get("schema"), expected=SCHEMA_VERSION)
    if not str(board.get("id") or "").strip():
        add("board_id_empty", "storyboard.id is empty")

    shots = board.get("shots")
    if not isinstance(shots, list) or not shots:
        add("no_shots", "storyboard has no shots")
        return errs

    policy = board.get("policy") or {}
    seen_n: set[int] = set()

    for idx, s in enumerate(shots):
        # `where` used to be computed from s.get() BEFORE the isinstance check, so a shot that
        # was a string (exactly the case the next line reports) raised AttributeError instead
        # of returning the error. Fall back to the positional number for a non-dict.
        if not isinstance(s, dict):
            add("shot_not_object", f"shot {idx + 1}: not an object", n=idx + 1)
            continue
        num = s.get("n", idx + 1)
        where = f"shot {num}"
        n_for_ui = num if isinstance(num, int) else idx + 1

        n = s.get("n")
        if not isinstance(n, int) or n < 1:
            add("shot_number", f"{where}: 'n' must be a positive integer",
                n=n_for_ui, field="n", got=n)
        elif n in seen_n:
            add("shot_duplicate", f"{where}: duplicate shot number {n}",
                n=n_for_ui, field="n", duplicate=n)
        else:
            seen_n.add(n)

        mode = s.get("mode")
        if mode not in VALID_MODES:
            add("bad_mode",
                f"{where}: mode {mode!r} is not one of {', '.join(VALID_MODES)}",
                n=n_for_ui, field="mode", mode=mode, valid=list(VALID_MODES))

        prompt = (s.get("prompt") or "").strip()
        if not prompt:
            add("empty_prompt", f"{where}: empty prompt", n=n_for_ui, field="prompt")

        cid = s.get("character_id")
        if cid:
            if chars and cid not in chars:
                add("unknown_character",
                    f"{where}: character {cid!r} is not installed "
                    f"(have: {', '.join(sorted(chars)) or 'none'})",
                    n=n_for_ui, field="character_id",
                    character_id=cid, have=sorted(chars))
            # The trigger is injected mechanically, never trusted to the LLM — but if a plan
            # arrives with a character and the trigger is missing from the prompt, the LoRA
            # will not fire and the shot silently renders a stranger. Catch it here.
            trig = (s.get("trigger") or cid or "").strip()
            if trig and not re.search(rf"\b{re.escape(trig)}\b", prompt):
                add("missing_trigger",
                    f"{where}: prompt is missing the character trigger {trig!r}",
                    n=n_for_ui, field="prompt", trigger=trig)
        elif mode == "character":
            add("character_without_id",
                f"{where}: mode 'character' requires a character_id",
                n=n_for_ui, field="character_id")

        dur = s.get("duration_s")
        if not isinstance(dur, (int, float)) or not (0 < float(dur) <= 60):
            add("bad_duration",
                f"{where}: duration_s must be between 0 and 60 (got {dur!r})",
                n=n_for_ui, field="duration_s", duration_s=dur)

        refs = s.get("refs") or []
        if not isinstance(refs, list):
            add("refs_not_list", f"{where}: refs must be a list",
                n=n_for_ui, field="refs")
        else:
            for r in refs:
                rp = Path(r)
                if ref_root is not None and not rp.is_absolute():
                    rp = Path(ref_root) / rp
                if not rp.is_file():
                    add("ref_missing", f"{where}: reference image not found: {r}",
                        n=n_for_ui, field="refs", ref=str(r), name=Path(str(r)).name)
        if mode == "remix" and not refs:
            add("remix_needs_ref",
                f"{where}: mode 'remix' needs at least one reference image",
                n=n_for_ui, field="refs")

    # Resolution legality for the active tier. Q4 machines clamp; a plan that assumes 1536
    # on a 16 GB Mac would either clamp silently or swap, so surface it now.
    if max_dim:
        for key in ("draft", "final"):
            p = policy.get(key) or {}
            w, h = p.get("width"), p.get("height")
            if isinstance(w, int) and isinstance(h, int) and max(w, h) > max_dim:
                add("over_cap",
                    f"policy.{key}: {w}x{h} exceeds this machine's {max_dim}px cap — "
                    f"lower it or the render will clamp",
                    field=f"policy.{key}", pass_name=key, width=w, height=h, max_dim=max_dim)
    return errs


def validate_storyboard(
    board: dict,
    *,
    known_character_ids: Iterable[str] = (),
    ref_root: Path | None = None,
    max_dim: int | None = None,
) -> list[str]:
    """Return a list of human-readable problems. Empty list == good to shoot.

    A formatter over validate_storyboard_detail(): same checks, same order, byte-identical
    strings. Kept because it is the documented public surface and it reads better at a REPL.
    """
    return [e["message"] for e in validate_storyboard_detail(
        board,
        known_character_ids=known_character_ids,
        ref_root=ref_root,
        max_dim=max_dim,
    )]


# ---------------------------------------------------------------------------
# Phase 3 — SHOOT: scheduling
# ---------------------------------------------------------------------------

def shot_engine(shot: dict) -> str:
    """The engine a shot renders on, normalised. Unknown / missing -> the built-in one."""
    e = str(shot.get("engine") or "").strip().lower()
    return e if e in VALID_ENGINES else DEFAULT_ENGINE


def bucket_key(shot: dict) -> tuple[str, str]:
    """The scheduling bucket: (engine, pipeline kind).

    ENGINE IS PART OF THE KEY, and that is the whole fix. Keying on `mode` alone put an H3
    `text` shot in the same bucket as an LTX one, so a mixed film interleaved the two — and an
    H3 job *tears the LTX warm helper down* before it runs (run_h3_job_inner kills it; 40 GiB
    + the helper's weights does not fit on a 64 GB Mac) and the helper cold-starts again on the
    next LTX job. Interleaved, that teardown is paid per switch, which INVERTS the exact cost
    this grouping exists to avoid.
    """
    return (shot_engine(shot), _PIPELINE_BUCKET.get(shot.get("mode"), "t2v"))


def shooting_order(shots: list[dict], pass_name: str = "draft") -> list[dict]:
    """Group shots by pipeline bucket so the warm helper reloads once per KIND, not per shot.

    This is the single biggest wall-clock win on Apple Silicon and it exists purely because
    of `_free_all_but(keep_kind)`: rendering in story order across 3 interleaved modes costs
    one full pipeline load per switch (~90 s each), while grouped rendering costs one per
    bucket. 12 alternating shots: ~12 loads -> 3.

    Order within a bucket, and the order of buckets, is by first appearance in the story, so
    output stays deterministic and a resumed run reproduces the same sequence. Clips are
    re-sorted by `n` at assembly, so viewing order is unaffected.

    `pass_name` decides what "still to shoot" MEANS — see shots_pending(). Calling this
    without it schedules the draft pass, which is what every pre-existing caller wanted.
    """
    pending = shots_pending(shots, pass_name)
    bucket_first: dict[tuple[str, str], int] = {}
    for s in pending:
        b = bucket_key(s)
        n = s.get("n") or 0
        if b not in bucket_first or n < bucket_first[b]:
            bucket_first[b] = n
    return sorted(
        pending,
        key=lambda s: (bucket_first.get(bucket_key(s), 1 << 30), s.get("n") or 0),
    )


def h3_length_for(duration_s: float) -> str:
    """Snap a duration onto H3's length axis. Ties go to the shorter window."""
    d = float(duration_s or 0) or 5.0
    return min(H3_LENGTHS, key=lambda k: (abs(_H3_LENGTH_SECONDS[k] - d), _H3_LENGTH_SECONDS[k]))


def h3_quality_for(quality: str) -> str:
    """A pass's quality -> H3's canvas axis."""
    return _H3_QUALITY_FOR_PASS.get((quality or "").strip().lower(), "standard")


def ltx_frames_for(duration_s: float) -> int:
    """Seconds -> an LTX frame count on the sampler's `frames % 8 == 1` grid, at 24 fps.

    3 s -> 73 · 5 s -> 121 · 7 s -> 169 · 10 s -> 241, which is exactly the table docs/API.md
    publishes, so a storyboard shot and a hand-typed generation of the same length produce the
    same number of frames.
    """
    d = float(duration_s or 0)
    if d <= 0:
        return 0
    n = max(1, round(d * LTX_FPS))
    return max(9, 8 * round((n - 1) / 8) + 1)


def h3_chain_prompts_for(shot: dict) -> list[str]:
    """Per-window prompts for a chained H3 shot. `[]` when it isn't chained.

    A 10 s clip renders as two 5-second windows and a 15 s as three, and by
    default EVERY window is asked for the same prompt — so a one-off action
    ("he raises his arm", a line of dialogue) happens once per window and the
    clip reads as a repeat. That is the artifact `H3_CHAIN_PROMPT_HELP` in the
    panel describes, and it is the single most-reported thing about long H3
    clips.

    The planner writes ONE description per shot, so there is no second beat to
    hand window 2 — but there IS a `settle`: the state the shot is required to
    end in (law L3, and the field exists because the model skipped the law when
    it was only written as a rule). So the first window plays the shot, and
    every later window is told to hold that settled state and start nothing new.
    That is the honest reading of a 10 s shot whose action was written for one
    window, and it is strictly better than saying it twice.

    Window 1 is `""` — the panel's own contract for "use the main prompt" — so
    the shot's real prompt is never duplicated into the array.
    """
    if shot_engine(shot) != "h3":
        return []
    windows = _H3_LENGTH_WINDOWS.get(h3_length_for(shot.get("duration_s") or 0), 1)
    if windows <= 1:
        return []
    settle = (shot.get("settle") or "").strip().rstrip(".")
    if not settle:
        # Nothing honest to say about how it should continue — leave the array
        # empty, which is exactly today's behaviour, rather than inventing one.
        return []
    sound = (shot.get("soundscape") or "").strip()
    tail = ("integrated_multimodal_description: The shot continues without a cut, "
            "exactly where it left off. The camera holds completely still, the "
            "frame never moves - no pan, no push-in, no reframing. Nothing new "
            f"begins and no action restarts: {settle}, and that settled state is "
            "simply held for the whole window. Every face holds the exact angle "
            "to the lens it has at the start. No text appears at any point.")
    if sound:
        tail += "\n\noverall_soundscape: " + sound
    tail += "\n\nnon_diegetic_music: N/A"
    return [""] + [tail] * (windows - 1)


def shot_render_secs(shot: dict, policy_pass: dict, *, h3_cost=None) -> float:
    """Wall clock for ONE shot, on the engine it actually renders on.

    `h3_cost(quality_key, length_key) -> seconds | None` is the panel's measured per-cell hook
    (H3_TIERS[cell]["eta_min"] * 60). When it is absent or returns nothing we fall back to
    `_H3_SECS_PER_VIDEO_SEC`, which is derived from the same measurements. LTX keeps the
    per-second table it has always used.
    """
    dur = float(shot.get("duration_s") or 0)
    quality = policy_pass.get("quality", "balanced")
    if shot_engine(shot) == "h3":
        lk = h3_length_for(dur)
        qk = h3_quality_for(quality)
        if h3_cost is not None:
            try:
                got = h3_cost(qk, lk)
            except Exception:
                got = None
            if got:
                return float(got)
        return _H3_SECS_PER_VIDEO_SEC.get(qk, 109.0) * _H3_LENGTH_SECONDS[lk]
    return dur * _SECS_PER_VIDEO_SEC.get(quality, 60.0)


def estimate(board: dict, *, pass_name: str = "final", h3_cost=None) -> dict:
    """Wall-clock estimate for a pass, accounting for pipeline reloads.

    Deliberately pessimistic. Its job is to let someone see '2 h 40 m' BEFORE committing,
    and to show what grouped scheduling saves versus naive story order.

    ENGINE-AWARE since 2026-08-11. It used to price every shot off the LTX per-second table,
    which under-reports an H3 shot by roughly 2x (an H3 5 s High clip is ~8.5 min measured
    against this table's ~5 min, and a Native 5 s is ~20). A number the summary bar prints
    before someone spends an afternoon has to be honest for the film they actually planned.

    Only LTX buckets are charged a pipeline load: an H3 job is a fresh subprocess that loads
    its own weights every time, so that cost is already inside its measured per-clip eta.
    """
    shots = shots_pending(board.get("shots") or [], pass_name)
    policy = (board.get("policy") or {}).get(pass_name) or {}

    render = sum(shot_render_secs(s, policy, h3_cost=h3_cost) for s in shots)

    grouped = {bucket_key(s) for s in shots}
    grouped_loads = len(grouped)
    grouped_ltx = sum(1 for b in grouped if b[0] != "h3")

    naive_loads = 0
    naive_ltx = 0
    prev = None
    for s in sorted(shots, key=lambda x: x.get("n") or 0):
        b = bucket_key(s)
        if b != prev:
            naive_loads += 1
            if b[0] != "h3":
                naive_ltx += 1
            prev = b

    engine_mix: dict[str, int] = {}
    for s in shots:
        e = shot_engine(s)
        engine_mix[e] = engine_mix.get(e, 0) + 1

    return {
        "pass": pass_name,
        "shots": len(shots),
        "render_secs": round(render),
        "pipeline_loads": grouped_loads,
        "total_secs": round(render + grouped_ltx * _PIPELINE_LOAD_SECS),
        "naive_total_secs": round(render + naive_ltx * _PIPELINE_LOAD_SECS),
        "saved_secs": round((naive_ltx - grouped_ltx) * _PIPELINE_LOAD_SECS),
        # Additive, for the UI: the run strip draws one segment per bucket, and the summary
        # bar says "Hailuo H3 isn't installed" only when it has to.
        "engine_mix": engine_mix,
        "runtime_secs": round(sum(float(s.get("duration_s") or 0) for s in shots)),
        "buckets": [
            {"engine": e, "kind": k,
             "shots": sorted(s.get("n") or 0 for s in shots if bucket_key(s) == (e, k))}
            for (e, k) in sorted(
                grouped,
                key=lambda b: min((s.get("n") or 0) for s in shots if bucket_key(s) == b),
            )
        ],
    }


def per_shot_estimate(board: dict, *, pass_name: str = "final", h3_cost=None) -> dict:
    """`{n: seconds}` for every shot in the board, on the same cost model estimate() uses.

    The shot card prints `~2 m` from this. Server-computed on purpose: the per-second constants
    and the measured H3 cells then live in exactly one place instead of being re-typed in JS.
    """
    policy = (board.get("policy") or {}).get(pass_name) or {}
    out: dict = {}
    for s in (board.get("shots") or []):
        if not isinstance(s, dict):
            continue
        out[str(s.get("n") or 0)] = round(shot_render_secs(s, policy, h3_cost=h3_cost))
    return out


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


def shot_to_job(shot: dict, policy_pass: dict, *,
                board_id: str = "", board_title: str = "",
                h3_available: bool = True,
                engine_mode: str = DEFAULT_ENGINE_MODE,
                h3_chain_prompts: bool = False) -> dict:
    """Translate one storyboard shot into the panel's ORDINARY job form fields.

    Deliberately produces the same shape a human clicking Generate would produce, so shots
    flow through `/queue/add` -> `make_job` -> the normal worker, land in `mlx_outputs/`, and
    show up in the usual gallery with a usual sidecar. No private execution path.

    Every key below is in `make_job`'s allowlist. That is not a style note: a form field
    make_job does not name is silently dropped on /queue/add — the known trap in this codebase —
    so a control can look perfectly wired and do nothing at all.

    NOTE `enhance: "off"` is not optional — see ensure_trigger() above.
    """
    trigger = (shot.get("trigger") or shot.get("character_id") or "").strip()
    prompt = shot.get("prompt") or ""
    if trigger:
        prompt = ensure_trigger(prompt, trigger)

    # The engine. Without this the job dict has no `engine` key, make_job falls back to
    # ENGINE_DEFAULT ("ltx"), and every H3 shot the planner wrote renders silently on a
    # different model — different aspect, no audio, none of it flagged anywhere.
    # resolve_engine() is the single place that decides, so the chip on the card, the estimate,
    # the scheduling bucket and this job dict can never disagree about what will run.
    engine = resolve_engine(shot, engine_mode=engine_mode, h3_available=h3_available)

    job = {
        # The panel has ONE backend mode for text and character alike; see _PANEL_MODE.
        "mode": _PANEL_MODE.get(shot.get("mode"), "t2v"),
        "engine": engine,
        "prompt": prompt,
        "quality": policy_pass.get("quality", "balanced"),
        "width": policy_pass.get("width"),
        "height": policy_pass.get("height"),
        "frames": policy_pass.get("frames"),
        "enhance": "off",            # never let Gemma touch a planned prompt
        # `auto_open` was never in make_job's allowlist, so it silently did nothing. The field
        # that actually exists is `open_when_done`, and it must be off: a 12-shot overnight run
        # must not pop a QuickTime window per shot.
        "open_when_done": "off",
    }

    # Duration. Without this every shot rendered at the pass's frame count, so the per-shot
    # length control was decoration. Each engine gets its OWN grid — LTX snaps to frames%8==1
    # at 24 fps, H3 renders whole cells off the 17n+5 grid and picks them by length key.
    duration_s = float(shot.get("duration_s") or 0)
    if engine == "h3":
        length = h3_length_for(duration_s) if duration_s > 0 else "5s"
        job["h3_length"] = length
        job["h3_quality"] = h3_quality_for(policy_pass.get("quality", "balanced"))
        # Per-window prompts, when the installed runner supports the flag. The
        # caller passes that capability in rather than this module probing for
        # it — storyboard.py stays model-free and panel-free.
        if h3_chain_prompts:
            chain = h3_chain_prompts_for(shot)
            if chain:
                job["h3_chain_prompts"] = json.dumps(chain)
        # make_job re-stamps geometry from the resolved cell; carrying the cell's own frame
        # count means the job dict is already self-consistent (and honest in a log or a test)
        # before it gets there.
        job["frames"] = _H3_LENGTH_FRAMES[length]
    elif duration_s > 0:
        job["frames"] = ltx_frames_for(duration_s)

    if shot.get("character_id"):
        job["character_id"] = shot["character_id"]
        # THE VOICE LOADS ONLY WHEN THERE ARE LINES TO SAY.
        #
        # Stacking a character's audio LoRA on a shot with no speech spends the
        # audio branch on nothing and, on some prompts, invites gibberish. The
        # panel already has the manual escape hatch (the "No voice" pill); this
        # makes the DEFAULT right in the one place it can be known EXACTLY
        # rather than guessed.
        #
        # And it is exact here: the planner's H3 dialect carries dialogue in
        # explicit <d>…</d> tags, so this is a derivation, not a heuristic. A
        # shot with a <d> tag that has any content speaks; one without does not.
        # (The Manual tab cannot know this and does not pretend to — it sets a
        # default and says it did.)
        job["no_voice"] = "off" if _HAS_DIALOGUE.search(prompt) else "on"
    if shot.get("seed") is not None:
        job["seed"] = shot["seed"]

    # Queue linkage. `preset_label` is what the queue card, the Now card, the job pill and the
    # Recent row already print (`j.params.label || snippet(prompt)`), so a storyboard job
    # identifies itself in the bottom pane with no bottom-pane code at all. `session_tag`
    # carries the provenance the badge and the gallery group on. Both keys are already in
    # make_job's allowlist — no allowlist edit, which is the point.
    n = shot.get("n")
    if board_id and isinstance(n, int):
        job["session_tag"] = f"sb:{board_id}#{n}"
    if isinstance(n, int):
        title = (board_title or "").strip()
        job["preset_label"] = f"S{n:02d} · {title}" if title else f"S{n:02d}"

    # `shot["refs"]` is DELIBERATELY not mapped. The four ref-based modes (remix / keyframe /
    # extend / a2v) each want a different field — image, start_image + end_image,
    # video_path, ingredient_images_json — and mapping them half-way would enqueue a job that
    # renders silent t2v from a prompt while looking like it used the reference, which reads
    # as a model bug rather than a missing feature. v1 plans `text` + `character` only and the
    # planner is constrained to the same two, so a board that carries refs is hand-written or
    # from a future version; the validator still checks them and the schema still keeps them.
    return {k: v for k, v in job.items() if v is not None}


# THE ONE POLICY LITERAL. storyboard_planner.default_policy() had its own copy and
# they had drifted: this said Draft 640x448 (what a Quick render actually delivers,
# ffprobe-verified) while the planner said 640x480, a canvas the panel's own engine
# registry lists as never delivered. The main path masked it by keeping the board's
# existing policy, so only a direct planner consumer would ever have seen the
# fictional geometry. One literal, imported by the planner.
DEFAULT_POLICY: dict = {
    "draft": {"quality": "quick", "width": 640, "height": 448, "frames": 49},
    "final": {"quality": "balanced", "width": 1024, "height": 576, "frames": 121},
}


def default_policy() -> dict:
    """A fresh copy of DEFAULT_POLICY — callers mutate it (the planner clamps it)."""
    return {k: dict(v) for k, v in DEFAULT_POLICY.items()}


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
        "policy": policy or default_policy(),
        "shots": shots or [],
    }
