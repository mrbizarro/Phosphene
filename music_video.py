"""Music video — a song and a set of pictures become a shot list.

THE WHOLE IDEA IS THAT THE SONG IS THE CLOCK. Everything the panel needs to
shoot a music video already exists: `a2v` renders a singing clip from one
picture and one stretch of a waveform (`audio_start_time`), `i2v` renders
B-roll from a still, `storyboard_edit.beat_map` says where the bars are, and
the storyboard machinery renders, reconciles and assembles a list of shots.
What was missing is the thing in between — a plan that says WHICH second of
the song each picture is looking at.

Two pure functions, in the order the pipeline runs them:

  `song_sections`     — the song's own structure: vocal stretches and
                        instrumental ones, in seconds.
  `plan_music_video`  — those sections plus the cast of pictures, as a
                        storyboard board the existing render/film path runs.

PURE ON PURPOSE. Nothing here imports the panel, touches the queue, or writes
a file: the planner is the part that has to be right before an hour of GPU
time is spent, so it must be testable with a synthetic WAV and no weights.
`storyboard` (stdlib-only) is imported for the frame grid; `storyboard_edit`
and numpy are imported lazily inside the one function that analyses audio, the
same way the panel defers them, so a caller that only plans pays for neither.

THE ONE INVARIANT, and every number below exists to keep it: the shots TILE
THE SONG, in order, with no gaps and no overlaps, and a singing shot's
`audio_start_time` is its own start in that tiling. Concatenate the rendered
clips, lay the song underneath from 0:00, and every mouth is on its own words.
That is why `film_start` is accumulated from the FRAME-EXACT durations rather
than from the section boundaries: a frame count lands on LTX's 8k+1 grid, so
a 4.30 s B-roll shot is really 4.375 s, and a planner that ignored the 75 ms
would have the last singing shot of a three-minute song lip-syncing to the
wrong line.
"""
from __future__ import annotations

import math
import re

import storyboard

# ---------------------------------------------------------------------------
# THE CAST — what a picture is for
# ---------------------------------------------------------------------------
#: Closed vocabulary. A Singer picture is a face that can be filmed singing
#: (a2v); Instrument and Room are B-roll (i2v). Three words, because the user
#: is tagging photographs on a drop area, not writing a shot list.
ROLES = ("singer", "instrument", "room")

#: Where a B-roll picture is pinned, if anywhere. "open" is the film's first
#: B-roll shot and "close" its last — the two slots a person actually has an
#: opinion about, because they are the ones an audience reads as a decision.
#: Everything else is "any" and takes its turn in the weighted rotation.
USES = ("open", "close", "any")

#: A picture's share of the rotation. Capped because a weight is a hint about
#: taste and a four-figure one is a typo that would build a ring of that many
#: entries.
MAX_WEIGHT = 20

#: The line each role contributes to a prompt, per what the shot is doing.
#: Plain English, no jargon: these get pasted in front of the user's one
#: "Look" line and the result has to read like a direction, not a config.
ROLE_LINES = {
    ("singer", "singing"): "close-up, singing to camera",
    # A Singer picture can still be B-roll — when there is nothing else to cut
    # to, or in the instrumental. It must NOT say "singing" there: the mouth
    # has no waveform driving it on an i2v shot.
    ("singer", "broll"): "close-up, still, eyes down between lines",
    ("instrument", "broll"): "hands on the piano keys, slow push-in",
    ("room", "broll"): "wide, small room, warm light",
}

# ---------------------------------------------------------------------------
# THE DURATION AXIS
# ---------------------------------------------------------------------------
#: The LTX length cells as (seconds, frames), longest first.
#:
#: THE PANEL OWNS THE REAL TABLE (`LTX_LENGTHS`) and the routes hand it in via
#: `cells_from_lengths()`, so at runtime there is exactly one source of truth.
#: This default exists because this module must plan with no panel in the
#: process (a unit test, a CLI, a machine with no weights) — and it states only
#: the SECONDS: the frame count is derived by `storyboard.ltx_frames_for`, the
#: same function `shot_to_job` uses, so the two cannot disagree about a cell.
#: `test_music_video.py` pins this tuple against the panel's own table.
LTX_CELL_SECONDS = (20, 10, 7, 5, 3)
LTX_CELLS = tuple((s, storyboard.ltx_frames_for(s)) for s in LTX_CELL_SECONDS)

#: A cell that is NOT available on every canvas, and the qualities it is.
#:
#: This mirrors the `qualities` column of the panel's own `LTX_LENGTHS`, and
#: `test_music_video.py` pins the two together. It exists because the cell set
#: is only half the duration axis: 20 s / 481 frames renders at 640×480 and
#: DIES AROUND FRAME 454 on the standard 1024×576 canvas (issue #46), so the
#: panel offers it on Quick alone. A planner that read the seconds and ignored
#: this column wrote 481-frame a2v shots onto a board whose delivery pass is
#: Standard — an hour of render per shot that cannot finish.
LTX_CELL_QUALITIES = {20: ("quick",)}

#: The pass a plan is made for when the caller does not name one. The board's
#: FINAL pass is what delivers the film and the panel ships it as "standard",
#: so an unqualified plan is planned for that canvas — the conservative half
#: of the choice, because a cell that is missing costs a cut and a cell that
#: cannot render costs the shot.
DEFAULT_QUALITY = "standard"

# The shortest shot the board will keep as planned (see plan_music_video).
MIN_SHOT_S = 1.0
FPS = storyboard.LTX_FPS

#: A vocal stretch is filmed in windows this long; a B-roll shot is this long.
#: The singing window's floor is what makes a singing shot worth its render —
#: below ten seconds the cut rate reads as a montage, not a performance.
SINGING_WINDOW = (10, 20)
BROLL_WINDOW = (3, 7)

#: Phrase length when the song has no score to read: eight bars is the unit
#: nearly all of this material is written in, and a phrase that straddles a
#: real section edge is fixed by the energy classifier, not by the grid.
PHRASE_BARS = 8

#: The band a voice lives in, and how much of the track's energy has to be in
#: it before a phrase counts as sung. Deliberately crude — it decides whether
#: a stretch gets a face or a piano, and a wrong call costs one shot, not the
#: film. The threshold is a ratio so it is level-independent.
VOCAL_BAND_HZ = (1000.0, 4000.0)
VOCAL_BAND_RATIO = 0.18


class MusicVideoError(Exception):
    """Raised when a song or a cast cannot be planned. Message is user-facing."""


# ---------------------------------------------------------------------------
# PART A — the song's own structure
# ---------------------------------------------------------------------------
# A YuE2 song knows more about itself than any analyser could work out: the
# sidecar carries the LYRICS (bracket tags, a bare tag meaning instrumental)
# and the SCORE (`M:` meter, `Q:` tempo, `% section` comments, bars between
# pipes). Bars times tempo is the section boundary, exactly, with no signal
# processing at all. Everything below the score path is the fallback for a
# song that came from anywhere else.

_ABC_SECTION_RE = re.compile(r"^%(?!%)\s*(.+?)\s*$")
_ABC_VOICE_RE = re.compile(r"^V:\s*(\S+)")
_ABC_MULTIREST_RE = re.compile(r"^Z(\d*)$")
_LYRIC_TAG_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _tempo_seconds_per_bar(headers: dict) -> float | None:
    """Seconds per bar from an ABC header block, or None when it cannot say.

    `M:` is the meter as a fraction of a whole note per bar; `Q:` is a note
    length and how many of them fit in a minute (`Q:1/4=128`), or a bare
    number meaning quarter notes per minute. Both forms appear in the wild and
    YuE2 writes the first.
    """
    meter = str(headers.get("M") or "").strip()
    tempo = str(headers.get("Q") or "").strip()
    if not meter or not tempo:
        return None
    try:
        if meter.upper() in ("C", "C|"):
            num, den = (4, 4) if meter.upper() == "C" else (2, 2)
        else:
            num, den = (int(x) for x in meter.split("/", 1))
        quarters_per_bar = 4.0 * num / den
    except (ValueError, ZeroDivisionError):
        return None
    try:
        if "=" in tempo:
            note, per_min = tempo.split("=", 1)
            a, b = (int(x) for x in note.strip().split("/", 1))
            per_min = float(per_min)
            # One of those notes lasts 60/per_min seconds; a quarter is 1/4 of
            # a whole note, the note is a/b of one.
            sec_per_quarter = (60.0 / per_min) * (b / (4.0 * a))
        else:
            sec_per_quarter = 60.0 / float(tempo)
    except (ValueError, ZeroDivisionError):
        return None
    if not (sec_per_quarter > 0) or not (quarters_per_bar > 0):
        return None
    return quarters_per_bar * sec_per_quarter


def _count_bars(line: str) -> int:
    """Bars on one ABC music line, multi-measure rests expanded.

    `Z4|` is four bars of silence written as one token — the Ins staff is full
    of them — so counting pipes alone undercounts a section by most of it.
    """
    n = 0
    for tok in line.split("|"):
        tok = tok.strip()
        if not tok:
            continue
        m = _ABC_MULTIREST_RE.match(tok)
        n += int(m.group(1) or 1) if m else 1
    return n


def score_sections(score_abc: str) -> tuple[list[dict], float | None]:
    """The score's own sections — `([{name, bars}], seconds_per_bar)`.

    Bars are counted PER VOICE and the longest voice wins: the two staves
    carry the same bars, but either one can be written sparsely (the melody as
    rests, the accompaniment as `Z8`), and the longer count is the one that is
    complete. A score with no `%` comments comes back as one unnamed section
    holding every bar, which is still a usable total.
    """
    headers: dict[str, str] = {}
    sections: list[dict] = []
    cur_name, cur_voice = "", ""
    per_voice: dict[str, int] = {}
    in_body = False

    def close() -> None:
        if per_voice:
            sections.append({"name": cur_name, "bars": max(per_voice.values())})
        per_voice.clear()

    for raw in str(score_abc or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if not in_body:
            m = re.match(r"^([A-Za-z]):\s*(.*)$", line)
            if m and len(m.group(1)) == 1:
                headers.setdefault(m.group(1), m.group(2).strip())
                # K: is the last header line in ABC — the body starts after it.
                if m.group(1) == "K":
                    in_body = True
                continue
            continue
        sm = _ABC_SECTION_RE.match(line)
        if sm:
            close()
            cur_name, cur_voice = sm.group(1).strip().lower(), ""
            continue
        vm = _ABC_VOICE_RE.match(line)
        if vm:
            cur_voice = vm.group(1)
            continue
        if line.startswith(("%", "w:", "W:")):
            continue
        per_voice[cur_voice] = per_voice.get(cur_voice, 0) + _count_bars(line)
    close()
    return ([s for s in sections if s["bars"] > 0],
            _tempo_seconds_per_bar(headers))


def lyric_sections(lyrics: str) -> list[dict]:
    """The lyrics' section tags in order — `[{name, vocal}]`.

    YuE2's own format: a bracket tag opens a section and the lines under it are
    what is sung there. A BARE TAG — nothing but the next tag after it — is how
    the format spells "instrumental", so `vocal` is simply "did anybody write
    words here".
    """
    out: list[dict] = []
    for raw in str(lyrics or "").splitlines():
        m = _LYRIC_TAG_RE.match(raw)
        if m:
            name = m.group(1).strip().lower()
            # "[Verse 2]" and "[Verse]" are the same kind of place.
            out.append({"name": re.sub(r"\s*\d+$", "", name), "vocal": False})
        elif raw.strip() and out:
            out[-1]["vocal"] = True
    return out


def tempo_disagreement(beats, score_abc, *, meter: int = 4,
                       tolerance: float = 0.06) -> str | None:
    """A sentence when the score's written tempo and the audio's disagree.

    THE SCORE IS A PLAN AND THE AUDIO IS A PERFORMANCE, and YuE2 does not
    promise they land on the same tempo — the `Q:` header is what the composer
    wrote, the beat map is what came out of the vocoder. When the two agree,
    section boundaries derived from bars are exact to the millisecond. When
    they do not, they are still the best structure anybody has (the ORDER and
    the RELATIVE lengths of the sections are right either way) and the times
    stretch or squash. That is worth a sentence on screen, never a refusal.

    An octave apart — 64 against 128 — is a beat tracker counting half bars,
    not a disagreement about the music, so it does not earn the sentence.
    """
    rows, sec_per_bar = score_sections(score_abc or "")
    measured = float((beats or {}).get("bpm") or 0.0)
    if not rows or not sec_per_bar or measured <= 0:
        return None
    written = 60.0 * int((beats or {}).get("meter") or meter) / sec_per_bar
    for octave in (1.0, 2.0, 0.5, 4.0, 0.25):
        if abs(measured / (written * octave) - 1.0) <= tolerance:
            return None
    return (f"The score says {written:.0f} BPM and the recording measures "
            f"{measured:.0f} — the sections are in the right order and the "
            f"right proportions, but their times are stretched to fit.")


def _decode(audio_path, sr: int, span):
    import storyboard_edit as sedit                            # noqa: PLC0415
    return sedit._decode_pcm(audio_path, sr=sr, span=span)


def _vocal_ratio(x, sr: int, t0: float, start: float, end: float) -> float:
    """Energy in the voice band over total energy, for one stretch.

    One rfft per stretch, not per frame: this answers a yes/no question about a
    section that is seconds long, and a spectrogram would be a megabyte spent
    on one number. Rectangular window, because the ratio of two band sums is
    barely affected by leakage at this resolution.
    """
    import numpy as np                                         # noqa: PLC0415
    i0 = max(0, int((start - t0) * sr))
    i1 = min(x.size, int((end - t0) * sr))
    seg = x[i0:i1]
    if seg.size < sr // 8:
        return 0.0
    mag = np.abs(np.fft.rfft(seg.astype(np.float32)))
    freqs = np.fft.rfftfreq(seg.size, 1.0 / sr)
    total = float((mag ** 2).sum())
    if total <= 1e-12:
        return 0.0
    lo, hi = VOCAL_BAND_HZ
    band = float((mag[(freqs >= lo) & (freqs < hi)] ** 2).sum())
    return band / total


def song_sections(audio_path, *, lyrics=None, score_abc=None,
                  beats=None, span=None, meter: int = 4,
                  phrase_bars: int = PHRASE_BARS,
                  vocal_ratio: float = VOCAL_BAND_RATIO) -> list[dict]:
    """The song's sections — `[{start, end, kind, name, source}]`.

    `kind` is "vocal" or "instrumental"; `start`/`end` are seconds into the
    file; `source` says which evidence produced the boundary ("score" or
    "phrase") so a reader can tell a fact from a heuristic.

    TWO PATHS, and the difference is what the caller knows about the song:

      * a YuE2 song hands in its sidecar's `lyrics` and `score_abc`, and the
        boundaries are ARITHMETIC — the score's bars times the tempo in its own
        `Q:`/`M:` headers, with the lyrics' tags saying which of those sections
        anybody sings in. No analysis, no threshold, no chance of being wrong
        about where the chorus is;
      * anything else is split into `phrase_bars`-bar phrases on the beat map's
        downbeats and classified by how much of each phrase's energy sits in
        the 1–4 kHz band. Crude and honest: a wrong call costs one shot.

    The score's bar count regularly OUTRUNS THE AUDIO — YuE2 writes a full
    arrangement and then the generation is cut at the user's max length
    (`truncated.semantic`) — so every section is clamped to the real duration
    and any section that starts past the end is dropped. The first section is
    pulled to 0.0 and the last pushed to the end of the file, because a music
    video that starts 300 ms late plays the song against the wrong pictures
    from the first bar.
    """
    import storyboard_edit as sedit                            # noqa: PLC0415
    bm = beats if isinstance(beats, dict) and beats.get("downbeats") else \
        sedit.beat_map(audio_path, meter=meter, span=span)
    t0 = float((bm.get("span") or [0.0])[0])
    duration = float(bm.get("duration") or 0.0)
    if duration <= 0:
        raise MusicVideoError("that song has no length to plan against")
    end_t = t0 + duration
    downbeats = [float(t) for t in (bm.get("downbeats") or [])]
    bar = float(bm.get("period") or 0.0) * int(bm.get("meter") or meter)

    rows: list[dict] = []
    score_rows, sec_per_bar = score_sections(score_abc or "")
    tags = lyric_sections(lyrics or "")

    if score_rows and sec_per_bar:
        # The score orders the film; the lyrics say who sings in it. A score
        # section whose name no tag claims (YuE2 adds its own intro and outro)
        # is instrumental by construction — nobody wrote it any words.
        pending = list(tags)
        cursor = t0
        for row in score_rows:
            length = row["bars"] * sec_per_bar
            name = row["name"]
            kind = "instrumental"
            for i, tag in enumerate(pending):
                if tag["name"] == name:
                    kind = "vocal" if tag["vocal"] else "instrumental"
                    del pending[:i + 1]
                    break
            rows.append({"start": cursor, "end": cursor + length,
                         "kind": kind, "name": name, "source": "score"})
            cursor += length
        if not tags:
            # A score with no lyrics beside it still has no idea who sings.
            rows = _classify(rows, audio_path, t0, duration, vocal_ratio)
    elif score_rows and tags:
        # Bars we cannot convert to seconds: share the run out over the tags.
        rows = _even_sections(tags, t0, duration)
    else:
        rows = _phrase_sections(downbeats, t0, duration, phrase_bars, bar)
        rows = _classify(rows, audio_path, t0, duration, vocal_ratio)

    # ---- the boundaries land where a cut can land ----------------------
    out: list[dict] = []
    for r in rows:
        start = max(t0, float(r["start"]))
        stop = min(end_t, float(r["end"]))
        if r["source"] == "score" and downbeats and bar > 0:
            start = sedit.snap(start, downbeats, bar / 2.0)
            stop = sedit.snap(stop, downbeats, bar / 2.0)
        start, stop = max(t0, start), min(end_t, stop)
        if stop - start < 1e-3:
            continue
        if out:
            start = out[-1]["end"]          # no gaps, ever
            if stop - start < 1e-3:
                continue
        out.append({"start": round(start, 6), "end": round(stop, 6),
                    "kind": r["kind"], "name": r["name"],
                    "source": r["source"]})
    if not out:
        raise MusicVideoError("that song is too short to cut into sections")
    out[0]["start"] = round(t0, 6)
    out[-1]["end"] = round(end_t, 6)
    return out


def given_sections(rows, *, duration: float,
                   start: float = 0.0) -> tuple[list[dict], list[str]]:
    """The structure the CALLER wrote down -> `(sections, notes)`.

    THE PERSON WITH THE SONG KNOWS MORE THAN ANY ANALYSER. `song_sections`
    reads a score when there is one and guesses from band energy when there
    is not; neither can know that the first 8.5 seconds are held on an empty
    stage on purpose, or where a line really lands on a take that was sung
    rather than written. The first real film was planned by hand-writing this
    list against measured vocal onsets, so it is a parameter now.

    One row is `{start, end, kind: vocal|instrumental, label?}` in seconds.
    Raises `MusicVideoError` — message user-facing — when the list cannot be
    used at all; everything it can fix quietly it fixes and says so in `notes`.

    THE FIRST SECTION IS PULLED TO THE START OF THE SONG, always. The film
    plays from 0:00 with the song laid underneath from 0:00, so a plan whose
    cursor began at 8.5 s would render every singing shot against a second of
    the song that is 8.5 s later than the one it will be played under. A
    caller who wants to open on silence writes an instrumental section there.
    """
    if not isinstance(rows, list) or not rows:
        raise MusicVideoError(
            "the sections must be a list with at least one section in it")
    out: list[dict] = []
    for i, r in enumerate(rows, start=1):
        if not isinstance(r, dict):
            raise MusicVideoError(f"section {i} is not an object")
        try:
            s, e = float(r["start"]), float(r["end"])
        except (KeyError, TypeError, ValueError):
            raise MusicVideoError(
                f"section {i} needs a start and an end, in seconds") from None
        kind = str(r.get("kind") or "").strip().lower()
        if kind not in ("vocal", "instrumental"):
            raise MusicVideoError(
                f"section {i}: kind {r.get('kind')!r} must be vocal or "
                f"instrumental")
        if not (e > s):
            raise MusicVideoError(
                f"section {i} ends at {e:g} s, which is not after its start "
                f"at {s:g} s")
        name = str(r.get("label") or r.get("name") or kind).strip()
        out.append({"start": s, "end": e, "kind": kind, "name": name,
                    "source": "given"})
    out.sort(key=lambda r: r["start"])
    for a, b in zip(out, out[1:]):
        if b["start"] < a["end"] - 1e-6:
            raise MusicVideoError(
                f"two sections overlap: {_clock(a['start'])}–{_clock(a['end'])} "
                f"and {_clock(b['start'])}–{_clock(b['end'])}")

    notes: list[str] = []
    end_t = start + float(duration or 0.0) if duration and duration > 0 else None
    kept: list[dict] = []
    for r in out:
        s = max(start, float(r["start"]))
        e = float(r["end"]) if end_t is None else min(end_t, float(r["end"]))
        if kept:
            s = kept[-1]["end"]            # no gaps, ever — as song_sections
        if e - s < 1e-3:
            continue
        kept.append({**r, "start": round(s, 6), "end": round(e, 6)})
    if not kept:
        raise MusicVideoError("none of those sections lands inside the song")
    if kept[0]["start"] > start + 1e-3:
        notes.append(
            f"The first section began at {_clock(kept[0]['start'])} and was "
            f"pulled back to the start of the song — the film plays from 0:00, "
            f"so a section that starts later would put every mouth on the "
            f"wrong words.")
        kept[0]["start"] = round(start, 6)
    if end_t is not None and kept[-1]["end"] < end_t - 0.5:
        notes.append(
            f"Your sections stop at {_clock(kept[-1]['end'])} and the song "
            f"runs to {_clock(end_t)}, so the last "
            f"{end_t - kept[-1]['end']:.0f} s of it has no shots.")
    return kept, notes


def _even_sections(tags: list[dict], t0: float, duration: float) -> list[dict]:
    """One section per lyrics tag, the run time shared out evenly."""
    n = max(1, len(tags))
    step = duration / n
    return [{"start": t0 + i * step, "end": t0 + (i + 1) * step,
             "kind": "vocal" if t["vocal"] else "instrumental",
             "name": t["name"], "source": "phrase"}
            for i, t in enumerate(tags)]


def _phrase_sections(downbeats: list[float], t0: float, duration: float,
                     phrase_bars: int, bar: float) -> list[dict]:
    """`phrase_bars`-bar phrases on the downbeat grid, covering the whole file.

    Falls back to a clock-based split when there are too few downbeats to make
    a phrase — a song the beat tracker could not read is still a song, and a
    plan on an even grid beats a refusal.
    """
    edges = [t for t in downbeats if t0 - 1e-6 <= t <= t0 + duration + 1e-6]
    if len(edges) <= phrase_bars:
        step = bar * phrase_bars if bar > 0 else 15.0
        n = max(1, int(round(duration / step)))
        step = duration / n
        return [{"start": t0 + i * step, "end": t0 + (i + 1) * step,
                 "kind": "vocal", "name": f"phrase {i + 1}", "source": "phrase"}
                for i in range(n)]
    rows = []
    for i in range(0, len(edges) - 1, phrase_bars):
        start = edges[i]
        j = min(i + phrase_bars, len(edges) - 1)
        end = edges[j] if j > i else t0 + duration
        rows.append({"start": start, "end": end, "kind": "vocal",
                     "name": f"phrase {len(rows) + 1}", "source": "phrase"})
    if rows:
        rows[0]["start"] = t0
        rows[-1]["end"] = t0 + duration
    return rows


def _classify(rows: list[dict], audio_path, t0: float, duration: float,
              threshold: float) -> list[dict]:
    """Stamp `kind` on every row from the voice-band energy ratio.

    One decode for the whole file, one rfft per row. A stretch whose ratio sits
    below the track's own median as well as below the absolute threshold is
    instrumental; the median makes the call relative to THIS mix, so a bright
    production does not come out all-vocal and a muddy one all-instrumental.
    """
    if not rows:
        return rows
    try:
        x = _decode(audio_path, 16000, (t0, t0 + duration))
    except Exception:                                          # noqa: BLE001
        # No decoder, no opinion — everything sung is the generous guess, and
        # the planner's own "no singer picture" note covers the other side.
        for r in rows:
            r["kind"] = "vocal"
        return rows
    ratios = [_vocal_ratio(x, 16000, t0, r["start"], r["end"]) for r in rows]
    live = sorted(v for v in ratios if v > 0)
    median = live[len(live) // 2] if live else 0.0
    for r, v in zip(rows, ratios):
        r["kind"] = "vocal" if (v >= threshold and v >= median * 0.85) \
            else "instrumental"
        r["vocal_ratio"] = round(v, 4)
    return rows


# ---------------------------------------------------------------------------
# PART B — the shot list
# ---------------------------------------------------------------------------

def cells_from_lengths(lengths, quality=None) -> tuple:
    """The panel's `LTX_LENGTHS` table as (seconds, frames), longest first.

    The routes call this so the planner's duration axis IS the panel's, with
    the table stated once. A dict, not the module, because this file does not
    import the panel.

    `quality` is the pass the film will really render at. A row that names
    `qualities` is offered on THOSE qualities only — see `LTX_CELL_QUALITIES`
    — and is dropped here for any other. `None` means "no quality stated" and
    hands the table back whole; the safety net in that case is
    `plan_music_video`, which applies the same restriction from its own
    default rather than trusting whatever cells it was given.
    """
    q = str(quality).strip().lower() if quality is not None else None
    rows = []
    for l in (lengths or {}).values():
        if not (l.get("seconds") and l.get("frames")):
            continue
        allowed = tuple(str(a).strip().lower()
                        for a in (l.get("qualities") or ()))
        if q is not None and allowed and q not in allowed:
            continue
        rows.append((int(l["seconds"]), int(l["frames"])))
    return tuple(sorted(set(rows), key=lambda r: -r[0])) or LTX_CELLS


def _cells_for_quality(cells, quality) -> tuple[tuple, tuple]:
    """`(kept, dropped)` — the cells this quality's canvas can really render.

    Applied to WHATEVER cells the caller handed in, not only to the ones this
    module derived: the caller that passes its own axis is exactly the caller
    that can pass a cell the canvas cannot hold (a hand-written tuple, a
    table from an older panel), and dropping it here is the difference
    between a cut and a dead render.
    """
    q = str(quality or "").strip().lower()
    if not q:
        return tuple(cells), ()
    kept, dropped = [], []
    for cell in cells:
        allowed = LTX_CELL_QUALITIES.get(int(cell[0]))
        (kept if (not allowed or q in allowed) else dropped).append(tuple(cell))
    # Every cell restricted is a table nobody can plan against; keep the axis
    # rather than refuse, and let the note below say what happened.
    return (tuple(kept) or tuple(cells)), tuple(dropped)


def _cell_for(seconds: float, cells) -> tuple | None:
    """The largest cell that fits inside `seconds`, or None."""
    for s, f in cells:
        if s <= seconds + 1e-6:
            return (s, f)
    return None


def _frames_for(seconds: float) -> int:
    return storyboard.ltx_frames_for(seconds)


def _clock(seconds: float) -> str:
    total = int(round(max(0.0, seconds)))
    return f"{total // 60}:{total % 60:02d}"


def _with_style(line: str, style: str) -> str:
    """One direction plus the film's look, the one way it is ever joined."""
    line = str(line or "").strip()
    style = str(style or "").strip().rstrip(".")
    return f"{line}, {style}" if style else line


def _prompt_for(image: dict, kind: str, style: str, *,
                silent: bool = False) -> str:
    """The shot's direction: the picture's own line, then the film's look.

    An image carries `prompt` when the user typed one for it, and that REPLACES
    the role line rather than joining it — somebody who wrote "he leans back
    and laughs" meant that instead of "close-up, singing to camera", not as
    well as.

    A SINGING SHOT GOES THROUGH THE A2V LAW on the way out (`storyboard`
    owns it, so the validator and both planners cannot disagree): stillness
    words are scrubbed, the direction is capped, and the literal sync
    contract is appended LAST so a user's own line cannot paraphrase it away.
    `silent=True` swaps that contract for the relaxed-closed-lips one.
    """
    line = (str(image.get("prompt") or "").strip()
            or ROLE_LINES.get((image.get("role"), kind))
            or ROLE_LINES[("room", "broll")])
    return _sing_prompt(_with_style(line, style), kind, silent=silent)


def _sing_prompt(text: str, kind: str, *, silent: bool = False) -> str:
    """One direction, put through the a2v law when the shot is audio-driven.

    B-roll is untouched: an i2v shot has no waveform, so a contract about
    syllables would be a sentence about a sound that is not there.
    """
    if kind != "singing":
        return text
    return storyboard.a2v_prompt(text, silent=silent)


def slice_is_silent(sections, t0: float, t1: float) -> bool:
    """True when nothing in `t0..t1` is a vocal stretch, as far as the
    sections know.

    A window with no singing in it is NOT the same as saying nothing: left
    alone the model keeps the mouth working through an instrumental bar. The
    threshold is deliberately low — a fifth of the shot overlapping a vocal
    section is enough to call it a singing window, because a mouth that sings
    slightly too long reads far better than one that stops mid-line.
    """
    span = max(1e-6, float(t1) - float(t0))
    vocal = 0.0
    for row in (sections or []):
        if str(row.get("kind") or "").strip().lower() != "vocal":
            continue
        lo = max(float(t0), float(row.get("start") or 0.0))
        hi = min(float(t1), float(row.get("end") or 0.0))
        if hi > lo:
            vocal += hi - lo
    return (vocal / span) < 0.2


def _shot_prompts(raw) -> dict[int, str]:
    """`{shot number: direction}` from whatever the caller handed in.

    The per-IMAGE `prompt` is a default that follows the picture into every
    shot it is cast in; this is a line for ONE shot. The last shot of the
    first real film had to say "the stage lights slowly fading down to black"
    and the same picture is used three times earlier, where a fade would be
    wrong — so the override has to be per shot or it is not an override.
    """
    out: dict[int, str] = {}
    for key, value in dict(raw or {}).items():
        try:
            n = int(key)
        except (TypeError, ValueError):
            continue
        text = str(value or "").strip()
        if n >= 1 and text:
            out[n] = text
    return out


def plan_music_video(sections, images, *, style: str, bpm_grid,
                     singing_window=SINGING_WINDOW,
                     broll_window=BROLL_WINDOW,
                     cells=None, quality: str = DEFAULT_QUALITY,
                     shot_prompts=None, shot_images=None,
                     title: str = "Music video",
                     song: str = "", board_id: str = "",
                     vocal_stem: str = "") -> dict:
    """Sections plus pictures -> a storyboard board that shoots a music video.

    Returns::

        {"board":   {...},            # the storyboard schema, shots and all
         "shots":   [...],            # board["shots"], for a caller's reply
         "summary": "14 shots · 6 singing · 8 B-roll · 3:20",
         "notes":   ["..."],          # what the plan could not do, in words
         "film_seconds": 200.04}

    HOW IT DECIDES. A vocal section is filled with SINGING SHOTS — mode `a2v`,
    one Singer picture each, alternating so the angle changes, `audio_start_time`
    set to that shot's own place in the song, and `frames` the largest LTX cell
    that fits the singing window. Whatever is left of the section, and every
    instrumental section, becomes B-ROLL — a still with a slow move on it,
    3–7 seconds, cut on the downbeats.

    THE CURSOR IS THE FILM, not the section list. Every shot's length is the
    one it will really render (a frame count on the 8k+1 grid), and the next
    shot starts where the last one ended, so `audio_start_time` is always the
    second of the song that will be playing under that clip once the shots are
    concatenated and the song is laid underneath. Section boundaries are the
    TARGET the cursor steers towards; when the frame grid overshoots one by
    20 ms the next section absorbs it, and nothing downstream ever has to know.

    `cells` is the panel's duration axis (`cells_from_lengths`); absent, the
    module's own `LTX_CELLS` — same numbers, derived the same way. `quality`
    is the pass the film will be delivered at, and the axis is cut down to
    what that canvas can really render before a single shot is emitted.

    CASTING B-ROLL. The rotation is weighted (`weight` on a picture, default
    1) and two slots can be pinned by hand: a picture whose `use` is "open"
    takes the first B-roll shot and one whose `use` is "close" takes the last,
    in the order they were given. `shot_prompts` is `{shot number: line}` and
    replaces that ONE shot's direction, the picture's own `prompt` staying the
    default everywhere else it is cast.

    CASTING THE SINGING. The rotation alternates so the angle changes. Which
    picture LEADS it is decided by face size when the caller has not said
    otherwise: a picture may carry `face_frac` (how much of the frame the face
    fills, 0..1) and the biggest measured face goes first, because a singing
    shot is a close-up and a mouth at a quarter of the frame is mush. A `use`
    pin or a `shot_images` pin is the caller saying which picture goes where,
    and it wins outright.

    `shot_images` is `{shot number: path}` and replaces who is in THAT ONE
    shot, leaving its frames, its `film_start` and its `audio_start_time`
    exactly where the tiling put them. It BEATS the `use` pins: naming a shot
    is the exact decision and "opens the film" is the coarse one, so an
    explicitly pinned shot is reserved and the open/close pictures take the
    B-roll shots around it.

    `vocal_stem` is a pre-separated vocal of the SAME song, and it rides on
    every singing shot as `audio_stem`: the model conditions on it, the panel
    muxes the original back, and the timing is untouched (the stem is the
    whole song, so a shot's `audio_start_time` means the same second in both).
    """
    cells = tuple(cells or LTX_CELLS)
    cells, cells_dropped = _cells_for_quality(cells, quality)
    overrides = _shot_prompts(shot_prompts)
    sing_min, sing_max = (float(x) for x in singing_window)
    broll_min, broll_max = (float(x) for x in broll_window)
    grid = [float(t) for t in ((bpm_grid or {}).get("downbeats") or [])]
    bar = float((bpm_grid or {}).get("period") or 0.0) \
        * int((bpm_grid or {}).get("meter") or 4)

    cast = _cast(images)
    notes: list[str] = []
    if cells_dropped:
        longest = max(int(s) for s, _ in cells_dropped)
        allowed = ", ".join(LTX_CELL_QUALITIES.get(longest, ()))
        notes.append(
            f"The {longest} s shot length is only offered at "
            f"{allowed or 'a smaller canvas'} and this film is planned for "
            f"{quality}, so the longest shot here is "
            f"{max(int(s) for s, _ in cells)} s.")
    image_pins, pin_notes = _shot_images(shot_images, cast)
    notes.extend(pin_notes)
    singers = [i for i in cast if i["role"] == "singer"]
    # WHO LEADS THE SINGING. Only when the caller has not said: a `use` pin or
    # a per-shot image pin is a decision, and this rule does not overrule one.
    singers, order_notes = _singer_order(
        singers,
        pinned=bool(image_pins) or any(i.get("use") in ("open", "close")
                                       for i in singers))
    notes.extend(order_notes)
    broll_pool = [i for i in cast if i["role"] in ("instrument", "room")]
    if not cast:
        raise MusicVideoError("a music video needs at least one picture")
    if not singers:
        notes.append("No picture is tagged Singer, so nobody sings on camera — "
                     "every section became B-roll.")
    if not broll_pool:
        # Only faces. They can still carry the instrumental; they just must not
        # be asked to sing there (see ROLE_LINES).
        broll_pool = list(cast)
        if singers:
            notes.append("No Instrument or Room picture, so the B-roll is cut "
                         "from the Singer pictures held still.")

    rows = [r for r in (sections or []) if float(r["end"]) > float(r["start"])]
    if not rows:
        raise MusicVideoError("that song has no sections to shoot")

    ring = _broll_ring(broll_pool)
    shots: list[dict] = []
    cursor = float(rows[0]["start"])
    singer_turn = 0
    broll_turn = 0

    def emit(image: dict, kind: str, frames: int, section: dict) -> None:
        nonlocal cursor
        dur = frames / float(FPS)
        n = len(shots) + 1
        # A picture pinned to THIS shot replaces the rotation's pick, and
        # nothing about the tiling moves: a pin is a casting decision.
        image = image_pins.get(n) or image
        line = overrides.get(n)
        # WHETHER THERE IS A VOICE IN THIS WINDOW, as far as the sections
        # know. A singing shot is normally cut inside a vocal stretch, but a
        # caller who hands in their own `sections` can place one anywhere, and
        # the frame grid can carry the tail of one past a boundary.
        silent = kind == "singing" and slice_is_silent(rows, cursor, cursor + dur)
        shot = {
            "n": n,
            "title": f"S{n:02d} {kind}",
            # The board's vocabulary is `storyboard.VALID_MODES`. A singing
            # shot is `a2v` there and in the panel; B-roll is `text` WITH A
            # STILL, which `shot_to_job` already turns into an anchored i2v —
            # the existing path, not a second one. `panel_mode` below is what
            # will really run, written down so a reader of the plan does not
            # have to know that rule.
            "mode": "a2v" if kind == "singing" else "text",
            "engine": "ltx",
            "prompt": _sing_prompt(_with_style(line, style), kind,
                                   silent=silent) if line
                      else _prompt_for(image, kind, style, silent=silent),
            "duration_s": round(dur, 4),
            "seed": -1,
            "refs": [],
            "still": image["path"],
            "status": "pending",
            "music_video": {
                "kind": kind,
                "role": image["role"],
                "image": image["path"],
                "frames": frames,
                "film_start": round(cursor, 4),
                "film_end": round(cursor + dur, 4),
                "section": section.get("name") or "",
                "section_kind": section.get("kind") or "",
                "panel_mode": "a2v" if kind == "singing" else "i2v",
            },
        }
        if line:
            shot["music_video"]["prompt_override"] = True
        if kind == "singing":
            shot["audio"] = str(song)
            # WHAT THE MOUTH LISTENS TO. The band is noise to the audio
            # encoder - it reads drums and guitar as syllables and the model
            # hedges, opening the mouth a little on everything. A separated
            # vocal conditions the shot and the panel muxes the original song
            # back over the render, so the film still plays the record.
            if vocal_stem:
                shot["audio_stem"] = str(vocal_stem)
            # THE WHOLE POINT. The clip is rendered against the song from
            # exactly the second it will be playing at in the finished film.
            shot["audio_start_time"] = round(cursor, 4)
        shots.append(shot)
        cursor += dur

    for sec in rows:
        target = float(sec["end"])
        if sec.get("kind") == "vocal" and singers:
            while target - cursor >= sing_min - 1e-6:
                cell = _cell_for(min(sing_max, target - cursor), cells)
                if cell is None:
                    break
                emit(singers[singer_turn % len(singers)], "singing",
                     cell[1], sec)
                singer_turn += 1
        for span in _broll_spans(cursor, target, window=(broll_min, broll_max),
                                 grid=grid, bar=bar):
            # NO SHOT SHORTER THAN A SECOND. The board writer normalises every
            # shot to at least 1 s, so a 0.375 s scrap here became a 1.04 s
            # shot there — and every singing shot after it slid off its own
            # words by the difference (Codex review, 2026-09-20). A scrap is
            # folded into the B-roll shot before it; only when there is none
            # does it become a one-second shot of its own.
            if span < MIN_SHOT_S - 1e-6 and shots and shots[-1]["music_video"]["kind"] == "broll":
                last = shots[-1]
                old_dur = last["music_video"]["frames"] / float(FPS)
                frames = _frames_for(old_dur + span)
                new_dur = frames / float(FPS)
                last["music_video"]["frames"] = frames
                last["duration_s"] = round(new_dur, 4)
                last["music_video"]["film_end"] = round(last["music_video"]["film_start"] + new_dur, 4)
                cursor += new_dur - old_dur
                continue
            frames = max(_frames_for(max(span, MIN_SHOT_S)), _frames_for(MIN_SHOT_S))
            emit(ring[broll_turn % len(ring)], "broll", frames, sec)
            broll_turn += 1

    if not shots:
        raise MusicVideoError("that song is too short to make a shot out of")

    # The pins are a POST-PASS on purpose: "close" means the last B-roll shot
    # of the film, and which shot that is is not known until the last section
    # has been tiled. Nothing about the tiling moves — only who is in frame.
    notes.extend(_pin_broll(shots, broll_pool, style, overrides,
                            reserved=set(image_pins)))
    orphans = [i["path"] for i in cast
               if i.get("use") in ("open", "close")
               and not any(i is b for b in broll_pool)]
    if orphans:
        notes.append(
            f"{len(orphans)} picture(s) asked to open or close the film but "
            f"are cast as Singer, and the pins only place B-roll: "
            f"{', '.join(orphans)}.")
    unused_pins = sorted(set(image_pins) - {int(s["n"]) for s in shots})
    if unused_pins:
        notes.append(
            f"This plan has {len(shots)} shots, so the picture(s) pinned to "
            f"shot {', '.join(str(n) for n in unused_pins)} were not used.")
    unused = sorted(set(overrides) - {int(s["n"]) for s in shots})
    if unused:
        notes.append(
            f"This plan has {len(shots)} shots, so the line(s) written for "
            f"shot {', '.join(str(n) for n in unused)} were not used.")

    n_sing = sum(1 for s in shots if s["music_video"]["kind"] == "singing")
    n_broll = len(shots) - n_sing
    film = sum(float(s["duration_s"]) for s in shots)
    summary = (f"{len(shots)} shots · {n_sing} singing · {n_broll} B-roll · "
               f"{_clock(film)}")

    board = storyboard.new_storyboard(
        board_id or "", title or "Music video", shots=shots)
    # Every shot is LTX: a2v only exists there, and a board that let H3 take a
    # B-roll shot would put a soundtrack-less 5 s cell where a 4.3 s one was
    # planned and slide every singing shot after it off its own words.
    board["engine_mode"] = "ltx"
    board["style"] = str(style or "")
    board["music_video"] = {
        "song": str(song),
        "vocal_stem": str(vocal_stem or ""),
        "images": cast,
        "style": str(style or ""),
        "summary": summary,
        "film_seconds": round(film, 4),
        "sections": [dict(r) for r in rows],
        "notes": notes,
    }
    return {"board": board, "shots": shots, "summary": summary,
            "notes": notes, "film_seconds": round(film, 4)}


def _cast(images) -> list[dict]:
    """The pictures, cleaned: `[{path, role, prompt, use, weight}]`, in order.

    An unknown role is not an error and not silently a Singer — a picture whose
    tag got lost is B-roll, which is the choice that cannot put a still mouth
    in front of a vocal line. `use` and `weight` are the same shape of
    forgiveness: an unreadable pin is "any" and an unreadable weight is 1,
    because a mistyped hint is a picture in the wrong slot, never a refused film.
    """
    out = []
    for im in (images or []):
        if not isinstance(im, dict):
            continue
        path = str(im.get("path") or "").strip()
        if not path:
            continue
        role = str(im.get("role") or "").strip().lower()
        if role not in ROLES:
            role = "room"
        use = str(im.get("use") or "any").strip().lower()
        if use not in USES:
            use = "any"
        try:
            weight = int(float(im.get("weight") or 1))
        except (TypeError, ValueError):
            weight = 1
        out.append({"path": path, "role": role,
                    "prompt": str(im.get("prompt") or "").strip(),
                    "use": use, "weight": max(1, min(weight, MAX_WEIGHT)),
                    "face_frac": _face_frac(im.get("face_frac"))})
    return out


def _face_frac(raw) -> float | None:
    """How much of the picture the face fills, 0..1, or None when nobody
    measured it.

    NOT GUESSED FROM THE PICTURE. The obvious cheap proxy - aspect ratio, or
    the short side - is worthless here: a 1:1 crop of a wide room and a 1:1
    crop of a face are the same number, and a portrait-shaped photograph of a
    band is portrait-shaped. So the measurement is a value the CALLER passes,
    and a picture nobody measured keeps its place in the order rather than
    being sorted by a number that was invented for it. The panel's captioner
    already looks at every uploaded picture and is where this can be filled in
    later; until then it is an optional field on the cast.
    """
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value or value <= 0:            # NaN, 0, negative
        return None
    return min(1.0, value)


def _singer_order(singers: list[dict], pinned: bool) -> tuple[list[dict], list[str]]:
    """The singing rotation, biggest face first. Returns (order, notes).

    A singing shot is a CLOSE-UP by default (`ROLE_LINES`), and at 23% of the
    frame a mouth is mush while at 46% it is legible - the difference between
    a lip-sync you believe and one you squint at. So when the caller has said
    nothing about which picture goes where, the measured faces lead.

    THE CALLER'S ORDER WINS whenever there is one. A `use` pin or a per-shot
    image pin is somebody saying which picture they want where, and a rule
    about face size is not entitled to overrule it.
    """
    if pinned or len(singers) < 2:
        return list(singers), []
    rated = [i for i in singers if i.get("face_frac") is not None]
    if not rated:
        return list(singers), []
    order = sorted(
        enumerate(singers),
        key=lambda pair: (0 if pair[1].get("face_frac") is not None else 1,
                          -(pair[1].get("face_frac") or 0.0), pair[0]))
    order = [im for _, im in order]
    if order == list(singers):
        return order, []
    lead = order[0]
    note = (f"The singing shots lead on {lead['path']}, the biggest face of "
            f"the {len(rated)} measured ({(lead['face_frac'] or 0) * 100:.0f}% "
            f"of the frame) - a close-up reads at that size and does not at a "
            f"quarter of it.")
    unrated = len(singers) - len(rated)
    if unrated:
        note += (f" {unrated} Singer picture(s) carry no face measurement and "
                 f"follow in the order they were given.")
    return order, [note]


def _shot_images(raw, cast: list[dict]) -> tuple[dict[int, dict], list[str]]:
    """`{shot number: the cast picture pinned to it}` plus notes.

    The per-shot twin of `_shot_prompts`: a line is one kind of decision about
    one shot and WHO IS IN IT is the other. A path that is not in the cast is
    a note rather than a refusal, for the same reason an unreadable weight is
    not - a mistyped hint is a picture in the wrong slot, never a refused film.
    """
    by_path = {im["path"]: im for im in cast}
    out: dict[int, dict] = {}
    notes: list[str] = []
    missing: list[str] = []
    for key, value in dict(raw or {}).items():
        try:
            n = int(key)
        except (TypeError, ValueError):
            continue
        path = str(value or "").strip()
        if n < 1 or not path:
            continue
        if path in by_path:
            out[n] = by_path[path]
        else:
            missing.append(path)
    if missing:
        notes.append(
            f"{len(missing)} picture(s) were pinned to a shot but are not in "
            f"this film's cast, so the rotation kept those shots: "
            f"{', '.join(sorted(set(missing)))}.")
    return out, notes


def _broll_ring(pool: list[dict]) -> list[dict]:
    """The B-roll rotation, `weight` copies of each picture, SPREAD not clumped.

    A blind round-robin gives every picture the same share, which is wrong
    whenever one of them is the film's own image and the rest are cutaways.
    The ring is built in passes — everybody once, then everybody with weight
    ≥ 2 again, and so on — so a weight-3 picture comes round three times as
    often without ever appearing three times in a row.
    """
    if not pool:
        return []
    top = max(int(i.get("weight") or 1) for i in pool)
    ring = [im for k in range(max(1, top)) for im in pool
            if int(im.get("weight") or 1) > k]
    return ring or list(pool)


def _recast(shot: dict, image: dict, style: str, overrides: dict) -> None:
    """Put a different picture in an already-planned B-roll shot.

    Everything about the TILING is left alone — frames, film_start, film_end,
    the section it belongs to — because the song is the clock and a pin is a
    casting decision, not a timing one.
    """
    block = shot["music_video"]
    shot["still"] = image["path"]
    block["image"] = image["path"]
    block["role"] = image["role"]
    line = overrides.get(int(shot["n"]))
    shot["prompt"] = (_with_style(line, style) if line
                      else _prompt_for(image, "broll", style))


def _pin_broll(shots: list[dict], pool: list[dict], style: str,
               overrides: dict, reserved: set[int] | None = None) -> list[str]:
    """Place the `use: open` / `use: close` pictures. Returns notes.

    `reserved` is the shot NUMBERS a caller pinned a picture to by hand. They
    are not slots this pass may fill: `emit()` already cast them, and recasting
    them here meant the later, coarser decision ("something opens the film")
    quietly overruled the earlier, exact one ("B is in shot 1") — the pin the
    caller wrote was worth nothing (Codex review, 2026-09-22).
    """
    reserved = reserved or set()
    slots = [i for i, s in enumerate(shots)
             if s["music_video"]["kind"] == "broll"
             and int(s["n"]) not in reserved]
    opens = [im for im in pool if im.get("use") == "open"]
    closes = [im for im in pool if im.get("use") == "close"]
    if not (opens or closes):
        return []
    held = sorted(int(s["n"]) for s in shots
                  if s["music_video"]["kind"] == "broll"
                  and int(s["n"]) in reserved)
    if not slots:
        return [f"Every B-roll shot in this film was pinned to a picture by "
                f"hand (shot {', '.join(str(n) for n in held)}), so there was "
                f"no slot left to open or close on."] if held else [
            "Every shot in this film is a singing shot, so there was no "
            "B-roll slot to open or close on."]
    notes = []
    if held:
        notes.append(
            f"Shot {', '.join(str(n) for n in held)} was pinned to a picture "
            f"by hand, so the open/close pins went to the B-roll shots around "
            f"it.")
    if len(opens) + len(closes) > len(slots):
        notes.append(
            f"{len(opens) + len(closes)} pictures are pinned to open or close "
            f"but this film has only {len(slots)} B-roll shots, so the ones "
            f"that did not fit were left in the rotation.")
    taken: set[int] = set()
    for k, im in enumerate(opens):
        if k >= len(slots):
            break
        _recast(shots[slots[k]], im, style, overrides)
        taken.add(slots[k])
    # Reversed, so the LAST picture pinned "close" is the last shot of the film.
    for k, im in enumerate(reversed(closes)):
        j = slots[len(slots) - 1 - k]
        if k >= len(slots) or j in taken:
            break
        _recast(shots[j], im, style, overrides)
        taken.add(j)
    return notes


def _broll_spans(t0: float, t1: float, *, window, grid, bar) -> list[float]:
    """Split `t0..t1` into B-roll shot lengths, cut on the downbeats.

    Returns LENGTHS, not boundaries, because the caller's cursor is the truth
    and a boundary computed here would be stale the moment a frame count
    rounded. The count is whatever gets closest to the middle of the window
    while keeping every shot inside it; the last shot takes the remainder so
    the section closes exactly where it was asked to.
    """
    lo, hi = window
    length = t1 - t0
    if length <= 1e-6:
        return []
    if length < lo - 1e-6:
        # A scrap at the end of a section. One short shot beats a hole: a gap
        # in the tiling is a gap between the song and the picture.
        return [length]
    n = max(1, int(round(length / ((lo + hi) / 2.0))))
    n = max(n, int(math.ceil(length / hi - 1e-9)))
    n = min(n, max(1, int(length // lo)))
    spans: list[float] = []
    cursor = t0
    for k in range(1, n + 1):
        if k == n:
            spans.append(t1 - cursor)
            break
        want = t0 + length * k / n
        end = _snap_between(want, grid, bar / 2.0 if bar > 0 else 0.0,
                            cursor + lo, min(cursor + hi, t1 - lo * (n - k)))
        spans.append(end - cursor)
        cursor = end
    return [s for s in spans if s > 1e-6]


def _snap_between(t: float, grid, tol: float, lo: float, hi: float) -> float:
    """Nearest grid point to `t` inside `[lo, hi]` and within `tol`, else `t`.

    A local copy of the planner's own snapping rather than a call into
    `storyboard_edit`: this module must plan without the audio machinery, and
    the rule is four lines. `storyboard_edit._snap_in_range` is the one used on
    the finished clips, where the constraints are different (a cut may not
    outrun its source) and the answer may legitimately be "no beat here".
    """
    if hi < lo:
        # Infeasible only if the caller mis-counted its own shots; `_broll_spans`
        # proves it cannot, so this is a guard, not a policy.
        return t
    t = max(lo, min(t, hi))
    if not grid or tol <= 0:
        return t
    best, dist = None, None
    for g in grid:
        if g < lo - 1e-9 or g > hi + 1e-9 or abs(g - t) > tol:
            continue
        d = abs(g - t)
        if dist is None or d < dist:
            best, dist = float(g), d
    return best if best is not None else t
