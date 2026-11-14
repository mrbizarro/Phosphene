"""i18n_plain.py — the frontend source as it read before the translation
plumbing (docs/TRANSLATING.md), for tests that pin English copy or markup by
substring.

    plain(text)  drops data-i18n / data-i18n-<attr> attributes and unwraps
                 T('key', 'English', vars) / TA(…) to the English literal,
                 so `'class="range-val">Auto<'` and `'Saved.'` still match.

Pure string work, no imports beyond re. A test that wants to see the lookup
itself reads the file as-is.
"""
from __future__ import annotations

import re

_ATTR = re.compile(r' data-i18n(?:-[a-z-]+)?="[^"]*"')


def _literal_end(s: str, i: int) -> int:
    q = s[i]
    j = i + 1
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == q:
            return j + 1
        j += 1
    return -1


def _call_end(s: str, j: int) -> int:
    """Index just past the ')' closing a call whose args continue at j."""
    depth = 1
    while j < len(s) and depth:
        c = s[j]
        if c in "'\"`":
            k = _literal_end(s, j) if c != "`" else s.find("`", j + 1) + 1
            if k <= 0:
                return -1
            j = k
            continue
        depth += (c in "([{") - (c in ")]}")
        j += 1
    return j if depth == 0 else -1


_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _decode(lit: str) -> str:
    body = lit[1:-1]
    def rep(m):
        e = m.group(1)
        if e[0] == "u":
            return chr(int(e[2:-1] if e.startswith("u{") else e[1:], 16))
        if e[0] == "x":
            return chr(int(e[1:], 16))
        if e == "\n":
            return ""
        return _ESC.get(e, e)
    return re.sub(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\n|.)", rep, body, flags=re.S)


def _tpl(text: str) -> str:
    """A decoded string written as template-literal body."""
    return text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")


def _split_top(src: str, sep: str) -> list[str]:
    parts, depth, cur, i = [], 0, [], 0
    while i < len(src):
        c = src[i]
        if c in "'\"`":
            k = _literal_end(src, i) if c != "`" else src.find("`", i + 1) + 1
            k = len(src) if k <= 0 else k
            cur.append(src[i:k]); i = k; continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(cur)); cur = []; i += 1; continue
        cur.append(c); i += 1
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _vars(src: str) -> dict:
    src = src.strip()
    if not (src.startswith("{") and src.endswith("}")):
        return {}
    out = {}
    for item in _split_top(src[1:-1], ","):
        kv = _split_top(item, ":")
        name = kv[0].strip()
        out[name] = ":".join(kv[1:]).strip() if len(kv) > 1 else name
    return out


def _filled(text: str, vs: dict) -> str:
    body = _tpl(text)
    return re.sub(r"\{([A-Za-z_]\w*)\}", lambda m: "${" + vs[m.group(1)] + "}" if m.group(1) in vs else m.group(0), body)


_TP_FN = ("((n, f, v) => Object.entries(Object.assign({count: n}, v || {})).reduce("
          "(s, [k, x]) => s.split('{' + k + '}').join(String(x)), (n === 1 && f.one) ? f.one : (f.other || f.one || '')))")


def plain(text: str) -> str:
    text = _ATTR.sub("", text)
    # The page's language is English in the plain view.
    text = re.sub(r"(?<![\w.$])i18nLang\(\)", "'en'", text)
    out, pos = [], 0
    for m in re.finditer(r"(?<![\w.$])(TA?|TP)\(\s*'[A-Za-z0-9_.\-]+'\s*,\s*", text):
        if m.start() < pos:
            continue
        end = _call_end(text, m.end())
        if end < 0:
            continue
        args = _split_top(text[m.end():end - 1], ",")
        wrapped = text[max(0, m.start() - 2):m.start()] == "${" and text[end:end + 1] == "}"
        a, b = (m.start() - 2, end + 1) if wrapped else (m.start(), end)
        if m.group(1) == "TP":
            rep = _TP_FN + "(" + ", ".join(x.strip() for x in args) + ")"
            rep = "${" + rep + "}" if wrapped else rep
        else:
            lit = args[0].strip()
            if not lit or lit[0] not in "'\"`" or (lit[0] == "`" and "${" in lit):
                continue
            vs = _vars(",".join(args[1:])) if len(args) > 1 else {}
            if wrapped:
                rep = _filled(_decode(lit), vs)
            elif vs:
                rep = "`" + _filled(_decode(lit), vs) + "`"
            else:
                rep = lit
        out.append(text[pos:a])
        out.append(rep)
        pos = b
    out.append(text[pos:])
    return "".join(out)


def install() -> None:
    """For a test module that pins English copy by substring: make
    Path.read_text() of files under webapp/ return plain(text). One call at
    the top of the test file; this process only, webapp files only."""
    import pathlib
    if getattr(pathlib.Path.read_text, "_i18n_plain", False):
        return
    orig = pathlib.Path.read_text

    def read_text(self, *a, **kw):
        t = orig(self, *a, **kw)
        parts = self.resolve().parts
        return plain(t) if "webapp" in parts else t
    read_text._i18n_plain = True
    pathlib.Path.read_text = read_text
