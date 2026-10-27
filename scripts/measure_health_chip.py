#!/usr/bin/env python3.11
"""A REAL browser gate: the header's health chip (the memory readout) is on
screen after boot, in both distributions, at every width it was designed for.

WHY THIS EXISTS (4.19.11). From v4.17.0 to v4.19.10 the chip was gone from
every install, Pinokio and the Mac app alike. `#healthCluster` carried a
stray `hidden` attribute, left over from the duplicate v4.5.0 build
(989aaf1f kept this markup but not that build's intent) and cleared by no
code at all. It only ever showed because `body > header #healthCluster
{ display: flex }` outranked the UA's `[hidden] { display: none }`. The
global `[hidden] { display: none !important }` rule (VC-07, 048464f) made
`hidden` win, as it should, and the chip vanished. Every static test still
passed: the markup, the CSS and `updateHealthChip()` were all intact, and
`#memPill` was still updated on every poll. Only a browser could tell that
nobody could see it.

What it asserts, per distribution (`PHOSPHENE_DISTRIBUTION` unset = Pinokio,
`mac-app` = the Mac app) and per width (1300 = Pinokio's window, 1440 = a
14-inch laptop, 1500, 1920):
  * `#healthCluster` and `#healthChip` do not compute `display: none`, have a
    real box, and sit inside the header and the viewport;
  * the chip is not clipped by the cluster's own `overflow: hidden` and is
    the element actually hit at its centre (nothing covers it);
  * the face shows the memory reading once /status has answered: the full
    "used/total GB · N%" text above 1400 px, the percentage below it, and
    it is PAINTED (a real width; not ellipsized from 1440 px up);
  * one click opens the popover, and its Memory row is visible.

`--url` measures a panel that is already running instead of booting one
(read-only: it only loads the page and clicks the chip). `--shots DIR`
saves a PNG of the header at each width.

Exit codes: 0 pass, 1 a measurement FAILED, 3 no browser (unittest skips),
4 the harness could not get far enough to measure.

    ./ltx-2-mlx/env/bin/python3.11 scripts/measure_health_chip.py -v
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_PORTS = {8199}
WIDTHS = (1300, 1440, 1500, 1920)
NARROW = 1400            # panel.css: below this the face shows data-short
DISTRIBUTIONS = ("pinokio", "mac-app")


def _load_editor_gate():
    p = ROOT / "scripts" / "measure_editor_layout.py"
    spec = importlib.util.spec_from_file_location("_phos_editor_gate", p)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load_editor_gate()
HarnessError = G.HarnessError
NoBrowser = G.NoBrowser

JS_READY = """
(() => {
  if (typeof updateHealthChip !== 'function') return 'app not booted';
  const f = document.getElementById('hcFace');
  if (!f) return 'no #hcFace';
  const t = (f.textContent || '').trim();
  return (t && t !== 'checking…') ? 'ok' : 'face still ' + JSON.stringify(t);
})()
"""

JS_MEASURE = r"""
(() => {
  const box = el => { if (!el) return null; const r = el.getBoundingClientRect();
    return {x: r.left, y: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height}; };
  const header = document.querySelector('body > header');
  const cl = document.getElementById('healthCluster');
  const chip = document.getElementById('healthChip');
  const face = document.getElementById('hcFace');
  const mem = document.getElementById('memPill');
  const cb = box(chip);
  let hit = null;
  if (cb && cb.w > 0 && cb.h > 0) {
    const el = document.elementFromPoint(cb.x + cb.w / 2, cb.y + cb.h / 2);
    hit = el ? (chip.contains(el) ? 'chip' : (el.id || el.tagName)) : 'nothing';
  }
  let shown = '';
  if (face) {
    const after = getComputedStyle(face, '::after').content;
    shown = (after && after !== 'none' && after !== 'normal')
      ? after.replace(/^"|"$/g, '') : (face.textContent || '').trim();
  }
  return {
    vw: innerWidth,
    clusterHiddenAttr: cl ? cl.hasAttribute('hidden') : null,
    clusterDisplay: cl ? getComputedStyle(cl).display : null,
    chipDisplay: chip ? getComputedStyle(chip).display : null,
    chipVisibility: chip ? getComputedStyle(chip).visibility : null,
    header: box(header), cluster: box(cl), chip: cb, hit,
    faceText: face ? (face.textContent || '').trim() : null,
    faceShort: face ? (face.dataset.short || '') : null,
    memoryWarning: !!(mem && (mem.classList.contains('pill-warn') || mem.classList.contains('pill-danger'))),
    chipWarning: !!(chip && (chip.classList.contains('is-warn') || chip.classList.contains('is-danger'))),
    shown,
    // What is PAINTED, not what the text says: a face squeezed to 0 px
    // still has its text (that was the narrow-width bug).
    faceBox: box(face),
    faceClipped: face ? face.scrollWidth > face.clientWidth + 1 : null,
    memText: mem ? (mem.textContent || '').trim() : null,
  };
})()
"""

JS_OPEN_POP = r"""
(() => {
  const chip = document.getElementById('healthChip');
  if (!chip) return {error: 'no chip'};
  chip.click();
  const pop = document.getElementById('healthPop');
  const row = pop && pop.querySelector('.hc-row[data-pill="memPill"]');
  const mem = document.getElementById('memPill');
  const r = row ? row.getBoundingClientRect() : null;
  const out = {
    popOpen: !!(pop && !pop.hidden && getComputedStyle(pop).display !== 'none'),
    expanded: chip.getAttribute('aria-expanded'),
    memRowVisible: !!(r && r.width > 0 && r.height > 0),
    memInRow: !!(row && mem && row.contains(mem)),
    memText: mem ? (mem.textContent || '').trim() : null,
  };
  if (typeof closeHealthPop === 'function') closeHealthPop();
  return out;
})()
"""


def check(dist: str, width: int, m: dict) -> list[str]:
    tag = f"{dist}@{width}"
    out: list[str] = []
    if m.get("clusterHiddenAttr"):
        out.append(f"{tag}: #healthCluster still carries the `hidden` attribute")
    if m.get("clusterDisplay") in (None, "none"):
        out.append(f"{tag}: #healthCluster computes display:{m.get('clusterDisplay')}")
    if m.get("chipDisplay") in (None, "none") or m.get("chipVisibility") == "hidden":
        out.append(f"{tag}: #healthChip computes display:{m.get('chipDisplay')} "
                   f"visibility:{m.get('chipVisibility')}")
        return out
    hb, cl, ch = m.get("header"), m.get("cluster"), m.get("chip")
    if not ch or ch["w"] < 24 or ch["h"] < 12:
        out.append(f"{tag}: the chip has no real box: {ch}")
        return out
    if ch["x"] < 0 or ch["r"] > m["vw"] + 0.5:
        out.append(f"{tag}: the chip runs off the viewport: {ch}")
    if hb and (ch["y"] < hb["y"] - 0.5 or ch["b"] > hb["b"] + 0.5):
        out.append(f"{tag}: the chip is outside the header: chip {ch} header {hb}")
    # #healthCluster is overflow:hidden - a chip wider than it is sliced.
    if cl and (ch["x"] < cl["x"] - 0.5 or ch["r"] > cl["r"] + 0.5):
        out.append(f"{tag}: the cluster clips the chip: chip {ch} cluster {cl}")
    if m.get("hit") != "chip":
        out.append(f"{tag}: something covers the chip's centre: {m.get('hit')}")
    fb = m.get("faceBox") or {}
    if (fb.get("w") or 0) < 14:
        out.append(f"{tag}: the face paints {fb.get('w')} px wide - the reading "
                   f"is there but nobody can see it")
    # 1440 is the 14-inch laptop the CSS was tuned for: the whole reading fits.
    if width >= 1440 and m.get("faceClipped"):
        out.append(f"{tag}: the face is ellipsized at {width} px")
    shown = m.get("shown") or ""
    if width < NARROW:
        # A busy Mac intentionally names the warning at narrow widths. The
        # percentage still has to exist in the Memory row; a bare label with
        # no actual warning/readout must not make this visibility gate pass.
        memory_warning = (shown == "memory" and m.get("memoryWarning")
                          and m.get("chipWarning") and "GB" in (m.get("memText") or "")
                          and "%" in (m.get("memText") or ""))
        if not shown.endswith("%") and not memory_warning:
            out.append(f"{tag}: narrow face should show the percentage or a confirmed memory warning, shows {shown!r}")
    else:
        if "GB" not in shown or "%" not in shown:
            out.append(f"{tag}: the face should carry the memory reading, shows {shown!r}")
    return out


def _wait_ready(cdp) -> None:
    deadline = time.monotonic() + 45.0
    while cdp.evaluate("document.readyState") != "complete":
        if time.monotonic() > deadline:
            raise HarnessError("the panel page never finished loading")
        time.sleep(0.2)
    deadline = time.monotonic() + 40.0
    why = "never checked"
    while time.monotonic() < deadline:
        why = cdp.evaluate(JS_READY)
        if why == "ok":
            return
        time.sleep(0.3)
    # Not a harness error: a chip that never fills IS the bug.
    raise HarnessError(f"the health chip never filled in: {why}")


def _shot(cdp, path: Path, width: int, header: dict | None) -> None:
    h = int((header or {}).get("b") or 64) + 6
    r = cdp.call("Page.captureScreenshot",
                 {"format": "png", "captureBeyondViewport": False,
                  "clip": {"x": 0, "y": 0, "width": width, "height": h,
                           "scale": 1}})
    path.write_bytes(base64.b64decode(r["data"]))


def measure_base(cdp, base: str, dist: str, shots: Path | None,
                 verbose: bool) -> tuple[list[str], list[dict]]:
    failures: list[str] = []
    rows: list[dict] = []
    for width in WIDTHS:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": width, "height": 900, "deviceScaleFactor": 1,
                  "mobile": False})
        cdp.call("Page.navigate", {"url": base + "/"})
        try:
            _wait_ready(cdp)
        except HarnessError as exc:
            if "never filled" not in str(exc):
                raise
            failures.append(f"{dist}@{width}: {exc}")
        # The TEST PANEL badge (mlx_ltx_panel.py page(): any panel whose state
        # dir is not the install's own) is ~90 px that no user ever sees, and
        # the cluster is the header's one shrinkable region, so leaving it in
        # would measure a header nobody has. Only the throwaway panel this
        # gate boots carries it; a --url run against a real install has none.
        cdp.evaluate("document.querySelectorAll('body > header .test-badge')"
                     ".forEach(e => e.remove())")
        time.sleep(0.4)                      # one layout after the face text
        m = cdp.evaluate(JS_MEASURE, timeout=15.0)
        if not isinstance(m, dict):
            failures.append(f"{dist}@{width}: the page returned {m!r}")
            continue
        f = check(dist, width, m)
        if not f:
            p = cdp.evaluate(JS_OPEN_POP, timeout=15.0) or {}
            if not (p.get("popOpen") and p.get("memRowVisible") and p.get("memInRow")):
                f.append(f"{dist}@{width}: clicking the chip did not show the "
                         f"Memory row: {p}")
        failures += f
        rows.append({"dist": dist, "width": width, "shown": m.get("shown"),
                     "chip": m.get("chip"), "ok": not f})
        if verbose:
            print(f"{dist}@{width}: shows {m.get('shown')!r} "
                  f"chip={m.get('chip')} {'OK' if not f else 'FAIL'}",
                  file=sys.stderr)
        if shots is not None:
            _shot(cdp, shots / f"chrome_{dist}_{width}.png", width, m.get("header"))
    return failures, rows


def run(verbose: bool, url: str | None, label: str | None,
        shots: Path | None) -> tuple[dict, int]:
    binary = G.find_browser()
    work = Path(tempfile.mkdtemp(prefix="phos-healthchip-gate-"))
    failures: list[str] = []
    rows: list[dict] = []
    targets = [(label or "url", url)] if url else list(DISTRIBUTIONS)
    try:
        for target in targets:
            dist, fixed = (target if isinstance(target, tuple) else (target, None))
            panel = chrome = ws = None
            old = os.environ.get("PHOSPHENE_DISTRIBUTION")
            sub = work / dist
            (sub / "chrome").mkdir(parents=True)
            try:
                if fixed:
                    base = fixed.rstrip("/")
                else:
                    if dist == "mac-app":
                        os.environ["PHOSPHENE_DISTRIBUTION"] = "mac-app"
                    else:
                        os.environ.pop("PHOSPHENE_DISTRIBUTION", None)
                    panel, base = G.start_panel(sub, sub / "panel.log", verbose)
                if base.rsplit(":", 1)[-1] in {str(p) for p in FORBIDDEN_PORTS}:
                    raise HarnessError("refusing to measure on a forbidden port")
                chrome, devtools = G.start_chrome(binary, sub / "chrome", verbose)
                ws = G.WebSocket(G.page_socket(devtools))
                cdp = G.CDP(ws)
                cdp.call("Page.enable")
                cdp.call("Runtime.enable")
                f, r = measure_base(cdp, base, dist, shots, verbose)
                if not fixed:
                    served = cdp.evaluate(
                        "(typeof BOOT === 'object' && BOOT) ? BOOT.distribution : null")
                    if served != dist:
                        f.append(f"{dist}: the panel booted as {served!r}, "
                                 f"not {dist!r}")
                failures += f
                rows += r
            finally:
                if old is None:
                    os.environ.pop("PHOSPHENE_DISTRIBUTION", None)
                else:
                    os.environ["PHOSPHENE_DISTRIBUTION"] = old
                if ws is not None:
                    ws.close()
                G.kill(chrome)
                G.kill(panel)
        return ({"ok": not failures, "failures": failures, "rows": rows},
                1 if failures else 0)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--url", help="measure this running panel instead of booting one")
    ap.add_argument("--label", help="name for --url in the report and shot names")
    ap.add_argument("--shots", help="save a header PNG per width into this dir")
    args = ap.parse_args(argv)
    shots = Path(args.shots) if args.shots else None
    if shots is not None:
        shots.mkdir(parents=True, exist_ok=True)
    try:
        doc, code = run(args.verbose, args.url, args.label, shots)
    except NoBrowser as exc:
        print(f"no browser: {exc}", file=sys.stderr)
        return 3
    except HarnessError as exc:
        print(json.dumps({"ok": False, "failures": [f"harness: {exc}"]}))
        print(f"harness: {exc}", file=sys.stderr)
        return 4
    print(json.dumps(doc, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
