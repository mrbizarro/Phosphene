#!/usr/bin/env python3
"""i18n_wrap.py — route inline JS copy through the Studio lookup.

    python3 scripts/i18n_tool.py wrap webapp/js/queue.js [more.js …]

Rewrites, byte-for-byte safe in English:
  'Saved.'                         -> T('queue.saved', 'Saved.')
  `Saved ${n} clips`               -> T('queue.saved_clips', 'Saved {n} clips', {n: n})
  `<b class="x">Hello ${who}</b>`  -> `<b class="x">${T('queue.hello', 'Hello {who}', {who: who})}</b>`
  `<i title="Open the log">`       -> `<i title="${TA('queue.open_the_log', 'Open the log')}">`

T() returns the English untouched when there is no translation, and fills
{name} with String(value) — exactly what ${value} did. A literal is left
alone when it looks like data rather than copy: compared (===, case,
includes), used as a key, sent to the server (FormData, JSON.stringify),
or passed to a DOM query. Run it again after a pass: interpolations moved
into a T() vars object are wrapped on the next pass.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import i18n_tool as K

_SKIP_BEFORE = re.compile(
    r"(console\.\w+|querySelector(All)?|getElementById|getElementsBy\w+|addEventListener|removeEventListener"
    r"|classList\.\w+|getAttribute|hasAttribute|removeAttribute|setAttribute\(\s*['\"](?!title|placeholder|aria-label|alt)[\w-]+['\"]\s*,"
    r"|matches|matchMedia|closest|\bimport|\bfrom|fetch|_?fetchJSON|dataset\.?\w*|localStorage\.\w+|sessionStorage\.\w+|new RegExp"
    r"|\.test|\.match|\.split|\.replace(All)?|\.includes|\.startsWith|\.endsWith|\.indexOf|\.lastIndexOf"
    r"|\.has|\.get|\.delete|\.set|\.append|URLSearchParams|createElement|createElementNS|\.toggle|\.add|\.remove"
    r"|\bT|\bTA|\bTP|postJSON|_post|_get|\.sendBeacon|\bnew Error|\bkey|Event|\.dispatchEvent|\.on)\s*\(\s*$")
_ASSIGN_DATA = re.compile(r"(className|\.id|\.type|\.href|\.src|\.name|\.value|\.style(\.\w+)?|cssText|\.rel|\.target"
                          r"|\.role|\.download|\.accept|\.kind|\.mode|\.status|\.state|\.action|\.method)\s*\+?=\s*$")
_CMP_BEFORE = re.compile(r"(===?|!==?|\bcase|\bin|\?\?|\|\||&&|[<>]=?)\s*$")
_CMP_AFTER = re.compile(r"^\s*(===?|!==?|\bin\b)")


def _is_key(src: str, a: int, b: int) -> bool:
    before = src[max(0, a - 3):a].rstrip()
    after = src[b:b + 3].lstrip()
    if after.startswith(":") and (before.endswith("{") or before.endswith(",") or before == ""):
        # `{ 'Label': …` / `, 'Label': …` — an object key, unless a ternary
        return not re.search(r"\?[^:;]*$", src[max(0, a - 200):a])
    if before.endswith("[") and after.startswith("]"):
        return True
    return False


def _in_data(src: str, a: int) -> bool:
    """Inside JSON.stringify(…) / a FormData body — values sent somewhere."""
    win = src[max(0, a - 400):a]
    for m in re.finditer(r"JSON\.stringify\s*\(", win):
        seg = win[m.end():]
        if seg.count("(") - seg.count(")") >= 0 and seg.count(")") <= seg.count("("):
            return True
    return False


def _raw_template(src: str, a: int, b: int):
    """Split a template literal src[a:b] into ('s', raw_static) and
    ('x', raw_expr) pieces (raw = exactly as written)."""
    i = a + 1
    end = b - 1
    pieces = []
    buf_start = i
    while i < end:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "$" and src[i + 1:i + 2] == "{":
            pieces.append(("s", src[buf_start:i]))
            j = i + 2
            d = 1
            while j < end and d:
                ch = src[j]
                if ch == "/" and src[j + 1:j + 2] in ("/", "*"):
                    k = src.find("\n" if src[j + 1] == "/" else "*/", j + 2)
                    j = len(src) if k < 0 else (k if src[j + 1] == "/" else k + 2); continue
                if ch in "'\"":
                    j = K._skip_str(src, j); continue
                if ch == "`":
                    j = _skip_tpl(src, j); continue
                if ch == "{":
                    d += 1
                elif ch == "}":
                    d -= 1
                    if d == 0:
                        break
                j += 1
            pieces.append(("x", src[i + 2:j]))
            i = j + 1
            buf_start = i
            continue
        i += 1
    pieces.append(("s", src[buf_start:end]))
    return pieces


def _skip_tpl(src, i):
    j = i + 1
    while j < len(src):
        c = src[j]
        if c == "\\":
            j += 2; continue
        if c == "`":
            return j + 1
        if c == "$" and src[j + 1:j + 2] == "{":
            d = 1; j += 2
            while j < len(src) and d:
                ch = src[j]
                if ch == "/" and src[j + 1:j + 2] in ("/", "*"):
                    k = src.find("\n" if src[j + 1] == "/" else "*/", j + 2)
                    j = len(src) if k < 0 else (k if src[j + 1] == "/" else k + 2); continue
                if ch in "'\"":
                    j = K._skip_str(src, j); continue
                if ch == "`":
                    j = _skip_tpl(src, j); continue
                d += (ch == "{") - (ch == "}")
                j += 1
            continue
        j += 1
    return j


def _js_str(s: str) -> str:
    """A JS single-quoted literal for a (decoded) string."""
    out = json.dumps(s, ensure_ascii=False)[1:-1]
    out = out.replace("\\\"", "\"").replace("'", "\\'")
    return "'" + out + "'"


def _var_name(expr: str, used: dict) -> str:
    e = expr.strip()
    m = re.fullmatch(r"(?:[A-Za-z_$][\w$]*\s*(?:\?\.|\.)\s*)*([A-Za-z_][A-Za-z0-9_]*)", e)
    base = m.group(1) if m else "v"
    if base in ("length", "value", "textContent", "innerHTML") and m:
        parts = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", e)
        base = parts[-2] if len(parts) >= 2 else base
    name, k = base, 2
    while name in used and used[name] != e:
        name = f"{base}{k}"; k += 1
    used[name] = e
    return name


def _vars_obj(used: dict) -> str:
    parts = []
    for name, expr in used.items():
        parts.append(name if expr.strip() == name else f"{name}: {expr.strip()}")
    return "{" + ", ".join(parts) + "}"


class Keys:
    def __init__(self, module: str, taken: dict):
        self.module = module
        self.taken = taken         # key -> english (whole catalog + this run)

    def key(self, english) -> str:
        sig = english
        for k, v in self.taken.items():
            if v == sig and k.startswith(self.module + "."):
                return k
        base = f"{self.module}.{K._slug(english if isinstance(english, str) else english.get('other', ''), 5)}"
        k, i = base, 2
        while k in self.taken and self.taken[k] != sig:
            k = f"{base}_{i}"; i += 1
        self.taken[k] = sig
        return k


def _decode_static(raw: str) -> str:
    return K._unescape_js(raw)


def _ui(text: str) -> bool:
    c = K.ui_text_candidates(text)
    return bool(c) and K._norm(K.html.unescape(text)).strip() == c[0] if len(c) == 1 else False


def _ui_loose(text: str) -> bool:
    return bool(K.ui_text_candidates(text))


def wrap_source(src: str, module: str, taken: dict) -> tuple[str, int]:
    lits = K.js_literals(src)
    comments = list(K.COMMENTS)
    keys = Keys(module, taken)
    # strings this file compares against anywhere: never translate those
    compared = set()
    for lit in lits:
        before = src[max(0, lit.start - 12):lit.start]
        after = src[lit.end:lit.end + 6]
        if lit.kind == "str" and (_CMP_BEFORE.search(before) or _CMP_AFTER.search(after)):
            compared.add(lit.value)
    t_args = {m.end() for m in K.T_CALL.finditer(src)}
    for m in K.TP_CALL.finditer(src):
        ob = src.find("{", m.end())
        for fm in re.finditer(r"\b(?:zero|one|two|few|many|other)\s*:\s*", src[ob:ob + 600]):
            t_args.add(ob + fm.end())
    edits = []        # (start, end, replacement)
    claimed = []      # ranges already being rewritten (skip literals inside)

    def inside_claimed(a, b):
        return any(x <= a and b <= y for x, y in claimed)

    for lit in sorted(lits, key=lambda l: (l.start, -l.end)):
        a, b = lit.start, lit.end
        if inside_claimed(a, b) or K._in_comment(a, comments) or a in t_args:
            continue
        before = src[max(0, a - 80):a]
        if _SKIP_BEFORE.search(before) or _ASSIGN_DATA.search(before) or _CMP_BEFORE.search(src[max(0, a - 12):a]) \
                or _CMP_AFTER.search(src[b:b + 6]) or _is_key(src, a, b) or _in_data(src, a):
            continue
        if lit.kind == "str":
            if lit.value in compared or not _ui(lit.value) or re.search(r"<[a-zA-Z/][^>]*>", lit.value):
                continue
            if re.search(r"\{[A-Za-z_]\w*\}", lit.value):
                continue
            k = keys.key(lit.value)
            edits.append((a, b, f"T('{k}', {src[a:b]})"))
            claimed.append((a, b))
            continue
        # template literal
        pieces = _raw_template(src, a, b)
        statics = "".join(_decode_static(p) for kind, p in pieces if kind == "s")
        has_tags = bool(re.search(r"<[a-zA-Z/][^>]*>", statics))
        if not has_tags:
            if any(re.search(r"\{[A-Za-z_]\w*\}", _decode_static(p)) for kind, p in pieces if kind == "s"):
                continue
            joined = "".join(_decode_static(p) if kind == "s" else " {} " for kind, p in pieces)
            if not _ui_loose(joined):
                continue
            cands = K.ui_text_candidates(joined)
            if K._norm(joined.replace(" {} ", " x ")).strip() and len(cands) != 1:
                continue
            used: dict = {}
            eng = []
            for kind, p in pieces:
                eng.append(_decode_static(p) if kind == "s" else "{" + _var_name(p, used) + "}")
            english = "".join(eng)
            if english in compared:
                continue
            k = keys.key(english)
            rep = f"T('{k}', {_js_str(english)}" + (f", {_vars_obj(used)})" if used else ")")
            edits.append((a, b, rep))
            claimed.append((a, b))
            continue
        # HTML template: rewrite the text runs between tags, and static
        # title/placeholder/aria-label/alt attribute values.
        new_raw, changed = _wrap_html_template(pieces, keys)
        if changed:
            edits.append((a, b, "`" + new_raw + "`"))
            claimed.append((a, b))
    edits.sort(key=lambda e: e[0], reverse=True)
    out = src
    last = None
    n = 0
    for a, b, rep in edits:
        if last is not None and b > last:
            continue
        out = out[:a] + rep + out[b:]
        last = a
        n += 1
    return out, n


_TAG = re.compile(r"<[a-zA-Z/!][^<>]*>")
_ATTR = re.compile(r'\b(title|placeholder|aria-label|alt)="([^"$`<>]*)"')


def _wrap_html_template(pieces, keys: Keys) -> tuple[str, int]:
    """Rewrite text runs in an HTML-building template. A run = the text
    between one tag and the next, possibly spanning ${…} interpolations."""
    # Flatten into tokens: ('tag', raw) | ('text', raw) | ('x', expr)
    toks = []
    in_tag = False
    for kind, raw in pieces:
        if kind == "x":
            toks.append(("xt" if in_tag else "x", raw))
            continue
        pos = 0
        while pos < len(raw):
            if in_tag:
                j = raw.find(">", pos)
                if j < 0:
                    toks.append(("tag", raw[pos:])); pos = len(raw)
                else:
                    toks.append(("tag", raw[pos:j + 1])); pos = j + 1; in_tag = False
            else:
                m = re.search(r"<[a-zA-Z/!]", raw[pos:])
                if not m:
                    toks.append(("text", raw[pos:])); pos = len(raw)
                else:
                    if m.start():
                        toks.append(("text", raw[pos:pos + m.start()]))
                    pos += m.start(); in_tag = True
    changed = 0
    out = []
    i = 0
    while i < len(toks):
        kind, raw = toks[i]
        if kind == "tag":
            def attr_rep(m):
                nonlocal changed
                val = K._unescape_js(m.group(2))
                if not K.ui_text_candidates(val) or "{" in val or "\\" in m.group(2):
                    return m.group(0)
                k = keys.key(val)
                changed += 1
                return f'{m.group(1)}="${{TA(\'{k}\', {_js_str(val)})}}"'
            out.append(_ATTR.sub(attr_rep, raw))
            i += 1
            continue
        if kind == "xt":
            out.append("${" + raw + "}")
            i += 1
            continue
        # a run of text / x tokens
        j = i
        run = []
        while j < len(toks) and toks[j][0] in ("text", "x"):
            run.append(toks[j]); j += 1
        static = "".join(K._unescape_js(r) for k2, r in run if k2 == "text")
        joined = "".join(K._unescape_js(r) if k2 == "text" else " {} " for k2, r in run)
        cands = K.ui_text_candidates(joined)
        ok = bool(cands) and K.meaningful(static) and not re.search(r"\{[A-Za-z_]\w*\}", static) \
            and "\\" not in "".join(r for k2, r in run if k2 == "text")
        if not ok:
            out.append("".join(r if k2 == "text" else "${" + r + "}" for k2, r in run))
            i = j
            continue
        # keep the run's outer whitespace outside the T() call (layout)
        first_raw = run[0][1] if run[0][0] == "text" else ""
        last_raw = run[-1][1] if run[-1][0] == "text" else ""
        lead = re.match(r"\s*", first_raw).group(0) if first_raw else ""
        trail = re.search(r"\s*$", last_raw).group(0) if last_raw else ""
        if len(run) == 1:
            trail = trail if len(first_raw) > len(lead) else ""
        used: dict = {}
        eng = []
        for idx, (k2, r) in enumerate(run):
            if k2 == "text":
                t = K._unescape_js(r)
                if idx == 0 and lead:
                    t = t[len(lead):]
                if idx == len(run) - 1 and trail:
                    t = t[:len(t) - len(trail)] if trail else t
                eng.append(t)
            else:
                eng.append("{" + _var_name(r, used) + "}")
        english = "".join(eng)
        if not english.strip():
            out.append("".join(r if k2 == "text" else "${" + r + "}" for k2, r in run))
            i = j
            continue
        k = keys.key(english)
        call = f"T('{k}', {_js_str(english)}" + (f", {_vars_obj(used)})" if used else ")")
        out.append(lead + "${" + call + "}" + trail)
        changed += 1
        i = j
    return "".join(out), changed


def wrap_files(paths: list[Path]) -> int:
    cat = K.I._read_json(K.SOURCE).get("strings", {})
    taken = {k: v["text"] for k, v in cat.items()}
    total = 0
    for p in paths:
        module = p.stem.replace("_", "")
        src = p.read_text(encoding="utf-8")
        for _ in range(4):
            new, n = wrap_source(src, module, taken)
            if not n:
                break
            src = new
            total += n
        p.write_text(src, encoding="utf-8")
        print(f"wrap: {p.name}")
    print(f"wrap: {total} literal(s) routed through T()")
    return 0
