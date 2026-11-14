#!/usr/bin/env python3
"""i18n_tool.py — translate the Studio (docs/TRANSLATING.md).

    python3 scripts/i18n_tool.py export es            # files to paste into a model
    python3 scripts/i18n_tool.py import es reply1.txt reply2.txt ...
    python3 scripts/i18n_tool.py glossary ja          # a new language: agree its core words first
    python3 scripts/i18n_tool.py review es            # native-reader pass over the translations
    python3 scripts/i18n_tool.py review-apply es i18n/rounds/es/es.reviewreply*.txt
    python3 scripts/i18n_tool.py status               # how much each language covers
    python3 scripts/i18n_tool.py pseudo               # en-XA (long) + ar-XB (RTL) test locales
    python3 scripts/i18n_tool.py check                # the gate (release_gates runs it)
    python3 scripts/i18n_tool.py tag                  # give new markup text its data-i18n keys
    python3 scripts/i18n_tool.py extract              # rebuild i18n/studio/en.json from the code
    python3 scripts/i18n_tool.py baseline             # re-record the not-yet-converted JS text

English lives in the code. Markup: an element whose own text is UI copy
carries data-i18n="key" (and data-i18n-title / -placeholder / -aria-label /
-alt for those attributes). JS: T('key', 'English {name}', {name}) and
TP('key', n, {one: '…', other: '…'}). `extract` collects all of it into
i18n/studio/en.json — the source catalog a translator works from.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import i18n as I  # noqa: E402

INDEX = REPO / "webapp" / "index.html"
JS_DIR = REPO / "webapp" / "js"
CAT_DIR = I.STUDIO_DIR
SOURCE = CAT_DIR / "en.json"
NOTES = CAT_DIR / "notes.json"            # optional hand-written context notes
LEGACY = REPO / "scripts" / "i18n_legacy_js.json"
PSEUDO = ("en-XA", "ar-XB")
# Modules that are not part of the Studio page (agent-connect.html loads its
# own script without i18n.js) or are the lookup itself.
NOT_STUDIO_JS = {"i18n.js", "agent-connect.js"}
ATTRS = ("title", "placeholder", "aria-label", "alt")

# Same rule as webapp/js/i18n.js _PAIRED / _isPaired — keep the two in step.
PAIRED = {"b", "i", "em", "strong", "a", "span", "small", "u", "s", "mark",
          "sub", "sup", "abbr", "q", "cite", "dfn", "time"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
SKIP_SUBTREE = {"script", "style", "svg", "textarea", "code", "pre", "kbd",
                "samp", "var", "template", "math"}
IMPLIED_CLOSE = {"option": {"option", "optgroup"}, "li": {"li"},
                 "dt": {"dt", "dd"}, "dd": {"dt", "dd"},
                 "tr": {"tr"}, "td": {"td", "th"}, "th": {"td", "th"}}


def _seams() -> dict:
    """The panel's noun seams (page() substitutes them before the browser
    ever sees the text), so the catalog holds the words users read."""
    src = (REPO / "mlx_ltx_panel.py").read_text(encoding="utf-8")
    out = {}
    for name, seam in (("SEQ_NOUN_CAP", "__SEQCAP__"), ("SEQ_NOUN_PL", "__SEQS__"),
                       ("SEQ_NOUN", "__SEQ__")):
        m = re.search(rf'^{name}\s*=\s*"([^"]*)"', src, re.M)
        out[seam] = m.group(1) if m else seam
    return out


def _deseam(s: str, seams: dict) -> str:
    for k, v in seams.items():
        s = s.replace(k, v)
    return s


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def meaningful(text: str) -> bool:
    """Is this real copy (not only product names, numbers, symbols)?"""
    s = re.sub(r"__[A-Z0-9_]+__", " ", text)      # page() seams are not copy
    for term in sorted(I.PROTECTED_TERMS, key=len, reverse=True):
        s = s.replace(term, " ")
    s = re.sub(r"\{[A-Za-z_]\w*\}|</?\d+/?>", " ", s)
    return bool(re.search(r"[A-Za-z]{2,}", s))


# ---------------------------------------------------------------- markup tree

class Node:
    __slots__ = ("tag", "attrs", "start", "end", "kids", "parent", "line")

    def __init__(self, tag, attrs, start, end, parent, line):
        self.tag, self.attrs, self.start, self.end = tag, dict(attrs), start, end
        self.kids: list = []          # Node | str (text, raw source) | None (comment)
        self.parent, self.line = parent, line


class _Builder(HTMLParser):
    def __init__(self, src: str):
        super().__init__(convert_charrefs=False)
        self.src = src
        self._lines = [0]
        for m in re.finditer("\n", src):
            self._lines.append(m.end())
        self.root = Node("#root", [], 0, 0, None, 0)
        self.stack = [self.root]

    def _off(self):
        ln, col = self.getpos()
        return self._lines[ln - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self._off()
        raw = self.get_starttag_text() or ""
        top = self.stack[-1]
        closes = IMPLIED_CLOSE.get(tag)
        if closes and top.tag in closes:
            self.stack.pop()
            top = self.stack[-1]
        if tag == "p" and top.tag == "p":
            self.stack.pop(); top = self.stack[-1]
        n = Node(tag, attrs, start, start + len(raw), top, self.getpos()[0])
        top.kids.append(n)
        if tag not in VOID and not raw.endswith("/>"):
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        start = self._off()
        raw = self.get_starttag_text() or ""
        top = self.stack[-1]
        top.kids.append(Node(tag, attrs, start, start + len(raw), top, self.getpos()[0]))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].kids.append(data)

    def handle_entityref(self, name):
        self.stack[-1].kids.append(f"&{name};")

    def handle_charref(self, name):
        self.stack[-1].kids.append(f"&#{name};")

    def handle_comment(self, data):
        self.stack[-1].kids.append(None)


def parse(src: str) -> Node:
    b = _Builder(src)
    b.feed(src)
    b.close()
    return b.root


def _merge_text(kids):
    """Adjacent text/entity pieces → one decoded string per run."""
    out, buf = [], []
    for k in kids:
        if isinstance(k, str):
            buf.append(k)
        else:
            if buf:
                out.append(html.unescape("".join(buf))); buf = []
            if k is not None:
                out.append(k)
    if buf:
        out.append(html.unescape("".join(buf)))
    return out


def is_paired(n: Node) -> bool:
    if n.tag not in PAIRED or "id" in n.attrs or "data-i18n" in n.attrs:
        return False
    if any(not isinstance(k, str) for k in n.kids if k is not None):
        return False
    return bool(re.search(r"[A-Za-z]", html.unescape("".join(k for k in n.kids if k))))


def message(n: Node, seams: dict) -> tuple[str, list[str]]:
    """The translatable message for a unit element, plus a description of
    what each opaque <n/> marker stands for (for the context note)."""
    parts, opaque = [], []
    idx = 0
    for k in _merge_text(n.kids):
        if isinstance(k, str):
            parts.append(k)
            continue
        if is_paired(k):
            inner = html.unescape("".join(x for x in k.kids if x))
            parts.append(f"<{idx}>{_norm(inner)}</{idx}>")
        else:
            parts.append(f"<{idx}/>")
            opaque.append(f"<{idx}/> = {_describe(k)}")
        idx += 1
    msg = _deseam(_norm("".join(parts)).strip(), seams)
    return msg, opaque


def _describe(n: Node) -> str:
    if n.tag == "svg":
        return "icon"
    txt = _norm(html.unescape("".join(_all_text(n)))).strip()
    if n.tag in ("input", "select", "textarea"):
        return f"a {n.tag} field"
    if n.tag in ("br",):
        return "line break"
    return f"<{n.tag}> “{txt[:40]}”" if txt else f"<{n.tag}>"


def _all_text(n: Node):
    for k in n.kids:
        if isinstance(k, str):
            yield k
        elif isinstance(k, Node) and k.tag not in ("script", "style", "svg"):
            yield from _all_text(k)


def has_own_text(n: Node) -> bool:
    return any(isinstance(k, str) and re.search(r"[A-Za-z]", html.unescape(k)) for k in n.kids)


def walk_units(root: Node, seams: dict):
    """Yield (node, kind, attr, english, opaque) for every translatable
    piece of markup: kind "text" (a unit element) or "attr"."""
    def rec(n: Node, covered: bool, skip: bool):
        if isinstance(n, Node) and n.tag != "#root":
            if n.attrs.get("translate") == "no":
                skip = True
            if not skip:
                for a in ATTRS:
                    v = n.attrs.get(a)
                    if v and meaningful(_deseam(html.unescape(v), seams)) and "__" not in v:
                        yield n, "attr", a, _deseam(_norm(html.unescape(v)).strip(), seams), []
        if n.tag in SKIP_SUBTREE:
            return
        unit = False
        if not skip and not covered and n.tag != "#root" and has_own_text(n):
            msg, opaque = message(n, seams)
            if meaningful(msg) and not _looks_like_seam(msg):
                unit = True
                yield n, "text", None, msg, opaque
        for k in n.kids:
            if isinstance(k, Node):
                yield from rec(k, unit and is_paired(k), skip)
    yield from rec(root, False, False)


def _looks_like_seam(msg: str) -> bool:
    return bool(re.fullmatch(r"\s*__[A-Z0-9_]+__\s*", msg))


# ---------------------------------------------------------------- tagging

def _slug(s: str, words: int = 4) -> str:
    s = re.sub(r"</?\d+/?>|\{[^}]*\}", " ", s)
    ws = re.findall(r"[A-Za-z0-9]+", s.lower())
    return "_".join(ws[:words])[:40] or "text"


def _prefix(n: Node) -> str:
    p = n
    while p is not None and p.tag != "#root":
        if p.attrs.get("id"):
            return re.sub(r"[^A-Za-z0-9_-]", "", p.attrs["id"])
        p = p.parent
    return "page"


def cmd_tag(args) -> int:
    """Add data-i18n keys to markup text that has none (idempotent)."""
    src = INDEX.read_text(encoding="utf-8")
    seams = _seams()
    root = parse(src)
    used = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', src))
    used |= set(js_strings()[0].keys())
    inserts: dict[int, list[str]] = {}
    added = 0
    for n, kind, attr, english, _ in walk_units(root, seams):
        name = "data-i18n" if kind == "text" else f"data-i18n-{attr}"
        if name in n.attrs:
            continue
        base = f"{_prefix(n)}.{attr + '.' if attr else ''}{_slug(english)}"
        key, i = base, 2
        while key in used:
            key = f"{base}_{i}"; i += 1
        used.add(key)
        raw = src[n.start:n.end]
        at = n.end - (2 if raw.endswith("/>") else 1)
        inserts.setdefault(at, []).append(f' {name}="{key}"')
        added += 1
    for at in sorted(inserts, reverse=True):
        src = src[:at] + "".join(inserts[at]) + src[at:]
    if added and not args.dry_run:
        INDEX.write_text(src, encoding="utf-8")
    print(f"tag: {added} new key(s){' (dry run)' if args.dry_run else ''}")
    return 0


# ---------------------------------------------------------------- JS

class JSLit:
    __slots__ = ("kind", "value", "start", "end", "line", "parts")

    def __init__(self, kind, value, start, end, line, parts=None):
        self.kind, self.value, self.start, self.end, self.line = kind, value, start, end, line
        self.parts = parts            # template literal: static parts


_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _unescape_js(s: str) -> str:
    def rep(m):
        e = m.group(1)
        if e[0] == "u":
            h = e[2:-1] if e.startswith("u{") else e[1:]
            return chr(int(h, 16))
        if e[0] == "x":
            return chr(int(e[1:], 16))
        if e == "\n":
            return ""
        return _ESC.get(e, e)
    return re.sub(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\n|.)", rep, s, flags=re.S)


def js_literals(src: str):
    """Every string and template literal in a JS source, comments skipped.
    Good enough for this codebase: regex literals are recognised by the
    token before the slash."""
    i, n = 0, len(src)
    out = []
    COMMENTS.clear()
    prev = ""            # last significant char, for the regex heuristic
    line = 1
    stack = []           # template nesting: brace depth at each ${
    depth = 0

    def read_template(i):
        nonlocal line
        start = i
        i += 1
        parts, buf = [], []
        while i < n:
            c = src[i]
            if c == "\\":
                buf.append(src[i:i + 2]); i += 2; continue
            if c == "`":
                parts.append("".join(buf))
                return i + 1, parts, start
            if c == "$" and i + 1 < n and src[i + 1] == "{":
                parts.append("".join(buf)); buf = []
                i += 2
                d = 1
                while i < n and d:
                    ch = src[i]
                    if ch == "/" and src[i + 1:i + 2] in ("/", "*"):
                        j = src.find("\n" if src[i + 1] == "/" else "*/", i + 2)
                        j = n if j < 0 else (j if src[i + 1] == "/" else j + 2)
                        COMMENTS.append((i, j)); line += src.count("\n", i, j); i = j; continue
                    if ch in "'\"":
                        j = _skip_str(src, i); out.append(_mk(src, i, j, line)); i = j; continue
                    if ch == "`":
                        j, p2, s2 = read_template(i)
                        out.append(JSLit("tpl", "\x00".join(_unescape_js(p) for p in p2), s2, j, line, [_unescape_js(p) for p in p2]))
                        i = j; continue
                    if ch == "{":
                        d += 1
                    elif ch == "}":
                        d -= 1
                    elif ch == "\n":
                        line += 1
                    i += 1
                continue
            if c == "\n":
                line += 1
            buf.append(c); i += 1
        return n, parts + ["".join(buf)], start

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1; i += 1; continue
        if c in " \t\r":
            i += 1; continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            COMMENTS.append((i, j)); i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            COMMENTS.append((i, j))
            line += src.count("\n", i, j); i = j
            continue
        if c in "'\"":
            j = _skip_str(src, i)
            out.append(_mk(src, i, j, line))
            prev = "a"; i = j; continue
        if c == "`":
            j, parts, s = read_template(i)
            ups = [_unescape_js(p) for p in parts]
            out.append(JSLit("tpl", "\x00".join(ups), s, j, line, ups))
            prev = "a"; i = j; continue
        if c == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%<>~^"):
            j = i + 1
            cls = False
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2; continue
                if ch == "[":
                    cls = True
                elif ch == "]":
                    cls = False
                elif ch == "/" and not cls:
                    break
                elif ch == "\n":
                    break
                j += 1
            i = j + 1
            while i < n and src[i].isalpha():
                i += 1
            prev = "a"; continue
        if c.isalnum() or c in "_$)]":
            # keyword before a slash means regex (return /x/)
            m = re.match(r"[A-Za-z0-9_$]+", src[i:])
            if m:
                word = m.group(0)
                prev = "(" if word in ("return", "typeof", "case", "in", "of", "do", "else", "void") else "a"
                i += len(word); continue
            prev = "a"; i += 1; continue
        prev = c
        i += 1
    return out


COMMENTS: list = []     # (start, end) of every comment in the last js_literals() source


def _in_comment(pos: int, spans) -> bool:
    return any(a <= pos < b for a, b in spans)


def _skip_str(src, i):
    q = src[i]
    j = i + 1
    while j < len(src):
        if src[j] == "\\":
            j += 2; continue
        if src[j] == q or src[j] == "\n":
            return j + 1
        j += 1
    return j


def _mk(src, i, j, line):
    return JSLit("str", _unescape_js(src[i + 1:j - 1]), i, j, line)


T_CALL = re.compile(r"(?<![\w.$])TA?\(\s*(['\"])([A-Za-z0-9_.\-]+)\1\s*,\s*")
TP_CALL = re.compile(r"(?<![\w.$])TP\(\s*(['\"])([A-Za-z0-9_.\-]+)\1\s*,")


def _lit_at(lits_by_start, pos):
    return lits_by_start.get(pos)


def js_strings():
    """({key: {"text", "where"}}, problems) from every T()/TP() call."""
    seams = _seams()
    found: dict = {}
    problems: list[str] = []
    for f in sorted(JS_DIR.glob("*.js")):
        src = f.read_text(encoding="utf-8")
        lits = js_literals(src)
        spans = list(COMMENTS)
        by_start = {l.start: l for l in lits}
        calls = []
        fns = [(m.start(), m.group(1) or m.group(2)) for m in re.finditer(
            r"^(?:async\s+)?function\s+(\w+)|^(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>", src, re.M)]
        for m in T_CALL.finditer(src):
            if _in_comment(m.start(), spans):
                continue
            key = m.group(2)
            lit = by_start.get(m.end())
            where = f"{f.name}:{src.count(chr(10), 0, m.start()) + 1}"
            if lit is None:
                problems.append(f"{where}: t('{key}', …) — the English must be a plain string literal")
                continue
            if lit.kind == "tpl" and len(lit.parts or []) > 1:
                problems.append(f"{where}: t('{key}', `…${{}}…`) — use {{name}} placeholders, not ${{}}")
                continue
            text = _deseam(lit.value if lit.kind == "str" else lit.parts[0], seams)
            _add(found, problems, key, text, where, _fn_before(fns, m.start()))
            calls.append((m.start(), lit.end, key, text))
        for m in TP_CALL.finditer(src):
            if _in_comment(m.start(), spans):
                continue
            key = m.group(2)
            where = f"{f.name}:{src.count(chr(10), 0, m.start()) + 1}"
            ob = src.find("{", m.end())
            seg = src[ob:ob + 600]
            forms = {}
            for fm in re.finditer(r"\b(zero|one|two|few|many|other)\s*:\s*", seg):
                lit = by_start.get(ob + fm.end())
                if lit is not None and lit.kind == "str":
                    forms[fm.group(1)] = _deseam(lit.value, seams)
                if "}" in seg[:fm.start()].replace("{", "", 1):
                    break
            if "other" not in forms:
                problems.append(f"{where}: tp('{key}', n, {{one, other}}) — needs at least an 'other' form")
                continue
            _add(found, problems, key, forms, where, _fn_before(fns, m.start()))
        # Fragments the code glues together: give each its neighbours.
        for i, (a, b, key, text) in enumerate(calls):
            if not _is_fragment(text) or key not in found or found[key].get("ctx"):
                continue
            parts = [text]
            j = i - 1
            while j >= 0 and _joined(src[calls[j][1]:a]) and len(parts) < 4:
                parts.insert(0, calls[j][3]); a = calls[j][0]; j -= 1
            j, e = i + 1, b
            while j < len(calls) and _joined(src[e:calls[j][0]]) and len(parts) < 6:
                parts.append(calls[j][3]); e = calls[j][1]; j += 1
            if len(parts) > 1:
                found[key]["ctx"] = " … ".join(p.strip() for p in parts if isinstance(p, str))
    return found, problems


def _add(found, problems, key, text, where, fn=None):
    if key in found and found[key]["text"] != text:
        problems.append(f"{where}: key '{key}' already means “{found[key]['text']}” at {found[key]['where']}")
        return
    found.setdefault(key, {"text": text, "where": where, "fn": fn})


def _is_fragment(text) -> bool:
    if not isinstance(text, str):
        return False
    t = text
    return t[:1].isspace() or t[-1:].isspace() or t[:1].islower() or t[:1] in ",.;:—)"


def _joined(between: str) -> bool:
    """Code between two T() calls that glues them into one sentence."""
    b = between.strip()
    return len(b) < 90 and "+" in b and ";" not in b or bool(re.fullmatch(r"\)?\s*\}[^`;]{0,80}\$\{", b))


def _fn_before(fns, pos):
    name = None
    for p, n in fns:
        if p > pos:
            break
        name = n
    return name


def _fn_words(name: str) -> str:
    """renderOutputsGrid -> 'renders outputs grid' (a hint, not prose)."""
    w = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.lstrip("_")).replace("_", " ").lower().split()
    return ("handles " + " ".join(w)) if w else "runs here"


# ---- the not-yet-converted JS copy (the ratchet) ----------------------------

_CODEY = re.compile(r"(^[./#@\-]|[{};=<>\\|]|\$\{|=>|::|\b[a-z]+[A-Z][a-zA-Z]*\b|\w_\w|^[a-z0-9-]+$"
                    r"|px\b|rgba?\(|var\(--|https?:|\.(js|py|json|png|mp4|wav|css|html|safetensors)\b"
                    r"|^\w+:\s|^[A-Z0-9_]+$|^[a-z]+(\s[a-z]+)?\(|%[sd]|\bnull\b|\bundefined\b)")


def ui_text_candidates(s: str) -> list[str]:
    """The user-visible copy inside one JS literal (heuristic)."""
    s = s.replace("\x00", " {} ")
    pieces = []
    if re.search(r"<[a-zA-Z/][^>]*>", s):
        for m in re.finditer(r'\b(?:title|placeholder|aria-label|alt)="([^"$]*)"', s):
            pieces.append(m.group(1))
        pieces += re.split(r"<[^>]*>", s)
    else:
        pieces.append(s)
    out = []
    for p in pieces:
        p = _norm(html.unescape(p)).strip()
        if not p or not meaningful(p):
            continue
        words = re.findall(r"[A-Za-z][A-Za-z'’]+", p)
        if not words:
            continue
        if re.search(r"""=\s*["']|\bon[a-z]+=|=>|\bthis\.|\bfunction\b|\breturn\b|;\s*$|\bvar\(|\$\{""", p):
            continue                      # markup attributes or code, never copy
        toks = p.split()
        if all(re.fullmatch(r"[a-z0-9]+([-_][a-z0-9]+)*", w) for w in toks) and any(re.search(r"[-_]", w) for w in toks):
            continue                      # a class list / identifiers
        if len(p.split()) >= 2:
            if _CODEY.search(p) and not re.search(r"[A-Z][a-z]+ [a-z]+ [a-z]+", p):
                continue
            if not re.search(r"[A-Za-z]{3,}", p):
                continue
            out.append(p)
        elif re.fullmatch(r"[A-Z][a-z]{2,}[.…!?:]?", p) and not _CODEY.search(p):
            out.append(p)
    return out


_SKIP_CALL = re.compile(r"(console\.\w+|querySelector(All)?|getElementById|addEventListener|removeEventListener"
                        r"|classList\.\w+|getAttribute|setAttribute\(\s*['\"](?:data|class|id|role|type)"
                        r"|matches|closest|import|fetch|dataset|localStorage\.\w+|new RegExp|\.test|\.match"
                        r"|\.split|\.replace|\.includes|\.startsWith|\.endsWith|\.indexOf)\s*\(\s*$")


def js_untranslated() -> dict:
    """{file: {text: count}} — UI-looking copy in JS that is not yet a t()."""
    out: dict = {}
    for f in sorted(JS_DIR.glob("*.js")):
        if f.name in NOT_STUDIO_JS:
            continue
        src = f.read_text(encoding="utf-8")
        lits = js_literals(src)
        t_args = set()
        for m in list(T_CALL.finditer(src)):
            t_args.add(m.end())
        for m in TP_CALL.finditer(src):
            ob = src.find("{", m.end())
            for fm in re.finditer(r"\b(?:zero|one|two|few|many|other)\s*:\s*", src[ob:ob + 600]):
                t_args.add(ob + fm.end())
        cnt: dict = {}
        for lit in lits:
            if lit.start in t_args:
                continue
            before = src[max(0, lit.start - 60):lit.start]
            if _SKIP_CALL.search(before):
                continue
            if re.search(r"(===?|!==?|case)\s*$", before):
                continue
            for c in ui_text_candidates(lit.value):
                cnt[c] = cnt.get(c, 0) + 1
        if cnt:
            out[f.name] = dict(sorted(cnt.items()))
    return out


# ---------------------------------------------------------------- extract

AREAS = CAT_DIR / "areas.json"
GLOSSARY = REPO / "i18n" / "glossary.json"


def areas() -> dict:
    return I._read_json(AREAS)


def area_of(n: Node, ar: dict) -> str:
    """What the part of the page this element sits in IS and DOES."""
    ids, prefixes = ar.get("ids", {}), [(k[:-1], v) for k, v in ar.get("ids", {}).items() if k.endswith("*")]
    prefixes.sort(key=lambda kv: -len(kv[0]))
    p = n
    while p is not None and p.tag != "#root":
        i = p.attrs.get("id")
        if i:
            if i in ids:
                return ids[i]
            for pre, v in prefixes:
                if i.startswith(pre):
                    return v
        p = p.parent
    return "the Studio, the main Phosphene window where the user makes videos, pictures and music"


def glossary() -> list[dict]:
    return I._read_json(GLOSSARY).get("terms", [])


def _term_re(word: str):
    flags = 0 if word[:1].isupper() else re.I
    return re.compile(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", flags)


def _strip_for(term: dict, flat: str, gl: list[dict]) -> str:
    """Text without file paths and without longer terms that contain this
    one ("Shot" inside the feature name "One Shot")."""
    flat = re.sub(r"[~/][\w./~-]*/[\w./-]*", " ", flat)
    for other in gl:
        if other is term:
            continue
        for w in (other.get("match") or []) + [other["term"]]:
            if any(x.lower() in w.lower() and x.lower() != w.lower() for x in term.get("match") or []):
                flat = re.sub(re.escape(w), " ", flat, flags=re.I)
    return flat


def terms_in(text, gl: list[dict]) -> list[dict]:
    flat0 = _flat(text)
    out = []
    for t in gl:
        words = t.get("match") or []
        flat = _strip_for(t, flat0, gl)
        if any(_term_re(w).search(flat) for w in words) or flat0.strip().rstrip(".…:") == t["term"]:
            out.append(t)
    return out


def _label_meaning(english: str, gl: list[dict]) -> str:
    e = english.strip().rstrip(".…:")
    for t in gl:
        if e.lower() == t["term"].lower():
            return f" · this label is the term “{t['term']}”: {t['meaning']}"
    return ""


def _note_for(n: Node, kind: str, attr, english: str, opaque: list[str], ar=None, gl=None) -> tuple[str, int | None]:
    ar = ar if ar is not None else areas()
    gl = gl if gl is not None else glossary()
    tag = n.tag
    role = {"button": "button", "option": "menu choice", "label": "field label",
            "h1": "heading", "h2": "heading", "h3": "heading", "h4": "heading",
            "summary": "expander title", "th": "table header", "legend": "group title",
            "a": "link"}.get(tag, "text")
    cls = n.attrs.get("class", "") or ""
    if any(c in cls for c in ("chip", "pill", "tab", "btn")):
        role = "button" if role == "text" else role
    if kind == "attr":
        role = {"title": "tooltip (explains a control on hover)", "placeholder": "placeholder in an empty field",
                "aria-label": "screen-reader name of a control", "alt": "image description"}[attr]
    note = f"Studio · {role} · in {area_of(n, ar)}"
    if kind == "text":
        tip = None
        p = n
        for _ in range(3):
            if p is None:
                break
            tip = p.attrs.get("title") or p.attrs.get("aria-label")
            if tip and "__" not in tip and tip.strip() != english:
                break
            tip = None
            p = p.parent
        if tip:
            note += f" · what it does (its tooltip): “{_norm(html.unescape(tip)).strip()[:160]}”"
    note += _label_meaning(english, gl)
    if opaque:
        note += " · " + "; ".join(opaque)
    tight = role in ("button", "menu choice", "table header", "field label") and len(english) <= 40
    mx = None
    if tight:
        mx = max(len(english) + 8, int(len(english) * 1.6))
        note += f" · tight space: keep under {mx} characters"
    return note, mx


def build_catalog() -> tuple[dict, list[str]]:
    seams = _seams()
    src = INDEX.read_text(encoding="utf-8")
    root = parse(src)
    cat: dict = {}
    problems: list[str] = []
    untagged = []
    ar, gl = areas(), glossary()
    for n, kind, attr, english, opaque in walk_units(root, seams):
        name = "data-i18n" if kind == "text" else f"data-i18n-{attr}"
        key = n.attrs.get(name)
        if not key:
            untagged.append(f"index.html:{n.line}: “{english[:60]}”")
            continue
        note, mx = _note_for(n, kind, attr, english, opaque, ar, gl)
        ent = {"text": english, "note": note, "where": f"index.html:{n.line}"}
        if mx:
            ent["max"] = mx
        if key in cat and cat[key]["text"] != english:
            problems.append(f"index.html:{n.line}: key '{key}' used for two different texts")
        cat[key] = ent
    # a data-i18n on an element that no longer has copy
    tagged = re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', src)
    for k in tagged:
        if k not in cat:
            problems.append(f"index.html: data-i18n=\"{k}\" is on an element with no translatable text "
                            "(remove the attribute)")
    js, jprob = js_strings()
    problems += jprob
    for k, v in js.items():
        if k in cat:
            problems.append(f"{v['where']}: key '{k}' is also used in index.html")
            continue
        text = v["text"]
        mod = v['where'].split(':')[0]
        note = f"Studio · in {ar.get('js', {}).get(mod, 'the Studio')}"
        if v.get("fn"):
            note += f" · written by the code that {_fn_words(v['fn'])}"
        note += _label_meaning(_flat(text), gl)
        if v.get("ctx"):
            note += f" · FRAGMENT: the app joins it with the text around it (… = a value or other piece): «{v['ctx'][:300]}»"
        ph = sorted(I.placeholders(text))
        if ph:
            note += " · placeholders " + " ".join(ph) + " are filled in by the app"
        if isinstance(text, dict):
            note += " · plural: {count} is the number"
        cat[k] = {"text": text, "note": note, "where": v["where"]}
    manual = I._read_json(NOTES)
    for k, extra in manual.items():
        if k in cat:
            cat[k]["note"] += " · " + extra
    problems += [f"{u} — no data-i18n key (run: python3 scripts/i18n_tool.py tag)" for u in untagged]
    return dict(sorted(cat.items())), problems


def _dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")


def cmd_extract(args) -> int:
    cat, problems = build_catalog()
    for p in problems:
        print("  !", p)
    _dump(SOURCE, {"meta": {"lang": "en", "surface": "studio",
                            "generated_by": "python3 scripts/i18n_tool.py extract — do not edit by hand"},
                   "strings": cat})
    words = sum(len(_flat(v["text"]).split()) for v in cat.values())
    print(f"extract: {len(cat)} strings, {words} words -> {SOURCE.relative_to(REPO)}")
    return 1 if problems else 0


def _flat(text) -> str:
    return " ".join(text.values()) if isinstance(text, dict) else str(text)


# ---------------------------------------------------------------- export / import

PROMPT = """You are translating the user interface of Phosphene, a free, open-source Mac app where
people make AI videos, pictures and music on their own Mac. Translate the English UI text below
into {name} ({lang}) for a native {name} speaker who uses creative apps.

How to translate — this matters most:
A. Translate the MEANING, not the words. Each "note" says what the thing IS and DOES in the app;
   pick the words a native speaker would expect on that screen. Never translate literally when the
   literal word is not what a native speaker would say (example: the tab "Outputs" is the user's
   gallery of their own videos and pictures, so it is a gallery word, not "outputs").
B. Use the words that popular video, photo and music apps already use in {name} (the way CapCut,
   iMovie, Photos, Premiere, DaVinci Resolve or Canva say it in {name}). Users must recognise them.
C. Keep it short and natural: UI labels, not sentences from a manual. Friendly and plain, like a
   well-made Mac app; use the informal "you" if {name} apps normally do.
D. One neutral {name} that reads well in every country that speaks it: no regional slang, no
   country-only words.
E. Follow the GLOSSARY below: use the agreed word for each term every time it appears.

Hard rules (a reply that breaks one is refused):
1. Keep these names exactly as written: {terms}.
2. Keep every placeholder exactly: {{name}}-style words in curly braces and the numbered tags
   <0>…</0>, <1/>. Move them where the {name} sentence needs them; never change, translate, drop
   or add one. Text between <0> and </0> is translated. A literal tag the English shows, such as
   <d>…</d> or <trigger>, is app syntax: keep it exactly.
3. No HTML, no Markdown, no new line breaks, no quotes around the text. Keep leading/trailing
   spaces of the English (the app joins some strings together). Inside the text, quote with your
   language's own quotation marks (“ ” « » „ “ 「 」), never the straight double quote ".
4. "max" is a hard length limit in characters for buttons and labels in tight spaces.
5. Entries whose "en" is an object are plural forms: reply with an object holding exactly these
   {name} forms: {plural}. Each form contains {{count}}.
6. Never translate the "note"; it is context for you.
7. Reply with ONE JSON object and nothing else: {{"id": "translation", ...}} with every id below.

"""

REVIEW_PROMPT = """You are a native {name} ({lang}) speaker reviewing the {name} interface of Phosphene, a Mac
app where people make AI videos, pictures and music. Each entry has the English ("en"), the current
{name} text ("tr") and a "note" saying what the thing IS and DOES in the app.

Flag an entry ONLY if a native user would find it wrong: unnatural or stiff, translated too
literally (a word-for-word rendering a native speaker would not say on that screen), the wrong
meaning for what the thing does, inconsistent with the GLOSSARY or with how the same thing is
named elsewhere, not neutral {name} (regional-only words), or clearly too long for a button.
Do not flag matters of taste. Fragments that the app joins to other text are expected.

For each flagged entry give a better text that keeps every {{placeholder}}, every <0>…</0> / <1/>
marker, leading/trailing spaces, and these names unchanged: {terms}.

Reply with ONE JSON object and nothing else: {{"id": {{"why": "short reason, in English", "fix": "better {name} text"}}}}
for flagged entries only. Reply {{}} if everything reads well.

"""


def glossary_block(texts, lang: str, gl=None) -> str:
    """The GLOSSARY section for one export file: the terms its strings use."""
    gl = gl if gl is not None else glossary()
    seen = []
    for t in texts:
        for term in terms_in(t, gl):
            if term not in seen:
                seen.append(term)
    if not seen:
        return ""
    lines = ["GLOSSARY (agreed words; use them every time):"]
    for term in seen:
        agreed = (term.get(lang) or {}).get("use")
        lines.append(f"- {term['term']}: {term['meaning']}" + (f" → {agreed}" if agreed else
                     " → no agreed word yet: choose the word native apps use and keep it the same everywhere"))
    return "\n".join(lines) + "\n\n"


def glossary_check(en, tr, lang: str, gl=None) -> tuple[list[str], list[str]]:
    """(errors, warnings): a strict term must keep its agreed word."""
    gl = gl if gl is not None else glossary()
    errs, warns = [], []
    flat_tr = _flat(tr).lower() if not isinstance(tr, str) else tr.lower()
    for term in terms_in(en, gl):
        ent = term.get(lang)
        if not ent or not ent.get("accept"):
            continue
        if not any(a.lower() in flat_tr for a in ent["accept"]):
            msg = f"glossary: “{term['term']}” should be “{ent.get('use', ent['accept'][0])}”"
            (errs if term.get("strict") else warns).append(msg)
    return errs, warns


def _dedupe_entries(todo: dict) -> list[dict]:
    """Group keys whose English is identical (a dozen "Close" buttons cost one line)."""
    groups: dict = {}
    for k, ent in todo.items():
        sig = json.dumps(ent["text"], ensure_ascii=False, sort_keys=True)
        g = groups.setdefault(sig, {"keys": [], "en": ent["text"], "notes": [], "max": None})
        g["keys"].append(k)
        if ent.get("note") and len(g["notes"]) < 2:
            g["notes"].append(ent["note"])
        if ent.get("max"):
            g["max"] = min(g["max"] or 10 ** 6, ent["max"])
    return list(groups.values())


def cmd_export(args) -> int:
    lang = args.lang
    src = I.source_catalog(CAT_DIR) if not args.source else json.loads(Path(args.source).read_text())["strings"]
    loc = I.load_locale(lang, CAT_DIR)
    todo = {}
    for k, ent in src.items():
        have = loc["strings"].get(k)
        if have in (None, "") or args.all:
            todo[k] = ent
        elif loc["source"].get(k) != I.text_hash(ent["text"]):
            todo[k] = ent                                   # English changed since
    if not todo:
        print(f"export: {lang} is complete — nothing to translate.")
        return 0
    groups = _dedupe_entries(todo)
    out_dir = Path(args.out or (REPO / "i18n" / "rounds" / lang))
    out_dir.mkdir(parents=True, exist_ok=True)
    name = I.NATIVE_NAMES.get(lang, lang)
    header = PROMPT.format(name=name, lang=lang, terms=", ".join(I.PROTECTED_TERMS),
                           plural=", ".join(I.plural_forms(lang)))
    batch = max(20, args.batch)
    files = []
    ids = {}
    for bi in range(0, len(groups), batch):
        chunk = groups[bi:bi + batch]
        body = {}
        for gi, g in enumerate(chunk, start=bi + 1):
            gid = f"s{gi}"
            ids[gid] = g["keys"]
            e = {"en": g["en"]}
            if g["notes"]:
                e["note"] = " | ".join(g["notes"])
            if g["max"]:
                e["max"] = g["max"]
            body[gid] = e
        part = bi // batch + 1
        p = out_dir / f"{lang}.part{part:02d}.txt"
        gblock = glossary_block([g["en"] for g in chunk], lang)
        p.write_text(header + gblock + "```json\n" + json.dumps(body, ensure_ascii=False, indent=1) + "\n```\n",
                     encoding="utf-8")
        files.append(p)
    _dump(out_dir / f"{lang}.ids.json", ids)
    print(f"export: {len(todo)} strings ({len(groups)} unique) for {name} in {len(files)} file(s):")
    for p in files:
        print(f"   {p.relative_to(REPO) if p.is_relative_to(REPO) else p}")
    rel = out_dir.relative_to(REPO) if out_dir.is_relative_to(REPO) else out_dir
    print(f"Paste each file into the model and save its reply next to it as {lang}.replyNN.txt "
          f"(same NN as the part), then:\n   python3 scripts/i18n_tool.py import {lang} {rel}/{lang}.reply*.txt")
    return 0


def _json_from_reply(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


HTML_TAG = re.compile(r"<(?!/?\d+/?>)[^>]*>")


def validate(en, tr, lang: str, mx: int | None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one translation against its English."""
    errs, warns = [], []
    if isinstance(en, dict):
        if not isinstance(tr, dict):
            return ["needs plural forms (an object)"], []
        need = I.plural_forms(lang)
        missing = [f for f in need if not tr.get(f)]
        if "other" not in tr:
            errs.append("plural forms need an 'other' form")
        elif missing:
            warns.append(f"missing plural form(s) {missing} — 'other' is used for them")
        for f, v in tr.items():
            e2, w2 = validate(en.get(f) or en.get("other"), v, lang, mx)
            errs += [f"[{f}] {e}" for e in e2]
            warns += [f"[{f}] {w}" for w in w2]
        return errs, warns
    if not isinstance(tr, str) or not tr.strip():
        return ["empty"], []
    # Literal syntax the English itself shows (the H3 <d>…</d> dialogue tag,
    # "<trigger>") must stay; anything else that looks like a tag is refused.
    en_tags = set(HTML_TAG.findall(en))
    rest = re.sub(r"</?\d+/?>", "", tr)
    for tg in en_tags:
        rest = rest.replace(tg, "")
    if HTML_TAG.search(rest) or re.search(r"[<>]", rest) and not re.search(r"[<>]", en):
        errs.append("contains HTML or < >")
    # A straight double quote can close an HTML attribute the app builds the
    # text into (title="…"): only allowed where the English has one.
    if '"' in tr and '"' not in en:
        errs.append("uses a straight double quote — use your language's quotation marks instead")
    lost_tags = [tg for tg in en_tags if tg not in tr]
    if lost_tags:
        errs.append(f"must keep {' '.join(lost_tags)} exactly as written")
    pe, pt = I.placeholders(en), I.placeholders(tr)
    if pe != pt:
        lost, extra = sorted(pe - pt), sorted(pt - pe)
        errs.append("placeholders changed" + (f"; missing {lost}" if lost else "") + (f"; unknown {extra}" if extra else ""))
    for term in ("Phosphene", "LTX", "MiniMax", "Pro"):
        if re.search(rf"\b{term}\b", en) and not re.search(rf"\b{term}\b", tr) and term != "Pro":
            warns.append(f"“{term}” is missing — it should stay as written")
    if "\n" in tr and "\n" not in en:
        errs.append("contains a line break")
    plain = re.sub(r"</?\d+/?>", "", tr)
    if mx and len(plain) > mx:
        warns.append(f"{len(plain)} characters, limit {mx}")
    elif len(en) >= 6 and len(plain) > 2.2 * len(en) + 10:
        warns.append(f"{len(plain)} characters vs {len(en)} in English — check it fits")
    return errs, warns


def _keep_edges(en, tr):
    """Give the translation the English's leading/trailing spaces (the code
    glues these strings to others: 'Saved ' + name)."""
    if isinstance(en, dict) and isinstance(tr, dict):
        return {f: _keep_edges(en.get(f) or en.get("other", ""), v) for f, v in tr.items()}
    if not isinstance(en, str) or not isinstance(tr, str):
        return tr
    lead = re.match(r"\s*", en).group(0)
    trail = re.search(r"\s*$", en).group(0) if en.strip() else ""
    return lead + tr.strip() + trail


def cmd_import(args) -> int:
    lang = args.lang
    src = I.source_catalog(CAT_DIR)
    rounds = Path(args.ids) if args.ids else None
    ids: dict = {}
    for f in args.files:
        cand = (Path(rounds) if rounds else Path(f).parent) / f"{lang}.ids.json"
        if cand.exists():
            ids.update(I._read_json(cand))
    path = I.locale_file(lang, CAT_DIR)
    cur = I._read_json(path) or {}
    meta = cur.get("meta") or {"lang": lang, "name": I.NATIVE_NAMES.get(lang, lang),
                               "dir": "rtl" if I.base_tag(lang) in I.RTL_LANGS else "ltr"}
    strings = cur.get("strings") or {}
    source = cur.get("source") or {}
    reviewed = cur.get("reviewed") or {}
    n_ok = n_bad = n_warn = 0
    for f in args.files:
        try:
            reply = _json_from_reply(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"  ✗ {f}: not readable as JSON ({e}) — ask the model again for one JSON object")
            n_bad += 1
            continue
        for rid, tr in reply.items():
            keys = ids.get(rid) or ([rid] if rid in src else [])
            if not keys:
                print(f"  ✗ {f}: unknown id {rid!r} (not in this export)")
                n_bad += 1
                continue
            ent = src.get(keys[0])
            if ent is None:
                print(f"  ✗ {rid}: the English for this string no longer exists — skipped")
                continue
            errs, warns = validate(ent["text"], tr, lang, min((src[k].get("max") or 10 ** 6) for k in keys if k in src))
            ge, gw = glossary_check(ent["text"], tr, lang)
            errs += ge
            warns += gw
            short = _flat(ent["text"])[:50]
            if errs:
                print(f"  ✗ {rid} “{short}”: {'; '.join(errs)} — NOT imported")
                n_bad += 1
                continue
            for w in warns:
                print(f"  ! {rid} “{short}”: {w}")
                n_warn += 1
            tr = _keep_edges(ent["text"], tr)
            for k in keys:
                if k in src:
                    strings[k] = tr
                    source[k] = I.text_hash(src[k]["text"])
                    n_ok += 1
    missing = [i for i in ids if not any(k in strings for k in ids[i])]
    if not args.dry_run:
        _dump(path, {"meta": meta, "strings": dict(sorted(strings.items())),
                     "source": dict(sorted(source.items())), "reviewed": dict(sorted(reviewed.items()))})
    print(f"import: {n_ok} string(s) saved to {path.relative_to(REPO)}, {n_bad} refused, {n_warn} warning(s)"
          + (f"; {len(missing)} id(s) of this round still have no translation" if missing else ""))
    return 1 if n_bad else 0


# ---------------------------------------------------------------- native review

def _rounds(lang: str, out=None) -> Path:
    d = Path(out or (REPO / "i18n" / "rounds" / lang))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_locale(lang: str, data: dict) -> None:
    _dump(I.locale_file(lang, CAT_DIR), {
        "meta": data.get("meta") or {"lang": lang, "name": I.NATIVE_NAMES.get(lang, lang),
                                     "dir": "rtl" if I.base_tag(lang) in I.RTL_LANGS else "ltr"},
        "strings": dict(sorted(data["strings"].items())),
        "source": dict(sorted(data["source"].items())),
        "reviewed": dict(sorted(data.get("reviewed", {}).items()))})


def cmd_review(args) -> int:
    """Files for a native-speaker model to read the translated UI and flag
    what is unnatural, too literal or inconsistent."""
    lang = args.lang
    src = I.source_catalog(CAT_DIR)
    loc = I.load_locale(lang, CAT_DIR)
    groups: dict = {}
    for k, ent in src.items():
        tr = loc["strings"].get(k)
        if tr in (None, ""):
            continue
        if not args.all and loc["reviewed"].get(k) == I.text_hash(tr):
            continue
        sig = json.dumps([ent["text"], tr], ensure_ascii=False, sort_keys=True)
        g = groups.setdefault(sig, {"keys": [], "en": ent["text"], "tr": tr, "note": ent.get("note", "")})
        g["keys"].append(k)
    if not groups:
        print(f"review: every {lang} string is reviewed.")
        return 0
    out = _rounds(lang, args.out)
    name = I.NATIVE_NAMES.get(lang, lang)
    head = REVIEW_PROMPT.format(name=name, lang=lang, terms=", ".join(I.PROTECTED_TERMS))
    items = list(groups.values())
    ids, files = {}, []
    for bi in range(0, len(items), args.batch):
        chunk = items[bi:bi + args.batch]
        part = bi // args.batch + 1
        body = {}
        for i, g in enumerate(chunk, start=bi + 1):
            rid = f"r{i}"
            ids[rid] = {"keys": g["keys"], "part": part}
            body[rid] = {"en": g["en"], "tr": g["tr"], "note": g["note"]}
        p = out / f"{lang}.review{part:02d}.txt"
        p.write_text(head + glossary_block([g["en"] for g in chunk], lang)
                     + "```json\n" + json.dumps(body, ensure_ascii=False, indent=1) + "\n```\n", encoding="utf-8")
        files.append(p)
    _dump(out / f"{lang}.review.ids.json", ids)
    rel = out.relative_to(REPO) if out.is_relative_to(REPO) else out
    print(f"review: {sum(len(g['keys']) for g in items)} strings ({len(items)} unique) in {len(files)} file(s), "
          f"e.g. {files[0].name}. Save each reply as {lang}.reviewreplyNN.txt, then:\n"
          f"   python3 scripts/i18n_tool.py review-apply {lang} {rel}/{lang}.reviewreply*.txt")
    return 0


def cmd_review_apply(args) -> int:
    lang = args.lang
    src = I.source_catalog(CAT_DIR)
    loc = I.load_locale(lang, CAT_DIR)
    data = {"meta": loc["meta"], "strings": dict(loc["strings"]), "source": dict(loc["source"]),
            "reviewed": dict(loc["reviewed"])}
    fixed = bad = ok_parts = 0
    for f in args.files:
        fp = Path(f)
        ids = I._read_json(fp.parent / f"{lang}.review.ids.json")
        m = re.search(r"(\d+)\D*$", fp.stem)
        part = int(m.group(1)) if m else None
        try:
            reply = _json_from_reply(fp.read_text(encoding="utf-8")) if fp.read_text(encoding="utf-8").strip() else {}
        except (OSError, ValueError) as e:
            print(f"  ✗ {f}: not readable as JSON ({e}) — nothing marked reviewed"); bad += 1; continue
        refused = set()
        for rid, v in reply.items():
            meta = ids.get(rid)
            if not meta or not isinstance(v, dict) or not v.get("fix"):
                continue
            keys = [k for k in meta["keys"] if k in src]
            if not keys:
                continue
            en = src[keys[0]]["text"]
            fix = _keep_edges(en, v["fix"])
            errs, warns = validate(en, fix, lang, min((src[k].get("max") or 10 ** 6) for k in keys))
            ge, gw = glossary_check(en, fix, lang)
            errs += ge
            if errs:
                print(f"  ✗ {rid} “{_flat(en)[:40]}”: fix refused ({'; '.join(errs)}) — still needs review")
                refused.add(rid); bad += 1; continue
            print(f"  ✓ {rid} “{_flat(en)[:40]}” → “{_flat(fix)[:50]}”  ({str(v.get('why', ''))[:70]})")
            for k in keys:
                data["strings"][k] = fix
                data["source"][k] = I.text_hash(src[k]["text"])
            fixed += 1
        # every entry of this part that was not refused now counts as reviewed
        for rid, meta in ids.items():
            if part is not None and meta.get("part") != part:
                continue
            if rid in refused:
                continue
            for k in meta["keys"]:
                if data["strings"].get(k):
                    data["reviewed"][k] = I.text_hash(data["strings"][k])
        ok_parts += 1
    if not args.dry_run:
        _save_locale(lang, data)
    print(f"review-apply: {fixed} fix(es) saved, {bad} refused, {ok_parts} part(s) marked reviewed")
    return 1 if bad else 0


# ---------------------------------------------------------------- glossary per language

GLOSSARY_PROMPT = """You are setting the core vocabulary for the {name} ({lang}) interface of Phosphene, a Mac
app where people make AI videos, pictures and music. For each English term below (with what it
means in the app), give the word a native {name} speaker expects to see, the way popular video,
photo and music apps (CapCut, iMovie, Photos, Premiere, DaVinci Resolve, Canva) say it in {name}.
Use one neutral {name} that works in every country. Keep names marked "keep" as written.

Reply with ONE JSON object and nothing else:
{{"Term": {{"use": "the agreed word (and a sentence form if different)", "accept": ["2-4 short stems or words that any correct translation containing this term will include"]}}, ...}}

"""


def cmd_glossary(args) -> int:
    lang = args.lang
    doc = I._read_json(GLOSSARY)
    terms = doc.get("terms", [])
    if args.import_file:
        reply = _json_from_reply(Path(args.import_file).read_text(encoding="utf-8"))
        n = 0
        for t in terms:
            v = reply.get(t["term"])
            if isinstance(v, dict) and v.get("use") and isinstance(v.get("accept"), list) and v["accept"]:
                t[lang] = {"use": str(v["use"]), "accept": [str(a) for a in v["accept"]]}
                n += 1
        _dump(GLOSSARY, doc)
        print(f"glossary: {n} {lang} term(s) saved to {GLOSSARY.relative_to(REPO)}")
        return 0
    missing = [t for t in terms if lang not in t]
    if not missing:
        print(f"glossary: every term has an agreed {lang} word.")
        return 0
    out = _rounds(lang, args.out)
    body = {t["term"]: t["meaning"] for t in missing}
    p = out / f"{lang}.glossary.txt"
    p.write_text(GLOSSARY_PROMPT.format(name=I.NATIVE_NAMES.get(lang, lang), lang=lang)
                 + "```json\n" + json.dumps(body, ensure_ascii=False, indent=1) + "\n```\n", encoding="utf-8")
    rel = out.relative_to(REPO) if out.is_relative_to(REPO) else out
    print(f"glossary: {len(missing)} term(s) without a {lang} word → {p.relative_to(REPO) if p.is_relative_to(REPO) else p}\n"
          f"Save the reply as {lang}.glossaryreply.txt, then:\n"
          f"   python3 scripts/i18n_tool.py glossary {lang} --import {rel}/{lang}.glossaryreply.txt")
    return 0


# ---------------------------------------------------------------- pseudo

_ACC = str.maketrans("aceinousyAEIOUCNYrtldgkhzw",
                     "àçéîñöüšýÅÉÎÖÜÇÑÝŕţļđğķĥžŵ")


def _pseudo_text(s: str, rtl: bool) -> str:
    parts = re.split(r"(\{[A-Za-z_]\w*\}|<[^<>]+>)", s)
    out = []
    for p in parts:
        if re.fullmatch(r"\{[A-Za-z_]\w*\}|<[^<>]+>", p or ""):
            out.append(p)
            continue
        if rtl:
            # Hebrew letters keep the bidi algorithm honest: real RTL text,
            # same shape as the English (word lengths kept).
            heb = "אבגדהוזחטיכלמנסעפצקרשת"
            out.append(re.sub(r"[A-Za-z]", lambda m: heb[(ord(m.group(0).lower()) - 97) % len(heb)], p))
        else:
            out.append(p.translate(_ACC))
    t = "".join(out)
    if not rtl:
        # ~30% longer, like German: the padding is words, so it wraps the
        # way real text does. The brackets show where a string starts and
        # ends — text with no brackets on screen is not in the lookup yet.
        pad = max(2, round(len(s) * 0.3))
        words = []
        while pad > 0:
            n = min(pad, 5)
            words.append("~" * n)
            pad -= n + 1
        t = "[" + t + " " + " ".join(words) + "]"
    return t


def cmd_pseudo(args) -> int:
    src = I.source_catalog(CAT_DIR)
    for lang, rtl, name in (("en-XA", False, "Pseudo · long"), ("ar-XB", True, "Pseudo · RTL")):
        strings = {}
        for k, ent in src.items():
            text = ent["text"]
            strings[k] = ({f: _pseudo_text(v, rtl) for f, v in text.items()}
                          if isinstance(text, dict) else _pseudo_text(text, rtl))
        _dump(I.locale_file(lang, CAT_DIR), {
            "meta": {"lang": lang, "name": name, "dir": "rtl" if rtl else "ltr", "pseudo": True},
            "strings": strings, "source": {k: I.text_hash(e["text"]) for k, e in src.items()}})
        print(f"pseudo: {lang} ({len(strings)} strings)")
    return 0


# ---------------------------------------------------------------- status / check

def _words(text) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z'’]*", _flat(text)))


def cmd_status(args) -> int:
    src = I.source_catalog(CAT_DIR)
    legacy = js_untranslated()
    lw = sum(_words(t) * c for f in legacy.values() for t, c in f.items())
    sw = sum(_words(e["text"]) for e in src.values())
    print(f"Studio English: {len(src)} strings in the lookup ({sw} words).")
    print(f"Not yet in the lookup (JS copy still written inline): "
          f"{sum(len(v) for v in legacy.values())} strings ({lw} words).")
    print(f"Coverage of the Studio's text by words: {100 * sw / max(1, sw + lw):.0f}%")
    for a in I.available(CAT_DIR)[1:]:
        loc = I.load_locale(a["code"], CAT_DIR)
        done = sum(1 for k in src if loc["strings"].get(k))
        stale = sum(1 for k in src if loc["strings"].get(k) and loc["source"].get(k) != I.text_hash(src[k]["text"]))
        rev = sum(1 for k in src if loc["strings"].get(k) and loc["reviewed"].get(k) == I.text_hash(loc["strings"][k]))
        gl_missing = sum(1 for t in glossary() if a["code"] not in t)
        state = "DONE" if done == len(src) and rev == done and not stale else "in progress"
        print(f"  {a['code']:8} {a['name']:22} {done}/{len(src)} translated, {rev} reviewed"
              + (f", {stale} need a refresh (English changed)" if stale else "")
              + (f", glossary missing {gl_missing} term(s)" if gl_missing and not loc["meta"].get("pseudo") else "")
              + f" — {state}")
    if args.verbose:
        for f, items in legacy.items():
            print(f"\n{f}: {len(items)}")
            for t, c in list(items.items())[:400]:
                print(f"   {c}× {t[:110]}")
    return 0


def cmd_baseline(args) -> int:
    _dump(LEGACY, {"about": "JS copy not yet converted to t() — the i18n gate refuses NEW entries. "
                            "Shrink it, never grow it: python3 scripts/i18n_tool.py baseline",
                   "files": js_untranslated()})
    print(f"baseline: written {LEGACY.relative_to(REPO)}")
    return 0


def check() -> list[str]:
    """Every gate problem as a line. Empty list = pass."""
    out: list[str] = []
    cat, problems = build_catalog()
    out += problems
    disk = I._read_json(SOURCE).get("strings", {})
    if disk != cat:
        changed = sorted(set(disk) ^ set(cat)) + sorted(k for k in set(disk) & set(cat) if disk[k] != cat[k])
        out.append(f"i18n/studio/en.json is out of date ({len(changed)} string(s), e.g. {changed[:3]}) "
                   "— run: python3 scripts/i18n_tool.py extract")
    base = (I._read_json(LEGACY).get("files") or {})
    now = js_untranslated()
    for f, items in now.items():
        old = base.get(f, {})
        for t, c in items.items():
            if c > old.get(t, 0):
                out.append(f"webapp/js/{f}: new English UI text outside the lookup: “{t[:80]}” — "
                           f"wrap it: T('<module>.<name>', '…')")
    for a in I.available(CAT_DIR)[1:]:
        loc = I.load_locale(a["code"], CAT_DIR)
        for k, tr in loc["strings"].items():
            if k not in cat:
                continue                     # an old key: harmless, dropped at runtime
            errs, _ = validate(cat[k]["text"], tr, a["code"], None)
            for e in errs:
                out.append(f"i18n/studio/{a['code']}.json: {k}: {e}")
    return out


def cmd_check(args) -> int:
    probs = check()
    for p in probs[:80]:
        print("  ✗", p)
    if len(probs) > 80:
        print(f"  … and {len(probs) - 80} more")
    print("i18n check: " + ("PASS" if not probs else f"FAIL ({len(probs)} problem(s))"))
    return 1 if probs else 0


def cmd_wrap(args) -> int:
    """Route inline JS copy through T() (scripts/i18n_wrap.py), then re-extract."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import i18n_wrap  # noqa: PLC0415
    rc = i18n_wrap.wrap_files([Path(f) for f in args.files if Path(f).name not in NOT_STUDIO_JS])
    cmd_extract(args)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("export"); s.add_argument("lang"); s.add_argument("--out")
    s.add_argument("--batch", type=int, default=120); s.add_argument("--all", action="store_true")
    s.add_argument("--source", help=argparse.SUPPRESS)
    s = sub.add_parser("import"); s.add_argument("lang"); s.add_argument("files", nargs="+")
    s.add_argument("--ids"); s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("review"); s.add_argument("lang"); s.add_argument("--out")
    s.add_argument("--batch", type=int, default=150); s.add_argument("--all", action="store_true")
    s = sub.add_parser("review-apply"); s.add_argument("lang"); s.add_argument("files", nargs="+")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("glossary"); s.add_argument("lang"); s.add_argument("--out")
    s.add_argument("--import", dest="import_file")
    sub.add_parser("pseudo")
    sub.add_parser("check")
    s = sub.add_parser("tag"); s.add_argument("--dry-run", action="store_true")
    sub.add_parser("extract")
    sub.add_parser("baseline")
    s = sub.add_parser("wrap"); s.add_argument("files", nargs="+")
    s = sub.add_parser("status"); s.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    return {"export": cmd_export, "import": cmd_import, "pseudo": cmd_pseudo, "check": cmd_check,
            "tag": cmd_tag, "extract": cmd_extract, "baseline": cmd_baseline,
            "status": cmd_status, "wrap": cmd_wrap, "review": cmd_review,
            "review-apply": cmd_review_apply, "glossary": cmd_glossary}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
