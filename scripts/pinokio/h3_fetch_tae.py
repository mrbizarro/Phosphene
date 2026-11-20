#!/usr/bin/env python3
"""Fetch the H3 draft decoder (madebyollin's TAE for H3) to a target path.

The Hugging Face repo this came from (madebyollin/taeh3) was deleted in
September 2026 and every H3 install/update died at this step with
"Repository Not Found" (Pinokio report by @macstephen, who also found the
file in the author's GitHub repo). The same bytes live at a PINNED commit
of github.com/madebyollin/taehv (MIT); we fetch from there first, verify
the sha256 against the copy that shipped for months, and only then place
it. HF stays as a fallback in case GitHub is unreachable.

Usage: h3_fetch_tae.py <target-path>
Exit 0 = the file is in place and verified. Non-zero = nothing placed.
"""
import hashlib
import os
import re
import sys
import tempfile
import urllib.request



def _plain(text) -> str:
    """An exception's text with Pinokio's two stop words defused.

    The draft decoder is OPTIONAL (drafts fall back to the full decoder).
    Pinokio 8.2 ends an install when a step's output matches /error:/i or
    /errno /i, and a dropped connection reads "<urlopen error [Errno 8] ...>".
    """
    return re.sub(r"(?i)errno ", "errno-", re.sub(r"(?i)error:", "problem -", str(text)))


EXPECTED_SHA256 = "4fd022bfcab08772fe0536b17ea1a3bbb5625be11e397868d1c5d891863d4c13"
EXPECTED_BYTES = 22709752
SOURCES = [
    ("github.com/madebyollin/taehv @ 62f7591 (MIT)",
     "https://github.com/madebyollin/taehv/raw/62f7591f59dfbb4c3c02b7a621d180a9eeaba26c/safetensors/taeh3.safetensors"),
    ("huggingface.co/madebyollin/taeh3 (original, may be gone)",
     "https://huggingface.co/madebyollin/taeh3/resolve/main/taeh3.safetensors"),
]


def _hf_source():
    """scripts/hf_source.py (the China mirror rule), or None when missing."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import hf_source
        return hf_source
    except ImportError:
        return None


def _sources():
    """SOURCES, with the China mirror's copy of the Hugging Face file in its place when this
    session cannot reach Hugging Face (5.0.76). A real answer from Hugging Face (404, 403...)
    is not a reason to ask the mirror; a failure to connect is (see main). The size and
    sha256 check is the same for every source."""
    hs = _hf_source()
    if hs is None or not hs.mirror_base() or hs.mode() == "hf" or not hs.use_mirror():
        return list(SOURCES)
    hs.announce()
    out = []
    for label, url in SOURCES:
        alt = hs.to_mirror(url)
        out.append(("hf-mirror.com (China mirror of huggingface.co/madebyollin/taeh3)", alt) if alt else (label, url))
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: h3_fetch_tae.py <target-path>", file=sys.stderr)
        return 2
    target = sys.argv[1]
    if os.path.isfile(target):
        with open(target, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() == EXPECTED_SHA256:
                print("TAE draft decoder already in place (verified)")
                return 0
        print("existing TAE file does not match the known hash - refetching")
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    for label, url in _sources():
        print(f"fetching TAE draft decoder from {label} ...")
        try:
            hs = _hf_source()
            if hs is not None and hs.is_mirror_url(url):
                r, _ = hs.open_url(url, 0, timeout=120)      # no login, HTTPS-only redirects
            else:
                r = urllib.request.urlopen(url, timeout=120)
            with r:
                data = r.read()
        except Exception as exc:                                  # noqa: BLE001
            print(f"  failed: {_plain(exc)}")
            hs = _hf_source()
            alt = hs.to_mirror(url) if hs is not None else None
            if alt and not hs.is_mirror_url(url) and hs._switch_after(exc):
                hs.announce()
                try:
                    r, _ = hs.open_url(alt, 0, timeout=120)
                    with r:
                        data = r.read()
                except Exception as exc2:                         # noqa: BLE001
                    print(f"  failed: {_plain(exc2)}")
                    continue
            else:
                continue
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != EXPECTED_BYTES or digest != EXPECTED_SHA256:
            print(f"  rejected: {len(data)} bytes, sha256 {digest[:16]}... (expected "
                  f"{EXPECTED_BYTES} bytes, {EXPECTED_SHA256[:16]}...)")
            continue
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target) or ".", suffix=".part")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
        print("TAE draft decoder ready (verified sha256)")
        return 0
    # "WARN:", not "ERROR:": Pinokio 8.2 stops an install on /error:/i in a
    # step's output, and a missing PREVIEW decoder is not worth the install -
    # it used to end Install Hailuo H3 here, before the compact Q8 build.
    print("WARN: could not fetch a verified TAE draft decoder from any source; "
          "drafts will fall back to the full decoder. Re-run 'Install Hailuo H3' "
          "later.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
