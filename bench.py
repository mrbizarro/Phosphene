"""Phosphene Bench — a fixed, versioned benchmark of local AI video on a Mac.

One button (Settings -> Phosphene Bench) renders the suite below through the
panel's OWN queue — the same make_job() path, the same worker, the same timer
that every real render and the public /benchmarks numbers use — and turns the
times into a result card (PNG), a JSON record and a plain-text summary.

What makes a benchmark worth quoting is that nobody can tune it, so the rules
live here, in code, not in a doc:

* THE SUITE IS FROZEN. Prompts, seeds, sizes, lengths and step order are data
  in SUITE. `suite_fingerprint()` hashes that data and test_bench.py pins the
  hash to SUITE_FINGERPRINT, so a change that does not also bump SUITE_ID
  ("PB1" -> "PB2") fails the build. Results from two suites are never mixed.
* THE SAME SETTINGS FOR EVERY MAC. The only per-Mac difference is the one the
  app already makes on every render: below 48 GB the Balanced canvas is the
  Compact tier's 768x448 (LTX_QUALITIES applies the clamp). The record says
  which canvas ran, so comparisons stay like for like.
* NOTHING ELSE ON THE GPU. A run refuses to start while anything is queued,
  running, or holding the GPU gate, and it never queues behind other work.
* NEVER A SILENT DOWNLOAD. A step whose model is not on disk is reported
  `missing` with an install offer; the run skips it. Nothing is fetched unless
  the person clicks Install.
* NO BEST-OF. A run that misses a core step is `complete: false` and cannot be
  submitted. Submitting sends EVERY complete run of this suite stored on the
  Mac, and the leaderboard takes each Mac's median.

This module never imports mlx_ltx_panel: the panel hands it a `host` object
(panel/routes_bench.py) with the handful of operations it needs, so the unit
tests drive the runner with a fake host and no GPU.
"""
from __future__ import annotations

import copy
import ctypes
import ctypes.util
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import threading
import time
import uuid
from pathlib import Path

import hostinfo

SCHEMA = "phosphene-bench/1"
SUITE_ID = "PB1"
SUITE_NAME = "Phosphene Bench 1"
# sha256 of the canonical suite data, first 12 hex. test_bench.py recomputes
# it: editing SUITE without bumping SUITE_ID (and this pin) fails the build.
SUITE_FINGERPRINT = "170df0bdfe5a"

# Fixed for every Mac. 20261028: the date this suite was frozen for.
SEED = 20261028
# A real prompt with real sound, not a smoke-test string — the Bench renders
# a clip people will look at, and audio is half of what LTX-2.5 generates.
VIDEO_PROMPT = (
    "A lighthouse keeper climbs a spiral iron staircase at dusk, a lantern "
    "swinging in his hand, warm light sweeping across rough stone walls and "
    "a small round window full of stormy sea. Audio: boots ringing on iron "
    "steps, wind whistling through a cracked pane, distant waves breaking, "
    "no music, no speech."
)
IMAGE_PROMPT = (
    "A ceramic teapot on a sunlit wooden table beside an open window, a thin "
    "curl of steam rising from the spout, soft morning light, linen curtain "
    "moving in the breeze, shallow depth of field, natural colour photograph."
)

LABEL_PREFIX = "Phosphene Bench"

# The steps, in run order. `form` is exactly what make_job() receives — the
# same fields the panel's own Generate button posts. Every field that could
# change the work is pinned explicitly, never left to a default that a later
# release, a saved setting or a Load Params could move:
#   enhance off (no prompt rewrite), upscale off (no export pass), accel off,
#   schedule_preset "" (the tuned default), temporal_mode native, one seed.
_LTX_COMMON = {
    "engine": "ltx", "mode": "t2v", "prompt": VIDEO_PROMPT, "seed": str(SEED),
    "frame_rate": "24", "enhance": "off", "upscale": "off", "accel": "off",
    "schedule_preset": "", "temporal_mode": "native", "no_music": "off",
    "steps": "8", "negative_prompt": "",
}
SUITE: dict = {
    "id": SUITE_ID,
    "name": SUITE_NAME,
    "seed": SEED,
    "steps": [
        {
            # UNTIMED warm-up. Loads the video engine so the timed LTX steps
            # measure the Mac, not the disk or the first-use compile. The
            # public /benchmarks drop each install's first render on an
            # engine for the same reason. Its wall time is still recorded.
            "id": "warmup", "kind": "ltx", "core": False, "timed": False,
            "label": "Engine warm-up", "what": "1-second LTX-2.5 Quick clip, excluded from timed results",
            "quality": "quick", "seconds": 1,
            "form": {**_LTX_COMMON, "quality": "quick", "frames": "25"},
        },
        {
            "id": "ltx_quick_5s", "kind": "ltx", "core": True, "timed": True,
            "label": "LTX-2.5 Quick · 5 s", "what": "5-second video with sound, 640×448, 121 frames at 24 fps",
            "quality": "quick", "seconds": 5,
            "form": {**_LTX_COMMON, "quality": "quick", "frames": "121"},
        },
        {
            "id": "ltx_balanced_5s", "kind": "ltx", "core": True, "timed": True,
            "headline": True,
            "label": "LTX-2.5 Balanced · 5 s", "what": "5-second video with sound, 1024×576, 121 frames at 24 fps",
            "quality": "balanced", "seconds": 5,
            "form": {**_LTX_COMMON, "quality": "balanced", "frames": "121"},
        },
        {
            "id": "image_klein", "kind": "image", "core": True, "timed": True,
            "label": "FLUX.2 [klein] 4B · 4 images", "what": "Four 1024×1024 images in one job, 4 steps, 4-bit",
            "images": 4,
            "form": {"mode": "image", "prompt": IMAGE_PROMPT, "seed": str(SEED),
                     "engine_override": "flux2_klein_inline", "aspect": "1:1",
                     "n": "4", "refs": "[]", "loras": "[]"},
        },
        {
            # OPTIONAL. Hailuo H3 is a 75 GB install whose licence excludes
            # the EU, UK, South Korea and the US — the Bench never offers to
            # download it and never puts it in the headline. It runs only
            # where it is already installed, capable and accepted. Best
            # sampler pinned (h3_tristep=0, no Turbo, Auto steps) so the
            # Fast adapter being installed or not cannot change the work.
            "id": "h3_draft_5s", "kind": "h3", "core": False, "timed": True,
            "label": "Hailuo H3 Draft · 5 s", "what": "Optional 5-second video, only with H3 already installed",
            "seconds": 5,
            "form": {"engine": "h3", "mode": "t2v", "prompt": VIDEO_PROMPT,
                     "seed": str(SEED), "h3_quality": "draft", "h3_length": "5s",
                     "h3_tristep": "0", "h3_turbo": "", "h3_steps": "",
                     "h3_upscale": "off", "h3_orientation": "landscape",
                     "enhance": "off", "negative_prompt": ""},
        },
    ],
}

STEP_IDS = tuple(s["id"] for s in SUITE["steps"])
CORE_STEP_IDS = tuple(s["id"] for s in SUITE["steps"] if s["core"])
HEADLINE_STEP = next(s["id"] for s in SUITE["steps"] if s.get("headline"))
# What the image step needs on disk (the HF cache) and how big it is.
KLEIN_REPO = "Runpod/FLUX.2-klein-4B-mflux-4bit"
KLEIN_GB = 4.3
# The snapshot folders mflux's FLUX.2 loader reads (checked before a run).
KLEIN_COMPONENTS = ("transformer", "vae", "text_encoder", "tokenizer")

STEP_STATUSES = ("ready", "missing", "optional_skip", "queued", "running",
                 "done", "failed", "skipped", "cancelled")
THERMAL_STATES = ("nominal", "fair", "serious", "critical", "unknown")


# ---- the suite fingerprint -------------------------------------------------

def suite_fingerprint(suite: dict | None = None) -> str:
    """sha256 over the canonical JSON of everything that defines the work.

    Labels and blurbs are copy, not work, so they are left out: rewording a
    step must not invalidate every result ever recorded."""
    s = suite or SUITE
    core = {
        "id": s["id"], "seed": s["seed"],
        "steps": [{k: v for k, v in st.items() if k not in ("label", "what")}
                  for st in s["steps"]],
    }
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def step_def(step_id: str) -> dict:
    for s in SUITE["steps"]:
        if s["id"] == step_id:
            return s
    raise KeyError(step_id)


def job_form(step_id: str) -> dict:
    """The make_job form for a step: a fresh copy (callers may not mutate the
    suite), with the queue label added. Values are strings, as a browser
    form would send them."""
    st = step_def(step_id)
    form = dict(st["form"])
    form["preset_label"] = f"{LABEL_PREFIX} · {st['label']}"
    return form


# ---- the plan: what WILL run on this Mac ------------------------------------

def plan(readiness: dict, canvases: dict | None = None) -> list[dict]:
    """Ordered steps with a status each, from a readiness snapshot.

    `readiness` (built by the host from the panel's own probes):
      {"ltx": {"ok": bool, "generation": "ltx25", "missing_keys": [...]},
       "image": {"ok": bool, "gb": 4.3},
       "h3": {"installed": bool, "capable": bool, "licence_ok": bool}}
    `canvases`: {"quick": "640×448", "balanced": "768×448", ...} — the canvas
      THIS Mac renders each quality at (already tier-clamped by the panel).

    Pure: the same readiness gives the same plan, byte for byte.
    """
    canvases = canvases or {}
    ltx = readiness.get("ltx") or {}
    img = readiness.get("image") or {}
    h3 = readiness.get("h3") or {}
    out: list[dict] = []
    for st in SUITE["steps"]:
        row = {"id": st["id"], "label": st["label"], "what": st["what"],
               "kind": st["kind"], "core": st["core"], "timed": st["timed"],
               "headline": bool(st.get("headline")), "status": "ready",
               "reason": "", "install": None}
        if st["kind"] == "ltx":
            canvas = canvases.get(st["quality"])
            if canvas:
                row["canvas"] = canvas
                row["what"] = re.sub(r"\d+×\d+", canvas, st["what"])
            if ltx.get("generation") and ltx["generation"] != "ltx25":
                row["status"], row["reason"] = "missing", "wrong_generation"
            elif not ltx.get("ok"):
                row["status"], row["reason"] = "missing", "ltx_missing"
                row["install"] = {"kind": "models",
                                  "keys": list(ltx.get("missing_keys") or [])}
        elif st["kind"] == "image":
            row["canvas"] = "1024×1024"
            if not img.get("ok"):
                row["status"], row["reason"] = "missing", "image_missing"
                row["install"] = {"kind": "image", "repo": KLEIN_REPO,
                                  "gb": float(img.get("gb") or KLEIN_GB)}
        elif st["kind"] == "h3":
            row["canvas"] = "640×384"
            if not h3.get("capable"):
                row["status"], row["reason"] = "optional_skip", "h3_not_capable"
            elif not h3.get("installed"):
                row["status"], row["reason"] = "optional_skip", "h3_not_installed"
            elif not h3.get("licence_ok"):
                row["status"], row["reason"] = "optional_skip", "h3_licence"
        out.append(row)
    return out


def plan_can_run(rows: list[dict]) -> tuple[bool, str]:
    """Every core step must be ready (a run that cannot be complete is not
    started — it would only produce a result nobody may compare)."""
    for r in rows:
        if r["core"] and r["status"] != "ready":
            return False, r["reason"] or "missing"
    warm = next((r for r in rows if r["id"] == "warmup"), None)
    if warm and warm["status"] != "ready":
        return False, warm["reason"] or "missing"
    return True, ""


# ---- the Mac -----------------------------------------------------------------

_THERMAL_NAMES = {0: "nominal", 1: "fair", 2: "serious", 3: "critical"}


def _objc_process_info():
    """NSProcessInfo.processInfo through the ObjC runtime, or None.

    ctypes, not pyobjc: the engine venv does not carry pyobjc, and this is
    two message sends. Off macOS (or on any failure) every caller degrades to
    "unknown" — a benchmark must never fail because a sensor is missing."""
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        cls = objc.objc_getClass(b"NSProcessInfo")
        if not cls:
            return None
        return objc, send(cls, objc.sel_registerName(b"processInfo"))
    except Exception:                                       # noqa: BLE001
        return None


_PI_CACHE: list = []


def thermal_state() -> str:
    """macOS's own thermal pressure verdict: nominal/fair/serious/critical."""
    if not hostinfo.IS_MAC:
        return "unknown"
    try:
        if not _PI_CACHE:
            got = _objc_process_info()
            if got is None:
                return "unknown"
            _PI_CACHE.append(got)
        objc, pi = _PI_CACHE[0]
        sendl = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        return _THERMAL_NAMES.get(int(sendl(pi, objc.sel_registerName(b"thermalState"))), "unknown")
    except Exception:                                       # noqa: BLE001
        return "unknown"


def low_power_mode() -> bool | None:
    if not hostinfo.IS_MAC:
        return None
    try:
        out = subprocess.run(["pmset", "-g"], capture_output=True, text=True,
                             errors="replace", timeout=3).stdout
        m = re.search(r"^\s*lowpowermode\s+(\d)", out, re.M)
        return bool(int(m.group(1))) if m else False
    except Exception:                                       # noqa: BLE001
        return None


def power_source() -> str:
    """"ac", "battery" or "unknown". A desktop Mac reports AC."""
    if not hostinfo.IS_MAC:
        return "unknown"
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True,
                             errors="replace", timeout=3).stdout
    except Exception:                                       # noqa: BLE001
        return "unknown"
    if "AC Power" in out:
        return "ac"
    if "Battery Power" in out:
        return "battery"
    return "unknown"


def _sysctl_int(name: str) -> int:
    try:
        v = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True,
                           errors="replace", timeout=3).stdout.strip()
        return int(v) if v.isdigit() else 0
    except Exception:                                       # noqa: BLE001
        return 0


def gpu_cores() -> int:
    if not hostinfo.IS_MAC:
        return 0
    try:
        out = subprocess.run(["system_profiler", "SPDisplaysDataType", "-json"],
                             capture_output=True, text=True, errors="replace",
                             timeout=20).stdout
        for gpu in json.loads(out or "{}").get("SPDisplaysDataType") or []:
            n = str(gpu.get("sppci_cores") or "")
            if n.isdigit():
                return int(n)
    except Exception:                                       # noqa: BLE001
        pass
    return 0


_CHIP_RE = re.compile(r"\bM(\d+)\s*(Pro|Max|Ultra)?\b", re.I)


def chip_family(brand: str) -> str:
    m = _CHIP_RE.search(brand or "")
    if not m:
        return "unknown"
    return f"M{m.group(1)}" + (f" {m.group(2).title()}" if m.group(2) else "")


def mac_info(tier: str = "") -> dict:
    """Hardware CLASS facts — every M4 Max 64 GB Mac16,9 reports the same.
    No serial, no hostname, no user name."""
    brand = hostinfo.chip_brand() or ""
    ram = hostinfo.total_ram_bytes()
    macos = platform.mac_ver()[0] if hostinfo.IS_MAC else ""
    build = ""
    if hostinfo.IS_MAC:
        try:
            build = subprocess.run(["sw_vers", "-buildVersion"], capture_output=True,
                                   text=True, errors="replace", timeout=3).stdout.strip()
        except Exception:                                   # noqa: BLE001
            build = ""
    return {
        "chip": brand or "unknown",
        "chip_family": chip_family(brand),
        "cpu_cores": {"performance": _sysctl_int("hw.perflevel0.physicalcpu"),
                      "efficiency": _sysctl_int("hw.perflevel1.physicalcpu")},
        "gpu_cores": gpu_cores(),
        "ram_gb": int(round(ram / 2**30)) if ram else 0,
        "model_id": hostinfo.hw_model() or "unknown",
        "macos": macos or "unknown",
        "macos_build": build or "unknown",
        "tier": tier or "unknown",
    }


# ---- memory + thermal sampling ------------------------------------------------

class Sampler:
    """Samples the Mac's memory in use (active + wired + compressed, the same
    reading hostinfo gives the rest of the panel) and swap, twice a second, and
    the thermal state every few seconds, while a step runs.

    Whole-Mac numbers on purpose: an MLX render lives in unified memory shared
    with the GPU, and the honest question a reviewer asks is "how much of this
    Mac did it take", which the pre-step baseline answers as `added_gb`."""

    def __init__(self, interval: float = 0.5, reader=None, thermal=None):
        self.interval = interval
        self._read = reader or hostinfo.memory_usage
        self._thermal = thermal or thermal_state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.baseline_used = 0
        self.peak_used = 0
        self.peak_swap = 0
        self.thermal_worst = "unknown"
        self.samples = 0

    def _sample(self) -> None:
        try:
            mem = self._read() or {}
        except Exception:                                   # noqa: BLE001
            return
        used, swap = int(mem.get("used") or 0), int(mem.get("swap_used") or 0)
        self.peak_used = max(self.peak_used, used)
        self.peak_swap = max(self.peak_swap, swap)
        self.samples += 1

    def _thermal_sample(self) -> None:
        self.thermal_worst = worse_thermal(self.thermal_worst, self._thermal())

    def start(self) -> None:
        try:
            mem = self._read() or {}
            self.baseline_used = int(mem.get("used") or 0)
        except Exception:                                   # noqa: BLE001
            self.baseline_used = 0
        self._sample()
        self._thermal_sample()

        def loop():
            n = 0
            while not self._stop.wait(self.interval):
                self._sample()
                n += 1
                if n % 6 == 0:
                    self._thermal_sample()
        self._thread = threading.Thread(target=loop, name="bench-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._sample()
        self._thermal_sample()
        gib = 2**30
        return {
            "peak_mem_gb": round(self.peak_used / gib, 1) if self.peak_used else None,
            "added_mem_gb": (round(max(0, self.peak_used - self.baseline_used) / gib, 1)
                             if self.peak_used else None),
            "swap_peak_gb": round(self.peak_swap / gib, 1),
            "thermal_worst": self.thermal_worst,
        }


def worse_thermal(a: str, b: str) -> str:
    order = {"unknown": -1, "nominal": 0, "fair": 1, "serious": 2, "critical": 3}
    return a if order.get(a, -1) >= order.get(b, -1) else b


_MLX_PEAK_RE = re.compile(r"\[mlx\] run peak ([\d.]+) GiB")


def new_log_lines(before: list, after: list) -> list:
    """The log lines added since `before` was taken. The panel log is capped
    (LOG_LIMIT), so an index would drift once it rotates; anchor on the last
    line we had instead."""
    if not before:
        return list(after)
    last = before[-1]
    for i in range(len(after) - 1, -1, -1):
        if after[i] == last:
            return list(after[i + 1:])
    return list(after)


def engine_peak_from_log(lines: list[str]) -> float | None:
    """The helper prints "[mlx] run peak N GiB" at the end of an LTX render;
    the last one in this step's slice of the log is the step's own figure."""
    peak = None
    for ln in lines or ():
        m = _MLX_PEAK_RE.search(str(ln))
        if m:
            peak = round(float(m.group(1)), 1)
    return peak


# ---- the result document ------------------------------------------------------

def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def summarize(steps: list[dict]) -> dict:
    by_id = {s["id"]: s for s in steps}
    complete = all((by_id.get(i) or {}).get("status") == "done" for i in CORE_STEP_IDS)
    core_total = (round(sum(by_id[i]["wall_sec"] for i in CORE_STEP_IDS), 1)
                  if complete else None)
    head = by_id.get(HEADLINE_STEP) or {}
    return {
        "complete": complete,
        "core_total_sec": core_total,
        "headline_step": HEADLINE_STEP,
        "headline_sec": head.get("wall_sec") if head.get("status") == "done" else None,
    }


def warnings_for(conditions: dict, steps: list[dict]) -> list[str]:
    w = []
    if conditions.get("power") == "battery":
        w.append("battery")
    if conditions.get("low_power_mode"):
        w.append("low_power_mode")
    if conditions.get("output_preset_default") is False:
        w.append("output_codec")
    if conditions.get("thermal_worst") in ("serious", "critical"):
        w.append("thermal_" + conditions["thermal_worst"])
    # Swap GROWTH, not swap: a Mac that already had swap in use before the
    # run (macOS keeps it for hours) did not page because of the Bench.
    base = float(conditions.get("swap_start_gb") or 0.0)
    if any((s.get("swap_peak_gb") or 0) - base >= 1.0 for s in steps if s.get("status") == "done"):
        w.append("swap")
    return w


def eligible(doc: dict) -> bool:
    """May this run go on the leaderboard? Complete, this suite, and not in
    Low Power Mode (which caps the GPU by design — a different machine)."""
    return bool(doc.get("suite", {}).get("id") == SUITE_ID
                and doc.get("suite", {}).get("fingerprint") == SUITE_FINGERPRINT
                and doc.get("summary", {}).get("complete")
                and not doc.get("conditions", {}).get("low_power_mode")
                # The encode settings live in Settings, not in the suite: a
                # non-default output codec changes the encode time, so it is
                # recorded and keeps the run off the board.
                and doc.get("conditions", {}).get("output_preset_default") is not False)


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def build_result(*, run_id: str, started: float, finished: float, app: dict, mac: dict,
                 conditions: dict, steps: list[dict]) -> dict:
    doc = {
        "schema": SCHEMA,
        "suite": {"id": SUITE_ID, "name": SUITE_NAME, "fingerprint": SUITE_FINGERPRINT},
        "run_id": run_id,
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "app": app,
        "mac": mac,
        "conditions": conditions,
        "steps": steps,
        "summary": summarize(steps),
    }
    doc["warnings"] = warnings_for(conditions, steps)
    doc["summary"]["eligible"] = eligible(doc)
    return doc


# ---- JSON Schema ----------------------------------------------------------------
# Published as docs/bench.schema.json (test_bench.py keeps the two identical)
# and enforced on every document this module writes by validate().

_NUM_OR_NULL = {"type": ["number", "null"], "minimum": 0}
_STEP_SCHEMA = {
    "type": "object",
    "required": ["id", "label", "status", "core", "timed", "wall_sec"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "enum": list(STEP_IDS)},
        "label": {"type": "string"},
        "kind": {"type": "string", "enum": ["ltx", "image", "h3"]},
        "core": {"type": "boolean"},
        "timed": {"type": "boolean"},
        "status": {"type": "string", "enum": ["done", "failed", "skipped", "cancelled"]},
        "reason": {"type": "string"},
        "canvas": {"type": ["string", "null"], "pattern": r"^\d+×\d+$"},
        "frames": {"type": ["integer", "null"], "minimum": 0},
        "images": {"type": ["integer", "null"], "minimum": 0},
        "wall_sec": _NUM_OR_NULL,
        "peak_mem_gb": _NUM_OR_NULL,
        "added_mem_gb": _NUM_OR_NULL,
        "swap_peak_gb": _NUM_OR_NULL,
        "engine_peak_gb": _NUM_OR_NULL,
        "thermal_worst": {"type": "string", "enum": list(THERMAL_STATES)},
    },
}
RESULT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://phosphene.video/bench/schema/phosphene-bench-1.json",
    "title": "Phosphene Bench result",
    "type": "object",
    "required": ["schema", "suite", "run_id", "started_at", "finished_at", "app",
                 "mac", "conditions", "steps", "summary", "warnings"],
    "additionalProperties": False,
    "properties": {
        "schema": {"type": "string", "enum": [SCHEMA]},
        "suite": {
            "type": "object", "required": ["id", "name", "fingerprint"],
            "additionalProperties": False,
            "properties": {"id": {"type": "string", "pattern": r"^PB\d+$"},
                           "name": {"type": "string"},
                           "fingerprint": {"type": "string", "pattern": r"^[0-9a-f]{12}$"}},
        },
        "run_id": {"type": "string", "pattern": r"^[0-9a-f]{16}$"},
        "started_at": {"type": "string", "pattern": r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"},
        "finished_at": {"type": "string", "pattern": r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"},
        "app": {
            "type": "object", "required": ["version", "edition", "distribution"],
            "additionalProperties": False,
            "properties": {"version": {"type": "string"},
                           "edition": {"type": "string", "enum": ["free", "pro"]},
                           "distribution": {"type": "string", "enum": ["pinokio", "mac-app"]},
                           "generation": {"type": "string"}},
        },
        "mac": {
            "type": "object",
            "required": ["chip", "chip_family", "cpu_cores", "gpu_cores", "ram_gb",
                         "model_id", "macos", "macos_build", "tier"],
            "additionalProperties": False,
            "properties": {
                "chip": {"type": "string"}, "chip_family": {"type": "string"},
                "cpu_cores": {"type": "object", "required": ["performance", "efficiency"],
                              "additionalProperties": False,
                              "properties": {"performance": {"type": "integer", "minimum": 0},
                                             "efficiency": {"type": "integer", "minimum": 0}}},
                "gpu_cores": {"type": "integer", "minimum": 0},
                "ram_gb": {"type": "integer", "minimum": 0},
                "model_id": {"type": "string"}, "macos": {"type": "string"},
                "macos_build": {"type": "string"}, "tier": {"type": "string"},
            },
        },
        "conditions": {
            "type": "object",
            "required": ["power", "low_power_mode", "thermal_start", "thermal_worst"],
            "additionalProperties": False,
            "properties": {
                "power": {"type": "string", "enum": ["ac", "battery", "unknown"]},
                "low_power_mode": {"type": ["boolean", "null"]},
                "thermal_start": {"type": "string", "enum": list(THERMAL_STATES)},
                "thermal_worst": {"type": "string", "enum": list(THERMAL_STATES)},
                "mem_used_start_gb": _NUM_OR_NULL,
                "swap_start_gb": _NUM_OR_NULL,
                "warmup_sec": _NUM_OR_NULL,
                "output_preset": {"type": ["string", "null"]},
                "output_preset_default": {"type": ["boolean", "null"]},
            },
        },
        "steps": {"type": "array", "items": _STEP_SCHEMA},
        "summary": {
            "type": "object",
            "required": ["complete", "eligible", "core_total_sec", "headline_step", "headline_sec"],
            "additionalProperties": False,
            "properties": {"complete": {"type": "boolean"}, "eligible": {"type": "boolean"},
                           "core_total_sec": _NUM_OR_NULL,
                           "headline_step": {"type": "string", "enum": list(STEP_IDS)},
                           "headline_sec": _NUM_OR_NULL},
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


def _type_ok(val, t) -> bool:
    types = t if isinstance(t, list) else [t]
    for name in types:
        if name == "null" and val is None:
            return True
        if name == "boolean" and isinstance(val, bool):
            return True
        if name == "integer" and isinstance(val, int) and not isinstance(val, bool):
            return True
        if name == "number" and isinstance(val, (int, float)) and not isinstance(val, bool):
            return True
        if name == "string" and isinstance(val, str):
            return True
        if name == "object" and isinstance(val, dict):
            return True
        if name == "array" and isinstance(val, list):
            return True
    return False


def validate(doc, schema: dict | None = None, path: str = "$") -> list[str]:
    """A small JSON Schema validator for exactly the keywords RESULT_SCHEMA
    uses (type, required, properties, additionalProperties, items, enum,
    pattern, minimum). The engine venv has no jsonschema; the schema file is
    still standard draft 2020-12, so anyone can check a result with any
    validator. Returns a list of errors; empty means valid."""
    s = RESULT_SCHEMA if schema is None else schema
    errs: list[str] = []
    if "type" in s and not _type_ok(doc, s["type"]):
        return [f"{path}: expected {s['type']}, got {type(doc).__name__}"]
    if "enum" in s and doc not in s["enum"]:
        errs.append(f"{path}: {doc!r} not one of {s['enum']}")
    if isinstance(doc, str) and "pattern" in s and not re.search(s["pattern"], doc):
        errs.append(f"{path}: {doc!r} does not match {s['pattern']}")
    if (isinstance(doc, (int, float)) and not isinstance(doc, bool)
            and "minimum" in s and doc < s["minimum"]):
        errs.append(f"{path}: {doc} < {s['minimum']}")
    if isinstance(doc, dict):
        for k in s.get("required", ()):
            if k not in doc:
                errs.append(f"{path}: missing {k!r}")
        props = s.get("properties", {})
        for k, v in doc.items():
            if k in props:
                errs.extend(validate(v, props[k], f"{path}.{k}"))
            elif s.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected {k!r}")
    if isinstance(doc, list) and "items" in s:
        for i, v in enumerate(doc):
            errs.extend(validate(v, s["items"], f"{path}[{i}]"))
    return errs


# ---- formatting --------------------------------------------------------------

def fmt_sec(sec) -> str:
    """48 s · 2m 19s · 1h 02m."""
    if sec is None:
        return "—"
    s = int(round(float(sec)))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


_THERMAL_WORDS = {"nominal": "nominal", "fair": "fair", "serious": "serious",
                  "critical": "critical", "unknown": "not reported"}


def summary_text(doc: dict) -> str:
    """The "Copy results" block: plain text a reviewer can paste anywhere."""
    mac, app, cond = doc["mac"], doc["app"], doc["conditions"]
    cores = mac.get("cpu_cores") or {}
    cpu = (cores.get("performance") or 0) + (cores.get("efficiency") or 0)
    lines = [
        f"{COPY['copy_results_header']} · suite {doc['suite']['fingerprint']}",
        f"{mac['chip']} · {mac['gpu_cores']}-core GPU · {cpu}-core CPU · {mac['ram_gb']} GB · "
        f"{mac['model_id']} · macOS {mac['macos']} ({mac['macos_build']})",
        f"Phosphene {app['version']} ({app['edition']}, {app['distribution']})",
        "",
    ]
    for st in doc["steps"]:
        if not st.get("timed"):
            continue
        t = fmt_sec(st.get("wall_sec")) if st["status"] == "done" else st["status"]
        mem = (f" · peak {st['peak_mem_gb']} GB in use (+{st['added_mem_gb']} GB)"
               if st.get("peak_mem_gb") is not None and st["status"] == "done" else "")
        canvas = f" · {st['canvas']}" if st.get("canvas") else ""
        lines.append(f"{st['label']}{canvas}: {t}{mem}")
    lines.append("")
    warm = cond.get("warmup_sec")
    lines.append(
        f"Engine warm-up {fmt_sec(warm)} (untimed) · power {cond['power']} · Low Power Mode "
        f"{'on' if cond.get('low_power_mode') else 'off'} · thermal {_THERMAL_WORDS.get(cond['thermal_start'])}"
        f" -> {_THERMAL_WORDS.get(cond['thermal_worst'])}")
    if not doc["summary"]["complete"]:
        lines.append(COPY["result_incomplete"])
    lines.append(f"Run {doc['run_id']} · {doc['finished_at']} · phosphene.video/bench")
    return "\n".join(lines)


# ---- analytics (the opt-in leaderboard submission) -----------------------------

def analytics_props(doc: dict, run_index: int = 1, runs_total: int = 1) -> dict:
    """The flat property dict for ONE `bench_result` event (docs/ANALYTICS.md).

    Hardware CLASS and timings only: no prompt (the suite's are fixed anyway),
    no path, no filename, no seed. Exact seconds are sent — unlike the
    bucketed render_completed — because publishing them is the point of an
    explicit, per-click opt-in."""
    mac, app, cond = doc["mac"], doc["app"], doc["conditions"]
    p = {
        "schema": doc["schema"],
        "suite_id": doc["suite"]["id"],
        "suite_fp": doc["suite"]["fingerprint"],
        "run_id": doc["run_id"],
        "run_index": int(run_index),
        "runs_total": int(runs_total),
        "version": app["version"],
        "edition": app["edition"],
        "distribution": app["distribution"],
        "chip_family": mac["chip_family"],
        "gpu_cores": mac["gpu_cores"],
        "cpu_p": (mac.get("cpu_cores") or {}).get("performance", 0),
        "cpu_e": (mac.get("cpu_cores") or {}).get("efficiency", 0),
        "ram_gb": mac["ram_gb"],
        "model_id": mac["model_id"],
        "os_version": ".".join(str(mac["macos"]).split(".")[:2]),
        "power": cond["power"],
        "low_power": bool(cond.get("low_power_mode")),
        "thermal_worst": cond["thermal_worst"],
        "warmup_sec": cond.get("warmup_sec"),
        "complete": bool(doc["summary"]["complete"]),
        "eligible": bool(doc["summary"]["eligible"]),
    }
    for st in doc["steps"]:
        if st["id"] == "warmup":
            continue
        k = st["id"]
        p[f"st_{k}"] = st["status"]
        p[f"s_{k}"] = st.get("wall_sec") if st["status"] == "done" else None
        p[f"mem_{k}"] = st.get("peak_mem_gb") if st["status"] == "done" else None
        p[f"add_{k}"] = st.get("added_mem_gb") if st["status"] == "done" else None
        p[f"cv_{k}"] = st.get("canvas")
    return p


def runs_to_submit(docs: list[dict]) -> list[dict]:
    """Every complete, eligible run of THIS suite, oldest first — not the one
    on screen. That is the no-best-of rule: the board takes each Mac's
    median, so sending all of them is what makes the median honest."""
    ok = [d for d in docs if eligible(d)]
    return sorted(ok, key=lambda d: d["started_at"])


def install_median(docs: list[dict], step_id: str) -> float | None:
    """What the leaderboard does per Mac (mirrored in the site's builder)."""
    vals = [s["wall_sec"] for d in docs for s in d["steps"]
            if s["id"] == step_id and s["status"] == "done" and s.get("wall_sec") is not None]
    return round(statistics.median(vals), 1) if vals else None


# ---- storage -------------------------------------------------------------------

class Store:
    """state/bench/<run_id>.json + <run_id>.png, and submitted.json (run ids
    already sent, so a second Submit sends only what is new)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, run_id: str, ext: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{16}", run_id or ""):
            raise ValueError("bad run id")
        return self.root / f"{run_id}.{ext}"

    def save(self, doc: dict) -> Path:
        errs = validate(doc)
        if errs:
            raise ValueError("bench result does not match its schema: " + "; ".join(errs[:5]))
        self._ensure()
        p = self.path(doc["run_id"], "json")
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, p)
        return p

    def load(self, run_id: str) -> dict | None:
        try:
            return json.loads(self.path(run_id, "json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def runs(self) -> list[dict]:
        out = []
        if not self.root.is_dir():
            return out
        for p in sorted(self.root.glob("*.json")):
            if not re.fullmatch(r"[0-9a-f]{16}\.json", p.name):
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and d.get("schema") == SCHEMA:
                out.append(d)
        return sorted(out, key=lambda d: d.get("started_at", ""), reverse=True)

    def submitted(self) -> set[str]:
        try:
            return set(json.loads((self.root / "submitted.json").read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return set()

    def mark_submitted(self, ids) -> None:
        self._ensure()
        cur = self.submitted() | set(ids)
        (self.root / "submitted.json").write_text(json.dumps(sorted(cur)) + "\n", encoding="utf-8")


# ---- the runner ----------------------------------------------------------------

class BenchBusy(RuntimeError):
    """The Mac is doing something else (queue, render, GPU gate, a run)."""


class Runner:
    """Runs the suite through the host's queue, one step at a time.

    host must provide:
      readiness() -> dict            (see plan())
      canvases() -> dict             quality -> "W×H" on this Mac
      busy_reason() -> str           "" when nothing is queued/running/holding the GPU
      make_job(form) -> job dict     the panel's make_job
      refusal(job) -> str            "" or why the panel would refuse this job
      enqueue(job)
      job(job_id) -> dict | None     the live job (queue, current or history)
      cancel(job_id)
      cold_start()                   drop the warm engine so every run starts the same
      log_lines() -> list[str]       the panel log, oldest first
      app_info() -> dict             {version, edition, distribution, generation}
      render_settings() -> dict      Settings that change the time but are not suite data
                                     ({output_preset, output_preset_default})
      tier() -> str
      store -> Store
      render_card(doc, path)         write the PNG
    """

    TERMINAL = ("done", "failed", "cancelled", "stopped", "error")
    # A step's job that is in neither the queue, `current` nor history for
    # this long was removed by hand (or trimmed); the run stops waiting.
    vanish_after = 60.0

    def __init__(self, host, poll: float = 1.0, sampler_factory=None, clock=time.time):
        self.host = host
        self.poll = poll
        self._sampler_factory = sampler_factory or (lambda: Sampler())
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.state: dict = {"running": False, "run_id": None, "steps": [], "step_index": None,
                            "step_started": None, "job_id": None, "error": "", "last_run": None,
                            "stopped": False}

    # -- public -------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            st = copy.deepcopy(self.state)
        if st.get("running") and st.get("step_started"):
            st["step_elapsed"] = round(self._clock() - st["step_started"], 1)
        return st

    def start(self, wait: bool = False) -> str:
        with self._lock:
            if self.state["running"]:
                raise BenchBusy("bench_running")
            busy = self.host.busy_reason()
            if busy:
                raise BenchBusy(busy)
            rows = plan(self.host.readiness(), self.host.canvases())
            ok, why = plan_can_run(rows)
            if not ok:
                raise BenchBusy(why)
            run_id = new_run_id()
            self._cancel.clear()
            self.state.update(running=True, run_id=run_id, step_index=None, job_id=None,
                              error="", stopped=False, step_started=None,
                              steps=[{"id": r["id"], "label": r["label"], "status":
                                      ("queued" if r["status"] == "ready" else r["status"]),
                                      "reason": r["reason"], "wall_sec": None} for r in rows])
        self._thread = threading.Thread(target=self._run, args=(run_id, rows),
                                        name="phosphene-bench", daemon=True)
        self._thread.start()
        if wait:
            self._thread.join()
        return run_id

    def cancel(self) -> bool:
        with self._lock:
            if not self.state["running"]:
                return False
            job_id = self.state.get("job_id")
        self._cancel.set()
        if job_id:
            try:
                self.host.cancel(job_id)
            except Exception:                               # noqa: BLE001
                pass
        return True

    # -- the thread -----------------------------------------------------------
    def _set_step(self, idx: int, **kw) -> None:
        with self._lock:
            self.state["steps"][idx].update(kw)

    def _run(self, run_id: str, rows: list[dict]) -> None:
        started = self._clock()
        results: list[dict] = []
        warmup_sec = None
        try:
            app = self.host.app_info()
            mac = mac_info(self.host.tier())
            mem0 = hostinfo.memory_usage()
            conditions = {
                "power": power_source(),
                "low_power_mode": low_power_mode(),
                "thermal_start": thermal_state(),
                "thermal_worst": "unknown",
                "mem_used_start_gb": round((mem0.get("used") or 0) / 2**30, 1),
                "swap_start_gb": round((mem0.get("swap_used") or 0) / 2**30, 1),
            }
            conditions["thermal_worst"] = conditions["thermal_start"]
            try:
                conditions.update(self.host.render_settings() or {})
            except Exception:                               # noqa: BLE001
                pass
            try:
                self.host.cold_start()
            except Exception:                               # noqa: BLE001
                pass
            for idx, row in enumerate(rows):
                st = step_def(row["id"])
                rec = {"id": row["id"], "label": row["label"], "kind": st["kind"],
                       "core": st["core"], "timed": st["timed"], "status": "skipped",
                       "reason": row["reason"], "canvas": row.get("canvas"),
                       "frames": None, "images": st.get("images"), "wall_sec": None,
                       "peak_mem_gb": None, "added_mem_gb": None, "swap_peak_gb": None,
                       "engine_peak_gb": None, "thermal_worst": "unknown"}
                if self._cancel.is_set():
                    rec["status"], rec["reason"] = "cancelled", "stopped"
                    self._set_step(idx, status="cancelled")
                    results.append(rec)
                    continue
                if row["status"] != "ready":
                    self._set_step(idx, status="skipped")
                    results.append(rec)
                    continue
                self._run_step(idx, row, rec)
                if rec["thermal_worst"] != "unknown":
                    conditions["thermal_worst"] = worse_thermal(conditions["thermal_worst"],
                                                                rec["thermal_worst"])
                if row["id"] == "warmup":
                    warmup_sec = rec["wall_sec"]
                    if rec["status"] != "done":
                        # Without a working engine the timed steps would fail
                        # the same way; stop here and say why.
                        if rec["status"] == "failed":
                            with self._lock:
                                self.state["error"] = f"Engine warm-up failed: {rec['reason']}"
                        self._cancel.set()
                results.append(rec)
            conditions["warmup_sec"] = warmup_sec
            # The warm-up is recorded in conditions, never as a result row a
            # reader could mistake for a measurement.
            steps_out = [r for r in results if r["id"] != "warmup"]
            doc = build_result(run_id=run_id, started=started, finished=self._clock(),
                               app=app, mac=mac, conditions=conditions, steps=steps_out)
            self.host.store.save(doc)
            try:
                self.host.render_card(doc, self.host.store.path(run_id, "png"))
            except Exception as exc:                        # noqa: BLE001
                with self._lock:
                    self.state["error"] = f"card: {exc}"
            with self._lock:
                self.state["last_run"] = run_id
                self.state["stopped"] = self._cancel.is_set()
        except Exception as exc:                            # noqa: BLE001
            with self._lock:
                self.state["error"] = str(exc)
        finally:
            with self._lock:
                self.state.update(running=False, step_index=None, job_id=None, step_started=None)

    def _wait_idle(self, timeout: float = 600.0) -> bool:
        """Between steps the worker is finishing its bookkeeping (history,
        persist); wait for the panel to be idle again before queueing, so a
        step never starts behind anything."""
        end = self._clock() + timeout
        while self._clock() < end:
            if self._cancel.is_set():
                return False
            if not self.host.busy_reason():
                return True
            time.sleep(self.poll)
        return False

    def _run_step(self, idx: int, row: dict, rec: dict) -> None:
        if not self._wait_idle():
            rec["status"] = "cancelled" if self._cancel.is_set() else "failed"
            rec["reason"] = "stopped" if self._cancel.is_set() else "busy"
            self._set_step(idx, status=rec["status"], reason=rec["reason"])
            return
        try:
            job = self.host.make_job(job_form(row["id"]))
        except Exception as exc:                            # noqa: BLE001
            rec["status"], rec["reason"] = "failed", f"could not build the job: {exc}"
            self._set_step(idx, status="failed", reason=rec["reason"])
            return
        refusal = self.host.refusal(job)
        if refusal:
            rec["status"], rec["reason"] = "failed", refusal
            self._set_step(idx, status="failed", reason=refusal)
            return
        params = job.get("params") or {}
        if row.get("kind") == "ltx" or rec["kind"] in ("ltx", "h3"):
            w, h = params.get("width"), params.get("height")
            if w and h:
                rec["canvas"] = f"{int(w)}×{int(h)}"
            if params.get("frames"):
                rec["frames"] = int(params["frames"])
        log_before = self.host.log_lines()
        sampler = self._sampler_factory()
        sampler.start()
        final = None
        try:
            with self._lock:
                self.state.update(step_index=idx, job_id=job["id"], step_started=self._clock())
            self._set_step(idx, status="running")
            if self._cancel.is_set():
                # Stop pressed while the job was being built: never queue it.
                job["status"] = "cancelled"
            else:
                self.host.enqueue(job)
                if self._cancel.is_set():
                    # Stop pressed between publishing the id and queueing:
                    # cancel() may have looked before the job existed.
                    try:
                        self.host.cancel(job["id"])
                    except Exception:                       # noqa: BLE001
                        pass
            missing = 0
            while job.get("status") != "cancelled":
                j = self.host.job(job["id"])
                # TERMINAL means the worker has also stamped the timer: it sets
                # `done` before its finally block writes finished_at and
                # elapsed_sec, and reading in between would record 0 s.
                if (j is not None and j.get("status") in self.TERMINAL
                        and (j.get("finished_at") or self._cancel.is_set())):
                    final = j
                    break
                if j is None:
                    missing += 1
                    # Removed from the queue by hand, or trimmed out of a
                    # history we never saw it land in: stop waiting.
                    if self._cancel.is_set() or missing * self.poll >= self.vanish_after:
                        break
                else:
                    missing = 0
                time.sleep(self.poll)
        finally:
            mem = sampler.stop()
        rec.update({k: mem[k] for k in ("peak_mem_gb", "added_mem_gb", "swap_peak_gb", "thermal_worst")})
        rec["engine_peak_gb"] = engine_peak_from_log(new_log_lines(log_before, self.host.log_lines()))
        if final is not None:
            fp = final.get("params") or {}
            if rec["kind"] in ("ltx", "h3") and fp.get("width") and fp.get("height"):
                rec["canvas"] = f"{int(fp['width'])}×{int(fp['height'])}"
            if fp.get("frames"):
                rec["frames"] = int(fp["frames"])
        status = (final or {}).get("status")
        if status == "done" and (final.get("elapsed_sec") or 0) > 0:
            rec["status"] = "done"
            rec["wall_sec"] = round(float(final["elapsed_sec"]), 1)
            rec["reason"] = ""
        elif status == "done":
            rec["status"], rec["reason"] = "failed", "the render finished without a time"
        elif status in ("cancelled", "stopped") or self._cancel.is_set():
            rec["status"], rec["reason"] = "cancelled", "stopped"
        else:
            rec["status"] = "failed"
            rec["reason"] = str((final or {}).get("error") or (
                "the step left the queue before it finished" if final is None
                else "the render did not finish"))[:300]
        self._set_step(idx, status=rec["status"], wall_sec=rec["wall_sec"], reason=rec["reason"])


# ---- copy ----------------------------------------------------------------------
# Strings the SERVER renders (the card and the copied text). The browser's
# strings live in webapp/index.html and webapp/js/bench.js. Written by Codex
# (today-2026-10-22/bench/codex/), fact-checked against this module.
COPY = {
    "copy_results_header": "Phosphene Bench PB1 results",
    "result_incomplete": "Incomplete: a core step did not finish. This run cannot go on the leaderboard.",
    "card_headline_label": "LTX-2.5 Balanced · 5-second video with sound",
    "card_footer": "Phosphene Bench PB1 · free, local AI on your Mac · phosphene.video/bench",
}


# ---- the result card -------------------------------------------------------------

_FONT_DIRS = ("/System/Library/Fonts",)
_BG = (0, 6, 26)
_PANEL = (12, 19, 48)
_TEXT = (240, 244, 251)
_MUTED = (182, 191, 209)
_FAINT = (110, 120, 150)
_PINK, _PURPLE, _CYAN = (255, 46, 159), (177, 74, 255), (94, 234, 255)
_AMBER = (210, 153, 34)


def _font(size: int, weight: str = "Regular", mono: bool = False):
    from PIL import ImageFont                                  # noqa: PLC0415
    name = "SFNSMono.ttf" if mono else "SFNS.ttf"
    for d in _FONT_DIRS:
        p = Path(d) / name
        if p.is_file():
            try:
                f = ImageFont.truetype(str(p), size)
                try:
                    f.set_variation_by_name(weight)
                except Exception:                           # noqa: BLE001
                    pass
                return f
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                                        # Pillow < 10.1
        return ImageFont.load_default()


def _gradient(w: int, h: int):
    """Pink -> purple -> cyan, left to right: the mark's three rings."""
    from PIL import Image                                      # noqa: PLC0415
    g = Image.new("RGB", (w, 1))
    px = g.load()
    for x in range(w):
        t = x / max(1, w - 1)
        a, b, u = ((_PINK, _PURPLE, t / 0.5) if t < 0.5 else (_PURPLE, _CYAN, (t - 0.5) / 0.5))
        px[x, 0] = tuple(int(a[i] + (b[i] - a[i]) * u) for i in range(3))
    return g.resize((w, h))


def render_card(doc: dict, out_path, mark_path=None, scale: int = 2) -> Path:
    """The shareable PNG. 1600×900 points, drawn at 2× (3200×1800 px).

    Every number on it comes from the result document — the card cannot say
    anything the JSON does not."""
    from PIL import Image, ImageDraw, ImageFilter              # noqa: PLC0415

    W, H = 1600 * scale, 900 * scale
    S = scale

    def s(v):
        return int(round(v * S))

    img = Image.new("RGB", (W, H), _BG)
    # A soft blue wash from the top, like the panel's own body background.
    wash = Image.new("L", (W, H), 0)
    ImageDraw.Draw(wash).ellipse((s(-200), s(-700), s(1800), s(420)), fill=40)
    wash = wash.filter(ImageFilter.GaussianBlur(s(140)))
    img.paste(Image.new("RGB", (W, H), (47, 129, 247)), (0, 0), wash)

    mark = None
    mp = Path(mark_path) if mark_path else Path(__file__).resolve().parent / "assets" / "phosphene_mark_square.png"
    if mp.is_file():
        try:
            mark = Image.open(mp).convert("RGBA")
        except OSError:
            mark = None
    if mark is not None:
        # The mark, huge and faint, bleeding off the right edge.
        # Centred behind the step panel, which is opaque, so only the outer
        # rings show around it (the yellow core stays hidden).
        big = mark.resize((s(1180), s(1180)), Image.LANCZOS)
        lum = big.convert("L").point(lambda v: 255 if v > 24 else 0)
        alpha = lum.point(lambda a: int(a * 0.13))
        img.paste(big.convert("RGB"), (s(1188 - 590), s(505 - 590)), alpha)

    d = ImageDraw.Draw(img)
    mac, app, cond, summ = doc["mac"], doc["app"], doc["conditions"], doc["summary"]
    steps = {st["id"]: st for st in doc["steps"]}
    X0 = s(84)

    # Header: mark + wordmark + suite pill, version on the right.
    if mark is not None:
        small = mark.resize((s(56), s(56)), Image.LANCZOS)
        circle = Image.new("L", small.size, 0)
        ImageDraw.Draw(circle).ellipse((0, 0, small.size[0] - 1, small.size[1] - 1), fill=255)
        img.paste(small.convert("RGB"), (X0, s(64)), circle)
    f_brand = _font(s(30), "Semibold")
    d.text((X0 + s(72), s(76)), "Phosphene Bench", font=f_brand, fill=_TEXT)
    bw = d.textlength("Phosphene Bench", font=f_brand)
    pill_x = X0 + s(72) + bw + s(16)
    f_pill = _font(s(17), "Semibold", mono=True)
    pill_txt = f"{doc['suite']['id']} · {doc['suite']['fingerprint'][:6]}"
    pw = d.textlength(pill_txt, font=f_pill)
    d.rounded_rectangle((pill_x, s(78), pill_x + pw + s(24), s(110)), radius=s(16),
                        outline=(61, 71, 122), width=max(1, S), fill=(19, 27, 63))
    d.text((pill_x + s(12), s(84)), pill_txt, font=f_pill, fill=_CYAN)
    f_meta = _font(s(18), "Regular", mono=True)
    ver = f"Phosphene {app['version']} · {app['edition']}"
    d.text((W - X0 - d.textlength(ver, font=f_meta), s(84)), ver, font=f_meta, fill=_MUTED)

    # The Mac.
    f_chip = _font(s(64), "Bold")
    d.text((X0, s(164)), mac["chip"], font=f_chip, fill=_TEXT)
    cores = mac.get("cpu_cores") or {}
    cpu = (cores.get("performance") or 0) + (cores.get("efficiency") or 0)
    spec = " · ".join(x for x in (
        f"{mac['gpu_cores']}-core GPU" if mac.get("gpu_cores") else "",
        f"{cpu}-core CPU" if cpu else "",
        f"{mac['ram_gb']} GB" if mac.get("ram_gb") else "",
        f"macOS {mac['macos']}",
        mac.get("model_id") or "") if x)
    d.text((X0, s(248)), spec, font=_font(s(24), "Regular"), fill=_MUTED)

    # Headline: the Balanced 5 s time, in the mark's gradient.
    d.text((X0, s(330)), COPY["card_headline_label"], font=_font(s(20), "Semibold"),
           fill=_MUTED)
    head = steps.get(HEADLINE_STEP) or {}
    head_txt = fmt_sec(head.get("wall_sec")) if head.get("status") == "done" else "—"
    f_big = _font(s(150), "Bold")
    bbox = d.textbbox((0, 0), head_txt, font=f_big)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    mask = Image.new("L", (tw + s(8), th + s(8)), 0)
    ImageDraw.Draw(mask).text((-bbox[0], -bbox[1]), head_txt, font=f_big, fill=255)
    img.paste(_gradient(mask.size[0], mask.size[1]), (X0, s(372)), mask)
    canvas_note = head.get("canvas") or ""
    if canvas_note:
        d.text((X0, s(372) + th + s(28)), f"{canvas_note} · 121 frames · 24 fps",
               font=_font(s(20), "Regular", mono=True), fill=_MUTED)
    if summ.get("core_total_sec"):
        peaks = [st["peak_mem_gb"] for st in doc["steps"]
                 if st.get("core") and st["status"] == "done" and st.get("peak_mem_gb") is not None]
        tot = f"Core suite {fmt_sec(summ['core_total_sec'])}"
        if peaks:
            tot += f"  ·  peak {max(peaks)} GB in use"
        y_tot = s(372) + th + s(84)
        d.rounded_rectangle((X0, y_tot, X0 + d.textlength(tot, font=_font(s(22), "Semibold")) + s(40),
                             y_tot + s(48)), radius=s(24), fill=(19, 27, 63), outline=(61, 71, 122),
                            width=max(1, S))
        d.text((X0 + s(20), y_tot + s(10)), tot, font=_font(s(22), "Semibold"), fill=_TEXT)

    # Step table (right column).
    tx, ty, tw_col = s(860), s(330), s(656)
    d.rounded_rectangle((tx - s(28), ty - s(26), tx + tw_col, ty + s(380)), radius=s(22),
                        fill=_PANEL, outline=(36, 45, 92), width=max(1, S))
    f_row = _font(s(22), "Semibold")
    f_sub = _font(s(16), "Regular")
    f_time = _font(s(28), "Semibold")
    timed = [st for st in doc["steps"] if st.get("timed")]
    done_times = [st["wall_sec"] for st in timed if st["status"] == "done" and st.get("wall_sec")]
    tmax = max(done_times) if done_times else 1.0
    y = ty
    for st in timed:
        label = st["label"]
        d.text((tx, y), label, font=f_row, fill=_TEXT if st["core"] else _MUTED)
        if st["status"] == "done":
            t = fmt_sec(st["wall_sec"])
            sub = " · ".join(x for x in (
                st.get("canvas") or "",
                f"peak {st['peak_mem_gb']} GB in use" if st.get("peak_mem_gb") is not None else "",
                "optional" if not st["core"] else "") if x)
            col = _TEXT
        else:
            t = {"skipped": "not run", "failed": "failed", "cancelled": "stopped"}.get(st["status"], st["status"])
            sub = "optional extra" if not st["core"] else (st.get("reason") or "")[:60]
            col = _FAINT if st["status"] == "skipped" else _AMBER
        tw_ = d.textlength(t, font=f_time)
        d.text((tx + tw_col - s(28) - tw_, y - s(2)), t, font=f_time, fill=col)
        d.text((tx, y + s(32)), sub, font=f_sub, fill=_FAINT)
        # Bar: the share of the slowest timed step.
        by = y + s(60)
        d.rounded_rectangle((tx, by, tx + tw_col - s(28), by + s(6)), radius=s(3), fill=(29, 39, 82))
        if st["status"] == "done" and st.get("wall_sec"):
            frac = max(0.02, min(1.0, float(st["wall_sec"]) / tmax))
            bar = _gradient(max(1, int((tw_col - s(28)) * frac)), s(6))
            m = Image.new("L", bar.size, 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, bar.size[0] - 1, bar.size[1] - 1), radius=s(3), fill=255)
            img.paste(bar, (tx, by), m)
        y += s(92)

    # Conditions + footer.
    f_foot = _font(s(17), "Regular", mono=True)
    warm = cond.get("warmup_sec")
    cond_line = " · ".join((
        {"ac": "On charger", "battery": "On battery", "unknown": "Power not reported"}[cond["power"]],
        f"Low Power Mode {'on' if cond.get('low_power_mode') else 'off'}",
        f"thermal {_THERMAL_WORDS.get(cond['thermal_worst'])}",
        f"warm-up {fmt_sec(warm)} untimed",
    ))
    d.text((X0, s(760)), cond_line, font=f_foot, fill=_MUTED)
    status_txt = ("Complete run" if summ["complete"] else "INCOMPLETE run: not comparable")
    d.text((X0, s(792)), f"{status_txt} · run {doc['run_id']} · {doc['finished_at'][:10]}",
           font=f_foot, fill=_MUTED if summ["complete"] else _AMBER)
    d.line((X0, s(838), W - X0, s(838)), fill=(36, 45, 92), width=max(1, S))
    d.text((X0, s(852)), COPY["card_footer"], font=_font(s(18), "Medium"), fill=_TEXT)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out
