#!/usr/bin/env python3
"""Executable UI contract for the main-stage live render preview.

The panel ships its client inside mlx_ltx_panel.py, so this gate extracts and
runs the real state-normalizer and stage renderer against a small DOM. It locks
the behavior that matters to a viewer: server-owned meaningful gates, both
engine schemas, playback protection, and completion handoff.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_element, extract_function, panel_source  # noqa: E402
import mlx_ltx_panel as panel  # noqa: E402


NODE = shutil.which("node")


DOM_SHIM = r"""
class Classes {
  constructor() { this.s = new Set(); }
  add(...xs) { xs.forEach(x => this.s.add(x)); }
  remove(...xs) { xs.forEach(x => this.s.delete(x)); }
  toggle(x, on) {
    if (on === undefined) on = !this.s.has(x);
    on ? this.s.add(x) : this.s.delete(x);
    return on;
  }
  contains(x) { return this.s.has(x); }
}
function node(id) {
  const n = {
    id, hidden: false, textContent: '', title: '', disabled: false,
    dataset: {}, style: { display: '', setProperty(k,v){this[k]=v}, removeProperty(k){delete this[k]} },
    classList: new Classes(), attrs: {}, children: {}, isConnected: true,
    setAttribute(k,v){ this.attrs[k]=String(v); if(k==='src') this.src=String(v); },
    getAttribute(k){ return this.attrs[k] ?? null; },
    removeAttribute(k){ delete this.attrs[k]; },
    querySelector(sel){ return this.children[sel] || null; },
    closest(sel){ return sel === '.player-surface' ? surface : null; },
  };
  let html = '';
  Object.defineProperty(n, 'innerHTML', {
    get(){ return html; },
    set(v){
      html = String(v); n.children = {};
      if (html.includes('live-stage-image')) n.children['.live-stage-image'] = node('liveImage');
      if (html.includes('live-stage-warming')) n.children['.live-stage-warming'] = node('warming');
      if (html.includes('<video')) {
        const vid = node('video'); vid.paused = false; vid.ended = false;
        vid.pause = () => { vid.paused = true; };
        n.children['video'] = vid;
      }
    },
  });
  return n;
}
const els = {};
function add(id){ return els[id] = node(id); }
const surface = add('surface'); surface.attrs = {};
surface.removeAttribute = k => { delete surface.attrs[k]; };
surface.setAttribute = (k,v) => { surface.attrs[k]=String(v); };
const wrap = add('playerWrap');
const overlay = add('liveStageOverlay'); overlay.hidden = true;
const chip = add('liveReturnChip'); chip.hidden = true;
add('liveStageBadge'); add('liveStageTitle'); add('liveStageEta'); add('liveStageStop');
add('playerOverlayTop'); add('playerOverlayActions');
global.document = {
  getElementById: id => els[id] || null,
  querySelector: sel => sel === '#playerWrap video' ? wrap.querySelector('video') : null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};
global.window = global;
global.LIVE_STAGE_PLAYBACK_HOLD_MS = 12000;
global.BOOT = {ltx:{
  preview_state:{on:true},
  preview_modes:['t2v','i2v','i2v_clean_audio'],
  qualities:[
    {key:'quick', preview_every:1, preview_meaningful_at:6},
    {key:'high', preview_every:2, preview_meaningful_at:2},
  ],
  help:{preview:'rough composition preview'}
},h3:{
  live_preview:{on:false,schema:'h3-live-preview/1',meaningful_at:1,help:'rough H3 composition preview'},
  modes:['t2v','i2v'],
}};
global.activePath = null;
global.currentOutputs = [];
global._stagePlaybackIntentAt = 0;
global._liveStageJobId = null;
global._liveStageOwnsPlayer = false;
global._liveStageForcedJobId = null;
global._liveStagePendingOutput = null;
global._stopEarlyRequested = null;
global.fmtMin = n => `${Math.round(Number(n))}s`;
global.findOutputByPath = path => path ? {path} : null;
global.selectCalls = [];
global.selectOutput = (path, opts) => { selectCalls.push({path, opts:opts||{}}); _liveStageOwnsPlayer=false; };
"""


FUNCTIONS = (
    "stageMayAutoSelectOutput",
    "normalizeLivePreview",
    "_liveStageMediaHeld",
    "_showLiveReturnChip",
    "_hideLiveStageChrome",
    "_restoreSelectedOutputAfterLive",
    "_handoffLiveStageToOutput",
    "returnToLiveRender",
    "_renderLiveStageFrame",
    "renderLiveStage",
)


def run_contract() -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    source = panel_source()
    script = DOM_SHIM + "\n".join(extract_function(name, source) for name in FUNCTIONS) + r"""
const out = {};

activePath = null; _liveStageOwnsPlayer = true;
out.autoSelectWhileLive = stageMayAutoSelectOutput();
_liveStageOwnsPlayer = false;
out.autoSelectWhenEmpty = stageMayAutoSelectOutput();

const ltxWarmStatus = {running:true,current:{id:'warm',params:{engine:'ltx',mode:'t2v',quality:'quick'}}};
out.ltxWarm = normalizeLivePreview(ltxWarmStatus, null);

const ltxSilentStatus = {running:true,current:{id:'extend',params:{engine:'ltx',mode:'extend',quality:'high'}}};
out.ltxSilent = normalizeLivePreview(ltxSilentStatus, null);

const h3SilentStatus = {running:true,current:{id:'h3none',params:{engine:'h3',mode:'t2v',quality:'draft'}}};
out.h3Silent = normalizeLivePreview(h3SilentStatus, {denoise_step:2,denoise_total:8});

BOOT.h3.live_preview.on = true;
const h3WarmStatus = {running:true,current:{id:'h3warm',params:{engine:'h3',mode:'t2v',quality:'draft'}}};
out.h3Warm = normalizeLivePreview(h3WarmStatus, {denoise_step:0,denoise_total:3});

const h3PreviewStatus = {running:true,current:{id:'h3live',params:{engine:'h3',mode:'t2v',quality:'draft'}}};
out.h3Preview = normalizeLivePreview(h3PreviewStatus, {
  live_preview:{preview_url:'/image?h3=1',preview_step:2,preview_total:7,meaningful:false,abortable:false},
  remaining_sec:91,
});

const ltxLiveStatus = {running:true,current:{id:'ltxlive',params:{engine:'ltx',mode:'t2v',quality:'quick'}}};
const ltxLive = normalizeLivePreview(ltxLiveStatus, {preview:{
  url:'/image?ltx=1',estimate:6,total:8,meaningful:true,abortable:true,saves_sec:42,
},remaining_sec:42});
renderLiveStage(ltxLiveStatus, ltxLive);
out.liveStage = {
  owns:_liveStageOwnsPlayer,
  state:wrap.dataset.liveState,
  src:(wrap.querySelector('.live-stage-image')||{}).src || '',
  overlayHidden:overlay.hidden,
  title:els.liveStageTitle.textContent,
  eta:els.liveStageEta.textContent,
  stopHidden:els.liveStageStop.hidden,
};

// The user is actively watching a clip: a new render may advertise itself but
// must not replace the video node.
_liveStageOwnsPlayer = false; _liveStageForcedJobId = null;
wrap.innerHTML = '<video></video>'; overlay.hidden = true; chip.hidden = true;
const heldStatus = {running:true,current:{id:'held',params:{engine:'ltx',mode:'t2v',quality:'quick'}}};
const heldPreview = normalizeLivePreview(heldStatus, {preview:{
  url:'/image?held=1',estimate:7,total:8,meaningful:true,abortable:true,
}});
renderLiveStage(heldStatus, heldPreview);
out.held = {
  videoStayed:!!wrap.querySelector('video'), chipHidden:chip.hidden,
  chipText:chip.textContent, owns:_liveStageOwnsPlayer,
};

// A paused/buffering clip is still protected for the grace window after the
// user touched its controls.
wrap.querySelector('video').paused = true;
_stagePlaybackIntentAt = Date.now(); chip.hidden = true;
renderLiveStage({running:true,current:{id:'recent',params:{engine:'ltx',mode:'t2v',quality:'quick'}}}, heldPreview);
out.recent = {videoStayed:!!wrap.querySelector('video'), chipHidden:chip.hidden};

// Completion while live owns the stage hands the last frame to selectOutput
// with the preserve-under-video option.
wrap.innerHTML = '<img class="live-stage-image">';
_liveStageOwnsPlayer = true; _liveStageJobId = 'done1'; _stagePlaybackIntentAt = 0;
renderLiveStage({running:false,current:null,history:[{id:'done1',status:'done',output_path:'/out/done1.mp4'}]}, null);
out.handoff = selectCalls[selectCalls.length - 1] || null;

// Gallery listing has a deliberate two-second mtime cutoff. Completion must
// leave the last preview mounted until the mp4 becomes selectable.
global.findOutputByPath = path => path === '/out/late.mp4' ? null : (path ? {path} : null);
wrap.innerHTML = '<img class="live-stage-image">';
_liveStageOwnsPlayer = true; _liveStageJobId = 'late'; _liveStagePendingOutput = null;
renderLiveStage({running:false,current:null,history:[{id:'late',status:'done',output_path:'/out/late.mp4'}]}, null);
out.pending = {
  owns:_liveStageOwnsPlayer,
  imageStayed:!!wrap.querySelector('.live-stage-image'),
  title:els.liveStageTitle.textContent,
  pending:_liveStagePendingOutput && _liveStagePendingOutput.path,
};

process.stdout.write(JSON.stringify(out));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        result = subprocess.run(
            [NODE, str(path)], capture_output=True, text=True, timeout=30
        )
        if result.returncode:
            raise AssertionError(result.stdout + "\n" + result.stderr)
        return json.loads(result.stdout)
    finally:
        path.unlink(missing_ok=True)


class StageLivePreviewContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_contract()

    def test_stage_elements_ship_in_real_markup(self) -> None:
        source = panel_source()
        self.assertIn("live-return-chip", extract_element("liveReturnChip", source))
        self.assertIn("live-stage-overlay", extract_element("liveStageOverlay", source))

    def test_first_output_waits_for_live_handoff(self) -> None:
        self.assertFalse(self.result["autoSelectWhileLive"])
        self.assertTrue(self.result["autoSelectWhenEmpty"])

    def test_ltx_lane_warms_before_first_meaningful_frame(self) -> None:
        self.assertTrue(self.result["ltxWarm"]["eligible"])
        self.assertFalse(self.result["ltxWarm"]["available"])
        self.assertFalse(self.result["ltxWarm"]["meaningful"])

    def test_engine_without_preview_stays_out_of_stage(self) -> None:
        self.assertFalse(self.result["h3Silent"]["eligible"])

    def test_h3_capable_lane_warms_before_first_frame(self) -> None:
        self.assertTrue(self.result["h3Warm"]["eligible"])
        self.assertFalse(self.result["h3Warm"]["available"])

    def test_ltx_special_lane_without_preview_stays_out_of_stage(self) -> None:
        self.assertFalse(self.result["ltxSilent"]["eligible"])

    def test_h3_schema_keeps_server_meaningful_gate(self) -> None:
        preview = self.result["h3Preview"]
        self.assertTrue(preview["eligible"])
        self.assertFalse(preview["meaningful"])
        self.assertEqual(preview["estimate"], 2)
        self.assertEqual(preview["total"], 7)

    def test_meaningful_preview_owns_full_stage(self) -> None:
        stage = self.result["liveStage"]
        self.assertTrue(stage["owns"])
        self.assertEqual(stage["state"], "meaningful")
        self.assertEqual(stage["src"], "/image?ltx=1")
        self.assertFalse(stage["overlayHidden"])
        self.assertEqual(stage["title"], "forming take · step 6/8")
        self.assertFalse(stage["stopHidden"])

    def test_playing_clip_is_not_stolen(self) -> None:
        held = self.result["held"]
        self.assertTrue(held["videoStayed"])
        self.assertFalse(held["chipHidden"])
        self.assertEqual(held["chipText"], "LIVE · return to render")
        self.assertFalse(held["owns"])

    def test_recently_touched_paused_clip_is_not_stolen(self) -> None:
        self.assertTrue(self.result["recent"]["videoStayed"])
        self.assertFalse(self.result["recent"]["chipHidden"])

    def test_completion_requests_seamless_handoff(self) -> None:
        handoff = self.result["handoff"]
        self.assertEqual(handoff["path"], "/out/done1.mp4")
        self.assertTrue(handoff["opts"]["liveHandoff"])

    def test_completion_keeps_last_frame_until_output_is_listed(self) -> None:
        pending = self.result["pending"]
        self.assertTrue(pending["owns"])
        self.assertTrue(pending["imageStayed"])
        self.assertEqual(pending["title"], "preparing finished take")
        self.assertEqual(pending["pending"], "/out/late.mp4")


class H3PreviewSchemaContract(unittest.TestCase):
    def test_ltx_meaningful_rules_stay_server_owned_per_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(panel, "STATE_DIR", Path(tmp)):
            directory = panel.live_preview_dir("ltx-job")
            directory.mkdir(parents=True)
            (directory / "preview_latest.png").write_bytes(b"png")
            status_path = directory / "status.json"

            status_path.write_text(json.dumps({"forward": 5, "total_forwards": 8}))
            early = panel._preview_progress(
                {"id": "ltx-job", "params": {"quality": "quick"}}, 60, "denoise"
            )["preview"]
            self.assertEqual(early["estimate"], 5)
            self.assertFalse(early["meaningful"])
            self.assertFalse(early["abortable"])

            status_path.write_text(json.dumps({"forward": 6, "total_forwards": 8}))
            distilled = panel._preview_progress(
                {"id": "ltx-job", "params": {"quality": "quick"}}, 45, "denoise"
            )["preview"]
            self.assertTrue(distilled["meaningful"])
            self.assertTrue(distilled["abortable"])

            status_path.write_text(json.dumps({"forward": 4, "total_forwards": 10}))
            res2s = panel._preview_progress(
                {"id": "ltx-job", "params": {"quality": "high"}}, 30, "denoise"
            )["preview"]
            self.assertEqual(res2s["estimate"], 2)
            self.assertTrue(res2s["meaningful"])

    def test_first_h3_forward_is_meaningful_and_abortable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(panel, "STATE_DIR", Path(tmp)), \
                mock.patch.object(panel, "h3_live_preview_ready", return_value=True):
            directory = panel.live_preview_dir("h3-job")
            directory.mkdir(parents=True)
            (directory / "preview_latest.png").write_bytes(b"png")
            (directory / "status.json").write_text(json.dumps({
                "schema": "h3-live-preview/1",
                "status": "running",
                "forward": 1,
                "total_forwards": 3,
                "window": 1,
                "total_windows": 1,
                "eta_seconds": 49.4,
            }))
            current = {"id": "h3-job", "progress": {"phase": "denoise"}}
            raw = panel._h3_preview_progress(current)["live_preview"]
            self.assertTrue(raw["meaningful"])
            self.assertTrue(raw["abortable"])
            self.assertEqual(raw["preview_step"], 1)
            self.assertEqual(raw["preview_total"], 3)
            self.assertEqual(raw["remaining_sec"], 49.4)
            self.assertIn("/image?path=", raw["preview_url"])
            current["progress"]["phase"] = "decode"
            self.assertFalse(panel._h3_preview_progress(current)["live_preview"]["abortable"])

    def test_unknown_h3_schema_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(panel, "STATE_DIR", Path(tmp)), \
                mock.patch.object(panel, "h3_live_preview_ready", return_value=True):
            directory = panel.live_preview_dir("future-job")
            directory.mkdir(parents=True)
            (directory / "status.json").write_text(json.dumps({
                "schema": "h3-live-preview/2", "forward": 1,
            }))
            self.assertEqual(panel._h3_preview_progress({"id": "future-job"}), {})

    def test_special_ltx_lane_gets_no_preview_job_spec(self) -> None:
        with mock.patch.object(panel, "live_preview_enabled", return_value=True):
            self.assertEqual(panel._live_preview_params(
                {"id": "extend-job"}, {"mode": "extend", "quality": "high"}), {})
            self.assertIn("live_preview_dir", panel._live_preview_params(
                {"id": "t2v-job"}, {"mode": "t2v", "quality": "quick"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
