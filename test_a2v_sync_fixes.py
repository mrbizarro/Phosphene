"""The a2v lip-sync fix — audio guidance defaults to the lane's own value.

Measured on one shot, at one seed, with the image, the audio window and the
prompt held fixed: the per-second correlation between the mouth's aperture and
the vocal-band energy of the soundtrack went **-0.065 -> +0.128** by changing
the audio-guidance default alone. -0.065 is worse than the same clip scored
against deliberately WRONG audio, i.e. the old default was not lip-syncing at
all on the Q8 lane.

Structural assertions (reading a source file rather than running a render) are
used where the alternative is a GPU: the a2v branch of `run_job_inner` needs a
weights pack, a helper subprocess and twenty minutes. They are written against
the exact line that decides the behaviour, never against prose.
"""

import unittest
from pathlib import Path

import mlx_ltx_panel as P

PANEL_SRC = Path(__file__).with_name("mlx_ltx_panel.py").read_text()
HELPER_SRC = Path(__file__).with_name("mlx_warm_helper.py").read_text()
INDEX_HTML = Path(__file__).with_name("webapp") / "index.html"
CHARACTERS_JS = Path(__file__).with_name("webapp") / "js" / "characters.js"


def _helper_functions(*names):
    """Exec the named top-level functions of mlx_warm_helper.py in isolation."""
    import ast
    tree = ast.parse(HELPER_SRC)
    defs = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in names]
    assert sorted(d.name for d in defs) == sorted(names), names
    ns: dict = {}
    exec(compile(ast.Module(body=defs, type_ignores=[]), "mlx_warm_helper.py",
                 "exec"), ns)
    return ns


# ---------------------------------------------------------------------------
# 1. THE LANE-AWARE AUDIO-GUIDANCE DEFAULT
# ---------------------------------------------------------------------------
class LaneAwareAudioScale(unittest.TestCase):
    """1.0 means "audio fully on" on Q4 and "audio exactly off" on Q8."""

    def test_the_lane_defaults_are_the_engines_own(self):
        # Q8: the vendored a2vid_two_stage hardcodes modality_scale=3.0.
        # Q4: a2vid_distilled multiplies the audio tokens, so 1.0 is identity.
        self.assertEqual(P.A2V_AUDIO_SCALE_ON["q8"], 3.0)
        self.assertEqual(P.A2V_AUDIO_SCALE_ON["q4"], 1.0)

    def test_a_q8_job_with_no_scale_gets_the_lanes_on_value(self):
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                self.assertEqual(P.a2v_audio_scale(raw, q8=True), 3.0)
                self.assertEqual(P.a2v_audio_scale(raw, q8=False), 1.0)

    def test_an_explicit_value_passes_through_unchanged_on_both_lanes(self):
        for raw in ("0.5", "1.0", 2.5, "5.0"):
            with self.subTest(raw=raw):
                self.assertEqual(P.a2v_audio_scale(raw, q8=True), float(raw))
                self.assertEqual(P.a2v_audio_scale(raw, q8=False), float(raw))

    def test_garbage_and_nonpositive_fall_back_to_the_lane(self):
        # Same rule the helper applies one level down: <= 0 means "the
        # engine's own default", never a negative multiplier on audio tokens.
        for raw in ("banana", "0", 0, -1, "-3.0", [], {}, "nan", "inf", "-inf"):
            with self.subTest(raw=raw):
                self.assertEqual(P.a2v_audio_scale(raw, q8=True), 3.0)
                self.assertEqual(P.a2v_audio_scale(raw, q8=False), 1.0)

    def test_make_job_no_longer_fabricates_a_number(self):
        job = P.make_job({"mode": "a2v", "prompt": "a face", "audio": "/x.wav"})
        self.assertEqual(job["params"]["audio_conditioning_scale"], "")

    def test_make_job_keeps_a_value_the_caller_sent(self):
        job = P.make_job({"mode": "a2v", "prompt": "a face", "audio": "/x.wav",
                          "audio_conditioning_scale": "2.5"})
        self.assertEqual(job["params"]["audio_conditioning_scale"], "2.5")

    def test_the_dispatch_resolves_against_the_lane_it_is_about_to_run(self):
        # The lane is `uses_q8`, which also carries the Q8->Q4 fallback when
        # the Q8 surface is missing. Resolving against anything else would put
        # a 3.0 meant for the guider onto the distilled lane's token multiplier.
        self.assertIn("a2v_audio_scale(\n                a2v_requested_scale(p), q8=uses_q8)",
                      PANEL_SRC)

    def test_the_fabricated_default_cannot_come_back(self):
        self.assertNotIn('float(f("audio_conditioning_scale"', PANEL_SRC)
        self.assertNotIn('float(p.get("audio_conditioning_scale", 1.0))', PANEL_SRC)

    def test_the_helper_still_treats_blank_as_the_engine_default(self):
        # A hand-written job spec that names nothing must stay safe on both
        # lanes even though the panel now always resolves before sending.
        # The helper cannot be imported (its main loop reads stdin at module
        # level), so the two parsers are lifted out of the source and RUN.
        ns = _helper_functions("_a2v_distilled_scale_value",
                               "_a2v_modality_scale_value")
        q4 = ns["_a2v_distilled_scale_value"]
        q8 = ns["_a2v_modality_scale_value"]
        for raw in (None, "", "   ", "0", 0, -2, "-1.5", "banana"):
            with self.subTest(lane="q4", raw=raw):
                self.assertEqual(q4(raw), 1.0)
            with self.subTest(lane="q8", raw=raw):
                self.assertIsNone(q8(raw))      # None = the engine's own 3.0
        for raw, want in (("2.5", 2.5), (0.7, 0.7), ("3", 3.0)):
            with self.subTest(explicit=raw):
                self.assertEqual(q4(raw), want)
                self.assertEqual(q8(raw), want)
        self.assertIn("ms = 3.0 if ms is None else float(ms)", HELPER_SRC)
        # And the Q4 branch really calls the guarded parser.
        self.assertIn("audio_conditioning_scale=_a2v_distilled_scale_value(",
                      HELPER_SRC)

    def test_the_sidecar_records_the_number_the_engine_ran_with(self):
        # `params` keeps the caller's blank (so a re-run resolves against its
        # own lane again); the resolved value is recorded beside it, so a clip
        # says which default it was rendered at.
        self.assertIn('"audio_conditioning_scale_used":\n'
                      '                a2v_params["audio_conditioning_scale"]',
                      PANEL_SRC)

    def test_the_slider_starts_on_auto_and_sends_nothing(self):
        html = INDEX_HTML.read_text()
        self.assertIn('id="audioConditioningScale"', html)
        self.assertIn('data-auto="1"', html)
        self.assertIn('class="range-val">Auto<', html)
        js = CHARACTERS_JS.read_text()
        # The field is set only inside the guard, so an untouched control
        # cannot send a number the panel would then treat as deliberate.
        self.assertIn("if (audioConditioningScale !== null", js)
        self.assertIn("(acsEl && !acsEl.dataset.auto)", js)



class JobsSavedBeforeTheFix(unittest.TestCase):
    """A job queued or finished under 4.15.1 carries the old make_job's float
    1.0 on every job. It survives Update in the saved queue and /queue/retry
    copies it verbatim; reading it as a deliberate 1.0 would keep Q8 audio
    guidance OFF for exactly the renders this fix is for."""

    def test_a_legacy_float_one_is_the_old_default_not_a_choice(self):
        req = P.a2v_requested_scale({"audio_conditioning_scale": 1.0})
        self.assertEqual(req, "")
        self.assertEqual(P.a2v_audio_scale(req, q8=True), 3.0)
        self.assertEqual(P.a2v_audio_scale(req, q8=False), 1.0)

    def test_a_legacy_float_that_was_dragged_is_kept(self):
        for raw in (2.5, 0.5, 4.0):
            with self.subTest(raw=raw):
                req = P.a2v_requested_scale({"audio_conditioning_scale": raw})
                self.assertEqual(P.a2v_audio_scale(req, q8=True), raw)

    def test_a_new_job_that_sends_one_point_oh_keeps_it(self):
        # Today's make_job stores what the form sent, as a STRING, so a user
        # who drags the slider to 1.0 on purpose gets 1.0.
        job = P.make_job({"mode": "a2v", "prompt": "a face", "audio": "/x.wav",
                          "audio_conditioning_scale": "1.0"})
        req = P.a2v_requested_scale(job["params"])
        self.assertEqual(P.a2v_audio_scale(req, q8=True), 1.0)

    def test_a_new_auto_job_and_a_missing_key_both_resolve_to_the_lane(self):
        job = P.make_job({"mode": "a2v", "prompt": "a face", "audio": "/x.wav"})
        for params in (job["params"], {}):
            with self.subTest(params=params.get("audio_conditioning_scale")):
                req = P.a2v_requested_scale(params)
                self.assertEqual(P.a2v_audio_scale(req, q8=True), 3.0)

    def test_make_job_never_stores_a_float(self):
        # The legacy rule above keys on the TYPE; it is only sound while no
        # current path stores a float here.
        for form in ({}, {"audio_conditioning_scale": "2.0"},
                     {"audio_conditioning_scale": "1.0"}):
            job = P.make_job({"mode": "a2v", "prompt": "p", "audio": "/x.wav",
                              **form})
            self.assertIsInstance(job["params"]["audio_conditioning_scale"], str)

    def test_the_run_log_calls_a_legacy_default_a_default(self):
        self.assertIn('_scale_note = "" if str(a2v_requested_scale(p) or "").strip()',
                      PANEL_SRC)



class TheDispatchSendsTheLaneValue(unittest.TestCase):
    """Behavioural, not structural: drive the REAL a2v branch of run_job_inner
    with a stub helper and read the spec it would have sent. No GPU, no weights,
    no subprocess — the helper, the pack preflight and the sidecar writer are
    the only things replaced, and all three are restored afterwards."""

    def _dispatch(self, acs, *, q8):
        import tempfile
        import time
        from unittest import mock

        tmp = Path(tempfile.mkdtemp(prefix="a2v-dispatch-"))
        wav = tmp / "a.wav"
        wav.write_bytes(b"RIFF0000WAVE")
        sent, sidecars = [], []

        class _Helper:
            ready_info: dict = {}

            def is_alive(self):
                return True

            def kill(self, *a, **k):
                pass

            def run(self, spec):
                sent.append(spec)
                Path(spec["params"]["output_path"]).write_bytes(b"x")
                return {"seed_used": 1, "elapsed_sec": 0.1}

        params = P.make_job({"mode": "a2v", "prompt": "a singer",
                             "audio": str(wav), "width": "512",
                             "height": "288", "frames": "49"})["params"]
        if acs is not None:
            params["audio_conditioning_scale"] = acs
        caps = dict(P.SYSTEM_CAPS, allows_q8=q8)
        with mock.patch.object(P, "HELPER", _Helper()), \
             mock.patch.object(P, "OUTPUT", tmp), \
             mock.patch.object(P, "SYSTEM_CAPS", caps), \
             mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
             mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
             mock.patch.object(P, "write_sidecar",
                               lambda _p, data: sidecars.append(data)):
            P.run_job_inner({"id": "t", "params": params,
                             "started_ts": time.time()})
        self.assertEqual(len(sent), 1)
        return (sent[0]["action"], sent[0]["params"]["audio_conditioning_scale"],
                sidecars[-1]["audio_conditioning_scale_used"])

    def test_the_matrix(self):
        cases = (
            # (what the job carries, Q8 lane result, Q4 lane result)
            (None,  3.0, 1.0),   # new job, slider on Auto (field not sent)
            ("1.0", 1.0, 1.0),   # new job, slider dragged to 1.0 on purpose
            ("2.5", 2.5, 2.5),   # new job, explicit value
            (1.0,   3.0, 1.0),   # saved by 4.15.1: the old fabricated default
            (2.2,   2.2, 2.2),   # saved by 4.15.1 after a real drag
        )
        for acs, want_q8, want_q4 in cases:
            with self.subTest(acs=acs, lane="q8"):
                self.assertEqual(self._dispatch(acs, q8=True),
                                 ("generate_a2v", want_q8, want_q8))
            with self.subTest(acs=acs, lane="q4"):
                self.assertEqual(self._dispatch(acs, q8=False),
                                 ("generate_a2v_distilled", want_q4, want_q4))


if __name__ == "__main__":
    unittest.main()
