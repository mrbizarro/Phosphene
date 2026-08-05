#!/usr/bin/env python3
"""Dry-run tests for the panel's anonymous usage analytics.

    python3 scripts/test_analytics_dryrun.py

No network, no panel, no renders: the whole suite runs against an isolated
temp state dir with `urllib.request.urlopen` replaced by a spy, so it can be
run on a machine that is mid-render without touching anything.

What it is actually here to prove, in priority order:

  1. NOTHING FIRES WITH NO KEY. The public tree ships ANALYTICS_KEY_DEFAULT
     = "", and that must mean zero sockets — not "a request that fails".
  2. NOTHING LEAKS. No prompt, path, filename or media string may appear in
     any outgoing payload, including via the one free-text field
     (error_signature) and including when a caller is careless.
  3. THE SCHEMA IS THE SCHEMA. Each event's property set is asserted
     field-by-field against docs/ANALYTICS.md, so the doc can't silently
     drift from the code.
  4. IT CANNOT BREAK A RENDER. Capture never raises, even on garbage input
     and even when the transport blows up.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
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
    """Common fixture: clean state dir, spy installed, analytics ON, no key."""

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
# 1. Inert without a key
# --------------------------------------------------------------------------

class TestInertWithoutKey(AnalyticsTestCase):

    def test_shipped_default_key_is_empty(self):
        """The public tree must never carry a real key."""
        self.assertEqual(P.ANALYTICS_KEY_DEFAULT, "")

    def test_no_key_means_no_socket(self):
        P._analytics_capture("app_boot", {"version": "3.4.1"})
        drain()
        self.assertEqual(self.spy.calls, [],
                         "analytics opened a socket with no key configured")

    def test_no_key_still_writes_the_local_mirror(self):
        """The local log is the always-on debugging view; it does not
        depend on a PostHog key."""
        P._analytics_capture("app_boot", {"version": "3.4.1"})
        drain()
        rows = self.log_lines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "app_boot")

    def test_disabled_writes_nothing_anywhere(self):
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
        for body in self.spy.bodies:
            if body["event"] == event:
                p = dict(body["properties"])
                p.pop("$process_person_profile", None)
                return p
        self.fail(f"no {event} event was captured")

    def test_app_boot_fields(self):
        P._analytics_boot()
        drain()
        p = self.props_of("app_boot")
        self.assertEqual(set(p), {
            "version", "os_version", "chip_family", "ram_gb", "cap_tier",
            "packs", "h3_chain_supported"})
        self.assertEqual(set(p["packs"]), {"h3", "sharp", "q8", "qwen"})
        for v in p["packs"].values():
            self.assertIsInstance(v, bool)
        self.assertIsInstance(p["ram_gb"], int)
        self.assertIn(p["cap_tier"], ("q4", "q8"))
        # os_version is major.minor only — no patch level.
        self.assertLessEqual(len(p["os_version"].split(".")), 2)

    def test_render_completed_fields(self):
        P._analytics_render_event({
            "status": "done", "elapsed_sec": 340.0,
            "params": {"mode": "t2v", "engine": "ltx", "quality": "standard",
                       "width": 1216, "height": 704, "frames": 121},
        })
        drain()
        p = self.props_of("render_completed")
        self.assertEqual(set(p), {
            "engine", "mode", "tier", "duration_bucket", "resolution", "frames"})
        self.assertEqual(p["resolution"], "1216x704")
        self.assertEqual(p["duration_bucket"], "5-15m")
        self.assertEqual(p["tier"], "standard")

    def test_render_failed_adds_only_error_signature(self):
        P._analytics_render_event({
            "status": "failed", "error": "OOM during VAE decode",
            "elapsed_sec": 61.0,
            "params": {"mode": "i2v", "engine": "h3", "h3_tier": "5s",
                       "width": 1280, "height": 720, "frames": 90},
        })
        drain()
        p = self.props_of("render_failed")
        self.assertEqual(set(p), {
            "engine", "mode", "tier", "duration_bucket", "resolution",
            "frames", "error_signature"})
        self.assertEqual(p["tier"], "5s", "H3 jobs report h3_tier as tier")
        self.assertEqual(p["duration_bucket"], "<2m")

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


# --------------------------------------------------------------------------
# 5. Local aggregation + the /stats/usage payload
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
