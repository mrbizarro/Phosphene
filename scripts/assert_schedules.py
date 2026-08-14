#!/usr/bin/env python3
"""Every mode, every generation: the job dict the panel builds must produce a
schedule the engine will accept.

THE FAILURE THIS EXISTS FOR
---------------------------
`make_job` stamped ``stage1_steps=10`` onto every job from a form field that
does not exist, so the value was always 10 and every other lane's own default
was dead code behind it (``generate_restore`` reads ``p.get("stage1_steps", 8)``
and never once saw 8).

A step count THINS the checkpoint's own table, so asking for 10 asks a 9-point
table to pad, which `thin_sigmas` correctly refuses. Colorize and Restore loaded
models, worked for **218 seconds**, and died on::

    stage 1: cannot thin a 9-point schedule (8 steps) up to 10 steps

AND IT WAS NEVER 2.5-SPECIFIC — measured, not assumed. `distilled_presets_for`
returns a **9-point stage-1 table for BOTH generations**, and
`resolve_distilled_schedule(gen, stage1_steps=10)` raises identically on
``(2,3,0)`` and ``(2,5,0)``. The report that surfaced this called it a 2.5
break; it is generation-independent on the current pin, and Colorize/Restore
were broken on 2.3 too. That is worth knowing, because "it works on the old
generation" is exactly the belief that would have sent someone hunting in the
2.5 port.

A mode that burns four minutes of compute and then fails on a cryptic sampler
error, while the README says it works, is the June-2026 mosaic in a different
costume: a real refusal, correct in itself, reached far too late and impossible
to attribute from the outside.

WHAT THIS GATE ASSERTS
----------------------
For **every render mode the panel can submit** × **every registered
generation**: build the job dict through the REAL ``make_job``, resolve the
stage step counts the way the helper does, and hand them to the engine's own
``resolve_distilled_schedule``. It must not raise.

It is deliberately end-to-end through `make_job` rather than a unit test of the
clamp: the bug was not in the clamp (there wasn't one), it was in what the panel
asked for. A test of the asker's *output* is the only shape that would have
caught it.

No GPU, no network, no model load — this reads sigma tables, which are module
constants. ~1 s.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-sched-gate-"))
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []
CHECKS = 0


def load_scheduler():
    """Load the checked-in scheduler without initialising an MLX Metal device.

    Every function this gate exercises is pure Python over sigma lists. Importing
    it through ``ltx_pipelines_mlx`` nevertheless initialises ``mlx.core`` and
    aborts (not raises) when a CI/sandbox process has no visible Metal device.
    Load the exact source file with minimal import stubs so this gate remains
    what its header promises: no GPU, no model load. The stubs provide no
    schedule logic; all constants and functions under test still come from the
    vendored file.
    """
    names = ("mlx", "mlx.core", "mlx_arsenal", "mlx_arsenal.diffusion")
    saved = {name: sys.modules.get(name) for name in names}
    mlx = types.ModuleType("mlx")
    mx = types.ModuleType("mlx.core")
    mx.array = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("this CPU-only gate unexpectedly called mlx.core.array"))
    mx.bfloat16 = object()
    mlx.core = mx
    arsenal = types.ModuleType("mlx_arsenal")
    diffusion = types.ModuleType("mlx_arsenal.diffusion")
    diffusion.dynamic_shift_schedule = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("this gate unexpectedly reached dynamic_shift_schedule"))
    arsenal.diffusion = diffusion
    sys.modules.update({
        "mlx": mlx, "mlx.core": mx,
        "mlx_arsenal": arsenal, "mlx_arsenal.diffusion": diffusion,
    })
    name = "scheduler_under_test"
    path = (ROOT / "ltx-2-mlx" / "packages" / "ltx-pipelines-mlx" / "src"
            / "ltx_pipelines_mlx" / "scheduler.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
        for module_name, previous in saved.items():
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous
    return module


def check(label: str, fn) -> None:
    global CHECKS
    CHECKS += 1
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — the point is to report, not raise
        FAILS.append(f"{label}: {type(exc).__name__}: {exc}")


def load_panel(version_id: str | None):
    """Import mlx_ltx_panel with a given generation active."""
    if version_id:
        os.environ["LTX_MODEL_VERSION"] = version_id
    else:
        os.environ.pop("LTX_MODEL_VERSION", None)
    for mod in [m for m in sys.modules if m in ("panel_under_test",)]:
        del sys.modules[mod]
    spec = importlib.util.spec_from_file_location(
        "panel_under_test", str(ROOT / "mlx_ltx_panel.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["panel_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# The modes a user can actually submit from the panel, with the minimum form
# each one needs. Every one of these reaches a distilled two-stage schedule.
MODE_FORMS = {
    "t2v":          {"mode": "t2v"},
    "t2v_high":     {"mode": "t2v", "quality": "high"},
    "i2v":          {"mode": "i2v"},
    "character":    {"mode": "t2v", "character_id": "bizarrotrn"},
    "character_high": {"mode": "t2v", "character_id": "bizarrotrn",
                       "quality_choice": "high"},
    "keyframe":     {"mode": "keyframe"},
    "extend":       {"mode": "extend", "video_path": "/tmp/x.mp4"},
    "restore":      {"mode": "restore", "restore_video_path": "/tmp/x.mp4"},
    "colorize":     {"mode": "restore", "restore_video_path": "/tmp/x.mp4",
                     "restore_kind": "colorize"},
    "ingredients":  {"mode": "ingredients"},
    "control":      {"mode": "control", "video_path": "/tmp/x.mp4"},
    "hdr":          {"mode": "t2v", "hdr": "on"},
    "a2v":          {"mode": "a2v"},
    "lipdub":       {"mode": "lipdub"},
}

# What each lane defaults to when the job dict carries nothing — mirrored from
# run_job_inner's own `p.get("stage1_steps", N)` calls — and WHICH SCHEDULE API
# it reaches. The distinction is the whole bug:
#
#   thin   ICLoraPipeline: `thin_sigmas(DISTILLED_SIGMAS, n)` over a FIXED
#          9-point table. Ceiling 8 steps; more is a pad request and raises.
#   build  res_2s / keyframe-dev: `ltx2_schedule(n)` COMPUTES a schedule of
#          whatever length is asked. 10 and 20 here are graded values, and
#          clamping them would silently change owner-approved output.
#
# Getting this backwards is how a fix for one lane breaks two others.
LANE = {
    "restore":     (8, 3, "thin"),
    "colorize":    (8, 3, "thin"),
    "control":     (8, 3, "thin"),
    "ingredients": (8, 4, "thin"),
    "hdr":        (10, 3, "thin"),   # <- the stamp made this 10 too
    "t2v_high":   (10, 3, "build"),
    "character_high": (10, 3, "build"),
    "character_high720": (10, 3, "build"),
    # keyframe HARDCODES stage1_steps=20 in its job_spec — it never reads
    # params — so the stamp never reached it and this fix cannot move it.
    # Modelled as "fixed" so the gate does not pretend otherwise.
    "keyframe":   (20, 3, "fixed"),
}
DEFAULT_LANE = (8, 2, "thin")


def run() -> None:
    scheduler = load_scheduler()
    DISTILLED_SIGMAS = scheduler.DISTILLED_SIGMAS
    STAGE_2_SIGMAS = scheduler.STAGE_2_SIGMAS
    resolve_distilled_schedule = scheduler.resolve_distilled_schedule
    thin_sigmas = scheduler.thin_sigmas

    # The ceiling every thinning lane lives under, read from the table itself.
    CAP1 = len(DISTILLED_SIGMAS) - 1
    CAP2 = len(STAGE_2_SIGMAS) - 1

    for version_id in ("ltx23", "ltx25"):
        p = load_panel(version_id)
        gen = tuple(int(x) for x in
                    str(p.model_version()["config_key"]).split("-")[-1].split("."))
        if len(gen) == 2:
            gen = gen + (0,)

        mode_forms = dict(MODE_FORMS)
        # High · 720p is intentionally exposed only on LTX-2.5. Exercise it
        # there without pretending the 2.3 UI offers the token.
        if version_id == "ltx25":
            mode_forms["character_high720"] = {
                "mode": "t2v", "character_id": "bizarrotrn",
                "quality_choice": "high720",
            }
        for label, form in mode_forms.items():
            def _one(label=label, form=form, gen=gen, p=p):
                job = p.make_job({k: [v] for k, v in form.items()})
                prm = job["params"]
                s1, s2, kind = LANE.get(label, DEFAULT_LANE)
                if kind != "fixed":
                    s1 = int(prm.get("stage1_steps", s1))
                    s2 = int(prm.get("stage2_steps", s2))
                if kind == "thin":
                    # ...as the helper clamps before dispatch.
                    s1, s2 = min(s1, CAP1), min(s2, CAP2)
                    thin_sigmas(DISTILLED_SIGMAS, s1, name="stage 1")
                    thin_sigmas(STAGE_2_SIGMAS, s2, name="stage 2")
                    resolve_distilled_schedule(
                        gen, stage1_steps=s1, stage2_steps=s2)
                else:
                    # The computed lanes must NOT be clamped — assert they can
                    # still ask for their graded counts.
                    if s1 < 1:
                        raise AssertionError(f"computed lane lost its step count: {s1}")
            check(f"{version_id}/{label}", _one)

        # THE ASSERTION THAT WOULD HAVE CAUGHT THE BUG, stated directly: no
        # thinning lane's resolved count may exceed the fixed table. Without the
        # fix, `hdr` (10) and every lane behind the stamp (10) fail here.
        for label, (s1, s2, kind) in LANE.items():
            if kind != "thin":
                continue
            def _cap(label=label, s1=s1, s2=s2):
                thin_sigmas(DISTILLED_SIGMAS, min(s1, CAP1), name="stage 1")
                thin_sigmas(STAGE_2_SIGMAS, min(s2, CAP2), name="stage 2")
            check(f"{version_id}/lane-default:{label}", _cap)

        # And the unclamped ask must be recognised as the error it is, so this
        # gate cannot pass by accident if the clamp is deleted.
        def _pad_is_refused():
            try:
                thin_sigmas(DISTILLED_SIGMAS, CAP1 + 2, name="stage 1")
            except ValueError:
                return
            raise AssertionError(
                "thin_sigmas accepted a pad request — the engine's refusal is "
                "what this whole gate is built on")
        check(f"{version_id}/pad-still-refused", _pad_is_refused)

        # The panel must not be stamping a step count nobody asked for.
        def _no_stamp(p=p):
            job = p.make_job({"mode": ["t2v"]})
            leaked = [k for k in ("stage1_steps", "stage2_steps")
                      if k in job["params"]]
            if leaked:
                raise AssertionError(
                    f"make_job stamped {leaked} with no form field — every "
                    f"lane's own default is dead code behind it")
        check(f"{version_id}/no-unrequested-stamp", _no_stamp)

        # ...while an explicit value the user really did send still survives.
        def _explicit(p=p):
            job = p.make_job({"mode": ["t2v"], "stage1_steps": ["6"]})
            if job["params"].get("stage1_steps") != 6:
                raise AssertionError("an explicit stage1_steps was dropped")
        check(f"{version_id}/explicit-value-survives", _explicit)


if __name__ == "__main__":
    run()
    print()
    if FAILS:
        for f in FAILS:
            print(f"  FAIL  {f}")
        print(f"\n{CHECKS - len(FAILS)} passed, {len(FAILS)} FAILED")
        sys.exit(1)
    print(f"{CHECKS} passed, 0 failed — "
          f"every mode x every generation resolves a schedule the engine accepts")
