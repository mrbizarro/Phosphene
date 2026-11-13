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
