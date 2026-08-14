#!/usr/bin/env python3
"""Executable receipts for the Settings-level Spicy/NSFW contract.

The panel ships its UI as markup and JavaScript embedded in
``mlx_ltx_panel.py``. These tests extract and execute those real functions
against a small DOM shim, then exercise the Python search boundary. This keeps
the receipt tied to what the rendered page and HTTP handler actually do.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-spicy-contract-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8298")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_element, extract_function  # noqa: E402

import mlx_ltx_panel as P  # noqa: E402

NODE = shutil.which("node")
# Exercise the server-rendered page, after bootstrap/profile/engine substitutions,
# rather than the Python source template. This is the exact HTML a GET / serves.
SOURCE = P.page()


DOM_SHIM = r"""
const _els = {};
function _mk(id, props) {
  const el = Object.assign({
    id, hidden: false, checked: false, value: '', textContent: '',
    className: '', innerHTML: '', style: {}, dataset: {}, disabled: false,
    classList: { toggle(){}, add(){}, remove(){} },
  }, props || {});
  _els[id] = el;
  return el;
}
global.document = {
  getElementById: (id) => _els[id] || null,
  querySelectorAll: (selector) => selector === '[data-spicy-only]'
    ? Object.values(_els).filter(el => Object.prototype.hasOwnProperty.call(el.dataset, 'spicyOnly'))
    : [],
  body: {dataset: {}},
};
global.escapeHtml = (value) => String(value);
"""


def run_node(script: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    proc = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=30,
    )
    if proc.returncode:
        raise AssertionError(f"node failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def js_function(name: str) -> str:
    return extract_function(name, SOURCE)


def model_card(model_id: int, *, nsfw: bool) -> dict:
    return {
        "id": model_id,
        "name": f"model-{model_id}",
        "nsfw": nsfw,
        "modelVersions": [{
            "id": model_id * 10,
            "files": [{
                "name": f"model-{model_id}.safetensors",
                "primary": True,
                "downloadUrl": f"https://civitai.com/api/download/models/{model_id * 10}",
            }],
            "images": [],
        }],
    }


class TestRenderedSpicyGate(unittest.TestCase):
    def test_static_markup_starts_hidden(self):
        markup = extract_element("civitaiNsfwPill", SOURCE)
        self.assertIn("data-spicy-only", markup)
        self.assertRegex(markup, r"\bhidden\b")

    def test_all_four_engine_setting_states(self):
        result = run_node(DOM_SHIM + """
let _settingsCache = {settings: {spicy_mode: false}};
%s
%s
const pill = _mk('civitaiNsfwPill', {hidden: true, dataset: {spicyOnly: ''}});
const checkbox = _mk('civitaiNsfw', {checked: true});
const receipts = [];
for (const engine of ['ltx', 'h3']) {
  for (const enabled of [true, false]) {
    document.body.dataset.engine = engine;
    _settingsCache.settings.spicy_mode = enabled;
    checkbox.checked = true;
    renderSpicyAccess();
    receipts.push({engine, enabled, visible: !pill.hidden, checked: checkbox.checked});
  }
}
console.log(JSON.stringify({receipts}));
""" % (js_function("spicyModeEnabled"), js_function("renderSpicyAccess")))

        self.assertEqual(result["receipts"], [
            {"engine": "ltx", "enabled": True, "visible": True, "checked": True},
            {"engine": "ltx", "enabled": False, "visible": False, "checked": False},
            {"engine": "h3", "enabled": True, "visible": True, "checked": True},
            {"engine": "h3", "enabled": False, "visible": False, "checked": False},
        ])

    def test_hidden_checked_control_cannot_submit_nsfw(self):
        result = run_node(DOM_SHIM + """
let _settingsCache = {settings: {spicy_mode: false}};
let _civitaiSearching = false;
let _civitaiCursor = '';
let _civitaiContext = 'video';
let _civitaiFamily = 'h3';
%s
%s
%s
_mk('civitaiNsfw', {checked: true});
_mk('civitaiQuery', {value: ''});
_mk('civitaiGrid');
_mk('civitaiStatus');
_mk('civitaiLoadMore');
global.civitaiRenderFamilyPills = () => {};
global.renderCivitaiGrid = () => {};
global._civitaiContextMeta = () => ({empty: 'No video LoRAs match'});
let requested = null;
global.fetch = async (url) => {
  requested = String(url);
  return {json: async () => ({items: [], has_more: false, next_cursor: ''})};
};
(async () => {
  await civitaiSearch();
  console.log(JSON.stringify({requested}));
})().catch(error => { console.error(error); process.exit(3); });
""" % (
            js_function("spicyModeEnabled"),
            js_function("civitaiNsfwRequested"),
            js_function("civitaiSearch"),
        ))
        self.assertNotIn("nsfw=true", result["requested"])


class TestServerSpicyGate(unittest.TestCase):
    def test_off_forces_safe_query_and_filters_nsfw_response(self):
        captured = {}
        old_get_settings = P.get_settings
        old_request = P._civitai_request
        try:
            P.get_settings = lambda: {"spicy_mode": False}

            def fake_request(path, params=None, **_kwargs):
                captured.update({"path": path, "params": params})
                return {
                    "items": [model_card(1, nsfw=False), model_card(2, nsfw=True)],
                    "metadata": {},
                }

            P._civitai_request = fake_request
            result = P._civitai_search(nsfw=True, family="h3")
        finally:
            P.get_settings = old_get_settings
            P._civitai_request = old_request

        self.assertEqual(captured["params"]["nsfw"], "false")
        self.assertEqual([item["id"] for item in result["items"]], [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
