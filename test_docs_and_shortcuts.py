#!/usr/bin/env python3
"""The keyboard table and the Docs: one source, and nothing may drift from it.

webapp/js/shortcuts.js is the ONE list of shortcuts. The Docs page renders it,
the Editor's Keys popover renders it, the tooltips read it. These receipts pin
the four ways that could still go wrong:

  * a shortcut in the table that the Docs page does not show;
  * a Docs link — in the nav, in a page, in the panel's code — that lands
    nowhere;
  * a shortcut that claims a chord the browser or macOS keeps (⌘R was the
    Editor's Render key: a reload queued a render);
  * the Editor's real keydown handler and the table disagreeing — driven for
    real in node with every Editor row's keys, in both directions.

Plus the one new key that can spend a render: ⌘⏎ goes through the Generate
button's own submit path and does nothing the button would refuse.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "webapp"
DOCS = WEB / "docs"
NODE = shutil.which("node")


def _node(script: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "harness.mjs"
        f.write_text(script, encoding="utf-8")
        r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError("node harness failed:\n" + r.stderr[-3000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def _url(p: Path) -> str:
    return p.resolve().as_uri()


def _load() -> dict:
    return _node(f"""
import {{ installShim }} from '{_url(ROOT / "scripts" / "webapp_import_shim.mjs")}';
import fs from 'node:fs';
installShim();
await import('{_url(WEB / "js" / "shortcuts.js")}');
await import('{_url(WEB / "js" / "docs.js")}');
const out = {{ shortcuts: [], reserved: SHORTCUT_RESERVED.map(shortcutNormalise), norm: {{}},
              sections: DOCS_SECTIONS, rendered: {{}} }};
for (const s of SHORTCUTS) {{
  out.shortcuts.push({{ id: s.id, scope: s.scope, combos: s.combos || [], hidden: s.hidden || [],
                        calls: s.calls || null, run: !!s.run, typing: !!s.typing }});
  for (const c of [...(s.combos || []), ...(s.hidden || [])]) out.norm[c] = shortcutNormalise(c);
}}
for (const sec of DOCS_SECTIONS) {{
  const md = fs.readFileSync('{DOCS.as_posix()}/' + sec.file, 'utf8');
  const r = docsRender(md, sec.id);
  out.rendered[sec.id] = {{ html: r.html, anchors: r.headings.map(h => h.anchor) }};
}}
console.log(JSON.stringify(out));
""")


@unittest.skipUnless(NODE, "node not on PATH")
class TheDocsShowTheTable(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = _load()
        cls.ids = [s["id"] for s in cls.r["shortcuts"]]

    def test_every_shortcut_appears_on_the_shortcuts_page(self):
        page = self.r["rendered"]["shortcuts"]["html"]
        for sid in self.ids:
            self.assertIn(f'data-shortcut="{sid}"', page, sid)

    def test_ids_are_unique(self):
        self.assertEqual(len(self.ids), len(set(self.ids)))

    def test_every_nav_section_has_a_page_with_a_title(self):
        for sec in self.r["sections"]:
            self.assertTrue((DOCS / sec["file"]).is_file(), sec["file"])
            self.assertTrue(self.r["rendered"][sec["id"]]["html"].startswith(f'<h1 id="docs-{sec["id"]}">'),
                            sec["id"])
        # every page on disk is in the nav — an orphan page is unreachable
        on_disk = sorted(p.name for p in DOCS.glob("*.md"))
        self.assertEqual(on_disk, sorted(s["file"] for s in self.r["sections"]))

    def test_every_docs_link_lands_on_a_heading(self):
        sections = {s["id"]: set(self.r["rendered"][s["id"]]["anchors"]) for s in self.r["sections"]}
        sources = [*DOCS.glob("*.md"), WEB / "index.html", *(WEB / "js").glob("*.js")]
        found = 0
        for f in sources:
            for m in re.finditer(r"#docs/([\w-]+)(?:/([\w-]+))?", f.read_text(encoding="utf-8")):
                sec, anchor = m.group(1), m.group(2)
                if sec == "' + sec.id + '" or "+" in m.group(0):
                    continue
                found += 1
                self.assertIn(sec, sections, f"{f.name}: {m.group(0)}")
                if anchor:
                    self.assertIn(anchor, sections[sec], f"{f.name}: {m.group(0)}")
        self.assertGreater(found, 10)
        # openDocs('<section>', '<anchor>') calls in the panel's code, too
        for f in [WEB / "index.html", *(WEB / "js").glob("*.js")]:
            for m in re.finditer(r"openDocs\('([\w-]+)'(?:,\s*'([\w-]+)')?\)", f.read_text(encoding="utf-8")):
                self.assertIn(m.group(1), sections, m.group(0))
                if m.group(2):
                    self.assertIn(m.group(2), sections[m.group(1)], m.group(0))

    def test_every_key_named_in_a_page_exists(self):
        for sec, r in self.r["rendered"].items():
            self.assertNotIn("docs-missing", r["html"], sec)

    def test_every_tooltip_names_a_real_shortcut(self):
        text = (WEB / "index.html").read_text(encoding="utf-8")
        for m in re.finditer(r'data-shortcut="([\w.-]+)"', text):
            self.assertIn(m.group(1), self.ids, m.group(0))
        for f in (WEB / "js").glob("*.js"):
            for m in re.finditer(r"(?:sbeKeyHint|shortcutHint)\('([\w.-]+)'\)", f.read_text(encoding="utf-8")):
                self.assertIn(m.group(1), self.ids, f"{f.name}: {m.group(0)}")

    def test_no_shortcut_claims_a_reserved_chord(self):
        reserved = set(self.r["reserved"])
        for c in ("mod+r", "mod+w", "mod+q", "mod+t", "mod+n", "mod+l", "mod+h", "mod+m",
                  "mod+tab", "mod+backquote", "mod+space"):
            self.assertIn(c, reserved, c)
        for s in self.r["shortcuts"]:
            for c in s["combos"] + s["hidden"]:
                self.assertNotIn(self.r["norm"][c], reserved, f"{s['id']} uses {c}")

    def test_only_the_explicit_keys_fire_while_typing(self):
        typing = sorted(s["id"] for s in self.r["shortcuts"] if s["typing"])
        self.assertEqual(typing, ["prompt.generate"])


# ---- the Editor's real handler, driven with the table's keys --------------------
_KEY = {"space": " ", "escape": "Escape", "backspace": "Backspace", "delete": "Delete",
        "arrowleft": "ArrowLeft", "arrowright": "ArrowRight", "arrowup": "ArrowUp",
        "arrowdown": "ArrowDown", "home": "Home", "end": "End", "plus": "+", "minus": "-",
        "equals": "=", "underscore": "_", "backslash": "\\", "enter": "Enter"}

# State each row needs for its branch to be the one that answers.
_STATE = {
    "editor.removeOverlay": {"sel": None, "ovSel": "o1"},
    "editor.removeTransition": {"txSel": "t1"},
}
# Called on the way to an action, or to read state — not actions of their own.
_HELPERS = {"sbeFps", "sbeFilmDuration", "sbePopAnyOpen", "sbeStop"}
# Esc closes an open menu or the Versions panel first — documented on the
# Esc rows, not rows of their own.
_ESC_COMPANIONS = {"sbePopCloseAll", "sbeVersionsClose"}


def _editor_listener(src: str) -> str:
    mark = "if (!SBE.open || document.body.dataset.workflow !== 'editor') return;"
    i = src.index(mark)
    j = src.rindex("document.addEventListener('keydown', (ev) => {", 0, i)
    body = src[j + len("document.addEventListener('keydown', (ev) => {"):]
    return body[:body.index("\n});")]


def _event(combo: str) -> dict:
    parts = combo.lower().split("+")
    key = parts[-1]
    mods = set(parts[:-1])
    k = _KEY.get(key, key)
    if len(k) == 1 and k.isalpha() and "shift" in mods:
        k = k.upper()
    return {"key": k, "code": "", "metaKey": "mod" in mods, "ctrlKey": False,
            "shiftKey": "shift" in mods, "altKey": "alt" in mods}


@unittest.skipUnless(NODE, "node not on PATH")
class TheEditorHandlerAgreesWithTheTable(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = (WEB / "js" / "editor.js").read_text(encoding="utf-8")
        cls.body = _editor_listener(cls.src)
        cls.rows = [s for s in _load()["shortcuts"] if s["scope"] == "editor"]
        names = sorted(set(re.findall(r"\b(sbe\w+)\(", cls.body)))
        cases = []
        for s in cls.rows:
            for c in s["combos"] + s["hidden"]:
                cases.append({"id": s["id"], "combo": c, "ev": _event(c), "state": _STATE.get(s["id"], {})})
        stubs = "\n".join(
            f"function {n}(...a) {{ calls.push('{n}'); "
            + ("return 24; }" if n == "sbeFps" else "return 10; }" if n == "sbeFilmDuration"
               else "return false; }" if n == "sbePopAnyOpen" else "}")
            for n in names)
        script = f"""
let calls = [];
let SBE;
const document = {{
  body: {{ dataset: {{ workflow: 'editor' }} }},
  querySelector: () => null,
  getElementById: () => ({{ hidden: true }}),
}};
{stubs}
const listener = (ev) => {{
{cls.body}
}};
const cases = {json.dumps(cases)};
const out = [];
for (const c of cases) {{
  SBE = Object.assign({{ open: true, sel: 'c1', ovSel: null, txSel: null, playhead: 1, muted: false }}, c.state);
  calls = [];
  const ev = Object.assign({{ target: {{ tagName: 'DIV', id: '' }}, prevented: false,
                             preventDefault() {{ this.prevented = true; }} }}, c.ev);
  listener(ev);
  out.push({{ id: c.id, combo: c.combo, calls, prevented: ev.prevented }});
}}
console.log(JSON.stringify({{ out, names: {json.dumps(names)} }}));
"""
        cls.result = _node(script)

    def test_every_editor_row_runs_the_function_it_names(self):
        want = {s["id"]: ([s["calls"]] if isinstance(s["calls"], str) else s["calls"]) for s in self.rows}
        for r in self.result["out"]:
            expected = set(want[r["id"]] or [])
            self.assertTrue(expected, f"{r['id']} has no `calls`")
            actions = [c for c in r["calls"] if c not in _HELPERS]
            self.assertTrue(expected & set(actions),
                            f"{r['id']} {r['combo']}: handler called {r['calls']}, table says {sorted(expected)}")
            self.assertTrue(set(actions) <= expected | _ESC_COMPANIONS,
                            f"{r['id']} {r['combo']}: also called {actions}")
            self.assertTrue(r["prevented"], f"{r['id']} {r['combo']} left the browser default on")

    def test_every_action_the_handler_takes_is_in_the_table(self):
        named = set()
        for s in self.rows:
            named |= {s["calls"]} if isinstance(s["calls"], str) else set(s["calls"] or [])
        for n in self.result["names"]:
            if n in _HELPERS or n in _ESC_COMPANIONS:
                continue
            self.assertIn(n, named, f"the Editor answers a key with {n}() and the shortcut table does not list it")

    def test_render_is_not_on_the_reload_chord(self):
        self.assertNotIn("(ev.key === 'r' || ev.key === 'R') && (ev.metaKey || ev.ctrlKey)", self.body)


@unittest.skipUnless(NODE, "node not on PATH")
class CommandReturnIsTheGenerateButton(unittest.TestCase):
    """⌘⏎ can spend a render, so it must be exactly the button: the same
    submit listener, and nothing the button would refuse."""

    def _run(self, target_id: str, disabled: bool) -> dict:
        return _node(f"""
import {{ installShim, el }} from '{_url(ROOT / "scripts" / "webapp_import_shim.mjs")}';
installShim();
globalThis.phosToast = (m) => (globalThis._toasts ||= []).push(m);
const btn = el('genBtn', {{ disabled: {str(disabled).lower()}, title: 'Needs Q8' }});
const form = el('genForm');
form.requestSubmit = (b) => {{ form.submitted = (form.submitted || 0) + 1; form.submitter = b && b.id; }};
const ibtn = el('imgStudioGenBtn', {{ disabled: {str(disabled).lower()} }});
globalThis.imgStudioGenerate = () => {{ globalThis._img = (globalThis._img || 0) + 1; }};
await import('{_url(WEB / "js" / "shortcuts.js")}');
const row = SHORTCUTS.find(s => s.id === 'prompt.generate');
const ev = {{ key: 'Enter', metaKey: true, ctrlKey: false, shiftKey: false, altKey: false,
             target: {{ id: '{target_id}', tagName: 'TEXTAREA' }} }};
const matched = row.combos.some(c => shortcutMatches(ev, c));
const res = row.run(ev);
console.log(JSON.stringify({{ matched, res: res === false ? 'declined' : 'handled',
  submitted: form.submitted || 0, submitter: form.submitter || '', img: globalThis._img || 0,
  toasts: globalThis._toasts || [] }}));
""")

    def test_video_prompt_submits_through_the_generate_button(self):
        r = self._run("prompt", False)
        self.assertTrue(r["matched"])
        self.assertEqual((r["submitted"], r["submitter"]), (1, "genBtn"))
        self.assertIn("requestSubmit(btn)", (WEB / "js" / "shortcuts.js").read_text(encoding="utf-8"))
        self.assertIn("document.getElementById('genForm').addEventListener('submit'",
                      (WEB / "js" / "queue.js").read_text(encoding="utf-8"))

    def test_a_disabled_generate_is_not_bypassed(self):
        r = self._run("prompt", True)
        self.assertEqual(r["submitted"], 0)
        self.assertEqual(r["toasts"], ["Needs Q8"])
        self.assertEqual(self._run("imgStudioPrompt", True)["img"], 0)

    def test_images_prompt_generates_and_other_boxes_are_left_alone(self):
        self.assertEqual(self._run("imgStudioPrompt", False)["img"], 1)
        r = self._run("osPrompt", False)          # One Shot has its own listener
        self.assertEqual((r["res"], r["submitted"], r["img"]), ("declined", 0, 0))


@unittest.skipUnless(NODE, "node not on PATH")
class TheMatcher(unittest.TestCase):

    def test_chords_match_the_way_a_mac_keyboard_sends_them(self):
        r = _node(f"""
import {{ installShim }} from '{_url(ROOT / "scripts" / "webapp_import_shim.mjs")}';
installShim();
await import('{_url(WEB / "js" / "shortcuts.js")}');
const e = (o) => Object.assign({{ key: '', code: '', metaKey: false, ctrlKey: false, shiftKey: false, altKey: false }}, o);
console.log(JSON.stringify({{
  question: shortcutMatches(e({{ key: '?', shiftKey: true }}), '?'),
  shiftDigit: shortcutMatches(e({{ key: '#', code: 'Digit3', shiftKey: true }}), 'shift+digit3'),
  bareDigit: shortcutMatches(e({{ key: '3', code: 'Digit3' }}), 'shift+digit3'),
  cmdS: shortcutMatches(e({{ key: 's', metaKey: true }}), 'mod+s'),
  ctrlS: shortcutMatches(e({{ key: 's', ctrlKey: true }}), 'mod+s'),
  plainSisNotCmdS: shortcutMatches(e({{ key: 's' }}), 'mod+s'),
  redo: shortcutMatches(e({{ key: 'Z', metaKey: true, shiftKey: true }}), 'shift+mod+z'),
  undoIsNotRedo: shortcutMatches(e({{ key: 'z', metaKey: true }}), 'shift+mod+z'),
  hint: shortcutHint('editor.redo'),
  split: shortcutHint('editor.split'),
}}));
""")
        self.assertEqual(r, {"question": True, "shiftDigit": True, "bareDigit": False, "cmdS": True,
                             "ctrlS": True, "plainSisNotCmdS": False, "redo": True,
                             "undoIsNotRedo": False, "hint": "⇧⌘Z", "split": "S or ⌘K or ⌘B"})


if __name__ == "__main__":
    unittest.main()
