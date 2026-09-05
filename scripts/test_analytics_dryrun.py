#!/usr/bin/env python3
"""Dry-run tests for the panel's anonymous usage analytics.

    python3 scripts/test_analytics_dryrun.py

No network, no panel, no renders: the whole suite runs against an isolated
temp state dir with `urllib.request.urlopen` replaced by a spy, so it can be
run on a machine that is mid-render without touching anything.

What it is actually here to prove, in priority order:

  1. THE OFF SWITCH IS THE OFF SWITCH. Since acfbdc7 the tree ships a live
     phc_ project key, so a stock install reports by default — which makes
     the toggle (and PHOSPHENE_ANALYTICS_DISABLED) the only thing standing
     between a user who said no and a socket. Both must produce zero
     sockets AND zero local-log lines. Clearing the key field is NOT an
     opt-out and is asserted not to be mistaken for one.
  2. NOTHING LEAKS. No prompt, path, filename or media string may appear in
     any outgoing payload, including via the one free-text field
     (error_signature) and including when a caller is careless.
  3. THE SCHEMA IS THE SCHEMA. Each event's property set is asserted
     field-by-field against docs/ANALYTICS.md, and every event name the
     panel can fire is asserted to appear in that page — app_installed
     shipped undocumented for two releases and this is the guard for it.
  4. IT CANNOT BREAK A RENDER. Capture never raises, even on garbage input
     and even when the transport blows up.

A note for whoever edits this file next: the three assertions that used to
sit under a heading called "inert without a key" were correct until
2026-08-09 and false afterwards, and a RED suite protects nothing. They
were rewritten (not deleted) on 2026-08-12 to pin the posture that actually
shipped. If a future commit changes the posture again, rewrite them again —
deleting a guard is how the drift goes unnoticed the next time.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Isolate every file the panel writes BEFORE importing it. mlx_ltx_panel
# loads/creates panel_settings.json at import time; pointing LTX_STATE_DIR at
# a temp dir keeps a live panel's real state (and a concurrent render) safe.
_TMP_STATE = tempfile.mkdtemp(prefix="phosphene-analytics-test-")
os.environ["LTX_STATE_DIR"] = _TMP_STATE
os.environ.pop("PHOSPHENE_ANALYTICS_KEY", None)
os.environ.pop("PHOSPHENE_ANALYTICS_QUERY_KEY", None)
os.environ.pop("PHOSPHENE_ANALYTICS_DISABLED", None)

sys.path.insert(0, str(REPO))
import mlx_ltx_panel as P  # noqa: E402


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------

class _Resp:
    """Minimal urlopen() return value: a context manager with .read()."""
    def __init__(self, body: bytes = b"{\"status\":1}"):
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Spy:
    """Records every urlopen() call instead of making it."""
    def __init__(self, raise_exc: Exception | None = None):
        self.calls: list[dict] = []
        self.raise_exc = raise_exc
        self._lock = threading.Lock()

    def __call__(self, req, timeout=None, **kw):
        with self._lock:
            self.calls.append({
                "url": getattr(req, "full_url", str(req)),
                "body": getattr(req, "data", None) or b"",
                "headers": dict(getattr(req, "headers", {}) or {}),
                "timeout": timeout,
            })
        if self.raise_exc:
            raise self.raise_exc
        return _Resp()

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(c["body"].decode("utf-8")) for c in self.calls]

    def raw(self) -> str:
        return "\n".join(c["body"].decode("utf-8", "replace") for c in self.calls)


def drain(timeout: float = 5.0) -> None:
    """Wait for every in-flight analytics delivery thread to finish."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [t for t in threading.enumerate()
                 if t.name.startswith("analytics-capture") and t.is_alive()]
        if not alive:
            return
        time.sleep(0.01)


class AnalyticsTestCase(unittest.TestCase):
    """Common fixture: clean state dir, spy installed, analytics ON, and no
    key OVERRIDE — which is the stock-install case, since an empty
    `analytics_key` falls back to the shipped ANALYTICS_KEY_DEFAULT.
    Subclasses that want a predictable key on the wire set their own."""

    def setUp(self):
        self.spy = Spy()
        self._real_urlopen = urllib.request.urlopen
        urllib.request.urlopen = self.spy
        try:
            P.USAGE_LOG_FILE.unlink()
        except OSError:
            pass
        self.configure(analytics_enabled=True, analytics_key="",
                       analytics_query_key="", analytics_last_packs={},
                       analytics_disclosed=True)

    def tearDown(self):
        drain()
        urllib.request.urlopen = self._real_urlopen

    @staticmethod
    def configure(**kv):
        P._settings_set_internal(**kv)

    @staticmethod
    def log_lines() -> list[dict]:
        return P._usage_log_read()


# --------------------------------------------------------------------------
# 1. The shipped key, and the two things that switch it off
# --------------------------------------------------------------------------

class TestShippedKeyAndOptOut(AnalyticsTestCase):

    def test_shipped_key_is_a_write_only_project_key(self):
        """What ships must be a phc_ PROJECT key: write-only, able to send
        events and nothing else, which is what makes committing it to a
        source-distributed app defensible (docs/ANALYTICS.md says exactly
        this to users). The failure this guards is not "a key exists" — it
        is a READ-capable personal key (phx_/phs_) reaching the tree, which
        would hand every cloner the whole project's data."""
        key = P.ANALYTICS_KEY_DEFAULT
        self.assertTrue(key.startswith("phc_"),
                        f"shipped capture key is not a phc_ project key: {key[:8]!r}...")
        self.assertGreaterEqual(len(key), 32, "shipped key looks truncated")
        for readable in ("phx_", "phs_"):
            self.assertFalse(key.startswith(readable),
                             "a READ-capable personal key must never be committed")
        # The stock-install path: no env override, no saved override.
        self.assertEqual(P._analytics_key(), key,
                         "an empty analytics_key setting must fall back to the "
                         "shipped key — it is an override field, not a switch")

    def test_opt_out_is_the_toggle_not_the_key(self):
        """The whole post-acfbdc7 contract in one test, in the order a user
        would discover it: a stock install really does send under the
        shipped key; clearing the key field really does NOT stop that; the
        toggle really does, for the socket and the local mirror alike."""
        # 1. Stock install -> exactly one POST, carrying the shipped key.
        P._analytics_capture("app_boot", {"version": "3.4.1"})
        drain()
        self.assertEqual(len(self.spy.calls), 1,
                         "a stock install must report — the project key ships")
        self.assertEqual(self.spy.bodies[0]["api_key"], P.ANALYTICS_KEY_DEFAULT,
                         "the shipped key must be the one on the wire")

        # 2. Clearing the key field is not an opt-out, and the doc says so.
        self.configure(analytics_key="")
        P._analytics_capture("app_boot", {"version": "3.4.1"})
        drain()
        self.assertEqual(len(self.spy.calls), 2,
                         "an empty analytics_key means 'no override', not 'off'; "
                         "if this ever becomes an off switch, fix docs/ANALYTICS.md "
                         "in the same commit")

        # 3. The toggle is. Nothing on the wire, nothing in the mirror —
        #    _analytics_capture() returns before it builds a payload.
        rows_before = len(self.log_lines())
        self.configure(analytics_enabled=False)
        P._analytics_capture("app_boot", {"version": "3.4.1"})
        P._analytics_render_event({"status": "done", "params": {"mode": "t2v"}})
        drain()
        self.assertEqual(len(self.spy.calls), 2,
                         "opt-out must open no socket, shipped key or not")
        self.assertEqual(len(self.log_lines()), rows_before,
                         "opt-out must stop the local log too")

    def test_every_capture_writes_the_local_mirror(self):
        """The local log is the always-on audit view: it is written before
        the network is touched, so the user can read back exactly what left
        (or would have left, when the endpoint was down)."""
        P._analytics_capture("app_boot", {"version": "3.4.1"})
        drain()
        rows = self.log_lines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "app_boot")

    def test_disabled_writes_nothing_anywhere(self):
        """Same guarantee as above via _analytics_boot()'s three call sites,
        with the shipped key live — this is the stock user who said no."""
        self.configure(analytics_enabled=False)
        P._analytics_capture("app_boot", {"version": "3.4.1"})
        P._analytics_render_event({"status": "done", "params": {"mode": "t2v"}})
        P._analytics_boot()
        drain()
        self.assertEqual(self.spy.calls, [])
        self.assertEqual(self.log_lines(), [],
                         "toggle OFF must stop the local log too")

    def test_env_kill_switch_beats_the_setting(self):
        os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
        try:
            self.assertFalse(P._analytics_enabled())
            P._analytics_capture("app_boot", {})
            drain()
            self.assertEqual(self.spy.calls, [])
            self.assertEqual(self.log_lines(), [])
        finally:
            os.environ.pop("PHOSPHENE_ANALYTICS_DISABLED", None)

    def test_query_key_absent_means_local_report(self):
        report = P._usage_report()
        self.assertEqual(report["source"], "local")
        self.assertIn("this machine only", report["note"])
        self.assertEqual(self.spy.calls, [],
                         "the usage report must not query PostHog with no key")


# --------------------------------------------------------------------------
# 2. Transport shape once a key IS configured
# --------------------------------------------------------------------------

class TestCaptureWithKey(AnalyticsTestCase):

    def setUp(self):
        super().setUp()
        self.configure(analytics_key="phc_fake_key_for_tests")

    def test_single_post_with_posthog_payload_shape(self):
        P._analytics_capture("app_boot", {"version": "3.4.1", "ram_gb": 64})
        drain()
        self.assertEqual(len(self.spy.calls), 1, "expected exactly one POST")
        call = self.spy.calls[0]
        self.assertTrue(call["url"].endswith("/i/v0/e/"), call["url"])
        self.assertEqual(call["timeout"], P.ANALYTICS_TIMEOUT_SEC)
        body = self.spy.bodies[0]
        self.assertEqual(body["api_key"], "phc_fake_key_for_tests")
        self.assertEqual(body["event"], "app_boot")
        self.assertEqual(body["properties"]["version"], "3.4.1")
        # Anonymous: no person profile is built for the install id.
        self.assertIs(body["properties"]["$process_person_profile"], False)
        self.assertTrue(body["timestamp"].endswith("Z"))

    def test_distinct_id_is_a_random_uuid4_and_stable(self):
        import re as _re
        P._analytics_capture("app_boot", {})
        drain()
        did = self.spy.bodies[0]["distinct_id"]
        self.assertRegex(did, _re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
        self.assertEqual(did, P._analytics_install_id(), "install id must be stable")
        # Not derived from anything about the machine.
        for leak in (os.uname().nodename, os.environ.get("USER", "x"), str(REPO)):
            self.assertNotIn(leak.lower(), did.lower())

    def test_env_key_overrides_the_setting(self):
        os.environ["PHOSPHENE_ANALYTICS_KEY"] = "phc_from_env"
        try:
            self.assertEqual(P._analytics_key(), "phc_from_env")
        finally:
            os.environ.pop("PHOSPHENE_ANALYTICS_KEY", None)

    def test_host_override_is_honoured(self):
        os.environ["PHOSPHENE_ANALYTICS_HOST"] = "https://selfhosted.example"
        try:
            P._analytics_capture("app_boot", {})
            drain()
            self.assertTrue(self.spy.calls[0]["url"].startswith(
                "https://selfhosted.example/"), self.spy.calls[0]["url"])
        finally:
            os.environ.pop("PHOSPHENE_ANALYTICS_HOST", None)

    def test_transport_failure_is_swallowed_and_log_still_written(self):
        """A dead endpoint must be indistinguishable from success to the
        caller — this is the property that keeps analytics out of renders."""
        urllib.request.urlopen = Spy(raise_exc=OSError("network down"))
        P._analytics_capture("render_completed", {"engine": "ltx"})
        drain()
        self.assertEqual(len(self.log_lines()), 1)

    def test_capture_never_raises_on_garbage(self):
        for bad in (None, object(), b"bytes", 12345):
            P._analytics_capture("weird_event", {"x": bad})
        P._analytics_capture(None, None)          # type: ignore[arg-type]
        P._analytics_render_event(None)           # type: ignore[arg-type]
        P._analytics_render_event({"status": "done"})
        drain()   # reaching here at all is the assertion


# --------------------------------------------------------------------------
# 3. No content ever leaves the machine
# --------------------------------------------------------------------------

class TestNoLeakage(AnalyticsTestCase):

    PROMPT = ("a weathered lighthouse keeper bizarrotrn turns toward camera, "
              "storm light raking his face")
    NEG = "blurry, extra fingers, watermark"
    IMG = "/Users/salo/Desktop/private-photos/keeper-reference.png"
    OUT = "/Users/salo/pinokio/drive/mlx_outputs/20260805_lighthouse_final.mp4"

    def setUp(self):
        super().setUp()
        self.configure(analytics_key="phc_fake_key_for_tests")

    def failed_job(self, error: str) -> dict:
        return {
            "id": "job-1", "status": "failed", "error": error,
            "elapsed_sec": 412.5, "output": self.OUT,
            "params": {
                "mode": "i2v", "engine": "ltx", "quality": "standard",
                "width": 1216, "height": 704, "frames": 121,
                "prompt": self.PROMPT, "negative_prompt": self.NEG,
                "image": self.IMG, "output": self.OUT, "seed": "12345",
                "character": "bizarrotrn_v2",
            },
        }

    def assert_clean(self):
        """No fragment of the user's content in ANY transmitted byte."""
        blob = self.spy.raw() + json.dumps(self.log_lines())
        for needle in (self.PROMPT, self.NEG, self.IMG, self.OUT,
                       "lighthouse", "keeper-reference", "private-photos",
                       "bizarrotrn", "/Users/", "Desktop", "mlx_outputs",
                       ".mp4", ".png"):
            self.assertNotIn(needle, blob,
                             f"leaked {needle!r} into an analytics payload")

    def test_failed_render_with_prompt_in_the_error_message(self):
        """The realistic leak: an exception that quotes the prompt back."""
        P._analytics_render_event(self.failed_job(
            f"RuntimeError: helper died while encoding '{self.PROMPT}'"))
        drain()
        self.assertEqual(len(self.spy.calls), 1)
        self.assert_clean()
        props = self.spy.bodies[0]["properties"]
        self.assertIn("<redacted>", props["error_signature"])

    def test_failed_render_with_paths_in_the_error_message(self):
        """Paths that came from the job get caught by the exact-secret pass
        (which runs first and is stronger); either marker is a pass, the
        assertion that matters is assert_clean()."""
        P._analytics_render_event(self.failed_job(
            f"FileNotFoundError: {self.IMG} is missing; wrote {self.OUT}"))
        drain()
        self.assert_clean()
        sig = self.spy.bodies[0]["properties"]["error_signature"]
        self.assertTrue("<path>" in sig or "<redacted>" in sig, sig)

    def test_paths_the_job_never_mentioned_are_still_stripped(self):
        """The path regex is the net for everything the exact-secret pass
        can't know about: helper temp dirs, HF cache entries, venv paths."""
        P._analytics_render_event(self.failed_job(
            "OSError: cannot load /Users/someone/.cache/huggingface/hub/"
            "models--Lightricks--LTX/snapshots/abc/model.safetensors"))
        drain()
        sig = self.spy.bodies[0]["properties"]["error_signature"]
        self.assertIn("<path>", sig)
        self.assertNotIn("someone", sig)
        self.assertNotIn("huggingface", sig)

    def test_completed_render_carries_no_content_at_all(self):
        job = self.failed_job("unused")
        job["status"] = "done"
        job.pop("error")
        P._analytics_render_event(job)
        drain()
        self.assert_clean()
        props = self.spy.bodies[0]["properties"]
        self.assertNotIn("error_signature", props,
                         "successful renders must not carry an error field")

    def test_forbidden_keys_are_dropped_even_if_a_caller_passes_them(self):
        """Defense in depth: a future `**params` spread must not leak."""
        dirty = {
            "prompt": self.PROMPT, "image": self.IMG, "output_path": self.OUT,
            "filename": "secret.mp4", "username": "salo", "lora_paths": [self.IMG],
            "engine": "ltx", "frames": 121,
        }
        clean = P._analytics_clean_props(dirty)
        self.assertEqual(set(clean), {"engine", "frames"})

    def test_nested_dict_props_are_scrubbed_too(self):
        clean = P._analytics_clean_props(
            {"packs": {"h3": True, "prompt": self.PROMPT, "q8": False}})
        self.assertEqual(clean["packs"], {"h3": True, "q8": False})

    def test_scrub_takes_first_line_and_truncates(self):
        sig = P._analytics_scrub_text("boom happened\nsecond line with detail")
        self.assertEqual(sig, "boom happened")
        long = P._analytics_scrub_text("x" * 500)
        self.assertLessEqual(len(long), P.ANALYTICS_STR_MAX)

    def test_scrub_strips_home_and_tilde_paths(self):
        for raw in ("could not open /Users/salo/AI/notes.txt",
                    "missing ~/pinokio/api/phosphene.git/models/x.safetensors",
                    "bad /private/var/folders/t3/zz/T/render.mp4",
                    "nested /a/b/c/d/e.bin"):
            out = P._analytics_scrub_text(raw)
            self.assertIn("<path>", out, raw)
            self.assertNotIn("/Users/", out)
            self.assertNotIn("salo", out)

    def test_short_secrets_do_not_blank_ordinary_words(self):
        """A 3-char 'prompt' must not turn every error into <redacted>."""
        out = P._analytics_scrub_text("out of memory during decode", ["out"])
        self.assertEqual(out, "out of memory during decode")


# --------------------------------------------------------------------------
# 4. Event schemas — kept in lockstep with docs/ANALYTICS.md
# --------------------------------------------------------------------------

class TestEventSchemas(AnalyticsTestCase):

    def setUp(self):
        super().setUp()
        self.configure(analytics_key="phc_fake_key_for_tests")

    def props_of(self, event: str) -> dict:
        """This event's own properties, with the receiver directives removed.

        The $-prefixed keys are instructions to PostHog, not part of an
        event's schema — they are asserted as a set, on every event, in
        TestReceiverDirectives. Anything else beginning with $ is a key
        nobody declared, so fail on it here rather than let it ride along
        into the next schema that gets written."""
        for body in self.spy.bodies:
            if body["event"] == event:
                p = dict(body["properties"])
                for k in P._ANALYTICS_RECEIVER_DIRECTIVES:
                    p.pop(k, None)
                stray = sorted(k for k in p if k.startswith("$"))
                self.assertEqual(stray, [], f"undeclared receiver key(s) on "
                                            f"{event}: {stray}")
                return p
        self.fail(f"no {event} event was captured")

    def test_app_boot_fields(self):
        P._analytics_boot()
        drain()
        p = self.props_of("app_boot")
        self.assertEqual(set(p), {
            "version", "os_version", "chip_family", "ram_gb", "cap_tier",
            "model_version", "packs", "h3_chain_supported"})
        self.assertEqual(set(p["packs"]), {"h3", "sharp", "q8", "qwen"})
        for v in p["packs"].values():
            self.assertIsInstance(v, bool)
        self.assertIsInstance(p["ram_gb"], int)
        self.assertIn(p["cap_tier"], ("q4", "q8"))
        # A NEW field, not a redefinition of cap_tier: the capability
        # series has to stay comparable across the 2.5 cutover.
        self.assertIn(p["model_version"], ("ltx23", "ltx25"))
        # os_version is major.minor only — no patch level.
        self.assertLessEqual(len(p["os_version"].split(".")), 2)

    # The schema-v2 render event's full closed field set (spec 2.4). Every
    # addition is a coarse class or a closed vocabulary — the exact-set
    # assertion is the guard that a content-shaped field cannot ride along.
    RENDER_V2_FIELDS = {
        "engine", "mode", "tier", "duration_bucket", "resolution", "frames",
        "version", "chip_family", "ram_gb", "os_version", "canvas_class",
        "steps", "accel", "temporal_mode", "upscale", "upscale_method",
        "schedule_preset", "chain_windows", "chain_prompts_used",
        "lora_count", "lora_kinds", "character_used", "source", "audio_mode",
    }

    def test_render_completed_fields(self):
        self.configure(analytics_first_render_reported=True)
        P._analytics_render_event({
            "status": "done", "elapsed_sec": 340.0,
            "params": {"mode": "t2v", "engine": "ltx", "quality": "standard",
                       "width": 1216, "height": 704, "frames": 121},
        })
        drain()
        p = self.props_of("render_completed")
        self.assertEqual(set(p), self.RENDER_V2_FIELDS | {"wall_sec_bucket"})
        self.assertEqual(p["resolution"], "1216x704")
        self.assertEqual(p["duration_bucket"], "5-15m")
        self.assertEqual(p["wall_sec_bucket"], 300)
        self.assertEqual(p["canvas_class"], "720p")
        self.assertEqual(p["tier"], "standard")
        # The machine-class trio matches what app_boot sends — same values,
        # repeated so PostHog breakdowns need no join (spec F6).
        self.assertEqual(p["chip_family"], P._analytics_chip_family())
        self.assertIsInstance(p["ram_gb"], int)
        self.assertEqual(p["schedule_preset"], "default")
        self.assertEqual(p["lora_kinds"], "none")
        self.assertFalse(p["character_used"])
        self.assertEqual(p["audio_mode"], "joint")

    def test_render_failed_adds_the_error_taxonomy(self):
        self.configure(analytics_first_render_reported=True)
        P._analytics_render_event({
            "status": "failed", "error": "OOM during VAE decode: SIGKILL",
            "elapsed_sec": 61.0,
            "params": {"mode": "i2v", "engine": "h3", "h3_tier": "hq_5s",
                       "width": 1280, "height": 720, "frames": 90},
        })
        drain()
        p = self.props_of("render_failed")
        self.assertEqual(set(p), self.RENDER_V2_FIELDS
                         | {"wall_sec_bucket", "error_class",
                            "error_signature"})
        # h3_tier is the wire format, and every legacy key still resolves to
        # the cell it means: the quality x length refactor renamed hq_5s to
        # standard_5s, and a job replayed from an older sidecar must land in
        # that bucket rather than inventing one.
        self.assertEqual(p["tier"], "standard_5s",
                         "a legacy h3_tier must resolve to its current cell")
        self.assertEqual(p["duration_bucket"], "<2m")
        self.assertEqual(p["wall_sec_bucket"], 60)
        # SIGKILL classifies as the jetsam class; a classified error carries
        # NO fingerprint — that field exists only for `other`.
        self.assertEqual(p["error_class"], "oom_jetsam")
        self.assertNotIn("error_fingerprint", p)
        self.assertEqual(p["audio_mode"], "h3_native")

    def test_unknown_error_ships_class_other_plus_fingerprint(self):
        self.configure(analytics_first_render_reported=True)
        P._analytics_render_event({
            "status": "failed", "error": "ZeroDivisionError: division by zero",
            "elapsed_sec": 10.0,
            "params": {"mode": "t2v", "engine": "ltx", "quality": "quick",
                       "width": 640, "height": 448, "frames": 73},
        })
        drain()
        p = self.props_of("render_failed")
        self.assertEqual(p["error_class"], "other")
        # 12 hex chars of the SCRUBBED line — countable, not readable.
        self.assertRegex(p["error_fingerprint"], r"^[0-9a-f]{12}$")
        self.assertEqual(
            p["error_fingerprint"],
            P._analytics_error_fingerprint(p["error_signature"]))

    def test_error_taxonomy_is_closed_and_ordered(self):
        cases = {
            "Command buffer exec failed: kIOGPUCommandBufferCallbackErrorTimeout":
                "metal_watchdog",
            # The watchdog marker must win over the generic signal words —
            # issue #44 must never be merged into native_crash.
            "SIGABRT after Caused GPU Timeout Error": "metal_watchdog",
            "helper died mid-job (no event)": "oom_jetsam",
            "signal SIGSEGV in worker": "native_crash",
            "helper failed to start: None": "helper_start_timeout",
            "Broken pipe while writing job": "helper_exit",
            "HTTP Error 403: gated repo": "download_failed",
            "checksum mismatch (download corrupt) - please retry":
                "download_failed",
            "safetensors header truncated": "model_corrupt",
            "Hailuo H3 isn't fully installed yet: dit": "model_missing",
            # AN INSTALL THAT FELL BEHIND THE PANEL IS NOT AN UNKNOWN. All
            # four raise sites — --lora (twice), --first-frame,
            # --chain-windows — used to land in `other` because each wrote
            # its own sentence. One shared phrase, one class, one remedy
            # (owner ruling 2026-08-23). See docs/ANALYTICS.md for the note
            # that this moves an existing series.
            "Turbo needs `--lora` support: the installed Hailuo H3 runner "
            "is behind this panel: it has no `--lora` (/x/generate_staged.py)."
            " Re-run 'Install Hailuo H3' from the Phosphene sidebar to update "
            "the clone - every weight already on disk is kept. Or turn Turbo "
            "off.": "model_missing",
            "Image mode conditions on a first frame: the installed Hailuo H3 "
            "runner is behind this panel: it has no `--first-frame` "
            "(/x/generate_staged.py).": "model_missing",
            "10s renders as 2 chained windows: the installed Hailuo H3 runner "
            "is behind this panel: it has no `--chain-windows` "
            "(/x/generate_staged.py).": "model_missing",
            "No module named 'mlx'": "venv_broken",
            "OSError: [Errno 28] No space left on device": "disk_full",
            "input image does not exist": "input_missing",
            # THE WIDEST-SPREAD REAL FAILURE IN THE FLEET, which used to
            # classify as `other` because every raise site says "not found"
            # and neither needle did. 35 events, 22 people, 14 days.
            "image not found: /Users/x/pinokio/api/phosphene.git/examples/"
            "reference.png": "input_missing",
            "ref image not found: /tmp/x.png": "input_missing",
            "The reference image is no longer on disk: /Users/x/a.png. It was "
            "moved, renamed or deleted after it was picked.": "input_missing",
            # ...and it must beat `download_failed`, because the text being
            # matched carries the user's own path and a great many missing
            # reference images live in ~/Downloads.
            "image not found: /Users/x/Downloads/pic.png": "input_missing",
            # But a hub lookup is still a fetch fault, not a missing input.
            "Hugging Face repo not found: foo/bar. Check the repo id.":
                "download_failed",
            "ffmpeg exited 1": "export_failed",
            "prompt required": "bad_params",
            "phase timed out after 300s": "timeout",
            "cancel requested, landing as failed": "cancelled_race",
            "some brand new exploding thing": "other",
            # REFUSALS WIN OVER EVERYTHING. Each of these used to land as
            # `other` — the first one was the single largest render_failed
            # signature in the whole fleet on 2026-08-23 — and each contains
            # a phrase that a lower row would otherwise have claimed.
            # "install the 2.3 pack" reads as model_missing:
            "Ingredients needs the LTX-2.3 generation. Its IC-LoRA is "
            "2.3-trained ... or install the 2.3 pack from the Train tab.":
                "refused",
            "High quality (Q8 two-stage) isn't supported on the Compact "
            "hardware tier - Q8 dev transformer (~19 GB) doesn't fit.":
                "refused",
            "Extend isn't supported on the Compact hardware tier": "refused",
            "FFLF (keyframe interpolation) isn't supported on the Compact "
            "hardware tier": "refused",
            # "needs about 64 GB" reads as bad_params ("must be"/"required"):
            "Hailuo H3 needs about 64 GB of unified memory; this Mac "
            "reports 24 GB. Render on the LTX engine instead.": "refused",
            "Hailuo H3 doesn't serve mode 'extend' - only t2v, i2v.":
                "refused",
            "H3's runner has 1 adapter slot - `--lora` takes a single path":
                "refused",
        }
        for raw, want in cases.items():
            self.assertEqual(P._analytics_error_class(raw), want, raw)

    def test_a_refusal_never_rides_on_a_render_failed_event(self):
        """`refused` is in the taxonomy so that classification can never
        drop a refusal into `other`. It is NOT a value render_failed may
        carry: the moment it classifies, the event becomes render_refused.
        If this ever fails, the failure rate has started lying again."""
        self.configure(analytics_first_render_reported=True)
        # Captured synchronously here rather than through the network spy:
        # this asserts once per needle, and the delivery thread makes
        # "which event was that one" racy at that granularity.
        seen = []
        orig = P._analytics_capture
        P._analytics_capture = lambda ev, props=None: seen.append((ev, props))
        try:
            for slug, needles in P._ANALYTICS_REFUSAL_REASONS:
                for needle in needles:
                    seen.clear()
                    P._analytics_render_event({
                        "status": "failed", "error": f"...{needle}...",
                        "elapsed_sec": 2.0,
                        "params": {"mode": "t2v", "engine": "ltx",
                                   "quality": "standard", "width": 640,
                                   "height": 448, "frames": 73}})
                    self.assertEqual([ev for ev, _ in seen],
                                     ["render_refused"], needle)
                    props = seen[0][1]
                    self.assertEqual(props["refusal"], slug, needle)
                    self.assertNotIn("error_class", props)
        finally:
            P._analytics_capture = orig

    def test_refusal_slugs_are_a_closed_vocabulary(self):
        """Same promise as every other string the panel transmits: the value
        is drawn from a set defined in the source, never from user text."""
        self.assertEqual(sorted(P._ANALYTICS_REFUSAL_SLUGS),
                         ["h3_lora_slots", "h3_mode", "h3_ram",
                          "hardware_tier",
                          # v4.9.3: the Image Studio memory guard.
                          "image_ram",
                          "ingredients_generation",
                          # v4.9.3: High/Keyframes/Extend without the Q8 pack.
                          "pack_missing",
                          # v4.9: the stale-vendored-engine gate (a 2.5 render
                          # on an engine predating the Gemma 4 tower is
                          # refused with the two-Update-clicks remedy).
                          "stale_engine"])
        self.assertEqual(len(set(P._ANALYTICS_REFUSAL_SLUGS)),
                         len(P._ANALYTICS_REFUSAL_SLUGS))
        # `refused` is a real member of the closed error taxonomy, and its
        # needles are DERIVED from the refusal table so the two cannot drift.
        classes = dict(P._ANALYTICS_ERROR_CLASSES)
        self.assertIn("refused", classes)
        self.assertEqual(P._ANALYTICS_ERROR_CLASSES[0][0], "refused",
                         "refused must be matched first or a lower row "
                         "claims the message")
        self.assertEqual(
            set(classes["refused"]),
            {n for _, ns in P._ANALYTICS_REFUSAL_REASONS for n in ns})
        self.assertIsNone(P._analytics_refusal_reason(
            "SIGSEGV in worker"), "a real crash is not a refusal")
        self.assertIsNone(P._analytics_refusal_reason(""))

    def test_the_structural_stamp_beats_the_text(self):
        """The raise sites use RenderRefused, and worker_loop stamps its
        `reason` onto the job. Text matching is only the fallback for a
        refusal that lost its type — so the stamp must win, and must work
        even when the message says nothing recognisable at all."""
        self.configure(analytics_first_render_reported=True)
        P._analytics_render_event({
            "status": "failed", "refused_reason": "h3_mode",
            "error": "no recognisable phrase in here whatsoever",
            "elapsed_sec": 3.0,
            "params": {"mode": "extend", "engine": "h3", "h3_tier": "hq_5s"}})
        drain()
        p = self.props_of("render_refused")
        self.assertEqual(p["refusal"], "h3_mode")
        # Exactly ONE extra field over a completed render: no error_class to
        # classify, no free text to scrub, no unknown to fingerprint.
        self.assertEqual(set(p),
                         self.RENDER_V2_FIELDS | {"wall_sec_bucket", "refusal"})
        for gone in ("error_class", "error_signature", "error_fingerprint"):
            self.assertNotIn(gone, p)

    def test_a_bogus_stamp_falls_back_to_the_fault_path(self):
        """A stamp is only trusted if it names a slug we declared. Anything
        else is treated as the crash it probably is, rather than being
        allowed to launder a failure out of the failure rate."""
        self.configure(analytics_first_render_reported=True)
        P._analytics_render_event({
            "status": "failed", "refused_reason": "definitely_not_a_slug",
            "error": "ZeroDivisionError: division by zero",
            "elapsed_sec": 4.0,
            "params": {"mode": "t2v", "engine": "ltx", "quality": "quick"}})
        drain()
        p = self.props_of("render_failed")
        self.assertEqual(p["error_class"], "other")
        self.assertNotIn("refusal", p)

    def test_a_genuine_crash_is_unchanged_by_any_of_this(self):
        """The regression guard for the whole change: the classes that
        existed before must classify exactly as they did before, and a real
        crash must still be a render_failed carrying the same three fields."""
        self.configure(analytics_first_render_reported=True)
        P._analytics_render_event({
            "status": "failed",
            "error": "Command buffer exec failed: "
                     "kIOGPUCommandBufferCallbackErrorTimeout",
            "elapsed_sec": 61.0,
            "params": {"mode": "t2v", "engine": "ltx", "quality": "standard",
                       "width": 1216, "height": 704, "frames": 121}})
        drain()
        names = [b["event"] for b in self.spy.bodies]
        self.assertIn("render_failed", names)
        self.assertNotIn("render_refused", names)
        p = self.props_of("render_failed")
        self.assertEqual(p["error_class"], "metal_watchdog")
        self.assertEqual(set(p), self.RENDER_V2_FIELDS
                         | {"wall_sec_bucket", "error_class",
                            "error_signature"})

    def test_wall_ladder_edges(self):
        f = P._analytics_wall_sec_bucket
        self.assertIsNone(f(None))
        self.assertIsNone(f(0))
        self.assertEqual(f(7), 0)
        self.assertEqual(f(15), 15)
        self.assertEqual(f(499.3), 420)
        self.assertEqual(f(3599), 2400)
        self.assertEqual(f(99999), 5400)

    def test_canvas_classes(self):
        f = P._analytics_canvas_class
        self.assertEqual(f(640, 448), "<=480p")
        self.assertEqual(f(1024, 576), "576p")
        self.assertEqual(f(1280, 704), "720p")
        self.assertEqual(f(1920, 1080), "1080p")
        self.assertEqual(f(2560, 1440), "native+")
        self.assertEqual(f(0, 0), "unknown")

    def test_first_render_fires_once_ever(self):
        self.configure(analytics_first_render_reported=False)
        job = {"status": "done", "elapsed_sec": 60.0,
               "params": {"mode": "t2v", "engine": "ltx", "quality": "quick",
                          "width": 640, "height": 448, "frames": 73}}
        P._analytics_render_event(job)
        drain()
        self.assertTrue(self.props_of("render_completed").get("first_render"))
        self.assertTrue(
            P.get_settings().get("analytics_first_render_reported"))
        self.spy.calls.clear()
        P._analytics_render_event(job)
        drain()
        self.assertNotIn("first_render", self.props_of("render_completed"))

    def test_app_installed_fields(self):
        """Fires once per install, ever, immediately before its first
        app_boot. Documented in docs/ANALYTICS.md since 2026-08-12 — it had
        been shipping and firing unnamed there, which by that page's own
        opening rule was a bug."""
        self.configure(analytics_install_reported=False)
        P._analytics_boot()
        drain()
        p = self.props_of("app_installed")
        self.assertEqual(set(p), {"version", "chip_family", "ram_gb"})
        self.assertIsInstance(p["ram_gb"], int)
        self.assertEqual([b["event"] for b in self.spy.bodies][:2],
                         ["app_installed", "app_boot"],
                         "app_installed must precede the first app_boot")
        # Once ever: the flag is persisted, so a reboot never re-counts.
        self.assertTrue(P.get_settings().get("analytics_install_reported"))
        self.spy.calls.clear()
        P._analytics_boot()
        drain()
        self.assertNotIn("app_installed", [b["event"] for b in self.spy.bodies],
                         "app_installed re-fired on a second boot")

    def test_cancelled_jobs_are_not_reported(self):
        P._analytics_render_event({"status": "cancelled", "params": {"mode": "t2v"}})
        drain()
        self.assertEqual(self.spy.calls, [])

    def test_pack_state_change_fires_on_a_true_to_false_flip(self):
        """The H3-vanished detector. Seed a previous boot that had H3, then
        boot with the real (H3-absent-or-present) state and require an event
        only when it actually changed."""
        packs_before = dict(P._analytics_pack_state())
        flipped = dict(packs_before, h3=not packs_before["h3"])
        self.configure(analytics_last_packs=flipped)
        P._analytics_boot()
        drain()
        p = self.props_of("pack_state_change")
        self.assertEqual(set(p), {"pack", "from", "to"})
        self.assertEqual(p["pack"], "h3")
        self.assertIs(p["from"], flipped["h3"])
        self.assertIs(p["to"], packs_before["h3"])

    def test_no_pack_event_when_nothing_changed(self):
        self.configure(analytics_last_packs=dict(P._analytics_pack_state()))
        P._analytics_boot()
        drain()
        self.assertEqual([b["event"] for b in self.spy.bodies], ["app_boot"],
                         "a steady-state boot must emit exactly one event")

    def test_boot_emits_no_heartbeat_thread(self):
        before = {t.name for t in threading.enumerate()}
        P._analytics_boot()
        drain()
        after = {t.name for t in threading.enumerate()}
        self.assertEqual(after - before, set(),
                         "analytics must not leave a background timer running")


class TestBucketsAndParsing(unittest.TestCase):

    def test_duration_buckets(self):
        cases = [(0, "unknown"), (None, "unknown"), (1, "<2m"), (119, "<2m"),
                 (120, "2-5m"), (299, "2-5m"), (300, "5-15m"), (899, "5-15m"),
                 (900, "15-40m"), (2399, "15-40m"), (2400, ">40m"), (9999, ">40m")]
        for secs, want in cases:
            self.assertEqual(P._analytics_duration_bucket(secs), want, secs)

    def test_chip_family_parsing(self):
        """'Apple M4 Max' -> 'M4 Max'. Live value on this Mac must parse."""
        got = P._analytics_chip_family()
        self.assertRegex(got, r"^(M\d+( (Pro|Max|Ultra))?|unknown|non-apple-silicon)$")

    def test_unrecognised_h3_tier_collapses_to_unknown(self):
        """Closed vocabulary: a tier key the panel does not recognise becomes
        "unknown" rather than riding through as free text. Every string we
        transmit has to be drawn from a set defined in the source, or the
        "no free text" promise is only true of the fields we remembered."""
        for bogus in ("5s", "not_a_tier_at_all", "", None):
            self.assertEqual(
                P._analytics_render_tier({"h3_tier": bogus}, "h3"), "unknown",
                f"unrecognised h3_tier {bogus!r} escaped the vocabulary")


class TestDocumentationParity(unittest.TestCase):
    """docs/ANALYTICS.md opens with "If the panel ever sends something that
    isn't listed here, that's a bug." app_installed was exactly that bug, for
    two releases, because nothing checked. This makes the promise executable:
    every event name the panel can capture must be named on that page."""

    def test_every_event_the_panel_fires_is_documented(self):
        doc = (REPO / "docs" / "ANALYTICS.md").read_text(encoding="utf-8")
        for name in sorted(P._ANALYTICS_EVENTS):
            self.assertIn(f"`{name}`", doc,
                          f"{name} is captured by the panel but docs/"
                          f"ANALYTICS.md never names it")

    def test_the_event_registry_covers_every_literal_call_site(self):
        """The registry replaced a regex over string literals, because the
        render call site stopped passing one when refusals got their own
        event name. The regex is kept as a cross-check in the other
        direction: any name still spelled literally at a call site has to
        be a member, so the registry cannot fall behind the code."""
        src = (REPO / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        fired = set(re.findall(r'_analytics_capture\(\s*"([a-z_]+)"', src))
        self.assertTrue(fired, "no literal event names found at all — the "
                               "cross-check has stopped checking anything")
        missing = fired - set(P._ANALYTICS_EVENTS)
        self.assertEqual(missing, set(),
                         f"captured but not in _ANALYTICS_EVENTS: {missing}")

    def test_the_render_path_can_only_produce_registry_names(self):
        """The three names the render call site can choose between are the
        reason the registry exists — drive all three and check."""
        seen = []
        orig = P._analytics_capture
        P._analytics_capture = lambda ev, props=None: seen.append(ev)
        try:
            base = {"params": {"mode": "t2v", "engine": "ltx",
                               "quality": "standard", "width": 640,
                               "height": 448, "frames": 73}}
            P._analytics_render_event(dict(base, status="done"))
            P._analytics_render_event(dict(base, status="failed",
                                           error="SIGSEGV in worker"))
            P._analytics_render_event(dict(base, status="failed",
                                           refused_reason="hardware_tier",
                                           error="…hardware tier…"))
        finally:
            P._analytics_capture = orig
        self.assertEqual(seen, ["render_completed", "render_failed",
                                "render_refused"])
        for name in seen:
            self.assertIn(name, P._ANALYTICS_EVENTS)


# --------------------------------------------------------------------------
# 5. Receiver directives — the things every event tells PostHog NOT to do
# --------------------------------------------------------------------------

class TestReceiverDirectives(AnalyticsTestCase):
    """The panel sends no location field. It also has to stop the receiver
    deriving one, which is a different promise and needs its own guard.

    Three $-prefixed keys ride on every payload: no person profile, no GeoIP
    enrichment, and an $ip the panel supplies itself so the connecting
    address is never written onto the stored event. All three are attached
    centrally in _analytics_post — which is exactly why this fires EVERY
    event type rather than a representative one. A refactor that built a
    payload anywhere else would drop all three at once, silently, and
    nothing else in this suite would notice."""

    def setUp(self):
        super().setUp()
        self.configure(analytics_key="phc_fake_key_for_tests",
                       analytics_install_reported=False)

    def fire_every_event(self) -> dict[str, dict]:
        """One of each event the panel can emit → {event: properties}."""
        packs = dict(P._analytics_pack_state())
        self.configure(analytics_last_packs=dict(packs, h3=not packs["h3"]))
        P._analytics_boot()      # app_installed + app_boot + pack_state_change
        P._analytics_render_event({
            "status": "done", "elapsed_sec": 120.0,
            "params": {"mode": "t2v", "engine": "ltx", "quality": "standard",
                       "width": 1216, "height": 704, "frames": 121}})
        P._analytics_render_event({
            "status": "failed", "error": "OOM during VAE decode",
            "elapsed_sec": 30.0,
            "params": {"mode": "i2v", "engine": "h3", "h3_tier": "hq_5s"}})
        # star_prompt has no boot or render path to ride in on — it is fired
        # straight from the /star-click handler, so it is captured directly
        # here with the same closed vocabulary that handler coerces `via` to.
        P._analytics_capture("star_prompt", {"via": "link"})
        # v4.9.7 events, fired the way their call sites fire them.
        P._analytics_feature("editor_open", "")
        P._analytics_capture("app_updated", {"from_version": "4.9.5", "to_version": "4.9.7"})
        P._analytics_capture("update_prompt", {"action": "later", "version": "4.9.7"})
        P._analytics_capture("broadcast_seen", {"version": "4.9.7"})
        P._analytics_capture("queue_paused_breaker", {"n_failed": 3, "queued": 2,
                                                       "error_class": "model_missing",
                                                       "version": "4.9.7"})
        # A refusal: the panel declining on purpose. Fired here for the same
        # reason as the rest — its payload has to carry the receiver
        # directives too, and it is the newest way to get an event out.
        P._analytics_render_event({
            "status": "failed", "refused_reason": "ingredients_generation",
            "error": "Ingredients needs the LTX-2.3 generation.",
            "elapsed_sec": 1.0,
            "params": {"mode": "ingredients", "engine": "ltx",
                       "quality": "standard"}})
        drain()
        seen = {b["event"]: b["properties"] for b in self.spy.bodies}
        # Coverage, read off the declared registry rather than trusted: if
        # someone adds an eighth event type, this test must be taught to fire
        # it instead of quietly checking seven out of eight.
        firable = set(P._ANALYTICS_EVENTS)
        self.assertEqual(sorted(firable - set(seen)), [],
                         "an event type exists that this test never fires, so "
                         "its payload goes unchecked — add it above")
        return seen

    def test_every_event_carries_every_receiver_directive(self):
        for event, props in self.fire_every_event().items():
            self.assertEqual(
                {k: props.get(k) for k in P._ANALYTICS_RECEIVER_DIRECTIVES},
                dict(P._ANALYTICS_RECEIVER_DIRECTIVES),
                f"{event} reached the wire without the directives intact")
            self.assertIs(props["$geoip_disable"], True,
                          f"{event} would be geolocated by the receiver")
            self.assertIs(props["$process_person_profile"], False,
                          f"{event} would build a person profile")

    def test_no_location_property_is_sent_or_invited(self):
        """We add no location field, and $geoip_disable is the only $geoip_*
        key that may appear — everything else in that namespace is something
        the receiver would have derived."""
        for event, props in self.fire_every_event().items():
            geo = sorted(k for k in props
                         if k.startswith("$geoip_") and k != "$geoip_disable")
            self.assertEqual(geo, [], f"{event} carried location data: {geo}")
        raw = self.spy.raw().lower()
        for word in ("country", "city", "latitude", "longitude", "timezone",
                     "time_zone", "subdivision", "locale", "continent"):
            self.assertNotIn(word, raw, f"a payload mentions {word!r}")

    def test_the_ip_placeholder_is_truthy_and_not_a_spoof_trigger(self):
        """Both ways of writing this so that it does nothing.

        PostHog's ingest fills properties.$ip from the socket only when the
        event did not bring one — `if (!properties['$ip'] && event.ip)`. Any
        falsy value (None, "", 0) is therefore not suppression, it is the
        default with extra steps, and the real address lands on the event
        anyway. Separately, the GeoIP transformation rewrites 127.0.0.1 and
        192.168.* to a real address in Sweden as a local-dev convenience, so
        a loopback placeholder would manufacture a location the day the
        disable flag got dropped. Hence: truthy, and neither of those."""
        ip = P.ANALYTICS_IP_PLACEHOLDER
        self.assertIsInstance(ip, str)
        self.assertTrue(ip, "a falsy $ip is silently replaced by the real one")
        self.assertNotEqual(ip, "127.0.0.1")
        self.assertFalse(ip.startswith("192.168."))
        P._analytics_capture("app_boot", {"version": "3.7.0"})
        drain()
        self.assertEqual(self.spy.bodies[0]["properties"]["$ip"], ip,
                         "the placeholder never reached the wire")

    def test_a_call_site_cannot_override_a_directive(self):
        """The directives are spread last for this reason. A props dict is
        built from job params in places this module does not own."""
        P._analytics_capture("app_boot", {"$geoip_disable": False,
                                          "$ip": "203.0.113.7",
                                          "$process_person_profile": True})
        drain()
        props = self.spy.bodies[0]["properties"]
        self.assertIs(props["$geoip_disable"], True)
        self.assertIs(props["$process_person_profile"], False)
        self.assertEqual(props["$ip"], P.ANALYTICS_IP_PLACEHOLDER)

    def test_the_local_mirror_records_only_our_own_properties(self):
        """The directives are transport, not data: state/usage-log.jsonl is
        the user's readable copy of what the panel MEANT, and padding it with
        receiver plumbing would make the page harder to check, not easier."""
        P._analytics_capture("app_boot", {"version": "3.7.0"})
        drain()
        props = self.log_lines()[0]["props"]
        self.assertEqual(sorted(k for k in props if k.startswith("$")), [])


# --------------------------------------------------------------------------
# 6. Local aggregation + the /stats/usage payload
# --------------------------------------------------------------------------

class TestUsageReport(AnalyticsTestCase):

    def seed(self, records):
        for rec in records:
            P._usage_log_append(rec)

    def test_local_aggregates(self):
        now = time.time()
        self.seed([
            {"event": "app_boot", "ts": now - 3600, "at": "x",
             "props": {"version": "3.4.1", "chip_family": "M4 Max", "ram_gb": 64}},
            {"event": "render_completed", "ts": now - 1800, "at": "x",
             "props": {"engine": "ltx"}},
            {"event": "render_completed", "ts": now - 1700, "at": "x",
             "props": {"engine": "h3"}},
            {"event": "render_failed", "ts": now - 1600, "at": "x",
             "props": {"engine": "ltx", "error_signature": "OOM during decode"}},
            {"event": "pack_state_change", "ts": now - 1500, "at": "x",
             "props": {"pack": "h3", "from": True, "to": False}},
        ])
        r = P._usage_local_report()
        self.assertEqual(r["source"], "local")
        self.assertEqual(r["tiles"]["renders_7d"], 3)
        self.assertAlmostEqual(r["tiles"]["h3_share_pct"], 33.3, places=1)
        self.assertAlmostEqual(r["tiles"]["error_rate_pct"], 33.3, places=1)
        self.assertEqual(r["top_errors"], [{"signature": "OOM during decode", "count": 1}])
        self.assertEqual(r["pack_flips"]["h3_lost"], 1)
        self.assertEqual(r["versions"], [{"version": "3.4.1", "count": 1}])
        self.assertEqual(r["chips"], [{"chip": "M4 Max", "count": 1}])

    def test_refusals_are_outside_every_render_number(self):
        """The whole point, expressed as arithmetic. Two completed, one
        failed, three refused: the error rate is 1-in-3, not 1-in-6, and
        `renders_7d` is 3, not 6. Getting this wrong is what made the
        published failure rate wrong in the first place."""
        now = time.time()
        self.seed([
            {"event": "render_completed", "ts": now - 1800, "at": "x",
             "props": {"engine": "ltx"}},
            {"event": "render_completed", "ts": now - 1700, "at": "x",
             "props": {"engine": "h3"}},
            {"event": "render_failed", "ts": now - 1600, "at": "x",
             "props": {"engine": "ltx", "error_signature": "OOM during decode"}},
            {"event": "render_refused", "ts": now - 1500, "at": "x",
             "props": {"engine": "ltx", "refusal": "ingredients_generation"}},
            {"event": "render_refused", "ts": now - 1400, "at": "x",
             "props": {"engine": "ltx", "refusal": "ingredients_generation"}},
            {"event": "render_refused", "ts": now - 1300, "at": "x",
             "props": {"engine": "h3", "refusal": "h3_ram"}},
        ])
        r = P._usage_local_report()
        self.assertEqual(r["tiles"]["renders_7d"], 3)
        self.assertAlmostEqual(r["tiles"]["error_rate_pct"], 33.3, places=1)
        # Not in the engine mix either — no engine ran.
        self.assertAlmostEqual(r["tiles"]["h3_share_pct"], 33.3, places=1)
        # Counted, loudly, in their own place.
        self.assertEqual(r["tiles"]["refusals_7d"], 3)
        self.assertEqual(r["top_refusals"], [
            {"refusal": "ingredients_generation", "count": 2},
            {"refusal": "h3_ram", "count": 1}])
        # And never in the error list, which is a crash leaderboard.
        self.assertEqual(r["top_errors"],
                         [{"signature": "OOM during decode", "count": 1}])

    def test_old_rows_fall_out_of_the_windows(self):
        old = time.time() - 40 * 86400
        self.seed([{"event": "render_completed", "ts": old, "at": "x",
                    "props": {"engine": "ltx"}}])
        r = P._usage_local_report()
        self.assertEqual(r["tiles"]["renders_7d"], 0)

    def test_empty_log_is_a_valid_report(self):
        r = P._usage_local_report()
        self.assertTrue(r["ok"])
        self.assertEqual(r["tiles"]["renders_7d"], 0)
        self.assertIsNone(r["tiles"]["h3_share_pct"])
        self.assertEqual(r["top_errors"], [])

    def test_corrupt_lines_are_skipped_not_fatal(self):
        with P.USAGE_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write("{not json at all\n")
            fh.write('{"event":"app_boot","ts":' + str(time.time()) + ',"props":{}}\n')
            fh.write('{"truncated":\n')
        self.assertEqual(len(P._usage_log_read()), 1)

    def test_log_rotation_caps_the_file(self):
        original = P.USAGE_LOG_MAX_BYTES
        P.USAGE_LOG_MAX_BYTES = 4096
        try:
            for i in range(400):
                P._usage_log_append({"event": "app_boot", "ts": time.time(),
                                     "at": "x", "props": {"i": i}})
            self.assertLess(P.USAGE_LOG_FILE.stat().st_size, 4096 * 2)
            self.assertGreater(len(P._usage_log_read()), 0)
        finally:
            P.USAGE_LOG_MAX_BYTES = original


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"\nstate dir used (safe to delete): {_TMP_STATE}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())


class SourceAndFeatureVocabularies(unittest.TestCase):
    """v4.9.7: the job-origin field and feature events are closed sets."""

    def test_source_normalises_to_the_closed_set(self):
        self.assertEqual(P._analytics_source({}, {}), "form")
        self.assertEqual(P._analytics_source({"source": "panel.image_studio"}, {}), "image_studio")
        self.assertEqual(P._analytics_source({"source": "characters"}, {}), "characters")
        self.assertEqual(P._analytics_source({"source": "storyboard"}, {}), "storyboard")
        self.assertEqual(P._analytics_source({"source": "retry"}, {}), "retry")
        self.assertEqual(P._analytics_source({"board_id": "b1"}, {}), "storyboard")
        self.assertEqual(P._analytics_source({"source": "/Users/x/secret"}, {}), "unknown")
        for v in P._ANALYTICS_SOURCES:
            self.assertRegex(v, r"^[a-z_]+$")

    def test_feature_event_drops_unknown_names_and_scrubs_detail(self):
        sent = []
        with unittest.mock.patch.object(P, "_analytics_capture", lambda e, pr=None: sent.append((e, pr))):
            P._analytics_feature("not_a_feature", "x")
            P._analytics_feature("editor_export", "NLE Premiere/../etc")
        self.assertEqual(len(sent), 1)
        ev, props = sent[0]
        self.assertEqual(ev, "feature_used")
        self.assertEqual(props["feature"], "editor_export")
        self.assertEqual(props["detail"], "nlepremiere..etc")
        self.assertNotIn("/", props["detail"])

    def test_render_event_carries_source(self):
        job = {"status": "done", "elapsed_sec": 30,
               "params": {"engine": "ltx", "mode": "t2v", "source": "storyboard",
                          "width": 768, "height": 432, "frames": 49, "quality": "balanced"}}
        sent = []
        with unittest.mock.patch.object(P, "_analytics_capture", lambda e, pr=None: sent.append((e, pr))):
            P._analytics_render_event(job)
        self.assertTrue(sent, "no event")
        self.assertEqual(sent[-1][1].get("source"), "storyboard")
