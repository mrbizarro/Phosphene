"""Section tags the way YuE2 reads them.

Gemma numbers its sections — "[Verse 1]", "[Chorus 2]" — no matter how the
prompt asks, and it invents "[Hook]" and "[Solo]". YuE2's lyrics protocol reads
BARE tags, and the studio's label pills are a closed list of them. So every
lyrics-producing path normalises once, deterministically, here — rather than
hoping the next model version obeys a prompt.
"""
from __future__ import annotations

import re

CANON = ("Intro", "Verse", "Pre-Chorus", "Chorus", "Bridge", "Instrumental", "Outro")
_ALIASES = {
    "intro": "Intro", "verse": "Verse", "prechorus": "Pre-Chorus", "chorus": "Chorus",
    "refrain": "Chorus", "hook": "Chorus", "bridge": "Bridge", "instrumental": "Instrumental",
    "interlude": "Instrumental", "solo": "Instrumental", "break": "Instrumental",
    "outro": "Outro", "ending": "Outro", "coda": "Outro",
}
_TAG_RX = re.compile(r"^[ \t]*\[[ \t]*([A-Za-z][A-Za-z \-]*?)[ \t]*\d*[ \t]*\][ \t]*$", re.MULTILINE)


def canonical_tag(name: str) -> str | None:
    """"Pre-Chorus 2" -> "Pre-Chorus"; "hook" -> "Chorus"; unknown -> None."""
    key = re.sub(r"[^a-z]", "", (name or "").lower())
    return _ALIASES.get(key)


def normalize_section_tags(text: str) -> str:
    """Rewrite every tag line to its bare canonical form; leave lines alone."""
    def fix(m):
        canon = canonical_tag(m.group(1))
        return f"[{canon}]" if canon else m.group(0)
    return _TAG_RX.sub(fix, text or "")
