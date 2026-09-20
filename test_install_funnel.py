"""The install funnel: `install_step` and `update_outcome`.

Why they exist (fleet, 14 days to 2026-09-19): 577 installs reported in and
only 44.5% ever rendered — 278 booted once and vanished. Between
`app_installed` and the first render the panel said nothing, so "they tried and
it was broken" and "they looked and left" were the same shape in the data.
And 23 of 139 people who pressed Update never ran a new version, which
`app_updated` cannot show by construction: a failed update emits nothing.

The invariants here are the ones that keep this from becoming a heartbeat or a
free-text field.
"""
import unittest

import mlx_ltx_panel as P


class _Capture:
    """Swap the capture function for a list."""

    def __enter__(self):
        self.seen = []
        self.orig = P._analytics_capture
        P._analytics_capture = lambda ev, props=None: self.seen.append((ev, dict(props or {})))
        return self

    def __exit__(self, *a):
        P._analytics_capture = self.orig

    def events(self, name):
        return [p for e, p in self.seen if e == name]


class _FreshInstall:
    """An install that has taken no funnel steps yet."""

    def __enter__(self):
        self.orig = P.get_settings
        self.store = {"analytics_install_steps": {}}
        P.get_settings = lambda: dict(self.store)
        self.orig_set = P._settings_set_internal
        P._settings_set_internal = lambda **kv: self.store.update(kv)
        return self

    def __exit__(self, *a):
        P.get_settings = self.orig
        P._settings_set_internal = self.orig_set


class OncePerInstall(unittest.TestCase):

    def test_a_step_reports_once(self):
        with _FreshInstall(), _Capture() as cap:
            for _ in range(5):
                P._analytics_install_step("first_queue", "started")
            self.assertEqual(len(cap.events("install_step")), 1)

    def test_the_same_step_reports_again_when_the_answer_changes(self):
        """The deliberate exception, and the most useful row on this event:
        a broken environment that gets repaired."""
        with _FreshInstall(), _Capture() as cap:
            P._analytics_install_step("engine_env", "failed", "venv_broken")
            P._analytics_install_step("engine_env", "failed", "venv_broken")
            P._analytics_install_step("engine_env", "ok")
            got = [(e["outcome"], e.get("error_class")) for e in cap.events("install_step")]
            self.assertEqual(got, [("failed", "venv_broken"), ("ok", None)])

    def test_every_step_together_is_a_handful_of_events_for_all_time(self):
        """Volume is the whole reason this is safe to ship. Four steps, and
        the page promises no heartbeats."""
        with _FreshInstall(), _Capture() as cap:
            for step in P._INSTALL_STEPS:
                for _ in range(10):
                    P._analytics_install_step(step, "ok")
            self.assertEqual(len(cap.events("install_step")), len(P._INSTALL_STEPS))
            self.assertLessEqual(len(P._INSTALL_STEPS), 6)


class ClosedVocabulary(unittest.TestCase):

    def test_an_unknown_step_never_reaches_the_wire(self):
        with _FreshInstall(), _Capture() as cap:
            P._analytics_install_step("rummaging_about", "ok")
            self.assertEqual(cap.events("install_step"), [])

    def test_an_unknown_outcome_never_reaches_the_wire(self):
        with _FreshInstall(), _Capture() as cap:
            P._analytics_install_step("first_boot", "sort of")
            self.assertEqual(cap.events("install_step"), [])

    def test_an_unknown_update_stage_never_reaches_the_wire(self):
        with _Capture() as cap:
            P._analytics_update_outcome("wandered_off", "ok")
            self.assertEqual(cap.events("update_outcome"), [])

    def test_no_signature_or_fingerprint_rides_along(self):
        """Stated as a rule in docs/ANALYTICS.md: an install-time error line
        is the likeliest place in the product for a username-bearing path to
        survive scrubbing, and the class answers the question on its own."""
        with _FreshInstall(), _Capture() as cap:
            P._analytics_install_step("engine_env", "failed", "venv_broken")
            props = cap.events("install_step")[0]
            self.assertNotIn("error_signature", props)
            self.assertNotIn("error_fingerprint", props)
            self.assertEqual(sorted(props),
                             ["error_class", "outcome", "step", "version"])


class DidTheUpdateLand(unittest.TestCase):

    def test_a_new_version_after_the_press_is_ok(self):
        with _Capture() as cap:
            P._analytics_update_outcome("running_new", "ok", "4.15.0")
            self.assertEqual(cap.events("update_outcome")[0]["outcome"], "ok")

    def test_the_same_version_after_the_press_is_the_hole_we_are_measuring(self):
        with _Capture() as cap:
            P._analytics_update_outcome("restart_pending", "failed", "4.15.0")
            ev = cap.events("update_outcome")[0]
            self.assertEqual(ev["stage"], "restart_pending")
            self.assertEqual(ev["from_version"], "4.15.0")

    def test_the_press_is_recorded_by_the_ui_route(self):
        """The marker is written where the click arrives; without it the next
        boot has nothing to compare against."""
        from pathlib import Path
        src = Path(__file__).with_name("panel") / "routes_meta.py"
        text = src.read_text()
        self.assertIn("analytics_update_pressed", text)
        self.assertIn('("update_now", "banner_update")', text)


class BothEventsAreDocumented(unittest.TestCase):
    """The page is the specification, and the dry-run suite enforces parity —
    this is the same check from the other direction, so a rename here fails
    close to the code that renamed it."""

    def test_the_vocabularies_appear_on_the_page(self):
        from pathlib import Path
        doc = (Path(__file__).with_name("docs") / "ANALYTICS.md").read_text()
        for word in P._INSTALL_STEPS + P._INSTALL_OUTCOMES + P._UPDATE_STAGES:
            self.assertIn(f"`{word}`", doc, f"{word} is undocumented")


if __name__ == "__main__":
    unittest.main(verbosity=2)
