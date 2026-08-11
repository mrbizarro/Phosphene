#!/usr/bin/env python3
"""Storyboard planner — concept in, valid film spec out.

Companion to `storyboard.py`. That module owns the SCHEMA, the VALIDATOR, the durable
state and the scheduler. This module owns the one thing it deliberately left out: turning
a sentence a human typed into a `shots` list that `validate_storyboard()` accepts on the
first try.

    storyboard.py        schema + validate + shooting order + job translation   (no models)
    storyboard_planner.py  concept -> spec, using a small local LLM             (this file)
    mlx_ltx_panel.py       UI + queue + rendering                               (someone else)

Nothing here imports mlx in the panel's process. See MEMORY POLICY below.


MODEL: gemma-3-12b-it-4bit, the weights the panel ALREADY has
-------------------------------------------------------------
`storyboard.py`'s docstring says the planner is Qwen3.5-4B-4bit (decision 4492775, 2026-07-24).
That decision is sound on the merits and stays the target — but it is not shippable on this
machine today and the module must not pretend otherwise:

  * Qwen3.5-4B-4bit is NOT on disk. Nothing named qwen*-instruct/-4bit exists in
    `mlx_models/` or in the HF hub cache; `qwen-edit-2511-q6` is an mflux IMAGE model.
  * The volume has ~6.5 GiB free. A 3.06 GB download is technically survivable and
    strategically stupid, and the owner's standing rule for this task is explicit:
    do not download new models.
  * The 19 GB ollama `heretic-32b` is out of the question for a step that must run
    BEFORE a render on a unified-memory Mac.

So the planner runs on `mlx_models/gemma-3-12b-it-4bit` — the exact weights
`/prompt/enhance` already loads (`mlx_warm_helper.get_gemma_lm()`), through the exact
runtime (`mlx_lm.load` + `mlx_lm.generate` + `make_sampler`, mlx-lm 0.31.1 in
`ltx-2-mlx/env`). Zero new bytes on disk, and the failure mode "planner model missing" is
impossible for anyone who can already enhance a prompt.

The Qwen3.5 story is not lost, it is a one-line switch: point `LTX_STORYBOARD_PLANNER` (or
the `model_path=` argument) at any mlx-lm-loadable directory and everything else here is
unchanged. `storyboard.py`'s three-layer validity argument (strip preamble, coerce, then
validate with one repair retry) is implemented here verbatim and is model-agnostic; it is
what makes a 4-bit 12B good enough for the job.


MEMORY POLICY — why a SUBPROCESS and not the warm helper
---------------------------------------------------------
The hard rule from the owner: planning must not clog RAM, must be fully released before
a single frame renders, and must never be resident concurrently with a pipeline.

Three candidate paths were considered. The chosen one is the least-RAM path:

1. REJECTED — call the running warm helper's `enhance_prompt` action.
   It is the only text-LLM route the daemon exposes, and it is hard-wired for a different
   job: `max_new_tokens` is the library default 512 (a 6-shot plan is 1200-2500 tokens, so
   every plan would be truncated mid-JSON), the panel handler force-prepends the Lightricks
   T2V system prompt plus the Phosphene enhance addendum to whatever system prompt you
   supply, and it post-edits the OUTPUT by splicing character triggers back in. Making it
   carry a plan means editing `mlx_warm_helper.py` and `mlx_ltx_panel.py` — both owned by
   other agents on this task. Worse for RAM anyway: the helper holds Gemma warm
   indefinitely after the call (only `release_pipelines()` on the next render frees it), so
   ~7.9 GB sits resident through the user's review of the plan.

2. REJECTED — `mlx_lm.load()` inside the panel process.
   The panel is a long-lived server (it is running on Python 3.9 today). MLX allocates from
   a Metal buffer cache that is not fully returned to the OS when the Python objects are
   dropped, so "unloaded" would still show up as a permanently fatter panel. There is no
   honest `release()` you can write for that.

3. CHOSEN — a short-lived subprocess that loads, generates, and dies.
   `release()` reaps the child; process exit is the only 100%-deterministic reclaim MLX
   offers. Peak RSS is measured, not assumed — the child reports its own
   `ru_maxrss` and `mx.get_peak_memory()`, and the parent independently reads
   `RUSAGE_CHILDREN.ru_maxrss`, so the number in the report needs no cooperation to trust.
   `plan_film()` releases in a `finally:`; there is no code path that returns a plan with a
   model still loaded. Measured: ~7.9 GB peak, gone the instant the call returns.

The child is kept alive ACROSS the repair round-trip inside one `plan_film()` call, because
paying the load twice for one plan is waste, not discipline. It never survives the call.


OUTPUT DIALECT
--------------
Per-shot prompts are emitted in the dialect of the engine that will render them:

  * `engine: "h3"`  (default) — MiniMax H3's official three-field form:
        integrated_multimodal_description: [Shot 1] ...
        overall_soundscape: ...
        non_diegetic_music: ...
    with the laws that were paid for in render hours: the camera is always pinned,
    every action completes and then holds, faces never turn, dialogue is wrapped in
    `<d>[English] ...</d>` with the mouth explicitly told to stop, and
    `non_diegetic_music` carries instrumentation/tempo/dynamics rather than mood words.
    Sources: ~/AI/projects/hailuo-mlx/notes/H3_PROMPTING_GUIDE.md and the graded AURELIUS
    round-2 shot table.

  * `engine: "ltx"` — LTX 2.3's single-paragraph prose with a trailing `Audio:` line and a
    verbatim master-style suffix. Chosen automatically for any shot cast with a trained
    Phosphene character, because character LoRAs ARE LTX LoRAs: identity is the one mode
    H3 does not have. That is also what makes trigger injection safe — `ensure_trigger()`
    prepends, which is legal in LTX prose and would be illegal in H3 (a T2VA prompt must
    begin with `integrated_multimodal_description:`).


WHAT THE MODEL IS AND IS NOT ALLOWED TO DECIDE
-----------------------------------------------
Everything the validator checks mechanically is produced mechanically. The model returns a
small flat JSON object carrying only the creative payload: a film title, and per shot a
title, an optional character, a duration, a camera CHOICE from a closed set, a prose
description, a settled end state, a soundscape and a music line. Python owns `schema`,
`id`, `created_at`, shot numbering, `mode`, `engine`, `tier`, `seed`, `refs`, `policy`,
trigger injection and every clamp. A small model cannot fail a check it was never asked
to pass.

Two of the prompting laws are enforced the same way, for the same reason. Stated as rules,
they were ignored: over the first measured sweep (5 concepts, 26 shots) the model reused ONE
camera sentence for every shot of every film, and wrote an end-state clause on 2 shots out
of 26. They are now a one-word choice plus a short phrase, and PYTHON writes the sentence —
after which camera variety and end-state coverage were 100%. If a law can be reduced to a
choice, reduce it; only leave prose to the model where prose is the point.

Pure stdlib. Python 3.9-compatible (the panel runs on 3.9 today) — the 3.11 venv is used
only for the child process, which is the only thing that imports mlx.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent

# --------------------------------------------------------------------------------------
# Model + runtime location
# --------------------------------------------------------------------------------------
# Mirrors mlx_ltx_panel.py:74-75 so a user who moved LTX_MODELS_DIR does not have to move
# anything twice. LTX_STORYBOARD_PLANNER is the escape hatch for pointing at Qwen3.5 (or
# anything else mlx-lm can load) without touching this file.
MODELS_DIR = Path(os.environ.get("LTX_MODELS_DIR", str(ROOT / "mlx_models")))
DEFAULT_MODEL_PATH = Path(
    os.environ.get("LTX_STORYBOARD_PLANNER")
    or os.environ.get("LTX_GEMMA_PATH")
    or (MODELS_DIR / "gemma-3-12b-it-4bit")
)

# The child needs mlx + mlx_lm; the panel's own interpreter may not have them (it is 3.9).
# Same resolution order as the panel's _resolve_helper_python().
_VENV_PY = ROOT / "ltx-2-mlx" / "env" / "bin" / "python3.11"


def _resolve_worker_python() -> Path:
    cand = os.environ.get("LTX_HELPER_PYTHON")
    if cand and Path(cand).is_file():
        return Path(cand)
    if _VENV_PY.is_file():
        return _VENV_PY
    alt = ROOT / "ltx-2-mlx" / "env" / "bin" / "python"
    if alt.is_file():
        return alt
    return Path(sys.executable)


WORKER_PYTHON = _resolve_worker_python()

# Every worker reply is one line prefixed with this, so stray stdout from mlx-lm (progress
# bars, warnings) can never be mistaken for the protocol.
_SENTINEL = "@@PLANNER@@ "

# Modes this planner is allowed to emit. `remix`/`keyframe`/`extend`/`a2v` all require
# inputs the planner does not have (reference images, a prior clip, an audio file), and
# validate_storyboard() rejects them without those — so they are simply not on the menu.
_PLANNABLE_MODES = ("text", "character")

# H3 renders in ~5 s windows (124 frames @ 24 fps); longer clips are chained windows.
# The panel's tier grid offers exactly these lengths, so durations snap to them.
_H3_LENGTHS = (3.0, 5.0, 10.0, 15.0)

_MIN_DURATION = 1.0
_MAX_DURATION = 60.0            # validate_storyboard(): 0 < duration_s <= 60

DEFAULT_TEMPERATURE = 0.15      # low, deliberately: this is structured output, not vibes
DEFAULT_MAX_TOKENS = 3600
DEFAULT_TIMEOUT_S = 900


class PlannerError(Exception):
    """Raised only for programmer errors (bad arguments). Model failures never raise —
    they come back as a structured error dict the UI can render."""


# --------------------------------------------------------------------------------------
# The validator we must satisfy — imported, never reimplemented
# --------------------------------------------------------------------------------------

def _load_validator():
    """Return (validate_fn, storyboard_module).

    The whole design principle of this module is that the schema is whatever
    `storyboard.py` says it is, so we call the real thing rather than modelling it.
    Tolerant about the name because the panel-side agent may expose it as `validate`.
    """
    sys_path_added = False
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
        sys_path_added = True
    try:
        import storyboard  # type: ignore
    except Exception as exc:  # pragma: no cover - only if storyboard.py is broken
        if sys_path_added:
            try:
                sys.path.remove(str(ROOT))
            except ValueError:
                pass
        raise PlannerError("cannot import storyboard.py: %s" % exc)
    fn = getattr(storyboard, "validate", None) or getattr(storyboard, "validate_storyboard", None)
    if fn is None:
        raise PlannerError("storyboard.py exposes neither validate() nor validate_storyboard()")
    return fn, storyboard


# --------------------------------------------------------------------------------------
# THE PROMPT
# --------------------------------------------------------------------------------------
# Everything below is few-shot first and rules second, on purpose. The exemplars are real
# graded work: two are trimmed from the AURELIUS round-2 table (owner-graded KEEP/MAYBE
# shots, ~/AI/projects/aurelius/video/clips/_work/shots_r2.py) and one is C1 from
# H3_PROMPTING_GUIDE.md §6.1. The abstract rules exist only to name what the exemplars
# already demonstrate, so a 12B model can pattern-match instead of reason.

_LAWS = """\
THE LAWS (each one was paid for with a wasted render; the examples above obey all of them)

L1  PIN THE CAMERA. Silence is not a tripod - an unnamed camera drifts. Every description
    names exactly ONE camera behaviour, as prose, with amplitude and speed. Locked shot:
    "The camera holds a static shot, the frame never moves - no pan, no push-in, no
    reframing." Moving shot: "The camera pushes in with small amplitude at slow speed."
L2  FACES NEVER TURN. Turning breaks identity. Heads stay square to the lens, shoulders do
    not pivot, nobody rotates toward or away from camera.
L3  THE ACTION COMPLETES, THEN HOLDS. If the arc is still running at the end of the clip
    the model invents - extra limbs, extra motion. Finish early and name the settled end
    state: "The movement is completely finished before the shot ends, and for the last two
    seconds <state>, with no new movement of any kind."
L4  ONE CONTINUOUS ACTION per 5 seconds for a human subject. Two beats maximum in 5 s, and
    only if the second is a reaction. Abstract/product/graphic subjects may have 3-4.
L5  NO UNANCHORED NEGATIONS. There is no negative prompt. Never write "not blurry", "high
    quality", "no distortion". The only refusals allowed are against things the model adds
    by itself: camera drift (L1) and unwanted lettering ("No text appears at any point.").
L6  DIALOGUE lives inside <d>[English] ...</d> and nowhere else. The speaker, their voice
    and their delivery are described OUTSIDE the tag. Immediately after the tag, stop the
    mouth: "his jaw ceases speaking motion and his mouth settles closed". One or two short
    sentences per 5 s. If a shot has no dialogue, it has no <d> at all.
L7  MUSIC IS INSTRUMENTATION, not mood. "Sparse piano at a slow tempo, joined by low
    strings that swell and cut out" - never "epic", "emotional", "uplifting". "N/A" is the
    correct value when there is no score, and is the right answer most of the time.
L8  SOUNDSCAPE IS AMBIENCE + PHYSICAL SOUND ONLY. Never repeat the dialogue there. 1-3
    sentences.
L9  ON-SCREEN TEXT: only if the brief asks for it, and then type the exact words in double
    quotes, 3-5 words, one row. Otherwise end the description with "No text appears at any
    point."
L10 CONVERT MOOD INTO BEHAVIOUR. Not "she is devastated" but "her eyes shine wet and she
    blinks once and keeps very still".
L11 THE FACE IS THE WHOLE POINT. A shot is judged on whether the face reads. If a person is
    on screen their face is IN the frame, whole, lit and turned to the lens - every time.
    NEVER write, and never imply, any of these unless the brief asked for it in so many
    words: "his face obscured", "seen from behind", "her back to the camera", "head out of
    frame", "cropped at the chin", "silhouetted against the light", "in silhouette", "his
    face hidden by his hands", "we never see her face". The temptation is strongest on the
    last shot of a film, where a silhouette feels like an ending - it is not, it is a shot
    with no face in it. End on the face instead.
    A face may be dark, wet, bruised, half in shadow or lit from one side. It may not be
    turned away, blocked, cut off by the frame edge, or reduced to an outline.
"""

_H3_EXAMPLES = """\
EXAMPLE SHOTS - H3 register. Copy this voice. Note that no "description" below contains a
camera sentence or an ending clause: those are the "camera" and "settle" keys.

{
  "n": 1,
  "title": "Clippers",
  "character_id": null,
  "duration_s": 5,
  "camera": "handheld",
  "face": "close",
  "description": "Live-action, cinematic, a close two-shot in a bright kitchen: a woman with a freshly buzzed head sits still while her teenage daughter stands behind her running hair clippers over her scalp, warm window light across both faces. Photoreal, heavy 35mm film grain. Played quietly and underplayed throughout: the daughter draws one more slow clipper pass and a small tuft of hair falls away, the mother's eyes shine wet and she blinks once and keeps very still, and she reaches up and closes her hand over her daughter's hand on her shoulder, and the smallest smile arrives at the corner of her mouth.",
  "settle": "that small smile is simply held, both of them still, hands joined on the shoulder",
  "soundscape": "The steady buzz of hair clippers, one soft unsteady breath, a dripping kitchen tap, and the hum of a fridge. Nobody speaks and no voice is heard at any point.",
  "music": "N/A"
}

{
  "n": 2,
  "title": "The box",
  "character_id": null,
  "duration_s": 5,
  "camera": "static",
  "face": "medium",
  "description": "Live-action, cinematic, a medium-wide shot at dusk of a man in an open-collared shirt beside a battered steel dumpster in an empty back street, a cardboard office box in his arms with a small potted plant balanced on top. Photoreal, heavy 35mm film grain. Blue dusk light. Everything happens at natural real-time speed, never in slow motion. He heaves the box up and away from his chest in one fast decisive movement and it drops hard into the dumpster with the plant tumbling in after it and a puff of dust rising, then his shoulders drop as the weight leaves him and he lets out one long exhale, his face staying square to the lens the whole time.",
  "settle": "he is standing empty-handed with his shoulders down and his face still to the lens",
  "soundscape": "Quiet back-street evening ambience with distant traffic, the hollow bang of cardboard hitting steel, a clatter of a plant pot, and one long relieved exhale.",
  "music": "N/A"
}

{
  "n": 3,
  "title": "Impossible",
  "character_id": null,
  "duration_s": 5,
  "camera": "push_in",
  "face": "close",
  "description": "Live-action, cinematic, a medium close-up of a man in a dark curly fur hat and heavy fur coat on an open dune ridge. He faces the camera squarely and holds eye contact for the entire duration as the wind lifts the fur at his collar. Hard low sun rakes from camera left at the end of the day, carving one bright warm edge down his cheekbone while the other side of his face falls into open shadow; the dune line behind him is a clean dark silhouette against a pale sky. The man, with a warm, measured, slightly gravelled voice (S1), says: <d>[English] They said this was impossible.</d> Exactly as his voice stops, his jaw ceases speaking motion and his mouth settles into a closed steady half-smile.",
  "settle": "his eyes stay on the lens and nothing but the fur at his collar moves",
  "soundscape": "A steady desert wind moves across open sand for the full duration, with the dry rustle of fur at his collar and one soft gust that rises and falls.",
  "music": "N/A"
}

{
  "n": 4,
  "title": "Crema",
  "character_id": null,
  "duration_s": 5,
  "camera": "push_in",
  "face": "none",
  "description": "Live-action, cinematic, an extreme macro of a warm glass cup under a polished steel spout, filling with espresso. A hard raking key light from camera left picks out the rim of the glass against a matte black background. Two dark streams meet and braid as they fall, the liquid climbing the glass while a dense hazelnut crema builds on the surface and settles into a smooth unbroken layer, one bead of condensation sliding down the outside of the glass.",
  "settle": "the crema lies flat and unbroken and the surface is completely still",
  "soundscape": "The low hiss of a pump, the fine trickle of liquid into glass, and a quiet kitchen room tone underneath.",
  "music": "One low sustained synth tone at a slow tempo that rises slightly as the glass fills and drops away at the end."
}
"""

_LTX_EXAMPLE = """\
EXAMPLE SHOT - LTX register. Use this voice ONLY for a shot whose "character_id" is not
null. It is one continuous prose paragraph, 70-120 words: no [Shot 1] marker, no <d> tags,
dialogue in single quotes with a voice descriptor in front of it, and no field labels. Do
NOT write the character's trigger word yourself - leave the person unnamed and described,
the trigger is attached afterwards.

{
  "n": 4,
  "title": "The confession",
  "character_id": "bizarrotrn",
  "duration_s": 10,
  "camera": "handheld",
  "face": "close",
  "description": "A weary man in a soft grey jacket sits in a sterile interview room, medium close-up, fluorescent overhead light, shallow depth of field. He breathes in, looks down at his hands, then up at the lens and holds there. He says quietly and clearly: 'I stopped leaving the chair.'",
  "settle": "he is still, jaw set, both eyes back on the lens",
  "soundscape": "Room tone, a fluorescent hum, one unsteady breath, clear dialogue.",
  "music": "N/A"
}

Words that wreck an LTX shot because they trigger letterbox bars: cinematic, filmic,
anamorphic, widescreen, epic, 2.39:1. Never use them in the LTX register. LTX also cannot
render crowds, rows of people, circles of seated people, or three or more faces the camera
must read - pick ONE principal and imply the rest in the soundscape. Do not describe
fingers gripping objects, and never ask for on-screen text.
"""


def _build_system_prompt(engine_hint: str, has_characters: bool,
                         allow_hidden: bool = False) -> str:
    dialect = """\
THE TARGET DIALECT

Your "description", "soundscape" and "music" for an H3 shot are assembled by the program
into MiniMax H3's official three-field prompt, exactly like this - you never type the field
labels or the shot marker yourself:

    integrated_multimodal_description: [Shot 1] <your description>

    overall_soundscape: <your soundscape>

    non_diegetic_music: <your music>

So "description" must START with the style token ("Live-action, cinematic," / "3D CG," /
"2D-animated," / "claymation," / "vintage film,") followed immediately by the shot size,
and must read as one continuous paragraph of 70-140 words.
"""
    contract = """\
OUTPUT CONTRACT - read this twice

Reply with ONE JSON object and NOTHING else. No preamble, no explanation, no markdown
outside the JSON. The object has exactly two keys:

{
  "title": "<short title for the whole film, 2-6 words>",
  "shots": [ <one object per shot, in story order> ]
}

Each shot object has exactly these nine keys and no others:

  "n"            integer, 1-based, in order
  "title"        2-5 words naming the beat
  "character_id" a cast id from CAST below, or null
  "duration_s"   3, 5 or 10
  "camera"       ONE of: static, push_in, pull_back, handheld, orbit, pan, tilt_up, tracking
  "face"         ONE of: close, medium, none. This is about WHO IS PRESENT, not about how
                 important the face is to you.
                   close  - a face is the subject of the shot and fills much of the frame
                   medium - a person is on screen anywhere at all - near, far, small, in the
                            background, in a wide shot, only their hands. Their face must
                            stay readable. When in doubt this is the answer.
                   none   - there is NO PERSON of any kind in this shot: an object, a
                            landscape, a machine, a graphic. Nobody. Not one.
                 There is no fourth option. See L11.
  "description"  the visual + action + dialogue paragraph, 70-140 words. It does NOT contain
                 a camera sentence and does NOT describe how the shot ends - those are the
                 "camera" and "settle" keys, and the program writes their sentences for you.
  "settle"       a short phrase naming the settled state the shot ENDS in, written to follow
                 "for the last two seconds ..." - e.g. "he is standing empty-handed with his
                 shoulders down". Every shot needs one.
  "soundscape"   1-3 sentences of ambience and physical sound
  "music"        1-2 sentences of instrumentation/tempo/dynamics, or "N/A"

The program - not you - assigns schema version, ids, seeds, modes, engines, resolutions and
character trigger words, and turns "camera" and "settle" into their sentences. Do not invent
other keys. Do not wrap the object in an array.
"""
    parts = [
        "You are the Phosphene storyboard planner.\n\n"
        "You turn one sentence of concept into a shot-by-shot film plan that a local video\n"
        "model will render unattended. You are the DIRECTOR, not a transcriber: the concept is\n"
        "INTENT, and your job is to express it as shots this model actually renders well.\n"
        "Every shot must be a single continuous take that can stand on its own - there is no\n"
        "editing, no cutting inside a shot, and no camera move that reframes.\n",
        contract,
    ]
    if engine_hint == "ltx":
        # Every shot will render on LTX, so the H3 dialect would only confuse the model.
        parts.append(_LTX_EXAMPLE)
        parts.append("EVERY shot in this film uses the LTX register shown above, whether or\n"
                     "not it has a character_id. Never write [Shot 1], <d> tags or field labels.\n")
    else:
        parts.append(dialect)
        parts.append(_H3_EXAMPLES)
        if has_characters and engine_hint != "h3":
            parts.append(_LTX_EXAMPLE)
    parts.append(_LAWS)
    parts.append("""\
COMPOSITION LIMITS - rewrite around these instead of asking for them

  crowd / audience / rally            -> ONE face from it, the rest implied in the soundscape
  circle of seated people, group ring -> a close shot on whoever is speaking
  rows of desks, classroom, newsroom  -> the principal in front, everything behind soft
  three or more faces the camera reads-> pick one principal; the others are off-frame
  fast hands, fingers gripping props  -> frame past the hands
  "the camera pulls back to reveal"   -> state the FINAL framing only, no reveal

ACID TEST before you write a shot: can a reader point at exactly one subject the camera
will hold? If not, rewrite it.

DURATION: 3 s for a beat or a cutaway, 5 s for one action or one short spoken line, 10 s
only when a line genuinely needs it. Speech runs about 2.5 words per second; add one second
of breath before the line and about one and a half seconds of silence after it, then round
up. Never trim a line the brief gave you in order to fit a duration - lengthen the shot.

VARIETY IS A HARD REQUIREMENT, not a preference. Before you answer, look down the list of
"camera" values you have written: no two consecutive shots may share one, and a six-shot
film must use at least three different ones. Do the same for shot size - vary across extreme
close-up, close-up, medium, wide. Let at most half the shots contain dialogue.

EVERY SHOT MUST CONTAIN A PHYSICAL ACTION that starts and finishes inside the clip. A shot
that only describes what a place looks like is a photograph, and this model fills the empty
time by inventing motion. If you cannot name something that moves, the shot does not exist.

WRITE WHAT A LENS COULD SEE. "A testament to human ingenuity", "a symbol of hope", "a space
that feels lived-in", "his face etched with solitude" are unrenderable - they instruct
nothing. Replace each with the visible fact underneath it.
""")
    if allow_hidden:
        parts.append(
            "FACES: this brief explicitly asked for hidden or obscured faces, so L11 is "
            "relaxed for the shots where the brief wants it - you may use \"hidden\" as a\n"
            "\"face\" value on those shots. Every other shot still keeps its face readable.\n")
    return "\n".join(parts)


def _build_user_prompt(
    concept: str,
    n_shots: int,
    style: str,
    cast: Sequence[Dict[str, str]],
    must_include: Sequence[str],
) -> str:
    lines = ["BRIEF", "", "CONCEPT: %s" % concept.strip(), "", "SHOT COUNT: exactly %d shots." % n_shots]
    if style and style.strip():
        lines += ["", "STYLE (applies to every shot; keep it identical across all of them): %s"
                  % style.strip()]
    if cast:
        lines += ["", "CAST - these are trained characters. A shot that features one sets"
                  " \"character_id\" to that id and is written in the LTX register:"]
        for c in cast:
            desc = (c.get("description") or "").strip()
            lines.append("  - %s%s%s" % (
                c["id"],
                (" (%s)" % c["name"]) if c.get("name") and c["name"] != c["id"] else "",
                (": " + desc) if desc else "",
            ))
        lines.append("  Any shot without a listed character sets \"character_id\": null.")
    else:
        lines += ["", "CAST: none. Every shot sets \"character_id\": null."]
    if must_include:
        lines += ["", "MUST APPEAR somewhere in the film:"]
        for m in must_include:
            lines.append("  - %s" % str(m).strip())
    lines += ["", "Return the JSON object now. %d shots. Nothing before it, nothing after it."
              % n_shots]
    return "\n".join(lines)


def _build_repair_prompt(bad_json: str, problems: Sequence[str], n_shots: int) -> str:
    return "\n".join([
        "Your previous reply did not pass validation.",
        "",
        "PROBLEMS FOUND:",
        "\n".join("  - %s" % p for p in problems),
        "",
        "YOUR PREVIOUS OUTPUT:",
        bad_json[:6000],
        "",
        "Fix ONLY those problems. Keep everything that was already good, word for word.",
        "Return the corrected JSON object - exactly %d shots, the same six keys per shot," % n_shots,
        "nothing before the object and nothing after it.",
    ])


def _build_film_feedback_prompt(previous: Dict[str, Any], note: str, n_shots: int) -> str:
    return "\n".join([
        "You already planned this film. The director has notes.",
        "",
        "DIRECTOR'S NOTES: %s" % note.strip(),
        "",
        "THE CURRENT PLAN:",
        json.dumps(_spec_to_model_view(previous), indent=1, ensure_ascii=False)[:9000],
        "",
        "Re-plan the film so the notes are satisfied. Change only what the notes require;",
        "leave everything else word for word as it was. Return the full corrected JSON",
        "object with exactly %d shots and nothing else." % n_shots,
    ])


def _build_shot_feedback_prompt(previous: Dict[str, Any], shot_n: int, note: str) -> str:
    shots = previous.get("shots") or []
    target = None
    for s in shots:
        if s.get("n") == shot_n:
            target = s
            break
    if target is None:
        raise PlannerError("shot %r is not in the plan (it has %d shots)" % (shot_n, len(shots)))
    neighbours = [s for s in shots if s.get("n") in (shot_n - 1, shot_n + 1)]
    return "\n".join([
        "You are re-rolling ONE shot of a film that is otherwise finished.",
        "",
        "THE FILM: %s" % (previous.get("title") or ""),
        "",
        "THE SHOTS AROUND IT, for continuity - do NOT return these:",
        json.dumps([_shot_to_model_view(s) for s in neighbours], indent=1, ensure_ascii=False)[:4000],
        "",
        "THE SHOT TO REPLACE (n=%d):" % shot_n,
        json.dumps(_shot_to_model_view(target), indent=1, ensure_ascii=False),
        "",
        "DIRECTOR'S NOTES ON THIS SHOT: %s" % note.strip(),
        "",
        "Return ONE JSON object: {\"title\": \"<the film title, unchanged>\", \"shots\": [ <the",
        "single replacement shot object, with n=%d> ]}. One shot only. Nothing else." % shot_n,
    ])


def _shot_to_model_view(shot: Dict[str, Any]) -> Dict[str, Any]:
    """The eight creative keys, as the model sees them (drops seeds/modes/engines/etc)."""
    return {
        "n": shot.get("n"),
        "title": shot.get("title", ""),
        "character_id": shot.get("character_id"),
        "duration_s": shot.get("duration_s"),
        "camera": shot.get("camera", "static"),
        "face": shot.get("face", "medium"),
        "description": shot.get("description", ""),
        "settle": shot.get("settle", ""),
        "soundscape": shot.get("soundscape", ""),
        "music": shot.get("music", "N/A"),
    }


def _spec_to_model_view(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": spec.get("title", ""),
        "shots": [_shot_to_model_view(s) for s in (spec.get("shots") or [])],
    }


# --------------------------------------------------------------------------------------
# JSON extraction from small-model output
# --------------------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_THINK_RE = re.compile(r"<think>.*?</think>|<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
_LINE_COMMENT_RE = re.compile(r"(^|\s)//[^\n]*")
_SMART = {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u2013": "-", "\u2014": "-"}


def _balanced_objects(text: str) -> List[str]:
    """Every top-level {...} run in `text`, string- and escape-aware.

    A small model happily writes 'Here is your plan:' before the object and 'Let me know if
    you want changes!' after it, and sometimes emits two objects. Regex cannot match nested
    braces; a two-state scanner can, and it is 20 lines.
    """
    out: List[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append(text[start:i + 1])
                    start = -1
    if depth > 0 and start >= 0:
        # Truncated output (hit max_tokens mid-object). Hand back what we have; the
        # bracket-closing repair below often rescues it.
        out.append(text[start:])
    return out


def _soften(raw: str) -> str:
    s = raw
    for bad, good in _SMART.items():
        s = s.replace(bad, good)
    s = _LINE_COMMENT_RE.sub(r"\1", s)
    s = _TRAILING_COMMA_RE.sub(r"\1", s)
    return s


def _close_brackets(raw: str) -> str:
    """Best-effort close of an object truncated by the token limit.

    Closers are emitted from an actual stack, innermost first — appending all the `]`
    before all the `}` produces `..."]}}` for a truncated shot object, which is not JSON.
    """
    stack: List[str] = []
    in_str = False
    esc = False
    for ch in raw:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
    s = raw
    if in_str:
        s += '"'
    s = _TRAILING_COMMA_RE.sub(r"\1", s.rstrip().rstrip(","))
    return s + "".join(reversed(stack))


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Pull the plan object out of whatever the model actually said.

    Handles: clean JSON, ```json fences, bare ``` fences, prose before/after, `<think>`
    leakage, trailing commas, smart quotes, // comments, an object wrapped in a list, and
    output truncated mid-object by the token limit. Returns None only when there is no
    recoverable object at all.
    """
    if not text:
        return None
    body = _THINK_RE.sub(" ", text)

    candidates: List[str] = []
    for m in _FENCE_RE.finditer(body):
        candidates.append(m.group(1))
    # An unterminated fence is common when generation is truncated.
    if not candidates and "```" in body:
        candidates.append(body.split("```", 1)[1].lstrip("jsonJSON \n"))
    candidates.append(body)

    scanned: List[str] = []
    for c in candidates:
        scanned.extend(_balanced_objects(c))
        scanned.append(c.strip())

    # Longest first: a plan object is bigger than any stray fragment before it.
    for chunk in sorted({c for c in scanned if c.strip()}, key=len, reverse=True):
        for attempt in (chunk, _soften(chunk), _close_brackets(_soften(chunk))):
            try:
                obj = json.loads(attempt)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, list):
                obj = next((o for o in obj if isinstance(o, dict)), None)
                if obj is None:
                    continue
            if isinstance(obj, dict):
                if "shots" in obj or "title" in obj:
                    return obj
                # Model returned a bare list of shots under some other key.
                for v in obj.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict) and "description" in v[0]:
                        return {"title": "", "shots": v}
    return None


# --------------------------------------------------------------------------------------
# Coercion — everything the validator checks, produced mechanically
# --------------------------------------------------------------------------------------

_DESC_KEYS = ("description", "integrated_multimodal_description", "visual", "prompt", "body", "text")
_SOUND_KEYS = ("soundscape", "overall_soundscape", "audio", "sound", "ambience")
_MUSIC_KEYS = ("music", "non_diegetic_music", "score", "soundtrack")

_SHOT_MARKER_RE = re.compile(r"^\s*\[Shot\s*\d+\][,\s]*", re.IGNORECASE)
_D_TAG_RE = re.compile(r"<d>\s*(?:\[[^\]]*\]\s*)?(.*?)</d>", re.DOTALL)

# --- the two laws the model demonstrably will not apply on its own ----------------------
# Measured over 5 concepts x 26 shots: with the laws stated as rules, the model reused ONE
# camera sentence for every shot in every film, and wrote the settle clause on 2 of 26
# shots. Both are now a small closed choice the model makes and a canonical sentence PYTHON
# writes — which is the same trick that makes the rest of the schema reliable.

_CAMERA_SENTENCES = {
    "static": "The camera holds a static shot, the frame never moves - no pan, no push-in, "
              "no reframing.",
    "push_in": "The camera pushes in with small amplitude at slow speed.",
    "pull_back": "The camera pulls back with small amplitude at slow speed.",
    "handheld": "The camera shakes slightly with small amplitude at slow speed, a handheld "
                "micro-sway that never reframes.",
    "orbit": "The camera arcs around the subject with small amplitude at slow speed.",
    "pan": "The camera pans with small amplitude at slow speed.",
    "tilt_up": "The camera tilts up with small amplitude at slow speed.",
    "tracking": "The camera tracks alongside the subject with medium amplitude at slow speed.",
}
CAMERA_KEYS = tuple(_CAMERA_SENTENCES)

# --- the face law ----------------------------------------------------------------------
# Faces are the quality metric for this project: a plan is judged on whether the face reads.
# Two failures were measured, so both are structural rather than advisory.
#
#   1. The model volunteers face-hiding framing, most often on the LAST shot of a film where
#      it reaches for something poetic — "silhouetted against the setting sun", "her
#      silhouette framed against the city lights", "his face obscured by the angle" (the last
#      one rendered a head half out of frame). Base rate before this law: 5 of 56 shots.
#   2. Turning breaks identity (H3 guide 7.2 / AURELIUS law 2).
#
# So `face` is a closed choice — close / medium / none / hidden — and Python writes the
# sentence, exactly as with `camera` and `settle`. `hidden` is refused unless the brief asked
# for it in so many words. Unambiguous blocking phrases are additionally scrubbed out of the
# prose, because the model writes them regardless of what it chose.

_FACE_LAW_CLOSE = (
    "The face fills much of the frame, and every face holds the exact angle to the lens it "
    "has at the start: heads stay square, shoulders do not pivot, and nobody rotates towards "
    "or away from the camera at any point. The face stays completely inside the frame for "
    "the entire duration with both eyes open and clearly readable, and is never cropped by "
    "the frame edge, never thrown into silhouette, and never covered by a hand, a prop or "
    "another person.")
_FACE_LAW_MEDIUM = (
    "Every face holds the exact angle to the lens it has at the start: heads stay square, "
    "shoulders do not pivot, and nobody rotates towards or away from the camera at any "
    "point. Each face stays completely inside the frame for the entire duration and is "
    "never cropped by the frame edge, never thrown into silhouette, and never covered by a "
    "hand, a prop or another person.")
_FACE_LAWS = {"close": _FACE_LAW_CLOSE, "medium": _FACE_LAW_MEDIUM, "none": "", "hidden": ""}
FACE_KEYS = ("close", "medium", "none", "hidden")

_PERSON_RE = re.compile(
    r"\b(man|woman|men|women|boy|girl|child|children|person|people|face|faces|keeper|"
    r"worker|he|she|his|her|him|hers|they|their|figure|hands?|crowd|speaker|"
    r"[a-z]+er's|[a-z]+man)\b", re.IGNORECASE)

# Unambiguous. These never describe anything but a face the viewer cannot read.
_FACE_BLOCK_RE = re.compile(
    r"\bobscur\w*"
    r"|\bfrom behind\b"
    r"|\bback to (?:the )?(?:camera|lens)\b"
    r"|\b(?:facing|turned|turning|looking) away from (?:the )?(?:camera|lens)\b"
    r"|\bout of (?:the )?frame\b"
    r"|\bcropped (?:out|off|at)\b"
    r"|\b(?:face|features|eyes) (?:is|are|were|stays?) (?:hidden|concealed|covered)\b"
    r"|\bhidden (?:by|behind|in) \w+"
    r"|\bconceal(?:s|ed|ing)\b"
    r"|\bwe (?:do not|don't|never) see\b", re.IGNORECASE)

# Ambiguous on its own — "the dune line behind him is a clean dark silhouette" and "the
# lighthouse stands silhouetted against the night sky" are good cinematography about things
# that have no face. Only the forms that bind the silhouette to a PERSON are blocking.
#
# The window is `[\w'\s]{0,20}?` — word characters, apostrophes and spaces only, so it
# reaches across "he's" and "she stands" but is stopped by any comma or full stop. An
# earlier `\W{0,8}` version was simply wrong: it required NON-word characters, so it matched
# neither "she stands silhouetted" nor "He's silhouetted", and both shipped.
_SIL_SUBJECT = (r"(?:he|she|they|man|woman|boy|girl|child|boxer|keeper|violinist|figure|"
                r"person|player|worker|dancer|singer|fighter)")
_PERSON_SILHOUETTE_RE = re.compile(
    r"^\s*silhouett"                                   # participial, inherits the subject
    r"|\b(?:his|her|their)\s+silhouette\b"
    r"|\b" + _SIL_SUBJECT + r"\b[\w'\s]{0,20}?\bsilhouett", re.IGNORECASE)

# The brief has to ask for a hidden face out loud before the planner will allow one.
_WANTS_HIDDEN_RE = re.compile(
    r"\bsilhouette|\bfrom behind\b|\bback to (?:the )?camera\b|\bfaceless\b|\bno faces?\b|"
    r"\bhidden face|\bface(?:s)? (?:hidden|obscured)|\banonymous\b|\bunidentified\b|"
    r"\bwithout showing (?:the |their |his |her )?face", re.IGNORECASE)


def _face_level(raw: Any, desc: str) -> Tuple[str, bool]:
    """Normalise the model's `face` choice. Returns (level, was_overridden).

    `none` is the one value the model can use to switch the whole face law off, so it is the
    one value that is checked against the prose. Observed: a wide shot whose description read
    "showing the woman standing beside the neon sign, silhouetted against the vibrant lights"
    was labelled `face: "none"`, which disabled the scrub and shipped the silhouette. If
    there is a person in the description there is a face to protect, whatever the label says.
    """
    k = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"closeup": "close", "close_up": "close", "tight": "close", "face": "close",
               "visible": "medium", "mid": "medium", "wide": "medium", "full": "medium",
               "no_face": "none", "nobody": "none", "n/a": "none", "": "",
               "obscured": "hidden", "silhouette": "hidden", "back": "hidden"}
    k = aliases.get(k, k)
    if k not in _FACE_LAWS:
        return ("medium" if _has_person(desc) else "none"), False
    if k == "none" and _has_person(desc):
        return "medium", True
    return k, False


def _scrub_face_blocking(text: str) -> Tuple[str, List[str]]:
    """Remove clauses that hide the face. Returns (text, removed clauses).

    Clause-level, like _clean_settle: sentences are split on `.` and clauses on `,`/`;`, and
    only the offending clause is dropped, so the rest of the direction survives. If every
    clause of a sentence is blocking, the sentence goes.
    """
    removed: List[str] = []
    out_sentences: List[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        if not sentence.strip():
            continue
        clauses = re.split(r"(?<=[,;])\s+", sentence)
        keep = []
        for c in clauses:
            if _FACE_BLOCK_RE.search(c) or _PERSON_SILHOUETTE_RE.search(c):
                removed.append(c.strip().rstrip(",;"))
                continue
            keep.append(c)
        if keep:
            joined = " ".join(keep).strip()
            joined = re.sub(r"[,;]\s*([.!?])", r"\1", joined).strip().rstrip(",;")
            if joined and joined[-1] not in ".!?":
                joined += "."
            out_sentences.append(joined)
    return " ".join(out_sentences).strip(), removed

_NO_TEXT = "No text appears at any point."

# H3 volunteers lettering unless refused — but refusing it on a shot that IS about lettering
# is worse than not refusing at all. Observed: a title-sequence plan that spelled a word in
# mercury and then told the model that no text may appear.
#
# The only reliable signal for "typography is intended" is a SHORT QUOTED RUN. Keyword
# matching was tried and rejected: "neon sign" tripped on a documentary about repairing neon
# (where the refusal is exactly right), and single-quoted LTX dialogue tripped on every
# talking shot. Keywords now only raise a warning; they never suppress the refusal.
_DQ_RE = re.compile(r'"([^"\n]{1,32})"')
_SQ_RE = re.compile(r"(?<![A-Za-z])'([^'\n]{1,32})'")   # lookbehind skips don't / it's
_TEXT_KEYWORD_RE = re.compile(
    r"\b(?:the word|the letter|the letters|spells?|spelling|typography|lettering|"
    r"title card|subtitle|caption)\b", re.IGNORECASE)


def _typography_strings(text: str) -> List[str]:
    """Quoted runs that read as ON-SCREEN TEXT rather than dialogue or an apostrophe.

    A title is a token or a shout ("PHOSPHENE", 'P'); dialogue is a sentence with spaces and
    mixed case. The spec for on-screen text is 3-5 words and <=32 characters, which is what
    the length bound encodes.
    """
    out = [m.group(1) for m in _DQ_RE.finditer(text or "")]
    for m in _SQ_RE.finditer(text or ""):
        s = m.group(1)
        if re.search(r"[A-Za-z0-9]", s) and (" " not in s or s == s.upper()):
            out.append(s)
    return out


def _camera_key(raw: Any) -> Tuple[str, bool]:
    """Canonical camera key + whether the input had to be forced.

    The stored `camera` field must be the key that was actually rendered, not whatever the
    model typed — a shot card reading `cam=medium` (observed: the model confused the `face`
    enum with this one) while the prompt says "holds a static shot" is a lie to the user.
    """
    k = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"locked": "static", "locked_off": "static", "tripod": "static", "none": "static",
               "push": "push_in", "zoom_in": "push_in", "dolly_in": "push_in",
               "pull": "pull_back", "pull_out": "pull_back", "zoom_out": "pull_back",
               "dolly_out": "pull_back", "handheld_sway": "handheld", "sway": "handheld",
               "shake": "handheld", "arc": "orbit", "slow_orbit": "orbit", "orbit_slow": "orbit",
               "slow_pan": "pan", "tilt": "tilt_up", "track": "tracking", "truck": "tracking"}
    k = aliases.get(k, k)
    if k in _CAMERA_SENTENCES:
        return k, False
    return "static", True


def _camera_sentence(key: Any) -> str:
    return _CAMERA_SENTENCES[_camera_key(key)[0]]


def _clean_settle(state: str) -> str:
    """A settled state describes the SUBJECT, never the camera.

    The model reliably writes "the camera stops orbiting" / "the camera holds on the scene"
    here, which is a camera instruction pasted into a clause that ends "with no new movement
    of any kind" — it contradicts itself and duplicates the camera direction. Clauses that
    talk about the camera are dropped; if that empties the phrase, there is no settle.
    """
    s = (state or "").strip().rstrip(".")
    if not s:
        return ""
    keep = [p for p in re.split(r",\s*", s) if "camera" not in p.lower()]
    return ", ".join(p for p in keep if p.strip()).strip()


def _settle_sentence(state: str) -> str:
    s = _clean_settle(state)
    if not s:
        return ""
    return ("The movement is completely finished before the shot ends, and for the last two "
            "seconds %s, with no new movement of any kind." % s)


def _has_person(text: str) -> bool:
    return bool(_PERSON_RE.search(text or ""))


def _plain_punctuation(text: str) -> str:
    """Curly quotes, en/em dashes and ellipses out.

    H3 guide 7.6: punctuation and separators the model has not seen in training can be
    rendered as literal on-screen text. The model emits U+2019 constantly ("you've"), so it
    is normalised on the way into the prompt rather than left to chance."""
    out = text
    for bad, good in list(_SMART.items()) + [("…", "..."), (" ", " ")]:
        out = out.replace(bad, good)
    return out


def _first(d: Dict[str, Any], keys: Sequence[str], default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return default


def _split_three_fields(desc: str) -> Tuple[str, str, str]:
    """If the model pasted the whole assembled prompt into one field, take it apart.

    Plain string slicing rather than a regex: the fields are literal labels in a fixed
    order, and a lazy DOTALL regex silently swallows the later labels into the first group.
    """
    low = desc.lower()
    if "integrated_multimodal_description" not in low:
        return desc, "", ""

    def cut(text: str, label: str) -> Tuple[str, str]:
        """-> (text before label, text after 'label:')"""
        i = text.lower().find(label)
        if i < 0:
            return text, ""
        rest = text[i + len(label):].lstrip()
        if rest.startswith(":"):
            rest = rest[1:]
        return text[:i], rest.strip()

    _, body = cut(desc, "integrated_multimodal_description")
    body, music = cut(body, "non_diegetic_music")
    body, sound = cut(body, "overall_soundscape")
    # non_diegetic_music can legally appear before overall_soundscape in sloppy output;
    # the second cut above already removed it from `body`, so nothing leaks either way.
    if music and "overall_soundscape" in music.lower():
        music, sound2 = cut(music, "overall_soundscape")
        sound = sound or sound2
    return body.strip(), sound.strip(), music.strip()


def _strip_h3_markup(text: str) -> str:
    """H3 markup -> LTX prose. `<d>[English] Hi.</d>` becomes `'Hi.'`, markers go away."""
    out = _D_TAG_RE.sub(lambda m: "'%s'" % m.group(1).strip(), text)
    out = re.sub(r"\[Shot\s*\d+\](?:\s*At\s*\d\d:\d\d\.\d+,)?", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\(S\d(?:,S\d)*\)", "", out)
    out = re.sub(r"</?d>", "", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def _fix_unbalanced_d(text: str) -> str:
    """A stray `<d>` with no `</d>` would be spoken as literal characters. Close it."""
    opens = len(re.findall(r"<d>", text))
    closes = len(re.findall(r"</d>", text))
    if opens == closes:
        return text
    if opens > closes:
        return text + ("</d>" * (opens - closes))
    return re.sub(r"</d>", "", text, count=(closes - opens))


def _snap_duration(value: Any, engine: str, default: float) -> float:
    try:
        d = float(value)
    except (TypeError, ValueError):
        d = default
    if not (d > 0):
        d = default
    d = max(_MIN_DURATION, min(_MAX_DURATION, d))
    if engine == "h3":
        d = min(_H3_LENGTHS, key=lambda cand: (abs(cand - d), cand))
    return float(d)


def _normalise_cast(characters: Optional[Iterable[Any]]) -> List[Dict[str, str]]:
    """Accept list_characters() records, plain ids, or {'id':..,'trigger':..} dicts."""
    out: List[Dict[str, str]] = []
    for c in characters or ():
        if isinstance(c, str):
            cid = c.strip()
            if cid:
                out.append({"id": cid, "trigger": cid, "name": cid, "description": ""})
            continue
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or c.get("trigger") or c.get("character_id") or "").strip()
        if not cid:
            continue
        out.append({
            "id": cid,
            "trigger": str(c.get("trigger") or cid).strip(),
            "name": str(c.get("name") or cid).strip(),
            "description": str(c.get("description") or c.get("bio") or "").strip(),
        })
    return out


def _match_character(raw: Any, cast: Sequence[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if raw is None or not cast:
        return None
    key = str(raw).strip()
    if not key or key.lower() in ("null", "none", "n/a", ""):
        return None
    low = key.lower()
    for c in cast:
        if c["id"].lower() == low or c["trigger"].lower() == low or c["name"].lower() == low:
            return c
    # The model wrote a description ("the fighter"); accept a containment match rather than
    # silently dropping the casting the director asked for.
    for c in cast:
        if c["id"].lower() in low or (c["name"] and c["name"].lower() in low):
            return c
    return None


def _seed_for(seed_base: int, n: int) -> int:
    return int((seed_base + n * 7919) % 2147483647)


def _stable_seed(concept: str) -> int:
    h = hashlib.sha256(concept.encode("utf-8")).hexdigest()[:8]
    return int(h, 16) % 2147483647


def _compose_body(desc: str, camera: Any, settle: str, face: str = "") -> str:
    """description + the camera law + the face law + the settle law, in exemplar order.

    If the model already wrote a camera sentence or an end-state clause of its own, that is
    honoured rather than duplicated — a prompt with two camera instructions is worse than a
    prompt with the wrong one.
    """
    body = _plain_punctuation(_SHOT_MARKER_RE.sub("", (desc or "").strip())).rstrip()
    if body and body[-1] not in ".!?":
        body += "."
    if "the camera" not in body.lower():
        body += " " + _camera_sentence(camera)
    law = _FACE_LAWS.get(face or "", "")
    if law and "holds the exact angle" not in body:
        body += " " + law
    if "completely finished before the shot ends" not in body.lower():
        s = _settle_sentence(_plain_punctuation(settle or ""))
        if s:
            body += " " + s
    if _NO_TEXT.lower() not in body.lower() and not _typography_strings(body):
        body += " " + _NO_TEXT
    return body


def _assemble_h3_prompt(desc: str, sound: str, music: str,
                        camera: Any = "static", settle: str = "", face: str = "") -> str:
    """The official three-field form. `[Shot 1]` carries no timestamp — every storyboard
    shot is one continuous take, so there is never a `[Shot 2]` inside a single prompt."""
    body = _fix_unbalanced_d(_compose_body(desc, camera, settle, face))
    sound = _plain_punctuation((sound or "").strip()) or "N/A"
    music = _plain_punctuation((music or "").strip()) or "N/A"
    return (
        "integrated_multimodal_description: [Shot 1] %s\n\n"
        "overall_soundscape: %s\n\n"
        "non_diegetic_music: %s" % (body, sound, music)
    )


def _assemble_ltx_prompt(desc: str, sound: str, style: str,
                         camera: Any = "static", settle: str = "", face: str = "") -> str:
    """LTX 2.3 prose: one paragraph, master style suffix verbatim, one trailing `Audio:`
    line (the shape mlx_warm_helper's enhance addendum says LTX was trained on)."""
    body = _strip_h3_markup(_compose_body(desc, camera, settle, face)).rstrip()
    if body and body[-1] not in ".!?":
        body += "."
    st = _plain_punctuation((style or "").strip().rstrip("."))
    if st and st.lower() not in body.lower():
        body += " %s." % st
    snd = _plain_punctuation((sound or "").strip().rstrip("."))
    if snd and snd.upper() != "N/A":
        body += " Audio: %s." % snd
    return body.strip()


def default_policy(max_dim: Optional[int] = None) -> Dict[str, Any]:
    """Same shape `storyboard.new_storyboard()` produces, clamped to the machine's cap.

    validate_storyboard() rejects a policy whose longest edge exceeds `max_dim`, so the
    clamp happens here rather than being discovered at validation time.
    """
    policy = {
        "draft": {"quality": "quick", "width": 640, "height": 480, "frames": 49},
        "final": {"quality": "balanced", "width": 1024, "height": 576, "frames": 121},
    }
    if max_dim:
        for key in ("draft", "final"):
            p = policy[key]
            longest = max(p["width"], p["height"])
            if longest > max_dim:
                scale = float(max_dim) / float(longest)
                p["width"] = max(64, int(p["width"] * scale) // 8 * 8)
                p["height"] = max(64, int(p["height"] * scale) // 8 * 8)
    return policy


def coerce_spec(
    raw: Any,
    *,
    concept: str,
    n_shots: int,
    style: str = "",
    cast: Optional[Sequence[Dict[str, str]]] = None,
    board_id: Optional[str] = None,
    engine: str = "auto",
    tier: str = "draft",
    duration_s: float = 5.0,
    seed_base: Optional[int] = None,
    max_dim: Optional[int] = None,
    created_at: Optional[int] = None,
    allow_hidden_faces: bool = False,
    storyboard_mod: Any = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Turn whatever the model returned into a schema-correct storyboard.

    Returns (spec, warnings). Never raises on bad model output — anything unusable is
    replaced by something legal and named in `warnings`, so the caller can decide whether
    the repair round is worth 40 seconds.
    """
    warnings: List[str] = []
    cast = list(cast or ())
    if seed_base is None:
        seed_base = _stable_seed(concept)

    if not isinstance(raw, dict):
        raw = {}
        warnings.append("model returned no JSON object")

    shots_raw = raw.get("shots")
    if not isinstance(shots_raw, list):
        for v in raw.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                shots_raw = v
                warnings.append("shots were under a differently-named key")
                break
    if not isinstance(shots_raw, list):
        shots_raw = []
        warnings.append("model returned no shots array")

    shots: List[Dict[str, Any]] = []
    for idx, s in enumerate(shots_raw):
        if not isinstance(s, dict):
            warnings.append("shot %d was not an object" % (idx + 1))
            continue

        desc = _first(s, _DESC_KEYS)
        sound = _first(s, _SOUND_KEYS)
        music = _first(s, _MUSIC_KEYS, "N/A")
        if "integrated_multimodal_description" in desc.lower():
            desc, split_sound, split_music = _split_three_fields(desc)
            sound = split_sound or sound
            music = split_music or music
            warnings.append("shot %d pasted the assembled prompt into one field" % (idx + 1))
        desc = _SHOT_MARKER_RE.sub("", desc.strip())
        if not desc:
            warnings.append("shot %d had an empty description and was dropped" % (idx + 1))
            continue

        char = _match_character(s.get("character_id") or s.get("character"), cast)
        if s.get("character_id") and char is None and cast:
            warnings.append("shot %d named an unknown character %r — recast as uncast"
                            % (idx + 1, s.get("character_id")))

        if engine in ("h3", "ltx"):
            eng = engine
        else:
            # auto: a trained Phosphene character is an LTX LoRA, and identity is the one
            # thing H3 cannot do. Everything else goes to H3.
            eng = "ltx" if char else "h3"

        n = len(shots) + 1
        dur = _snap_duration(s.get("duration_s") or s.get("duration") or s.get("seconds"),
                             eng, duration_s)
        camera, cam_forced = _camera_key(s.get("camera") or s.get("camera_move"))
        if cam_forced and str(s.get("camera") or "").strip():
            warnings.append("shot %d asked for camera %r, which is not one of %s — locked off"
                            % (idx + 1, str(s.get("camera")).strip(), ", ".join(CAMERA_KEYS)))
        settle_raw = str(s.get("settle") or s.get("end_state") or s.get("ending") or "").strip()
        settle = _clean_settle(settle_raw)
        if settle_raw and not settle:
            warnings.append("shot %d described the camera instead of an end state" % (idx + 1))

        # --- the face law ---------------------------------------------------------------
        face, face_forced = _face_level(
            s.get("face") or s.get("face_visible") or s.get("framing"), desc)
        if face_forced:
            warnings.append("shot %d said no face, but a person is on screen — the face law "
                            "was applied anyway" % (idx + 1))
        if face == "hidden" and not allow_hidden_faces:
            face = "medium"
            warnings.append("shot %d asked to hide the face; the brief did not ask for that, "
                            "so the face is kept visible" % (idx + 1))
        if face in ("close", "medium"):
            desc, cut_d = _scrub_face_blocking(desc)
            settle, cut_s = _scrub_face_blocking(settle)
            settle = settle.rstrip(".")
            for c in (cut_d + cut_s):
                warnings.append("shot %d: removed face-hiding framing %r" % (idx + 1, c[:70]))
            if not desc.strip():
                warnings.append("shot %d was entirely face-hiding and was dropped" % (idx + 1))
                continue
        if _TEXT_KEYWORD_RE.search(desc) and not _DQ_RE.search(desc):
            warnings.append("shot %d names on-screen text but does not put it in double "
                            "quotes — H3 renders described strings as letter-shaped noise"
                            % (idx + 1))

        if eng == "h3":
            prompt = _assemble_h3_prompt(desc, sound, music, camera, settle, face)
        else:
            prompt = _assemble_ltx_prompt(desc, sound, style, camera, settle, face)

        shot: Dict[str, Any] = {
            "n": n,
            "title": str(s.get("title") or s.get("label") or "Shot %d" % n).strip()[:80],
            "mode": "character" if char else "text",
            "engine": eng,
            "tier": tier,
            "prompt": prompt,
            "duration_s": dur,
            "seed": _seed_for(seed_base, n),
            "refs": [],
            "status": "pending",
            # The creative payload is kept alongside the assembled prompt so a re-roll can
            # edit one field without parsing it back out of the finished string.
            "description": desc,
            "camera": camera,
            "face": face,
            "settle": settle,
            "soundscape": sound,
            "music": music,
        }
        if char:
            shot["character_id"] = char["id"]
            shot["trigger"] = char["trigger"]
            if storyboard_mod is not None and hasattr(storyboard_mod, "ensure_trigger"):
                shot["prompt"] = storyboard_mod.ensure_trigger(shot["prompt"], char["trigger"])
            elif not re.search(r"\b%s\b" % re.escape(char["trigger"]), shot["prompt"]):
                shot["prompt"] = "%s %s" % (char["trigger"], shot["prompt"])
            if eng == "h3":
                # An H3 T2VA prompt must begin with the field label, so a prepended trigger
                # would be illegal (grammar rule 5). Put it inside the description instead.
                shot["prompt"] = _assemble_h3_prompt(
                    "%s The on-screen subject is %s." % (desc, char["trigger"]),
                    sound, music, camera, settle, face)
        shots.append(shot)

    if len(shots) != n_shots:
        warnings.append("asked for %d shots, model returned %d" % (n_shots, len(shots)))
    cams = {_camera_sentence(s.get("camera")) for s in shots}
    if len(shots) > 2 and len(cams) == 1:
        # Not corrected — overriding a director's camera is worse than telling them. But it
        # is the single most common small-model tell, so the UI gets to say so.
        warnings.append("every shot uses the same camera behaviour (%s) — consider varying it"
                        % (shots[0].get("camera") or "static"))
    if len(shots) > 2 and not any((s.get("settle") or "").strip() for s in shots):
        warnings.append("no shot named an end state — H3 invents motion in the tail")

    title = str(raw.get("title") or raw.get("film_title") or "").strip()
    if not title:
        title = concept.strip()[:60] or "Untitled storyboard"
        warnings.append("model returned no title")

    created = int(created_at if created_at is not None else time.time())
    bid = board_id or "sb-%d-%s" % (created, hashlib.sha1(
        ("%s|%d" % (concept, seed_base)).encode("utf-8")).hexdigest()[:6])

    spec = {
        "schema": getattr(storyboard_mod, "SCHEMA_VERSION", 1) if storyboard_mod else 1,
        "id": bid,
        "title": title,
        "created_at": created,
        "cast": [{"id": c["id"], "trigger": c["trigger"], "name": c["name"]}
                 for c in cast if any(s.get("character_id") == c["id"] for s in shots)],
        "policy": default_policy(max_dim),
        "shots": shots,
    }
    return spec, warnings


# --------------------------------------------------------------------------------------
# The model process
# --------------------------------------------------------------------------------------

class PlannerSession(object):
    """A short-lived child process holding the planner model.

    Use it as a context manager when you want several generations to share one load
    (`plan_film()` does this internally for the repair round). It is NEVER left running:
    `plan_film()` releases in a finally, and `__exit__` releases too.

        with PlannerSession() as s:
            out = s.generate(system="...", user="...")
        # model is gone here, guaranteed by process exit
    """

    def __init__(self,
                 model_path: Optional[Any] = None,
                 python_exe: Optional[Any] = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S):
        self.model_path = Path(model_path or DEFAULT_MODEL_PATH)
        self.python_exe = Path(python_exe or WORKER_PYTHON)
        self.timeout_s = float(timeout_s)
        self.proc = None  # type: Optional[subprocess.Popen]
        self.stats = {
            "model_path": str(self.model_path),
            "python": str(self.python_exe),
            "load_s": None,
            "calls": 0,
            "gen_s_total": 0.0,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "peak_rss_bytes": 0,
            "mx_peak_bytes": 0,
            "released": False,
        }

    # -- lifecycle ----------------------------------------------------------------
    def _spawn(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        if not self.model_path.exists():
            raise PlannerError(
                "planner model not found at %s — set LTX_STORYBOARD_PLANNER to an "
                "mlx-lm-loadable directory" % self.model_path)
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("TOKENIZERS_PARALLELISM", "false")
        self.proc = subprocess.Popen(
            [str(self.python_exe), str(Path(__file__).resolve()), "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=str(ROOT), text=True, bufsize=1,
        )
        self.stats["released"] = False

    def release(self) -> Dict[str, Any]:
        """Kill the child and reclaim every byte. Idempotent; safe to call twice."""
        proc = self.proc
        self.proc = None
        if proc is None:
            self.stats["released"] = True
            return self.stats
        try:
            if proc.poll() is None and proc.stdin is not None:
                try:
                    proc.stdin.write(json.dumps({"action": "exit"}) + "\n")
                    proc.stdin.flush()
                except (OSError, ValueError):
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
        # Independent of anything the child said about itself.
        try:
            import resource
            rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            self.stats["children_maxrss_bytes"] = int(rss if sys.platform == "darwin" else rss * 1024)
        except Exception:
            pass
        self.stats["released"] = True
        return self.stats

    # Context manager so "load -> plan -> release" cannot be forgotten.
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    unload = release   # alias: the panel's vocabulary is "unload"

    # -- generation ---------------------------------------------------------------
    def generate(self, system: str, user: str,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 temperature: float = DEFAULT_TEMPERATURE,
                 seed: int = 10) -> Dict[str, Any]:
        self._spawn()
        req = {
            "action": "generate",
            "model_path": str(self.model_path),
            "system": system,
            "user": user,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "seed": int(seed),
        }
        assert self.proc is not None and self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise PlannerError("planner subprocess died before it could be asked: %s" % exc)

        deadline = time.time() + self.timeout_s
        assert self.proc.stdout is not None
        while True:
            if self.proc.poll() is not None:
                err = ""
                try:
                    if self.proc.stderr is not None:
                        err = self.proc.stderr.read()[-2000:]
                except Exception:
                    pass
                raise PlannerError("planner subprocess exited (%s)%s"
                                   % (self.proc.returncode, (": " + err) if err else ""))
            line = self.proc.stdout.readline()
            if not line:
                raise PlannerError("planner subprocess closed its output")
            if line.startswith(_SENTINEL):
                resp = json.loads(line[len(_SENTINEL):])
                break
            if time.time() > deadline:
                raise PlannerError("planner timed out after %.0fs" % self.timeout_s)

        if resp.get("error"):
            raise PlannerError("planner model error: %s" % resp["error"])

        st = self.stats
        if st["load_s"] is None and resp.get("load_s") is not None:
            st["load_s"] = resp["load_s"]
        st["calls"] += 1
        st["gen_s_total"] = round(st["gen_s_total"] + float(resp.get("gen_s") or 0.0), 2)
        st["prompt_tokens"] += int(resp.get("prompt_tokens") or 0)
        st["output_tokens"] += int(resp.get("output_tokens") or 0)
        st["peak_rss_bytes"] = max(st["peak_rss_bytes"], int(resp.get("peak_rss_bytes") or 0))
        st["mx_peak_bytes"] = max(st["mx_peak_bytes"], int(resp.get("mx_peak_bytes") or 0))
        return resp


# --------------------------------------------------------------------------------------
# plan_film
# --------------------------------------------------------------------------------------

def is_plan_error(result: Any) -> bool:
    """True when plan_film() returned a structured error rather than a film spec."""
    return isinstance(result, dict) and result.get("ok") is False


def _error(kind: str, message: str, *, hint: str = "", problems: Optional[Sequence[str]] = None,
           raw: str = "", meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A failure the UI can render as a sentence and a fix-list. Never a traceback."""
    return {
        "ok": False,
        "error": {
            "kind": kind,
            "message": message,
            "hint": hint,
            "problems": list(problems or ()),
            "raw_excerpt": (raw or "")[:1200],
        },
        "_planner": dict(meta or {}),
    }


def _parse_feedback(feedback: Any) -> Tuple[str, Optional[int], str]:
    """-> (mode, shot_n, note). mode is 'none' | 'film' | 'shot'."""
    if feedback is None:
        return "none", None, ""
    if isinstance(feedback, dict):
        note = str(feedback.get("note") or feedback.get("text") or feedback.get("feedback") or "").strip()
        shot = feedback.get("shot", feedback.get("shot_n", feedback.get("n")))
        if shot is not None:
            try:
                return "shot", int(shot), note
            except (TypeError, ValueError):
                pass
        return ("film", None, note) if note else ("none", None, "")
    text = str(feedback).strip()
    if not text:
        return "none", None, ""
    m = re.match(r"^\s*shots?\s*#?\s*(\d+)\s*[:\-\u2014]\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if m:
        return "shot", int(m.group(1)), m.group(2).strip()
    return "film", None, text


def plan_film(
    concept: str,
    n_shots: int = 6,
    style: str = "",
    characters: Optional[Iterable[Any]] = None,
    must_include: Optional[Iterable[Any]] = None,
    feedback: Any = None,
    *,
    previous: Optional[Dict[str, Any]] = None,
    engine: str = "auto",
    tier: str = "draft",
    duration_s: float = 5.0,
    allow_hidden_faces: Optional[bool] = None,
    board_id: Optional[str] = None,
    known_character_ids: Optional[Iterable[str]] = None,
    ref_root: Optional[Any] = None,
    max_dim: Optional[int] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    seed: Optional[int] = None,
    max_tokens: Optional[int] = None,
    model_path: Optional[Any] = None,
    python_exe: Optional[Any] = None,
    session: Optional[PlannerSession] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Dict[str, Any]:
    """Concept -> film spec that `storyboard.validate_storyboard()` accepts.

    Returns EITHER a storyboard dict (schema/id/title/created_at/cast/policy/shots, plus a
    `_planner` block of metadata the validator ignores) OR a structured error dict — test
    with `is_plan_error(result)`. It never raises for model behaviour and never surfaces a
    traceback to a user.

    Args:
      concept        one or two sentences of intent. The only required argument.
      n_shots        how many shots to plan.
      style          master style, reused verbatim on every shot.
      characters     trained characters available for casting. Accepts `list_characters()`
                     records, bare id strings, or {'id','trigger','name','description'}.
                     A shot cast with one renders on LTX (that is where character LoRAs
                     live); everything else renders on H3.
      must_include   things that must appear somewhere in the film.
      feedback       None for a fresh plan. For a re-plan pass `previous=` plus either:
                       film-level : "make it colder, drop the voiceover"
                       per-shot   : "shot 4: he should not turn his head"
                                    or {"shot": 4, "note": "..."}
                     Per-shot re-rolls replace ONLY that shot; every other shot object is
                     carried across by reference, so the rest of the plan is byte-stable.
      engine         "auto" (default) | "h3" | "ltx". Auto = H3 unless the shot is cast.
      tier           per-shot tier, default "draft" — plans are for reviewing, not shipping.
      allow_hidden_faces
                     None (default) auto-detects from the brief: a face may only be hidden
                     if the concept, style or must_include asked for it in so many words
                     ("silhouette", "from behind", "faceless", ...). Otherwise every shot
                     with a person carries the face law and face-hiding framing is scrubbed
                     out of the prose. Pass True/False to override the detection.
      max_dim        this machine's resolution cap; the policy is clamped to it so the
                     validator's tier check cannot fire.
      session        an already-open PlannerSession to borrow. If given it is NOT released
                     (the owner keeps it); otherwise a private one is opened and always
                     released before returning.

                     Pass one when the caller needs a handle on the running model — a
                     Cancel button is the case that matters: `plan_film()` blocks for
                     20-40 s, and the only way to stop it is `sess.release()` from another
                     thread, which kills the child and makes this call raise into its own
                     `finally`. A caller that supplies a session MUST release it.
    """
    t_start = time.time()
    if not str(concept or "").strip():
        raise PlannerError("concept is empty")
    n_shots = max(1, int(n_shots))
    cast = _normalise_cast(characters)
    must = [str(m).strip() for m in (must_include or ()) if str(m).strip()]
    fb_mode, fb_shot, fb_note = _parse_feedback(feedback)
    if fb_mode != "none" and previous is None:
        previous = feedback.get("previous") if isinstance(feedback, dict) else None
    if fb_mode != "none" and not isinstance(previous, dict):
        raise PlannerError("feedback needs the plan it refers to — pass previous=<spec>")

    # A hidden face is opt-in, and the brief is the only thing that may opt in. Detection
    # reads the concept, the style and the must-includes — not the model's output, which is
    # exactly the thing being guarded against.
    if allow_hidden_faces is None:
        brief_text = " ".join([concept or "", style or ""] + must)
        allow_hidden = bool(_WANTS_HIDDEN_RE.search(brief_text))
    else:
        allow_hidden = bool(allow_hidden_faces)

    validate, sb = _load_validator()
    seed_base = int(seed) if seed is not None else _stable_seed(concept)
    known_ids = list(known_character_ids) if known_character_ids is not None \
        else [c["id"] for c in cast]
    budget = int(max_tokens) if max_tokens else min(
        8192, max(1200, 380 * (1 if fb_mode == "shot" else n_shots) + 500))

    system = _build_system_prompt(engine, bool(cast), allow_hidden)
    if fb_mode == "shot":
        user = _build_shot_feedback_prompt(previous, fb_shot, fb_note)
    elif fb_mode == "film":
        user = _build_film_feedback_prompt(previous, fb_note, n_shots)
    else:
        user = _build_user_prompt(concept, n_shots, style, cast, must)

    owned = session is None
    sess = session or PlannerSession(model_path=model_path, python_exe=python_exe,
                                     timeout_s=timeout_s)
    result: Dict[str, Any] = {}
    try:
        result = _plan_with_session(
            sess, system=system, user=user, fb_mode=fb_mode, fb_shot=fb_shot,
            previous=previous, validate=validate, sb=sb, concept=concept, n_shots=n_shots,
            style=style, cast=cast, board_id=board_id, engine=engine, tier=tier,
            duration_s=duration_s, seed_base=seed_base, max_dim=max_dim,
            known_ids=known_ids, ref_root=ref_root, temperature=temperature,
            budget=budget, model_path=model_path, t_start=t_start,
            allow_hidden_faces=allow_hidden)
    finally:
        # The model is gone before this function returns, on every path including an
        # exception. Peak RSS is only final once the child has been reaped, so the
        # measurement is patched in AFTER release.
        stats = sess.release() if owned else sess.stats
        blk = result.get("_planner") if isinstance(result, dict) else None
        if isinstance(blk, dict):
            blk.update(_session_meta_from(stats))
    return result


def _plan_with_session(sess, *, system, user, fb_mode, fb_shot, previous, validate, sb,
                       concept, n_shots, style, cast, board_id, engine, tier, duration_s,
                       seed_base, max_dim, known_ids, ref_root, temperature, budget,
                       model_path, t_start, allow_hidden_faces) -> Dict[str, Any]:
    """The generate -> extract -> coerce -> validate -> ONE repair loop.

    Split out of plan_film() so the `finally:` that releases the model is three lines with
    nothing else in it — an unload that shares a code path with the happy path is an unload
    that eventually gets skipped.
    """
    meta = {"model": Path(model_path or sess.model_path).name, "attempts": 0}

    def coerce(obj):
        return _coerce_for_mode(
            obj, fb_mode, fb_shot, previous, concept=concept, n_shots=n_shots, style=style,
            cast=cast, board_id=board_id, engine=engine, tier=tier, duration_s=duration_s,
            seed_base=seed_base, max_dim=max_dim, sb=sb,
            allow_hidden_faces=allow_hidden_faces)

    def check(spec):
        return list(validate(spec, known_character_ids=known_ids,
                             ref_root=Path(ref_root) if ref_root else None, max_dim=max_dim))

    # ---- attempt 1 ------------------------------------------------------------------
    try:
        resp = sess.generate(system, user, max_tokens=budget,
                             temperature=temperature, seed=seed_base % 100000)
    except PlannerError as exc:
        return _error("model_unavailable", str(exc),
                      hint="Check that the planner model exists and that ltx-2-mlx/env "
                           "has mlx-lm installed.",
                      meta=dict(meta, elapsed_s=round(time.time() - t_start, 2)))
    meta["attempts"] = 1
    raw_text = resp.get("text") or ""
    obj = extract_json_object(raw_text)
    spec, warnings = coerce(obj)
    errs = check(spec)
    first_try = list(errs)
    count_off = (fb_mode != "shot") and (len(spec.get("shots") or []) != n_shots)
    first_try_clean = not first_try and not count_off and obj is not None

    # ---- ONE repair round-trip, carrying the REAL validator's words ------------------
    # Not a retry loop: a second failure means the concept is the problem, and burning
    # another 40 s of a user's evening to hear the same complaint helps nobody.
    if errs or count_off or obj is None:
        problems = list(errs)
        if count_off:
            problems.append("the plan has %d shots but exactly %d were requested"
                            % (len(spec.get("shots") or []), n_shots))
        if obj is None:
            problems.append("your reply did not contain a JSON object at all")
        try:
            resp2 = sess.generate(system, _build_repair_prompt(raw_text, problems, n_shots),
                                  max_tokens=budget,
                                  temperature=max(0.0, temperature * 0.5),
                                  seed=(seed_base + 1) % 100000)
            meta["attempts"] = 2
            raw2 = resp2.get("text") or ""
            obj2 = extract_json_object(raw2)
            if obj2 is not None:
                spec2, warn2 = coerce(obj2)
                errs2 = check(spec2)
                off2 = (fb_mode != "shot") and (len(spec2.get("shots") or []) != n_shots)
                # Keep the repair only if it is genuinely better, so a worse second draft
                # cannot destroy a first draft that merely had the wrong shot count.
                if (len(errs2), off2) < (len(errs), count_off):
                    spec, warnings, errs, count_off, raw_text = spec2, warn2, errs2, off2, raw2
                    warnings.append("repaired on the second pass")
        except PlannerError as exc:
            warnings.append("repair round failed: %s" % exc)

    if errs:
        return _error(
            "invalid_plan",
            "The planner could not turn this concept into a valid storyboard.",
            hint="Try a shorter, more concrete concept, or fewer shots.",
            problems=errs, raw=raw_text,
            meta=dict(meta, warnings=warnings, elapsed_s=round(time.time() - t_start, 2),
                      **_session_meta(sess)))

    spec["_planner"] = dict(
        meta,
        ok=True,
        warnings=warnings,
        first_try_errors=first_try,
        first_try_clean=first_try_clean,
        shot_count_ok=not count_off,
        engine_mix=_engine_mix(spec),
        concept=concept.strip(),
        feedback_mode=fb_mode,
        elapsed_s=round(time.time() - t_start, 2),
        **_session_meta(sess)
    )
    return spec


def _session_meta(sess: PlannerSession) -> Dict[str, Any]:
    return _session_meta_from(sess.stats)


def _session_meta_from(stats: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "model_path": stats.get("model_path"),
        "load_s": stats.get("load_s"),
        "gen_s": stats.get("gen_s_total"),
        "prompt_tokens": stats.get("prompt_tokens"),
        "output_tokens": stats.get("output_tokens"),
        "peak_rss_bytes": stats.get("peak_rss_bytes"),
        "peak_rss_gb": round((stats.get("peak_rss_bytes") or 0) / float(2 ** 30), 2),
        "mx_peak_gb": round((stats.get("mx_peak_bytes") or 0) / float(2 ** 30), 2),
        "model_released": bool(stats.get("released")),
    }


def _engine_mix(spec: Dict[str, Any]) -> Dict[str, int]:
    mix: Dict[str, int] = {}
    for s in spec.get("shots") or ():
        k = s.get("engine") or "h3"
        mix[k] = mix.get(k, 0) + 1
    return mix


def _coerce_for_mode(obj, fb_mode, fb_shot, previous, *, concept, n_shots, style, cast,
                     board_id, engine, tier, duration_s, seed_base, max_dim, sb,
                     allow_hidden_faces=False):
    """Coerce a fresh plan, or splice one re-rolled shot into an existing plan."""
    if fb_mode != "shot":
        return coerce_spec(obj, concept=concept, n_shots=n_shots, style=style, cast=cast,
                           board_id=board_id, engine=engine, tier=tier,
                           duration_s=duration_s, seed_base=seed_base, max_dim=max_dim,
                           allow_hidden_faces=allow_hidden_faces, storyboard_mod=sb)

    # Per-shot re-roll: coerce the single returned shot, then splice. Every other shot
    # object is carried across by reference, so the untouched part of the plan is
    # byte-identical when re-serialised.
    one, warnings = coerce_spec(obj, concept=concept, n_shots=1, style=style, cast=cast,
                                board_id=previous.get("id"), engine=engine, tier=tier,
                                duration_s=duration_s, seed_base=seed_base, max_dim=max_dim,
                                allow_hidden_faces=allow_hidden_faces, storyboard_mod=sb)
    new_shots = one.get("shots") or []
    spec = copy.copy(previous)
    spec["shots"] = list(previous.get("shots") or [])
    if not new_shots:
        warnings.append("re-roll returned no usable shot; the original was kept")
        return spec, warnings
    replacement = new_shots[0]
    for i, s in enumerate(spec["shots"]):
        if s.get("n") == fb_shot:
            replacement["n"] = fb_shot
            # A re-roll should look different: nudge the seed rather than re-render the
            # same latent with new words.
            replacement["seed"] = _seed_for(seed_base + int(time.time()) % 9973, fb_shot)
            for carry in ("status",):
                if carry in s:
                    replacement[carry] = s[carry]
            spec["shots"][i] = replacement
            break
    else:
        warnings.append("shot %r was not in the plan; nothing replaced" % fb_shot)
    return spec, warnings


# --------------------------------------------------------------------------------------
# The worker process (runs under ltx-2-mlx/env python3.11 — the only place mlx is imported)
# --------------------------------------------------------------------------------------

def _worker_serve() -> int:
    import resource

    def emit(obj):
        sys.stdout.write(_SENTINEL + json.dumps(obj) + "\n")
        sys.stdout.flush()

    def peak_rss_bytes():
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(r if sys.platform == "darwin" else r * 1024)

    model = tokenizer = None
    loaded_path = None
    load_s = None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as exc:
            emit({"error": "bad request: %s" % exc})
            continue
        action = req.get("action")
        if action == "exit":
            break
        if action == "ping":
            emit({"pong": True, "peak_rss_bytes": peak_rss_bytes()})
            continue
        if action != "generate":
            emit({"error": "unknown action %r" % action})
            continue
        try:
            import mlx.core as mx
            from mlx_lm import load as mlx_lm_load, generate as mlx_generate
            from mlx_lm.sample_utils import make_sampler

            path = req["model_path"]
            if model is None or loaded_path != path:
                t0 = time.time()
                model, tokenizer = mlx_lm_load(path)
                loaded_path = path
                load_s = round(time.time() - t0, 2)

            messages = [
                {"role": "system", "content": req.get("system") or ""},
                {"role": "user", "content": req.get("user") or ""},
            ]
            try:
                chat = tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True)
            except Exception:
                # Some chat templates refuse a system role — fold it into the user turn.
                chat = tokenizer.apply_chat_template(
                    [{"role": "user", "content": (req.get("system") or "") + "\n\n"
                      + (req.get("user") or "")}],
                    tokenize=False, add_generation_prompt=True)

            mx.random.seed(int(req.get("seed") or 10))
            t0 = time.time()
            text = mlx_generate(
                model=model, tokenizer=tokenizer, prompt=chat,
                max_tokens=int(req.get("max_tokens") or DEFAULT_MAX_TOKENS),
                sampler=make_sampler(temp=float(req.get("temperature") or 0.0)),
                verbose=False,
            )
            gen_s = round(time.time() - t0, 2)
            try:
                mx_peak = int(mx.get_peak_memory())
            except Exception:
                mx_peak = 0
            emit({
                "text": text,
                "load_s": load_s,
                "gen_s": gen_s,
                "prompt_tokens": len(tokenizer.encode(chat)),
                "output_tokens": len(tokenizer.encode(text)),
                "peak_rss_bytes": peak_rss_bytes(),
                "mx_peak_bytes": mx_peak,
            })
        except Exception as exc:  # never let a traceback reach the parent as protocol
            import traceback
            emit({"error": "%s: %s" % (type(exc).__name__, exc),
                  "trace": traceback.format_exc()[-1500:]})
    return 0


# --------------------------------------------------------------------------------------
# CLI — `--serve` is the worker; no args plans a film and prints it
# --------------------------------------------------------------------------------------

def _main(argv: Sequence[str]) -> int:
    if "--serve" in argv:
        return _worker_serve()
    import argparse
    ap = argparse.ArgumentParser(description="Plan a Phosphene storyboard from a concept.")
    ap.add_argument("concept", nargs="?", help="one or two sentences of intent")
    ap.add_argument("-n", "--shots", type=int, default=6)
    ap.add_argument("--style", default="")
    ap.add_argument("--character", action="append", default=[],
                    help="trigger of a trained character available for casting")
    ap.add_argument("--must", action="append", default=[])
    ap.add_argument("--engine", default="auto", choices=("auto", "h3", "ltx"))
    ap.add_argument("--tier", default="draft")
    ap.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    ap.add_argument("--model", default=None)
    args = ap.parse_args([a for a in argv if a != "--serve"])
    if not args.concept:
        ap.error("a concept is required")
    out = plan_film(args.concept, n_shots=args.shots, style=args.style,
                    characters=args.character, must_include=args.must,
                    engine=args.engine, tier=args.tier, temperature=args.temperature,
                    model_path=args.model)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 1 if is_plan_error(out) else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
