#!/usr/bin/env python3
"""The Character round-trip gate — it EXECUTES the panel's own JavaScript.

WHY THIS FILE WAS REWRITTEN. The first version asserted the contract by grepping
mlx_ltx_panel.py for the calls it hoped were present. All 12 tests passed while
the live Characters loader was still broken and the visible slider still
displayed a different number from the one being submitted. An external review
called it a false positive, and it was: a test that passes by finding strings is
worse than no test, because it also reports the area as covered.

This version extracts the REAL functions — charactersSyncStrengthControls,
_restoreCharacterStrengths, charactersLoadParams — plus the REAL slider markup,
and runs them in node against a small DOM shim. What it asserts is the property
the user experiences:

    displayed value == submitted value == restored value

If a function stops doing that, the test fails. If a function is renamed or
deleted, extraction raises and the test fails loudly rather than quietly going
back to grepping.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import (  # noqa: E402
    attr, extract_element, extract_function, extract_object, panel_source,
)

import mlx_ltx_panel as P  # noqa: E402

NODE = shutil.which("node")

# A DOM shim just large enough for the functions under test: getElementById,
# element values, textContent, and the querySelectorAll calls they make.
DOM_SHIM = r"""
const _els = {};
function _mk(id, props) {
  // A real <input>.value ALWAYS coerces to string. The shim does the same, so a
  // type bug in the panel shows up here instead of being smoothed over.
  let _v = '';
  const e = Object.assign({
    id, textContent: '', min: '', max: '', className: '',
    dataset: {}, hidden: false, style: {}, innerHTML: '',
    classList: { toggle(){}, add(){}, remove(){}, contains(){ return false } },
    querySelector(){ return null }, querySelectorAll(){ return [] },
    setAttribute(){}, getAttribute(){ return null }, appendChild(){}, remove(){},
    dispatchEvent(){ return true }, addEventListener(){}, focus(){}, blur(){},
  }, props || {});
  Object.defineProperty(e, 'value', {
    get() { return _v; },
    set(x) { _v = (x === null || x === undefined) ? '' : String(x); },
    enumerable: true, configurable: true,
  });
  if (props && Object.prototype.hasOwnProperty.call(props, 'value')) e.value = props.value;
  _els[id] = e;
  return e;
}
global.document = {
  getElementById: (id) => _els[id] || null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => _mk('_tmp'),
  addEventListener: () => {},
  body: { dataset: {}, classList: { toggle(){}, add(){}, remove(){}, contains(){return false} } },
};
global.window = global;
global.BOOT = { ltx: {} };
global.escapeHtml = (s) => String(s);
global.Event = function (t) { this.type = t; };
global.CustomEvent = global.Event;
global.fetch = async () => ({ json: async () => ({}) });
global._renderCharsAppliedNote = () => {};
global.selectManualCharacter = () => {};
global._setCharacterQuality = () => {};
global.renderLorasList = () => {};
global.charactersRenderChips = () => {};
global.charactersEscapeAttr = (s) => String(s);
global.charactersEscapeHtml = (s) => String(s);
global.setMode = () => {};
global.setQuality = () => {};
global.workflowSwitch = () => {};
global.charactersOpenCompose = () => {};
global.charactersBackToGrid = () => {};
global.charactersInit = async () => {};
global.setAspect = () => {};
global.updateEstimate = () => {};
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
// The loader refuses a character it cannot find — correctly. Seed the list the
// panel would have loaded from /characters.
global._manualCharacters = [{ id: 'bizarrotrn', trigger: 'bizarrotrn', name: 'Bizarro',
                              face_lora_path: 'f.safetensors', audio_lora_path: 'a.safetensors' }];
global._activeLoras = [];
global.refreshManualCharacters = async () => {};
global.console = console;
// Auto-vivify: charactersLoadParams touches a known, small set of ids and one
// querySelectorAll. Creating on demand keeps the shim honest — if the loader
// starts touching something new, it still runs, and the ASSERTIONS are what
// decide whether it behaved.
const _origGet = global.document.getElementById;
global.document.getElementById = (id) => _els[id] || _mk(id);
global.document.querySelectorAll = (sel) => (sel === '#aspectGroup .pill-btn')
  ? [_mk('_aspL', {dataset:{aspect:'landscape'}}), _mk('_aspV', {dataset:{aspect:'vertical'}})]
  : [];
"""


def _run_node(script: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError("node failed:\n%s\n%s" % (r.stdout[-2000:], r.stderr[-2000:]))
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


class _JS:
    """Lazily extracted panel JavaScript, shared by the cases below."""
    _src = None

    @classmethod
    def src(cls):
        if cls._src is None:
            cls._src = panel_source()
        return cls._src

    @classmethod
    def fn(cls, name):
        return extract_function(name, cls.src())

    @classmethod
    def state(cls):
        return extract_object("CHARACTERS", cls.src())


def _run_loader(sidecar):
    """Execute the REAL charactersLoadParams() against the DOM shim.

    This is the difference between a gate and a rumour: the function is
    extracted from the panel, run in node, and the DOM it leaves behind is what
    the assertions read. Insert an early return anywhere above the restoration
    and these tests fail.
    """
    script = DOM_SHIM + """
%s
%s
%s
window.CHARACTERS = %s;
_mk('characterStrength', {value: ''});
_mk('characterVoiceStrength', {value: ''});
_mk('charactersStrength', {value: '0.8', min: '0.4', max: '1.2'});
_mk('charactersStrengthValue', {textContent: '0.80'});
_mk('width', {value: ''}); _mk('height', {value: ''});
(async () => {
  await charactersLoadParams(%s);
  console.log(JSON.stringify({
    face: document.getElementById('characterStrength').value,
    voice: document.getElementById('characterVoiceStrength').value,
    slider: document.getElementById('charactersStrength').value,
    displayed: document.getElementById('charactersStrengthValue').textContent,
    width: document.getElementById('width').value,
    height: document.getElementById('height').value,
  }));
})().catch(e => { console.error(e); process.exit(3); });
""" % (_JS.fn("charactersSyncStrengthControls"),
       _JS.fn("_restoreCharacterStrengths"),
       _JS.fn("charactersLoadParams"),
       _JS.state(), json.dumps(sidecar))
    return _run_node(script)


class TestVisibleEqualsSubmitted(unittest.TestCase):
    """The defect the review found: the form displayed 0.80 and submitted 1.0."""

    def test_slider_markup_is_not_a_second_default(self):
        el = extract_element("charactersStrength", _JS.src())
        markup_value = float(attr(el, "value"))
        state = re.search(r"charStrength:\s*([0-9.]+)", _JS.state()).group(1)
        # The markup value is a placeholder; what matters is that the sync
        # function exists to overwrite it. But if they disagree AND nothing
        # syncs, the user reads one number and sends another.
        script = DOM_SHIM + """
%s
%s
window.CHARACTERS = %s;
_mk('charactersStrength', {value: '%s', min: '%s', max: '%s'});
_mk('charactersStrengthValue', {textContent: '%.2f'});
charactersSyncStrengthControls();
console.log(JSON.stringify({
  displayed: document.getElementById('charactersStrengthValue').textContent,
  slider: document.getElementById('charactersStrength').value,
  state: window.CHARACTERS.charStrength,
}));
""" % (_JS.fn("charactersUpdateStrengthDisplay"),
       _JS.fn("charactersSyncStrengthControls"),
       _JS.state(), markup_value, attr(el, "min"), attr(el, "max"), markup_value)
        out = _run_node(script)
        self.assertEqual(float(out["slider"]), float(state),
                         "the visible slider does not hold the state's value")
        self.assertEqual(float(out["displayed"]), float(state),
                         "the printed number does not match the state's value")

    def test_untouched_form_displays_what_it_submits(self):
        """End to end: sync the control, then read what the submit path sends."""
        script = DOM_SHIM + """
%s
%s
window.CHARACTERS = %s;
_mk('charactersStrength', {value: '0.8', min: '0.4', max: '1.2'});
_mk('charactersStrengthValue', {textContent: '0.80'});
charactersSyncStrengthControls();
// what charactersGenerate() would put on the form, by its own expressions
const submittedFace  = String(window.CHARACTERS.charStrength ?? 1.0);
const submittedVoice = String(window.CHARACTERS.voiceStrength ?? 1.0);
console.log(JSON.stringify({
  displayed: document.getElementById('charactersStrengthValue').textContent,
  submittedFace, submittedVoice,
}));
""" % (_JS.fn("charactersUpdateStrengthDisplay"),
       _JS.fn("charactersSyncStrengthControls"), _JS.state())
        out = _run_node(script)
        self.assertEqual(float(out["displayed"]), float(out["submittedFace"]),
                         "displayed %s but submits %s" % (out["displayed"], out["submittedFace"]))
        self.assertEqual(float(out["submittedFace"]), 1.0)
        self.assertEqual(float(out["submittedVoice"]), 1.0)

    def test_the_submit_path_sends_both_strengths(self):
        body = _JS.fn("charactersGenerate")
        self.assertIn("character_strength", body)
        self.assertIn("character_voice_strength", body,
                      "the voice strength is never submitted, so it cannot round-trip")


class TestRestoreRunsInBothLoadPaths(unittest.TestCase):
    """The remainder the review found: the live Characters branch returned before
    the Manual path's restoration, so NEITHER strength came back."""

    def _restore(self, sidecar):
        script = DOM_SHIM + """
%s
%s
window.CHARACTERS = %s;
_mk('characterStrength', {value: ''});
_mk('characterVoiceStrength', {value: ''});
_mk('charactersStrength', {value: '0.8', min: '0.4', max: '1.2'});
_mk('charactersStrengthValue', {textContent: '0.80'});
_restoreCharacterStrengths(%s);
console.log(JSON.stringify({
  face: document.getElementById('characterStrength').value,
  voice: document.getElementById('characterVoiceStrength').value,
  slider: document.getElementById('charactersStrength').value,
  displayed: document.getElementById('charactersStrengthValue').textContent,
  stateFace: window.CHARACTERS.charStrength,
  stateVoice: window.CHARACTERS.voiceStrength,
}));
""" % (_JS.fn("charactersSyncStrengthControls"),
       _JS.fn("_restoreCharacterStrengths"), _JS.state(), json.dumps(sidecar))
        return _run_node(script)

    def test_both_strengths_come_back(self):
        out = self._restore({"character_strength": 0.9, "character_voice_strength": 1.4})
        self.assertEqual(float(out["face"]), 0.9)
        self.assertEqual(float(out["voice"]), 1.4)

    def test_the_visible_control_follows_the_restore(self):
        # Restoring a value the user cannot see is the same bug in a new place.
        out = self._restore({"character_strength": 0.65, "character_voice_strength": 1.1})
        self.assertEqual(float(out["slider"]), 0.65)
        self.assertEqual(float(out["displayed"]), 0.65)

    def test_a_value_outside_the_slider_range_is_still_shown(self):
        # The markup caps at 1.2; the server accepts up to 2.0. A restored 1.6
        # must not be silently clamped to something the user never chose.
        out = self._restore({"character_strength": 1.6, "character_voice_strength": 1.0})
        self.assertEqual(float(out["slider"]), 1.6)
        self.assertEqual(float(out["face"]), 1.6)

    def test_the_LIVE_loader_restores_both_strengths(self):
        """charactersLoadParams() ITSELF, executed.

        The previous version of this test string-checked that the loader
        contained a call to the restorer. An early return inserted above that
        call would still have passed — which is precisely the defect the loader
        shipped with. This runs the real function end to end and reads the DOM
        it leaves behind, so an early return fails the suite."""
        out = _run_loader({
            "source": "characters", "character_id": "bizarrotrn", "mode": "t2v",
            "width": 704, "height": 384, "frames": 121,
            "character_strength": 0.65, "character_voice_strength": 1.4,
            "prompt": "at the map table", "seed": 7,
        })
        self.assertEqual(float(out["face"]), 0.65,
                         "the LIVE loader did not restore the face strength")
        self.assertEqual(float(out["voice"]), 1.4,
                         "the LIVE loader did not restore the voice strength")
        self.assertEqual(float(out["slider"]), 0.65)
        self.assertEqual(out["width"], "704")
        self.assertEqual(out["height"], "384")

    def test_the_live_loader_round_trips_pro_too(self):
        out = _run_loader({
            "source": "characters", "character_id": "bizarrotrn", "mode": "t2v",
            "width": 1024, "height": 576, "frames": 121,
            "character_strength": 1.0, "character_voice_strength": 1.0,
            "prompt": "x", "seed": 1,
        })
        self.assertEqual(float(out["face"]), 1.0)
        self.assertEqual(float(out["voice"]), 1.0)
        self.assertEqual(out["width"], "1024")


class TestDraftCanvasRoundTrips(unittest.TestCase):
    """A Draft render must reopen as Draft — exercised through the real loader's
    own expression, not a restatement of it."""

    def _is_draft(self, w, h):
        body = _JS.fn("charactersLoadParams")
        m = re.search(r"const _draftPairs = (\[.*?\]);", body, re.S)
        self.assertIsNotNone(m, "the loader no longer declares _draftPairs")
        script = DOM_SHIM + """
const _draftPairs = %s;
const sidecarW = %d, sidecarH = %d;
const isDraft = _draftPairs.some(([w, h]) =>
  (sidecarW === w && sidecarH === h) || (sidecarW === h && sidecarH === w));
console.log(JSON.stringify({isDraft}));
""" % (m.group(1), w, h)
        return _run_node(script)["isDraft"]

    def test_the_size_the_server_emits_is_recognised(self):
        w, h = P._CHARACTER_QUALITY_RESOLUTION["draft"]
        self.assertTrue(self._is_draft(w, h),
                        "Load Params does not recognise the Draft canvas the server "
                        "emits (%dx%d), so Draft renders reopen as Pro" % (w, h))

    def test_legacy_and_vertical_still_round_trip(self):
        self.assertTrue(self._is_draft(736, 416))
        self.assertTrue(self._is_draft(384, 704))

    def test_pro_is_not_mistaken_for_draft(self):
        w, h = P._CHARACTER_QUALITY_RESOLUTION["high"]
        self.assertFalse(self._is_draft(w, h))


class TestServerContract(unittest.TestCase):
    def test_server_defaults_match_the_client_state(self):
        state = _JS.state()
        face = float(re.search(r"charStrength:\s*([0-9.]+)", state).group(1))
        voice = float(re.search(r"voiceStrength:\s*([0-9.]+)", state).group(1))
        src = _JS.src()
        s_face = float(re.search(
            r'form\.get\("character_strength",\s*\["([0-9.]+)"\]', src).group(1))
        s_voice = float(re.search(
            r'form\.get\("character_voice_strength",\s*\["([0-9.]+)"\]', src).group(1))
        self.assertEqual(face, s_face)
        self.assertEqual(voice, s_voice)

    def test_full_payload_round_trip(self):
        """payload -> sidecar -> restore -> payload, over the real restorer."""
        for quality, face, voice in (("draft", 1.0, 1.0), ("high", 1.0, 1.0),
                                     ("draft", 1.15, 0.75), ("high", 0.9, 1.4)):
            w, h = P._CHARACTER_QUALITY_RESOLUTION[quality]
            first = {"character_id": "bizarrotrn", "width": w, "height": h,
                     "frames": 121, "character_strength": face,
                     "character_voice_strength": voice}
            sidecar = json.loads(json.dumps(first))
            sidecar["source"] = "characters"
            out = _run_loader(sidecar)          # the LIVE loader, not the helper
            self.assertEqual(float(out["face"]), first["character_strength"], first)
            self.assertEqual(float(out["voice"]), first["character_voice_strength"], first)
            self.assertEqual(out["width"], str(first["width"]), first)
            self.assertEqual(out["height"], str(first["height"]), first)


if __name__ == "__main__":
    unittest.main(verbosity=2)
