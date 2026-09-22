"""The A2V "Audio conditioning strength" slider — routed to a knob that exists.

The panel shipped this slider (0.5-5.0, "Higher = stronger audio adhesion")
from the day A2V landed, and on the Q8 two-stage path it did NOTHING for its
whole life: the vendored `A2VidPipelineTwoStage.generate_and_save()` has no
`audio_conditioning_scale` parameter, so `_filter_unsupported_kwargs` dropped
the kwarg silently on every render. The fix routes the slider to the knob
that does exist — `modality_scale` on the stage-1 video guider, which the
vendored `_denoise_stage1` hardcodes to 3.0 — via a helper-side monkeypatch.

The Q4 distilled path is the OPPOSITE case and this suite pins both sides:
`A2VidDistilledPipeline` (local a2vid_distilled.py) natively accepts
`audio_conditioning_scale` (it amplifies the audio tokens before the DiT) and
its denoise is plain `denoise_loop` — no guiders, so no `modality_scale`
exists there. The first draft of the fix removed that working native kwarg
and staged the (inert) guider patch with a `patched=True` log line. This
suite exists so that class of confusion cannot ship again.

House rules honoured here:
- Helper functions are read out of `mlx_warm_helper.py` with `ast`, never
  imported — the module body ends in a blocking stdin read.
- The vendored original is asserted from its FILE, not from the class —
  installing the patch in-process replaces the class attribute, and a test
  that inspects the patched method thinks upstream still matches forever.
- The guider params object driven through the plumbing is the REAL
  `MultiModalGuiderParams`, not a stub.
"""

import ast
import importlib.util
import inspect
import unittest
from pathlib import Path

HELPER = Path(__file__).with_name("mlx_warm_helper.py")

_WANTED = {
    "_A2V_STATE",
    "_a2v_modality_scale_value",
    "_a2v_stage1_guider_params",
    "_install_a2v_modality_patch",
}

# The vendored method's exact positional shape. The replacement must match it
# name-for-name, default-for-default: pipelines call it positionally.
_STAGE1_ARGS = [
    "self", "x0_model", "video_state", "audio_state", "video_embeds",
    "audio_embeds", "neg_video_embeds", "neg_audio_embeds", "sigmas",
    "cfg_scale", "stg_scale",
]
_STAGE1_DEFAULTS = {"cfg_scale": 3.0, "stg_scale": 1.0}


def _extract_helpers() -> dict:
    """Exec ONLY the a2v adhesion state + functions, out of the real file."""
    tree = ast.parse(HELPER.read_text())
    picked, seen = [], set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & _WANTED:
                picked.append(node)
                seen |= names & _WANTED
        elif isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            picked.append(node)
            seen.add(node.name)
    missing = _WANTED - seen
    if missing:
        raise AssertionError(
            f"mlx_warm_helper.py no longer defines: {sorted(missing)}")
    ns: dict = {"__name__": "a2v_adhesion_extract"}
    exec(compile(ast.Module(body=picked, type_ignores=[]), str(HELPER), "exec"), ns)
    return ns


def _vendored_source() -> str:
    """The a2vid_two_stage FILE — immune to in-process monkeypatching."""
    spec = importlib.util.find_spec("ltx_pipelines_mlx.a2vid_two_stage")
    if spec is None or not spec.origin:
        raise AssertionError("ltx_pipelines_mlx.a2vid_two_stage is not installed")
    return Path(spec.origin).read_text()


def _vendored_stage1_def() -> ast.FunctionDef:
    tree = ast.parse(_vendored_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "A2VidPipelineTwoStage":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "_denoise_stage1":
                    return item
    raise AssertionError(
        "A2VidPipelineTwoStage._denoise_stage1 no longer exists upstream — "
        "the adhesion patch targets a method that is gone")


class SliderParse(unittest.TestCase):
    """'14.0' -> 14.0; everything that means 'unset' -> None."""

    def setUp(self):
        self.parse = _extract_helpers()["_a2v_modality_scale_value"]

    def test_table(self):
        for raw, want in (
            ("14.0", 14.0), ("9", 9.0), ("3.5", 3.5), (5, 5.0), (0.5, 0.5),
            ("", None), (None, None), ("0", None), (0, None), (0.0, None),
            ("-2", None), ("garbage", None), ([], None),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self.parse(raw), want)


class UpstreamContract(unittest.TestCase):
    """The vendored engine still has the shape the patch assumes."""

    def test_q8_generate_and_save_still_lacks_the_kwarg(self):
        # The day this fails is a GOOD day: upstream grew a native audio
        # conditioning parameter. Route the slider to it and delete
        # _install_a2v_modality_patch — do not keep both.
        from ltx_pipelines_mlx.a2vid_two_stage import A2VidPipelineTwoStage
        sig = inspect.signature(A2VidPipelineTwoStage.generate_and_save)
        self.assertNotIn(
            "audio_conditioning_scale", sig.parameters,
            "Upstream generate_and_save now accepts audio_conditioning_scale "
            "natively — pass it through and remove the modality_scale patch.")

    def test_vendored_stage1_signature_matches_the_replacement(self):
        node = _vendored_stage1_def()
        args = [a.arg for a in node.args.args]
        self.assertEqual(args, _STAGE1_ARGS,
                         "Upstream _denoise_stage1 changed shape — the "
                         "replacement in mlx_warm_helper.py must be re-mirrored")
        defaults = {
            a.arg: d.value
            for a, d in zip(args[-len(node.args.defaults):] and
                            node.args.args[-len(node.args.defaults):],
                            node.args.defaults)
            if isinstance(d, ast.Constant)
        }
        self.assertEqual(defaults, _STAGE1_DEFAULTS)

    def test_vendored_stage1_still_hardcodes_3_0(self):
        # The patch exists BECAUSE upstream hardcodes modality_scale=3.0 on
        # the stage-1 video guider. If that line changes, the replacement's
        # None -> 3.0 fallback no longer reproduces upstream behaviour.
        src = ast.get_source_segment(_vendored_source(), _vendored_stage1_def())
        self.assertIn("modality_scale=3.0", src)

    def test_guider_params_accepts_what_we_pass(self):
        from ltx_core_mlx.components.guiders import MultiModalGuiderParams
        gp = MultiModalGuiderParams(cfg_scale=2.0, stg_scale=1.0,
                                    rescale_scale=0.7, modality_scale=14.0,
                                    stg_blocks=[28])
        self.assertEqual(gp.modality_scale, 14.0)

    def test_guided_denoise_loop_accepts_our_kwargs(self):
        from ltx_pipelines_mlx.utils.samplers import guided_denoise_loop
        params = inspect.signature(guided_denoise_loop).parameters
        for name in ("model", "video_state", "audio_state", "video_text_embeds",
                     "audio_text_embeds", "video_guider_factory",
                     "audio_guider_factory", "sigmas"):
            self.assertIn(name, params)

    def test_install_succeeds_and_is_idempotent(self):
        ns = _extract_helpers()
        install = ns["_install_a2v_modality_patch"]
        self.assertTrue(install(), "patch failed to install against the venv")
        from ltx_pipelines_mlx.a2vid_two_stage import A2VidPipelineTwoStage
        self.assertTrue(A2VidPipelineTwoStage._phos_modality_patched)
        first = A2VidPipelineTwoStage._denoise_stage1
        self.assertTrue(install())
        self.assertIs(A2VidPipelineTwoStage._denoise_stage1, first,
                      "second install re-wrapped the method")

    def test_factories_are_built_before_the_flush(self):
        # Upstream builds both guider factories and THEN calls
        # _pre_denoise_flush; the flush frees memory and that ordering is part
        # of its contract. The replacement must keep it.
        src = HELPER.read_text()
        tree = ast.parse(src)
        block = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and \
                    node.name == "_install_a2v_modality_patch":
                block = ast.get_source_segment(src, node)
        self.assertIsNotNone(block)
        self.assertLess(block.index("_mk("), block.index("_pre_denoise_flush("))


class ValuePlumbing(unittest.TestCase):
    """The slider value reaches the real MultiModalGuiderParams object."""

    def setUp(self):
        self.ns = _extract_helpers()
        from ltx_core_mlx.components.guiders import MultiModalGuiderParams
        self.gp_cls = MultiModalGuiderParams

    def _params(self, staged, cfg_scale=3.0, stg_scale=1.0):
        self.ns["_A2V_STATE"]["modality_scale"] = staged
        return self.ns["_a2v_stage1_guider_params"](self.gp_cls,
                                                    cfg_scale, stg_scale)

    def test_staged_value_lands_on_the_video_guider(self):
        for staged in (0.5, 3.0, 9.0, 14.0):
            with self.subTest(staged=staged):
                video_gp, _ = self._params(staged)
                self.assertEqual(video_gp.modality_scale, staged)

    def test_unset_reproduces_upstream_exactly(self):
        video_gp, _ = self._params(None)
        self.assertEqual(video_gp.modality_scale, 3.0)

    def test_cfg_and_stg_pass_through(self):
        video_gp, _ = self._params(9.0, cfg_scale=2.5, stg_scale=0.5)
        self.assertEqual(video_gp.cfg_scale, 2.5)
        self.assertEqual(video_gp.stg_scale, 0.5)
        self.assertEqual(video_gp.rescale_scale, 0.7)
        self.assertEqual(video_gp.stg_blocks, [28])

    def test_audio_guider_stays_bare_default(self):
        # Stage 1 freezes audio; upstream gives it a bare params object. The
        # slider must never leak onto the audio guider.
        _, audio_gp = self._params(14.0)
        bare = self.gp_cls()
        self.assertEqual(audio_gp.modality_scale, bare.modality_scale)
        self.assertEqual(audio_gp.cfg_scale, bare.cfg_scale)


class DistilledPathStaysNative(unittest.TestCase):
    """Q4 distilled has its OWN knob and no guiders — leave it alone."""

    def test_distilled_accepts_the_kwarg_natively(self):
        from a2vid_distilled import A2VidDistilledPipeline
        sig = inspect.signature(A2VidDistilledPipeline.generate_and_save)
        self.assertIn("audio_conditioning_scale", sig.parameters)
        self.assertEqual(
            sig.parameters["audio_conditioning_scale"].default, 1.0)

    def test_distilled_never_reaches_the_patched_method(self):
        # The fact that makes the guider patch inert on this path. If the
        # distilled pipeline ever starts calling _denoise_stage1 or a guided
        # loop, the helper's distilled branch needs the modality routing too.
        src = Path(__file__).with_name("a2vid_distilled.py").read_text()
        self.assertNotIn("_denoise_stage1", src)
        self.assertNotIn("guided_denoise_loop", src)

    def test_helper_stages_modality_scale_on_exactly_one_branch(self):
        # The Q8 branch stages the patch; the distilled branch passes the
        # native kwarg. A second staging site means someone re-attached the
        # inert patch to the distilled path (the first-draft mistake).
        src = HELPER.read_text()
        self.assertEqual(
            src.count('_A2V_STATE["modality_scale"] = _a2v_modality_scale_value('),
            1)
        # The distilled branch parses the value with its OWN <= 0 guard
        # (`_a2v_distilled_scale_value`) rather than the guider staging.
        self.assertIn("audio_conditioning_scale=_a2v_distilled_scale_value(", src)


if __name__ == "__main__":
    unittest.main()
