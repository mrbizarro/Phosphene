"""Phosphene Bench routes (bench.py is the suite, the runner and the card).

  GET  /bench/plan            what will run on THIS Mac, why not, past runs
  GET  /bench/status          the live run (polled by webapp/js/bench.js)
  POST /bench/start           run the suite (409 + a code when it cannot)
  POST /bench/stop            stop the run and the step it is rendering
  GET  /bench/run?id=         a result as JSON  (&download=1 saves it)
  GET  /bench/card?id=        the result card PNG (&download=1 saves it)
  GET  /bench/text?id=        the plain-text summary for "Copy results"
  POST /bench/submit          opt-in: send every unsent complete run
  POST /bench/install_image   fetch FLUX.2 [klein] on an explicit click

The runner drives the panel through `_Host` below — the same make_job(), the
same queue, the same worker and job timer every render uses — so a Bench time
is a real render time and nothing about the work is bench-specific except
that its settings are frozen. `P` is the running mlx_ltx_panel module,
assigned at wiring time (see panel/__init__.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import bench
import distribution
from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time

_RUNNER: "bench.Runner | None" = None
_RUNNER_LOCK = threading.Lock()
_INSTALL = {"state": "idle", "error": "", "started": None, "pid": None}
_INSTALL_LOCK = threading.Lock()


class _Host:
    """The handful of panel operations bench.Runner needs."""

    def __init__(self):
        self.store = bench.Store(P.STATE_DIR / "bench")

    # -- readiness --------------------------------------------------------
    def readiness(self) -> dict:
        return readiness()

    def canvases(self) -> dict:
        q = P.LTX_QUALITIES
        return {k: q[k]["canvas"] for k in ("quick", "balanced") if k in q}

    def tier(self) -> str:
        return str(P.SYSTEM_TIER)

    def app_info(self) -> dict:
        return app_info()

    def render_settings(self) -> dict:
        """Settings that change a render's time but are not suite data."""
        preset = str((P.output_codec_settings() or {}).get("preset") or "")
        return {"output_preset": preset or None,
                "output_preset_default": preset == P.DEFAULT_OUTPUT_PRESET}

    # -- the queue --------------------------------------------------------
    def busy_reason(self) -> str:
        return busy_reason()

    def make_job(self, form: dict) -> dict:
        job = P.make_job(form)
        # Tagged so the fleet's render_completed can tell a Bench render
        # from a person's own (docs/ANALYTICS.md `source`).
        job.setdefault("params", {})["source"] = "bench"
        return job

    def refusal(self, job: dict) -> str:
        params = job.get("params") or {}
        why = P.job_input_refusal(params)
        if why:
            return str(why)
        lic = P.job_licence_refusal(params)
        if lic:
            return str(lic.get("error") or "licence not accepted")
        return ""

    def enqueue(self, job: dict) -> None:
        with P.QUEUE_COND:
            P.STATE["queue"].append(job)
            P.QUEUE_COND.notify_all()
        P.persist_queue()

    def job(self, job_id: str) -> dict | None:
        with P.LOCK:
            cur = P.STATE.get("current")
            if cur is not None and cur.get("id") == job_id:
                return dict(cur)
            for j in P.STATE["queue"]:
                if j.get("id") == job_id:
                    return dict(j)
            for j in P.STATE["history"]:
                if j.get("id") == job_id:
                    return dict(j)
        return None

    def cancel(self, job_id: str) -> None:
        with P.LOCK:
            for i, j in enumerate(P.STATE["queue"]):
                if j.get("id") == job_id:
                    P.STATE["queue"].pop(i)
                    j["status"] = "cancelled"
                    j["finished_at"] = P.iso_now()
                    P.STATE["history"].insert(0, j)
                    break
            else:
                j = None
        if j is not None:
            P.persist_queue()
            return
        P.stop_current_job(expect_id=job_id)

    def cold_start(self) -> None:
        """Every run starts from the same place: no warm LTX engine. Only
        when nothing is rendering — busy_reason() was checked at start."""
        if busy_reason():
            return
        with P.LOCK:
            if P.STATE.get("current") is not None or P.STATE.get("queue"):
                return
        P.HELPER.kill()

    def log_lines(self) -> list:
        with P.LOCK:
            return list(P.STATE.get("log") or [])

    def render_card(self, doc: dict, path) -> None:
        bench.render_card(doc, path, mark_path=P.ROOT / "assets" / "phosphene_mark_square.png")


def runner() -> "bench.Runner":
    global _RUNNER
    with _RUNNER_LOCK:
        if _RUNNER is None:
            _RUNNER = bench.Runner(_Host())
        return _RUNNER


def app_info() -> dict:
    return {
        "version": str(P.running_version() or "unknown"),
        "edition": distribution.edition() or ("pro" if distribution.is_pro() else "free"),
        "distribution": distribution.distribution(),
        "generation": str(P.ACTIVE_MODEL_VERSION),
    }


def busy_reason() -> str:
    """"" when the Mac is free for a benchmark; else a code bench.js words."""
    with P.LOCK:
        if P.STATE.get("current") is not None or P.STATE.get("queue"):
            return "busy_queue"
        # A paused worker would take the warm-up and never start it.
        if P.STATE.get("paused"):
            return "busy_paused"
    if (P.gpu_gate_status() or {}).get("busy"):
        return "busy_gpu"
    with P.DOWNLOAD_LOCK:
        if P.DOWNLOAD.get("active"):
            return "busy_download"
    if _INSTALL["state"] == "downloading":
        return "busy_download"
    return ""


def _klein_ready() -> bool:
    """The mflux FLUX.2 command exists AND the weights are in the HF cache.
    Without the second check the first image job would download 4.3 GB
    inside a timed step — silently, which the Bench promises never to do."""
    try:
        cfg = P.agent_image_engine.ImageEngineConfig(kind="mflux", mflux_family="flux2")
        if not P.agent_image_engine._resolve_mflux_bin(cfg):
            return False
    except Exception:                                       # noqa: BLE001
        return False
    snap = P._repo_hf_cache_dir(bench.KLEIN_REPO)
    if snap is None:
        return False
    # Every component mflux loads must be on disk, and every weight file must
    # resolve (an interrupted download leaves dangling blob symlinks): a
    # half-fetched snapshot would otherwise be "ready" and the missing parts
    # would download inside the timed step.
    try:
        root = Path(snap)
        for comp in bench.KLEIN_COMPONENTS:
            files = [p for p in (root / comp).rglob("*") if not p.is_dir()]
            if not files or not all(p.exists() for p in files):
                return False
            if comp != "tokenizer" and not any(p.suffix == ".safetensors" for p in files):
                return False
        return True
    except OSError:
        return False


def readiness() -> dict:
    render = P.capability_state("render")
    by_key = {r.get("key"): r for r in P._repos()}
    missing_keys = []
    for key in P.capability_repo_keys("render"):
        repo = by_key.get(key)
        if repo and P._repo_effective_missing(repo):
            missing_keys.append(key)
    try:
        h3 = P.h3_status()
    except Exception:                                       # noqa: BLE001
        h3 = {}
    return {
        "ltx": {"ok": bool(render.get("ok")), "generation": str(P.ACTIVE_MODEL_VERSION),
                "missing_keys": missing_keys},
        "image": {"ok": _klein_ready(), "gb": bench.KLEIN_GB},
        "h3": {"installed": bool(h3.get("available")), "capable": bool(P.h3_capable()),
               "licence_ok": P.licence_refusal_for("h3") is None},
    }


def _conditions_now() -> dict:
    return {"power": bench.power_source(), "low_power_mode": bench.low_power_mode(),
            "thermal": bench.thermal_state()}


def _run_row(doc: dict, submitted: set) -> dict:
    s = doc.get("summary") or {}
    return {"run_id": doc.get("run_id"), "finished_at": doc.get("finished_at"),
            "suite": (doc.get("suite") or {}).get("id"),
            "headline_sec": s.get("headline_sec"), "complete": bool(s.get("complete")),
            "eligible": bool(s.get("eligible")), "submitted": doc.get("run_id") in submitted,
            "version": (doc.get("app") or {}).get("version")}


@get("/bench/plan")
def get_bench_plan(h, parsed) -> None:
    r = runner()
    rows = bench.plan(readiness(), r.host.canvases())
    ok, why = bench.plan_can_run(rows)
    busy = busy_reason()
    store = r.host.store
    runs = store.runs()
    sent = store.submitted()
    h._json({
        "ok": True,
        "suite": {"id": bench.SUITE_ID, "name": bench.SUITE_NAME,
                  "fingerprint": bench.SUITE_FINGERPRINT},
        "steps": rows,
        "can_run": bool(ok and not busy),
        "why": why or busy,
        "conditions": _conditions_now(),
        "analytics_enabled": bool(P._analytics_enabled()),
        "runs": [_run_row(d, sent) for d in runs[:20]],
        "unsent_eligible": len([d for d in bench.runs_to_submit(runs)
                                if d["run_id"] not in sent]),
        "install": dict(_INSTALL),
        "state": r.snapshot(),
    })


@get("/bench/status")
def get_bench_status(h, parsed) -> None:
    h._json({"ok": True, "state": runner().snapshot(), "install": dict(_INSTALL)})


@post("/bench/start")
def post_bench_start(h, path, qs, ctype) -> None:
    try:
        run_id = runner().start()
    except bench.BenchBusy as exc:
        h._json({"ok": False, "code": str(exc)}, 409); return
    h._json({"ok": True, "run_id": run_id})


@post("/bench/stop")
def post_bench_stop(h, path, qs, ctype) -> None:
    h._json({"ok": True, "stopped": runner().cancel()})


def _doc_from_qs(h, parsed) -> dict | None:
    rid = (P.parse_qs(parsed.query).get("id", [""])[0] or "").strip()
    try:
        doc = runner().host.store.load(rid)
    except ValueError:
        doc = None
    if doc is None:
        h._json({"ok": False, "error": "no such Bench run"}, 404)
    return doc


def _send(h, body: bytes, ctype: str, filename: str | None) -> None:
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Cache-Control", "no-store")
    h.send_header("Content-Length", str(len(body)))
    if filename:
        h.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    h.end_headers()
    h.wfile.write(body)


def _file_stem(doc: dict) -> str:
    chip = (doc.get("mac") or {}).get("chip_family", "mac").replace(" ", "-")
    day = (doc.get("finished_at") or "")[:10]
    return f"phosphene-bench-{doc['suite']['id']}-{chip}-{day}-{doc['run_id'][:6]}"


@get("/bench/run")
def get_bench_run(h, parsed) -> None:
    doc = _doc_from_qs(h, parsed)
    if doc is None:
        return
    dl = P.parse_qs(parsed.query).get("download", [""])[0] == "1"
    body = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _send(h, body, "application/json", f"{_file_stem(doc)}.json" if dl else None)


@get("/bench/card")
def get_bench_card(h, parsed) -> None:
    doc = _doc_from_qs(h, parsed)
    if doc is None:
        return
    store = runner().host.store
    png = store.path(doc["run_id"], "png")
    if not png.is_file():
        try:
            runner().host.render_card(doc, png)
        except Exception as exc:                            # noqa: BLE001
            h._json({"ok": False, "error": f"could not draw the card: {exc}"}, 500); return
    dl = P.parse_qs(parsed.query).get("download", [""])[0] == "1"
    _send(h, png.read_bytes(), "image/png", f"{_file_stem(doc)}.png" if dl else None)


@get("/bench/text")
def get_bench_text(h, parsed) -> None:
    doc = _doc_from_qs(h, parsed)
    if doc is None:
        return
    h._json({"ok": True, "text": bench.summary_text(doc)})


@post("/bench/submit")
def post_bench_submit(h, path, qs, ctype) -> None:
    """The opt-in. The analytics switch is the consent this rides on: off
    means nothing leaves the Mac, whatever is clicked here."""
    if not P._analytics_enabled():
        h._json({"ok": False, "code": "analytics_off"}, 403); return
    store = runner().host.store
    sent = store.submitted()
    todo = [d for d in bench.runs_to_submit(store.runs()) if d["run_id"] not in sent]
    if not todo:
        h._json({"ok": False, "code": "nothing_to_send"}, 400); return
    # Delivered on this thread so only runs the receiver actually took are
    # marked sent; an offline click leaves the rest for the next one.
    sent_ids = []
    for i, doc in enumerate(todo, 1):
        if P._analytics_capture_sync("bench_result", bench.analytics_props(doc, i, len(todo))):
            sent_ids.append(doc["run_id"])
    store.mark_submitted(sent_ids)
    if not sent_ids:
        h._json({"ok": False, "code": "send_failed"}, 502); return
    h._json({"ok": True, "sent": len(sent_ids), "failed": len(todo) - len(sent_ids)})


def _install_image_thread() -> None:
    env = dict(os.environ)
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    env.pop("HF_HUB_OFFLINE", None)
    try:
        proc = subprocess.Popen([str(P.HF_BIN), "download", bench.KLEIN_REPO],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, errors="replace", env=env)
        _INSTALL["pid"] = proc.pid
        _, err = proc.communicate()
        if proc.returncode == 0 and _klein_ready():
            _INSTALL.update(state="done", error="")
            P.push("[bench] FLUX.2 [klein] 4B downloaded for Phosphene Bench.")
        else:
            tail = (err or "").strip().splitlines()[-1:] or ["the download did not finish"]
            _INSTALL.update(state="failed", error=tail[0][:300])
    except Exception as exc:                                # noqa: BLE001
        _INSTALL.update(state="failed", error=str(exc)[:300])
    finally:
        _INSTALL["pid"] = None


@post("/bench/install_image")
def post_bench_install_image(h, path, qs, ctype) -> None:
    """FLUX.2 [klein] lives in the Hugging Face cache, not in a models pack,
    so /models/download cannot fetch it. This is the explicit click the
    Bench's "never downloads silently" rule asks for."""
    if P.HF_BIN is None:
        h._json({"ok": False, "error": "The download tool (hf) is missing. Use Repair engine in Settings."}, 500); return
    with _INSTALL_LOCK:
        if _INSTALL["state"] == "downloading":
            h._json({"ok": True, "already": True}); return
        if runner().state.get("running"):
            h._json({"ok": False, "code": "bench_running"}, 409); return
        # Check the filesystem that will receive the weights, including on
        # a fresh install where the cache directory does not exist yet.
        cache_home = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        hf_home = Path(os.environ.get("HF_HOME", str(cache_home / "huggingface")))
        cache = Path(os.environ.get("HF_HUB_CACHE") or
                     os.environ.get("HUGGINGFACE_HUB_CACHE") or str(hf_home / "hub"))
        while not cache.exists() and cache != cache.parent:
            cache = cache.parent
        required = int((bench.KLEIN_GB + 15) * 10**9)
        try:
            free = shutil.disk_usage(cache).free
        except OSError:
            h._json({"ok": False, "error": "Could not check free space for the image model. Check your cache drive and try again."}, 503); return
        if free < required:
            h._json({"ok": False, "code": "disk_space", "error":
                     f"The image model needs {bench.KLEIN_GB:g} GB plus 15 GB of free space. "
                     f"Only {free / 10**9:.1f} GB is available on the cache drive."}, 409); return
        _INSTALL.update(state="downloading", error="", started=time.time())
    threading.Thread(target=_install_image_thread, name="bench-klein-download", daemon=True).start()
    h._json({"ok": True})
