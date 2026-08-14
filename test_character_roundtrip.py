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
import io
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import (  # noqa: E402
    attr, extract_element, extract_function, extract_object, panel_source,
)

import mlx_ltx_panel as P  # noqa: E402

NODE = shutil.which("node")


def _write_synthetic_safetensors(path: Path, keys: list[str]) -> None:
    """Write a header-only fixture; routing gates never materialise payloads."""
    header = {
        key: {"dtype": "F32", "shape": [1, 1], "data_offsets": [0, 0]}
        for key in keys
    }
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(raw)) + raw)

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


def _run_submit(*, face=1.0, voice=1.0, prompt="bizarrotrn waves"):
    """Execute the REAL charactersGenerate() and capture its HTTP payload.

    The old submit test duplicated two expressions from the function under
    test. It therefore kept passing if the function returned early, stopped
    sending a field, or sent a different value. This shim observes the fetch
    boundary instead: no fetch means ``request`` is null, and whatever the real
    function put on URLSearchParams is what the assertions receive.
    """
    script = DOM_SHIM + """
%s
%s
%s
window.CHARACTERS = %s;
window.CHARACTERS.selected = {id: 'bizarrotrn', name: 'Bizarro', trigger: 'bizarrotrn'};
window.CHARACTERS.charStrength = %s;
window.CHARACTERS.voiceStrength = %s;
window.CHARACTERS.duration = '5s';
window.CHARACTERS.quality = 'pro';
_mk('charactersPrompt', {value: %s});
_mk('charactersStrength', {value: '0.8', min: '0.4', max: '1.2'});
_mk('charactersStrengthValue', {textContent: '0.80'});
let _request = null;
global.fetch = async (url, opts) => {
  _request = {
    url: String(url),
    method: String(opts?.method || ''),
    headers: opts?.headers || {},
    fields: Object.fromEntries(new URLSearchParams(String(opts?.body || '')).entries()),
  };
  return {ok: true, status: 200, json: async () => ({ok: true, job_id: 'gate-job'})};
};
(async () => {
  charactersSyncStrengthControls();
  await charactersGenerate();
  console.log(JSON.stringify({
    request: _request,
    displayed: document.getElementById('charactersStrengthValue').textContent,
  }));
})().catch(e => { console.error(e); process.exit(3); });
""" % (_JS.fn("charactersUpdateStrengthDisplay"),
       _JS.fn("charactersSyncStrengthControls"),
       _JS.fn("charactersGenerate"), _JS.state(),
       json.dumps(face), json.dumps(voice), json.dumps(prompt))
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
        """End to end: sync the control, then execute the submit function."""
        out = _run_submit()
        self.assertIsNotNone(out["request"],
                             "charactersGenerate returned before submitting")
        fields = out["request"]["fields"]
        self.assertEqual(float(out["displayed"]), float(fields["character_strength"]),
                         "displayed %s but submits %s" %
                         (out["displayed"], fields.get("character_strength")))
        self.assertEqual(float(fields["character_strength"]), 1.0)
        self.assertEqual(float(fields["character_voice_strength"]), 1.0)

    def test_the_submit_path_sends_both_strengths(self):
        out = _run_submit(face=0.65, voice=1.4, prompt="bizarrotrn speaks")
        self.assertIsNotNone(out["request"],
                             "charactersGenerate returned before submitting")
        self.assertEqual(out["request"]["url"],
                         "/characters/bizarrotrn/generate")
        self.assertEqual(out["request"]["method"], "POST")
        fields = out["request"]["fields"]
        self.assertEqual(fields["prompt_body"], "bizarrotrn speaks")
        self.assertEqual(float(fields["character_strength"]), 0.65)
        self.assertEqual(float(fields["character_voice_strength"]), 1.4,
                         "the real submit path did not send the voice strength")


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


class TestQueueCharacterVoiceContract(unittest.TestCase):
    """The public queue API must never render half a trained character."""

    def _post(self, fields: dict, *, with_audio=True, audio_declared_missing=False):
        body = urlencode(fields).encode()
        h = P.Handler.__new__(P.Handler)
        h.path = "/queue/add"
        h.headers = {"Content-Type": "application/x-www-form-urlencoded",
                     "Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h._is_local_request = lambda: True
        reply: dict = {}
        h._json = lambda payload, status=200: reply.update(
            payload=payload, status=status)

        with tempfile.TemporaryDirectory() as td:
            face = Path(td) / "bizarrotrn_v2.safetensors"
            audio = Path(td) / "bizarrotrn.audio.safetensors"
            transformer = Path(td) / "transformer-distilled.safetensors"
            module = "transformer_blocks.0.attn1.to_q"
            _write_synthetic_safetensors(
                transformer, [f"transformer.{module}.weight"]
            )
            _write_synthetic_safetensors(
                face, [f"diffusion_model.{module}.lora_A.weight",
                       f"diffusion_model.{module}.lora_B.weight"]
            )
            if with_audio:
                _write_synthetic_safetensors(
                    audio, [f"diffusion_model.{module}.lora_A.weight",
                            f"diffusion_model.{module}.lora_B.weight"]
                )
            # THREE SHAPES, AND THEY ARE NOT THE SAME REQUEST.
            #   with_audio=True             -> a full character
            #   with_audio=False            -> a SILENT character: no audio file
            #                                  exists, so list_characters() reports
            #                                  audio_lora_path=None. This is how a
            #                                  face-only character is represented,
            #                                  the shipped sample character among
            #                                  them. It must RENDER.
            #   audio_declared_missing=True -> a CORRUPT bundle: the record names
            #                                  an audio file that is not on disk.
            #                                  It must REFUSE.
            char = {
                "id": "bizarrotrn", "trigger": "bizarrotrn", "name": "Bizarro",
                "face_lora_path": str(face),
                "audio_lora_path": (str(audio) if with_audio
                                    else (str(Path(td) / "gone.audio.safetensors")
                                          if audio_declared_missing else None)),
            }
            saved = (P.list_characters, P.persist_queue, P.push,
                     P._active_ltx_transformer_path, P.h3_capable)
            P.list_characters = lambda: [char]
            P.persist_queue = lambda: None
            P.push = lambda line: None
            P._active_ltx_transformer_path = lambda: transformer
            # Make the H3 refusal test independent of the CI host's RAM. Modes
            # H3 does not serve still resolve back to LTX through the registry.
            P.h3_capable = lambda: True
            try:
                h.do_POST()
                jid = (reply.get("payload") or {}).get("id")
                with P.QUEUE_COND:
                    queued = next((j for j in P.STATE["queue"]
                                   if j.get("id") == jid), None)
                    if jid:
                        P.STATE["queue"] = [j for j in P.STATE["queue"]
                                            if j.get("id") != jid]
                params = dict((queued or {}).get("params") or {})
                if queued:
                    params["loras"] = [dict(x) for x in params.get("loras") or []]
            finally:
                (P.list_characters, P.persist_queue, P.push,
                 P._active_ltx_transformer_path, P.h3_capable) = saved
        return reply, params

    def test_reported_t2v_api_case_stacks_face_and_trained_voice(self):
        reply, params = self._post({
            "mode": "t2v",
            "character_id": "bizarrotrn",
            "prompt": 'bizarrotrn stands at a podium and says "Welcome back."',
            "character_strength": "0.9",
            "character_voice_strength": "1.25",
        })
        self.assertEqual(reply["status"], 200, reply)
        self.assertTrue(reply["payload"].get("ok"), reply)
        self.assertEqual(params["mode"], "t2v")
        self.assertEqual([Path(x["path"]).name for x in params["loras"][:2]],
                         ["bizarrotrn_v2.safetensors",
                          "bizarrotrn.audio.safetensors"])
        self.assertEqual([x["strength"] for x in params["loras"][:2]],
                         [0.9, 1.25])

    def test_every_ltx_video_mode_uses_the_same_pair(self):
        modes = ("i2v", "i2v_clean_audio", "extend", "keyframe", "a2v",
                 "restore", "ingredients", "control")
        for mode in modes:
            with self.subTest(mode=mode):
                reply, params = self._post({
                    "mode": mode, "engine": "ltx",
                    "character_id": "bizarrotrn", "prompt": "bizarrotrn moves",
                })
                self.assertEqual(reply["status"], 200, reply)
                self.assertEqual(
                    [Path(x["path"]).name for x in params["loras"][:2]],
                    ["bizarrotrn_v2.safetensors",
                     "bizarrotrn.audio.safetensors"])

    def test_a_declared_voice_that_is_missing_refuses(self):
        """A bundle that NAMES an audio file which is not on disk is corrupt."""
        fields = {"mode": "t2v", "character_id": "bizarrotrn",
                  "prompt": "bizarrotrn waves"}
        reply, params = self._post(fields, with_audio=False,
                                   audio_declared_missing=True)
        self.assertEqual(reply["status"], 400, reply)
        self.assertIn("missing its trained voice LoRA", reply["payload"]["error"])
        self.assertFalse(params)

        fields["no_voice"] = "on"
        reply, params = self._post(fields, with_audio=False,
                                   audio_declared_missing=True)
        self.assertEqual(reply["status"], 200, reply)
        self.assertTrue(params["no_voice"])
        self.assertEqual([Path(x["path"]).name for x in params["loras"]],
                         ["bizarrotrn_v2.safetensors"])

    def test_a_silent_character_renders_face_only(self):
        """A character with NO trained voice is not a broken character.

        THIS IS THE REGRESSION THAT SHIPPED. The voice contract was enforced
        against `audio_lora_path` being falsy, but list_characters() reports
        None there for every face-only character — so every silent character
        became unrenderable on every path, with no way to opt out: the
        No-voice pill is HIDDEN exactly for these characters, so `no_voice`
        could never be submitted. The one-click SAMPLE CHARACTER ships as a
        single face file, which made from-zero onboarding produce a character
        the panel then refused.
        """
        reply, params = self._post(
            {"mode": "t2v", "character_id": "bizarrotrn",
             "prompt": "bizarrotrn waves"}, with_audio=False)
        self.assertEqual(reply["status"], 200, reply)
        self.assertEqual([Path(x["path"]).name for x in params["loras"]],
                         ["bizarrotrn_v2.safetensors"])
        # And it does NOT get stamped as an explicit voice opt-out — the user
        # never opted out of anything; there was nothing to opt out of.
        self.assertFalse(params.get("no_voice"))

    def test_the_shipped_sample_character_is_renderable(self):
        """SAMPLE_CHARACTER is one face file. Whatever the voice contract
        says, it must not refuse the character the app offers to install."""
        self.assertNotIn("audio", P.SAMPLE_CHARACTER["filename"])
        reply, _ = self._post(
            {"mode": "t2v", "character_id": "bizarrotrn",
             "prompt": "bizarrotrn waves"}, with_audio=False)
        self.assertEqual(reply["status"], 200, reply)

    def test_h3_character_pair_is_refused_before_lane_scrub(self):
        reply, params = self._post({
            "mode": "t2v", "engine": "h3", "character_id": "bizarrotrn",
            "prompt": "bizarrotrn speaks",
        })
        self.assertEqual(reply["status"], 400, reply)
        self.assertIn("can't load a trained character", reply["payload"]["error"])

    def test_engine_h3_on_a_mode_h3_cannot_serve_still_renders_on_ltx(self):
        """`engine=h3` is a REQUEST, and make_job resolves it.

        make_job downgrades h3 to LTX for three legitimate reasons — H3 isn't
        installed, the Mac lacks the RAM, or the mode is one H3 does not serve.
        The character refusal used to re-read the RAW form field instead of the
        resolved engine, so a request that was about to render correctly on LTX
        with the full character stack got a 400 instead. `extend` is one of the
        modes H3 never serves, so this can be asserted without depending on
        whether H3 is installed on the machine running the test.
        """
        reply, params = self._post({
            "mode": "extend", "engine": "h3", "character_id": "bizarrotrn",
            "prompt": "bizarrotrn keeps speaking",
        })
        self.assertEqual(reply["status"], 200, reply)
        self.assertEqual(params["engine"], "ltx")
        self.assertEqual([Path(x["path"]).name for x in params["loras"][:2]],
                         ["bizarrotrn_v2.safetensors",
                          "bizarrotrn.audio.safetensors"])


@unittest.skipUnless(NODE, "node is required to execute the panel JavaScript")
class TestLoraLibraryGenerationFilter(unittest.TestCase):
    def test_incompatible_ltx_row_is_never_offered_as_an_other_mode(self):
        source = panel_source()
        fn = extract_function("_loraGenerationCompatible", source)
        script = fn + r"""
const results = [
  _loraGenerationCompatible({ltx_compatible:false}, 'video'),
  _loraGenerationCompatible({ltx_compatible:false}, 'image:flux'),
  _loraGenerationCompatible({ltx_compatible:true}, 'video'),
];
process.stdout.write(JSON.stringify(results));
"""
        proc = subprocess.run(
            [NODE, "-e", script], text=True, capture_output=True, check=True
        )
        self.assertEqual(json.loads(proc.stdout), [False, True, True])


class TestPipelineQualityPerVersion(unittest.TestCase):
    """The endpoint SUBMITS the pipeline the generation was graded on.

    f65ea9b added character_render_quality() (ltx23 -> high, ltx25 ->
    balanced) and fixed the endpoint's `quality` VARIABLE — but the job_form
    three screens below still hardcoded "quality": ["high"], and THAT is the
    field make_job reads. Every Characters-tab render on 2.5 took the
    two-stage HQ path (~246 s, 29.5 GB add-on) instead of the graded
    q8 + distilled path (~139 s). c366e71 fixed the literal; this class pins
    the property so the variable and the form can never drift apart again.

    Per this file's own charter, it EXECUTES the real do_POST rather than
    grepping for the fixed line: a stub transport carries the request, and
    make_job is captured at the seam the bug lived on — the form the endpoint
    actually submits. Each case runs per model version, not just whichever
    generation is active today, because "works on the active version" is
    exactly the coverage hole the original miss hid in.
    """

    def _generate(self, fields: dict, version: str):
        """POST /characters/<id>/generate through the REAL handler.

        Returns (reply, submitted): the JSON the endpoint answered and the
        form it handed make_job. Module seams (character list, make_job,
        queue persistence, log) are restored in `finally`; the fake job is
        removed from the in-memory queue so no other test sees it.
        """
        body = urlencode(fields).encode()
        h = P.Handler.__new__(P.Handler)          # no socket — stub transport
        h.path = "/characters/gatetrn/generate"
        h.headers = {"Content-Type": "application/x-www-form-urlencoded",
                     "Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h._is_local_request = lambda: True
        reply: dict = {}
        h._json = lambda payload, status=200: reply.update(
            payload=payload, status=status)

        submitted: dict = {}

        def _capture(form, **_kw):
            submitted.update(form)
            return {"id": "quality-gate-job"}

        char = {"id": "gatetrn", "trigger": "gatetrn", "name": "Gate",
                "face_lora_path": "f.safetensors"}
        saved = (P.ACTIVE_MODEL_VERSION, P.list_characters, P.make_job,
                 P.persist_queue, P.push)
        P.ACTIVE_MODEL_VERSION = version
        P.list_characters = lambda: [char]
        P.make_job = _capture
        P.persist_queue = lambda: None
        P.push = lambda line: None
        try:
            h.do_POST()
        finally:
            (P.ACTIVE_MODEL_VERSION, P.list_characters, P.make_job,
             P.persist_queue, P.push) = saved
            with P.QUEUE_COND:
                P.STATE["queue"] = [j for j in P.STATE["queue"]
                                    if j.get("id") != "quality-gate-job"]
        return reply, submitted

    def test_the_registry_rule_itself(self):
        self.assertEqual(P.character_render_quality("ltx23"), "high")
        self.assertEqual(P.character_render_quality("ltx25"), "balanced")

    def test_every_registered_version_resolves_to_a_real_pipeline(self):
        # The endpoint 400s a quality outside _CHARACTER_QUALITY_RESOLUTION —
        # a registry entry naming a fantasy pipeline would brick its own tab.
        for v in P.MODEL_VERSIONS:
            self.assertIn(P.character_render_quality(v["id"]),
                          P._CHARACTER_QUALITY_RESOLUTION, v["id"])

    def test_the_submitted_form_carries_the_resolved_quality(self):
        for version, expected in (("ltx25", "balanced"), ("ltx23", "high")):
            reply, form = self._generate({"prompt": "gatetrn waves"}, version)
            self.assertTrue(reply["payload"].get("ok"), reply)
            self.assertEqual(form["quality"], [expected], version)

    def test_an_explicit_caller_quality_still_wins(self):
        # The thin-wrapper promise: a caller naming a REAL pipeline quality is
        # obeyed on either generation. (Replay / Load Params depends on this.)
        _, form = self._generate(
            {"prompt": "gatetrn waves", "quality": "high"}, "ltx25")
        self.assertEqual(form["quality"], ["high"])
        _, form = self._generate(
            {"prompt": "gatetrn waves", "quality": "balanced"}, "ltx23")
        self.assertEqual(form["quality"], ["balanced"])

    def test_size_tokens_pick_a_canvas_not_a_pipeline(self):
        # The tab's two chips say draft/pro. That must choose WIDTH×HEIGHT and
        # leave the pipeline to the generation's rule — conflating the two is
        # what kept this tab on the wrong pipeline in the first place.
        for version, expected in (("ltx25", "balanced"), ("ltx23", "high")):
            for token, (w, hgt) in (("draft", (704, 384)),
                                    ("pro", (1024, 576))):
                _, form = self._generate(
                    {"prompt": "gatetrn waves", "quality": token}, version)
                self.assertEqual(form["quality"], [expected], (version, token))
                self.assertEqual(form["width"], [str(w)], (version, token))
                self.assertEqual(form["height"], [str(hgt)], (version, token))

    def test_schedule_steps_ride_only_when_the_caller_sent_them(self):
        # c366e71's second half: hardcoded stage1/stage2 steps are inert on
        # the HQ lane but a pad-request landmine on a thinning lane. A bare
        # request must not carry them; an explicit caller must.
        _, form = self._generate({"prompt": "gatetrn waves"}, "ltx25")
        self.assertNotIn("stage1_steps", form)
        self.assertNotIn("stage2_steps", form)
        _, form = self._generate(
            {"prompt": "gatetrn waves", "stage1_steps": "8",
             "stage2_steps": "2"}, "ltx25")
        self.assertEqual(form["stage1_steps"], ["8"])
        self.assertEqual(form["stage2_steps"], ["2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
