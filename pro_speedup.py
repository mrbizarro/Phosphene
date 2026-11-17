"""Pro speed-up for LTX-2.5 clips: which renders ask for it, and what to say about their sound.

The Pro side decides whether and how a render is sped up; this file only decides whether the
Studio asks. It asks for the renders that were measured (Balanced text or picture to video, up to
10 seconds, landscape, vertical or square, on a Mac the Pro side says it can serve: 32 GB or more,
and since 5.0.20 16 to 24 GB too) and never for anything else, so a Pro member's other renders are
exactly the Free ones.

Measured on an M4 Max 64 GB against the same renders on Free, same prompt and seed: a 5-second
clip about a quarter faster, a 10-second clip about a fifth. The sound of a sped-up clip can come
out quieter, so the Studio levels every one of them (h3_loudness, the same pass H3 clips get) and
says so plainly when a clip that asked for sound came out near silent.

Pure functions only: unit-tested without a model, a GPU or a licence.
"""
from __future__ import annotations

import re

import pro_perks

# What was measured, and nothing past it.
QUALITIES = ("balanced",)
MODES = ("t2v", "i2v")
MAX_FRAMES = 241            # 10 s at 24 fps
MAX_PIXELS = 1024 * 576     # landscape 1024x576, vertical 576x1024, square 768x768
MAX_SIDE = 1024
FPS = 24.0
OFF_VALUES = ("0", "false", "off", "no")

# Words that say the prompt wants sound: a spoken line, music, an effect.
_SOUND_RX = re.compile(
    r"[\"“”]|\bsound|\bmusic|\bsong|\bsing|\bspeak|\bsays?\b|\bsaid\b|\btalk|\bvoice|"
    r"\bdrum|\bbeat|\bnoise|\baudio|\bhum|\broar|\blaugh|\bshout|\bwhisper|\bbark|\bbirdsong|"
    r"\bwind\b|\bsurf\b|\bwaves?\b|\bcrowd|\bapplause|\bengine|\bclink|\bhiss|\bsplash", re.I)

QUIET_NOTE = ("This clip's sound came out almost silent, and the prompt asked for sound. "
              "That can happen with the Pro speed-up. Render it again without the speed-up "
              "for the usual sound.")


def draft_on(value) -> bool:
    """The per-render Fast draft choice from the form: off unless it says on."""
    return str(value if value is not None else "off").strip().lower() in ("1", "true", "on", "yes")


# ---- Fast draft (owner, 12 Nov 2026: "LTX fast drafts for Pro") ----------------------------------
# The Speed row's Fast draft pill. On Free it runs the engine's fast schedule (about 13 % faster). For
# a Pro member, on a render the Pro speed-up serves, the Pro add-on makes the draft instead: about
# half the time of the usual clip, a different take of the same scene. How is the add-on's business.
#
# MEASURED, per Mac class, on the same clip and seed (5-second LTX-2.5 Balanced, 121 frames). A Mac
# with 24 GB or less streams the model from disk, so it gets the streamed row; 32 GB and up the
# resident one. Every number shown to a user is one of these ratios times a real time (this clip's
# own, or the fleet's for this Mac), never a guess. "where" says how it was measured.
# The numbers live in pro_perks.json (perk "ltx-fast-draft", with how it was measured),
# the one list every Pro surface reads; here they keep the shape this module always used ({free_s, draft_s,
# frames, where}). 5 s Balanced, 13 Nov 2026: Free 147.2/150.5/150.9/150.4 s, Fast draft 75.5/75.9/76.3/76.2 s
# on 64 GB.
def _draft_row(row) -> dict:
    if not isinstance(row, dict) or not row.get("free_s") or not row.get("pro_s"):
        return {}
    return {"free_s": float(row["free_s"]), "draft_s": float(row["pro_s"]),
            "frames": int(row.get("frames") or 121), "where": str(row.get("where") or "")}


FAST_DRAFT_MEASURED = {cls: r for cls, r in ((c, _draft_row(pro_perks.rows("ltx-fast-draft").get(c)))
                                            for c in ("streamed", "resident")) if r}
FAST_DRAFT_MIN_RAM_GB = 16
# The Pro moment never claims a saving smaller than this.
MOMENT_MIN_SAVED_SEC = 20.0


def mac_class(low_memory_mac: bool) -> str:
    return "streamed" if low_memory_mac else "resident"


def draft_ratio(cls: str, frames: int | None = None) -> float | None:
    """Fast draft / Free wall time for this Mac class, or None where nothing was measured."""
    row = FAST_DRAFT_MEASURED.get(cls) or {}
    free_s, draft_s = row.get("free_s"), row.get("draft_s")
    if not free_s or not draft_s or (frames is not None and int(frames) != int(row.get("frames") or 0)):
        return None
    return float(draft_s) / float(free_s)


def fmt_secs(sec: float, unit_of: float | None = None) -> str:
    """'50 s', or '4 min' when the larger time of the pair (`unit_of`) is 10 minutes or more, so a pair
    always reads in one unit: '79 s instead of 155 s', never '79 s instead of 3 min'."""
    sec = max(1.0, float(sec))
    big = max(sec, float(unit_of or 0))
    return f"{int(round(sec))} s" if big < 600 else f"{max(1, int(round(sec / 60.0)))} min"


def moment(*, took: float, cls: str, frames: int, fits: bool, ram_gb: float) -> dict | None:
    """The Pro moment's numbers for one finished Free clip, or None: {"pro_sec", "saved_sec", "text"}.
    `took` is this clip's own time on this Mac; `fits` is whether Fast draft would have served it."""
    if not fits or float(ram_gb or 0) < FAST_DRAFT_MIN_RAM_GB:
        return None
    ratio = draft_ratio(cls, frames)
    try:
        took = float(took)
    except (TypeError, ValueError):
        return None
    if ratio is None or took <= 0:
        return None
    pro_sec = took * ratio
    if took - pro_sec < MOMENT_MIN_SAVED_SEC:
        return None
    return {"pro_sec": round(pro_sec, 1), "saved_sec": round(took - pro_sec, 1),
            "text": moment_text("ltx-fast-draft",
                                pro=fmt_secs(pro_sec, took), free=fmt_secs(took))}


MOMENT_FALLBACK = "Pro would have made this clip in about {pro} instead of {free}, with Fast draft. Pro keeps Phosphene free."


def moment_text(perk_id: str, *, pro: str, free: str) -> str:
    """The perk's own moment line from pro_perks.json, with this clip's two times filled in."""
    t = pro_perks.moment(perk_id) or MOMENT_FALLBACK
    try:
        return t.format(pro=pro, free=free)
    except (KeyError, IndexError, ValueError):
        return MOMENT_FALLBACK.format(pro=pro, free=free)


def toggle_on(value) -> bool:
    """The per-render switch from the form: on unless it says off."""
    return str(value if value is not None else "on").strip().lower() not in OFF_VALUES


def wanted(*, toggle, version: str, quality: str, mode: str, added_styles, character: bool, hdr: bool,
           inspire: bool, schedule_preset: str, fps: float, frames: int, width: int, height: int,
           windows: bool) -> tuple[bool, str]:
    """(ask?, why not). `why not` is a short reason for the log; "" when it asks."""
    if not toggle_on(toggle):
        return False, "switched off for this render"
    if version != "ltx25":
        return False, "LTX-2.5 only"
    if quality not in QUALITIES or mode not in MODES:
        return False, "Balanced text or picture to video only"
    if added_styles or character or hdr:
        return False, "not with characters or added styles"
    if inspire or (schedule_preset or "") not in ("", "default"):
        return False, "not with Inspire or a speed preset"
    if windows or float(fps) != FPS or not 0 < int(frames) <= MAX_FRAMES:
        return False, "clips up to 10 seconds at 24 fps"
    if int(width) * int(height) > MAX_PIXELS or max(int(width), int(height)) > MAX_SIDE:
        return False, "the Balanced canvas only"
    return True, ""


def asks_for_sound(prompt: str) -> bool:
    return bool(_SOUND_RX.search(prompt or ""))


def came_out_quiet(sound_level: dict | None, prompt: str, silent_below: float) -> bool:
    """True when a sped-up clip's sound measured near silent and the prompt wanted sound."""
    before = (sound_level or {}).get("before") or {}
    lufs = before.get("lufs")
    if lufs is None:
        return False
    try:
        lufs = float(lufs)
    except (TypeError, ValueError):
        return False
    return lufs < silent_below and asks_for_sound(prompt)
