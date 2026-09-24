#!/usr/bin/env python3
"""Train a voice from recordings, so YuE2 can sing new songs with it.

WHAT THIS IS
------------
An AR-branch LoRA trainer for the YuE2 MLX engine. It takes somebody's
recordings, turns them into YuE2's own semantic codes with our real-audio
tokenizer head (`yue2_tokenizer.py`), and teaches the AR model to WRITE that
code stream from style + lyrics alone. Generation then runs with
`--lora <file> --lora-trigger <word>` and no conversion step anywhere: the
singer is never resynthesised from a stranger's performance, which is what
makes a converted "clone" sound robotic (`notes/yue2/MAESTRO_VOICE_STUDY.md` §4).

The objective is Mothersuperior's `ar_lora.py` and Maestro's
`artist_training.py`, which agree to the character:

    sequence = token_prefixes(SongRequest(style, lyrics, ...)) + [code + CODEC_OFFSET ...]
    loss     = cross-entropy on the CODE TAIL ONLY

The prefix is built by the engine's own `token_prefixes`, so what the model
sees while training is the byte-identical thing it will see at inference.
Nothing else in this file is allowed to invent a prompt layout.

TWO DIALECTS
------------
`--dialect direct`  `cot="off"` — the prefix ends `[ABC_START, ABC_END,
                    MUSIC_START]` and the model writes codes with no score.
                    Generate with `--mode off`.
`--dialect score`   `cot="melody"` (or `full`) with a real ABC score of the
                    SAME window inside the prefix. Generate with
                    `--mode melody --abc-file <score>`. This exists because a
                    direct-dialect voice cannot follow a supplied score: the
                    engine refuses `abc` with `cot="off"`
                    (`protocol.SongRequest.__post_init__`) and a voice that has
                    never seen a score in its prefix has no reason to obey one.

WINDOWS, AND WHY THE LYRICS ARE SLICED
--------------------------------------
Training pairs ONE prefix with ONE stretch of codes. If the lyrics in the
prefix are the whole song while the codes are a random slice of it, the pair
is a lie: the model learns "this style → this singer's codes" and never learns
"these words → these sounds". It then reproduces trained fragments instead of
pronouncing new lyrics. So a recording is cut into `--window-seconds` windows
(Maestro's 512 frames = 20.48 s) at `--hop-seconds`, and each window gets the
lyric lines that were actually sung inside it — from a `<stem>.lines.json`
timing sidecar when there is one, otherwise sliced by time in proportion.

The trainer stays pure: it does not run demucs, whisper or SheetSage2. Line
timings and window scores come in as sidecars; `--emit-windows` writes the
window audio so those tools can be pointed at it outside this process.

OUTPUT
------
`<out>/<name>.safetensors` in the Mothersuperior key layout that
`scripts/music/yue2_lora.py` already loads, plus `<name>.json` recording the
trigger, the dialect, the base model, the steps, a hash of the training data
and the loss curve. Drop both in `mlx_models/yue2-loras/user/` (or pass
`--install`) and the studio picker offers it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "yue2-mlx" / "vendor" / "yue" / "src"))

from yue2.protocol import CODEC_OFFSET, CODEC_SIZE, MUSIC_END, SongRequest, token_prefixes  # noqa: E402

__all__ = [
    "AUDIO_SUFFIXES", "ATTN_PROJECTIONS", "MLP_PROJECTIONS", "FRAME_RATE",
    "Window", "adapter_key", "build_dataset", "data_fingerprint", "emit_windows",
    "codes_cache_key", "file_digest", "lyrics_for_window", "match_sidecar",
    "plan_windows", "read_regularizer_cache", "read_timed_lines", "resolve_head",
    "resolve_recordings", "sidecar_metadata", "supervised_span", "training_sequence",
    "window_request", "write_regularizer_cache",
]

#: 25 semantic codes a second, like everything else in this pipeline.
FRAME_RATE = 25
#: Maestro's excerpt length: 512 frames. Kept as the default window.
DEFAULT_WINDOW_SECONDS = 512 / FRAME_RATE
DEFAULT_HOP_SECONDS = DEFAULT_WINDOW_SECONDS / 2
#: Every projection an AR LoRA targets, 28 layers × 7 = 196.
ATTN_PROJECTIONS = ("q_proj", "k_proj", "v_proj", "o_proj")
MLP_PROJECTIONS = ("gate_proj", "up_proj", "down_proj")
AUDIO_SUFFIXES = (".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus")
DIALECTS = ("direct", "score")
#: A window with fewer codes than this is a scrap, not an excerpt.
MIN_WINDOW_FRAMES = 64


# --------------------------------------------------------------- the inputs --

def resolve_recordings(paths) -> list[Path]:
    """`--recordings` is a directory or a list of files; either way, sorted files."""
    found: list[Path] = []
    for item in paths:
        path = Path(item).expanduser()
        if path.is_dir():
            found += sorted(p for p in path.iterdir()
                            if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES)
        elif path.is_file():
            found.append(path)
        else:
            raise FileNotFoundError(f"No recording at {path}")
    if not found:
        raise ValueError("No recordings found — give a folder of audio or the files")
    return found


def match_sidecar(audio: Path, sources, suffixes, *, allow_single=False) -> Path | None:
    """The file that belongs to this recording: same stem, one of `suffixes`.

    `sources` is whatever the caller passed (folders and/or files). With
    `allow_single`, one file given for one recording wins outright, so the
    one-song case does not have to rename anything to match.
    """
    candidates: list[Path] = []
    for item in sources or []:
        path = Path(item).expanduser()
        if path.is_dir():
            candidates += [p for p in path.iterdir() if p.is_file()]
        elif path.is_file():
            candidates.append(path)
    for path in candidates:
        if path.stem == audio.stem and path.suffix.lower() in suffixes:
            return path
    for path in candidates:
        if path.stem == audio.stem:
            return path
    if allow_single and len(candidates) == 1:
        return candidates[0]
    return None


_SECTION = re.compile(r"^\s*\[[^\]]*\]\s*$")


def read_timed_lines(path: Path | None) -> list[dict] | None:
    """A `<stem>.lines.json` timing sidecar → [{text, onset, end, section}].

    Shape accepted: a bare list, or `{"lines": [...]}`. Each entry carries
    `text` and `onset` (seconds); `end` and `section` are optional. Lines
    without an onset are dropped — an unlocated line cannot be attributed to a
    window, and guessing would put the wrong words in the prefix.
    """
    if path is None or not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["lines"] if isinstance(data, dict) else data
    lines: list[dict] = []
    for row in rows:
        text = str(row.get("text", "")).strip()
        onset = row.get("onset", row.get("s"))
        if not text or onset is None:
            continue
        end = row.get("end", row.get("e"))
        lines.append({"text": text, "onset": float(onset),
                      "end": None if end is None else float(end),
                      "section": (str(row.get("section")).strip()
                                  if row.get("section") else None)})
    lines.sort(key=lambda row: row["onset"])
    return lines or None


def lyrics_for_window(lyrics_text: str, lines: list[dict] | None,
                      start: float, end: float, duration: float) -> str:
    """The words sung inside [start, end) — timed when we know, proportional when not.

    Timed: every line whose onset falls in the window (plus a line still
    running into it). Untimed: the song's sung lines cut by the window's share
    of the recording, which is crude but keeps each prefix roughly honest about
    its own codes. Section tags ride along so the prefix looks like the one
    inference builds.
    """
    if lines:
        chosen = [row for row in lines
                  if start <= row["onset"] < end
                  or (row["end"] is not None and row["onset"] < start < row["end"])]
        if not chosen:
            return ""
        out: list[str] = []
        section = chosen[0].get("section")
        if section:
            out.append(section)
        for row in chosen:
            if row.get("section") and row["section"] != section:
                section = row["section"]
                out.append(section)
            out.append(row["text"])
        return "\n".join(out)

    body = [line.strip() for line in lyrics_text.splitlines() if line.strip()]
    sung = [line for line in body if not _SECTION.match(line)]
    if not sung or duration <= 0:
        return ""
    first = min(int(len(sung) * start / duration), len(sung) - 1)
    last = max(first + 1, min(int(math.ceil(len(sung) * end / duration)), len(sung)))
    head = [line for line in body[:body.index(sung[first]) + 1] if _SECTION.match(line)]
    return "\n".join(head[-1:] + sung[first:last])


# ------------------------------------------------------------- the sequences --

@dataclass(frozen=True)
class Window:
    """One training excerpt: where it came from and what its prefix says."""
    source: str
    index: int
    start: float
    seconds: float
    lyrics: str
    abc: str | None
    frames: int


def plan_windows(frames: int, window_frames: int, hop_frames: int) -> list[tuple[int, int]]:
    """[start, end) code spans covering the track, the last one flush with the end."""
    if frames < MIN_WINDOW_FRAMES:
        return []
    if window_frames < MIN_WINDOW_FRAMES or hop_frames < 1:
        raise ValueError("A window needs at least 64 frames and a positive hop")
    if frames <= window_frames:
        return [(0, frames)]
    spans = [(start, start + window_frames)
             for start in range(0, frames - window_frames + 1, hop_frames)]
    if spans[-1][1] < frames:
        spans.append((frames - window_frames, frames))
    return spans


def window_request(style: str, trigger: str, lyrics: str, *, dialect: str,
                   abc: str | None, cot: str, identifier: str) -> SongRequest:
    """The request whose prefix this window trains on — the engine's own dataclass."""
    if dialect not in DIALECTS:
        raise ValueError(f"dialect must be one of {DIALECTS}")
    text = style.strip()
    trigger = trigger.strip()
    if trigger and trigger.lower() not in text.lower():
        text = f"{trigger}, {text}" if text else trigger
    if dialect == "direct":
        return SongRequest(style=text, lyrics=lyrics, cot="off", seed=1, id=identifier)
    if not (abc or "").strip():
        raise ValueError("The score dialect needs an ABC score for the window")
    return SongRequest(style=text, lyrics=lyrics, cot=cot, abc=abc, seed=1, id=identifier)


def training_sequence(prefix, codes, max_len: int, rng=None):
    """`prefix + codes` as ids, with the index the loss is allowed to start at.

    Mothersuperior truncates a long track to its head. With windowed data the
    codes already fit; the random window here only matters when a caller hands
    in a whole track, and it is a window over the performance rather than a
    permanent bias towards its first minute.
    """
    prefix = [int(token) for token in prefix]
    codes = np.asarray(codes).reshape(-1)
    if codes.size and (codes.min() < 0 or codes.max() >= CODEC_SIZE):
        raise ValueError("Codes outside the 32768-symbol semantic vocabulary")
    room = max_len - len(prefix) - 1
    if room < MIN_WINDOW_FRAMES:
        raise ValueError(f"The prefix leaves room for {room} codes; raise --max-len")
    if codes.size <= room:
        body = [int(code) + CODEC_OFFSET for code in codes] + [MUSIC_END]
    else:
        start = 0 if rng is None else rng.randrange(0, int(codes.size) - room + 1)
        body = [int(code) + CODEC_OFFSET for code in codes[start:start + room]]
    return prefix + body, len(prefix)


def supervised_span(prefix_len: int, total: int):
    """Which hidden states are scored, and against which ids — the code tail only.

    Position `i`'s hidden state predicts token `i+1`, so the first state that
    may be scored is the last PREFIX position and the first target is the first
    CODE token. Score one position earlier and the model is being taught to
    write the prompt; one later and the first code is never supervised. This is
    the loss mask, and it is the whole difference between teaching a voice and
    teaching a prompt.
    """
    if not 1 <= prefix_len < total:
        raise ValueError("The prefix has to be shorter than the sequence")
    return slice(prefix_len - 1, total - 1), slice(prefix_len, total)


# ------------------------------------------------------------ the dataset --

def file_digest(path: Path) -> str:
    """sha256 of a file's bytes, streamed. Identity, not name."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_head(head: Path | None, lora_dir: Path | None) -> Path:
    """The tokenizer head to encode with, checked BEFORE anything expensive.

    `--head` defaults to the pinned one in the music LoRA pack. Leaving it
    unresolved used to hand `None` all the way down to `load_head()`, where
    `Path(None)` raised a bare TypeError halfway through a training run — and
    only on a cold codes cache, so it hid until somebody trained a fresh voice.
    """
    if head is not None:
        head = Path(head).expanduser()
        if not head.is_file():
            raise FileNotFoundError(f"No tokenizer head at {head}")
        return head
    root = Path(lora_dir).expanduser() if lora_dir else (
        Path(os.environ.get("LTX_MUSIC_LORAS") or ROOT / "mlx_models" / "yue2-loras"))
    sys.path.insert(0, str(ROOT))
    from scripts.pinokio.music_lora_fetch import head_path       # noqa: PLC0415

    candidate = head_path(root)
    if not candidate.is_file():
        raise FileNotFoundError(
            f"The tokenizer head is not installed ({candidate}). Install the music "
            f"LoRA pack, or pass --head. It is what turns a recording into codes.")
    return candidate


def codes_cache_key(audio: Path, head: Path, mert_layer=None) -> str:
    """What a cached encode is allowed to be reused for.

    Keyed by the recording's CONTENTS and the head that read them, never by
    file name: two albums both holding a `take.wav` used to share one cache
    entry, so the second voice trained on the first one's codes — paired with
    its own lyrics. Replacing a recording in place had the same effect.
    """
    parts = [file_digest(audio), Path(head).name, file_digest(head)[:16],
             str(mert_layer if mert_layer is not None else "default")]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _codes_for(audio: Path, cache: Path | None, head_file: Path,
               mert_dir: Path | None, say, *, mert_layer=None) -> np.ndarray:
    """Semantic codes for a recording, tokenised once and cached by identity."""
    target = None
    if cache is not None:
        key = codes_cache_key(audio, head_file, mert_layer)
        target = Path(cache) / f"{audio.stem}.{key}.codes.npy"
        sidecar = target.with_suffix(".json")
        if target.is_file() and sidecar.is_file():
            try:
                stored = json.loads(sidecar.read_text()).get("cache_key")
            except (json.JSONDecodeError, OSError):
                stored = None
            if stored == key:
                say(f"codes cached {audio.name} ({key})")
                return np.load(target, allow_pickle=False).reshape(-1).astype(np.int64)
            say(f"codes cache MISS for {audio.name}: the entry is for other bytes")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import yue2_tokenizer as T                                   # noqa: PLC0415

    codes, metadata = T.encode_codes(audio, head_file=head_file, mert_dir=mert_dir,
                                     on_stage=lambda stage: say(f"  {audio.name}: {stage}"))
    codes = np.asarray(codes).reshape(-1).astype(np.int64)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        np.save(target, codes.astype(np.int32))
        target.with_suffix(".json").write_text(json.dumps(
            {"cache_key": codes_cache_key(audio, head_file, mert_layer),
             "source": str(audio), "source_sha256": file_digest(audio),
             "head": str(head_file), "frames": int(codes.size),
             "metadata": metadata}, indent=2, default=str))
    return codes


def build_dataset(recordings, *, style: str, trigger: str, dialect: str, cot: str,
                  lyrics_sources=None, scores_dir: Path | None = None,
                  window_frames: int, hop_frames: int, tokenizer,
                  codes_of, say=print):
    """Every window of every recording, as (Window, prefix ids, codes).

    `codes_of(audio)` hands back the semantic codes — injected so the caller
    decides whether that means the tokenizer head or a cache, and so this is
    testable without a GPU.
    """
    items, windows, skipped = [], [], {"no_score": 0, "short": 0, "no_lyrics": 0}
    recordings = list(recordings)
    for audio in recordings:
        codes = np.asarray(codes_of(audio)).reshape(-1)
        duration = len(codes) / FRAME_RATE
        lyrics_file = match_sidecar(audio, lyrics_sources, (".txt", ".md"),
                                    allow_single=len(recordings) == 1)
        lyrics_text = lyrics_file.read_text(encoding="utf-8") if lyrics_file else ""
        lines = read_timed_lines(audio.with_suffix(".lines.json"))
        if lines is None and lyrics_file is not None:
            lines = read_timed_lines(lyrics_file.with_suffix(".lines.json"))
        spans = plan_windows(len(codes), window_frames, hop_frames)
        say(f"{audio.name}: {len(codes)} frames ({duration:.1f}s) → {len(spans)} windows"
            f"{' (timed lyrics)' if lines else ''}")
        for index, (first, last) in enumerate(spans):
            if last - first < MIN_WINDOW_FRAMES:
                skipped["short"] += 1
                continue
            start, end = first / FRAME_RATE, last / FRAME_RATE
            words = lyrics_for_window(lyrics_text, lines, start, end, duration)
            abc = None
            if dialect == "score":
                score_file = None
                if scores_dir is not None:
                    score_file = Path(scores_dir) / f"{audio.stem}.w{index:03d}.abc"
                if score_file is None or not score_file.is_file():
                    skipped["no_score"] += 1
                    continue
                abc = score_file.read_text(encoding="utf-8")
            if not words.strip():
                skipped["no_lyrics"] += 1
            request = window_request(style, trigger, words, dialect=dialect, abc=abc,
                                     cot=cot, identifier=f"w{len(items):04d}")
            prefix = token_prefixes(request, tokenizer)
            window = Window(source=audio.name, index=index, start=round(start, 3),
                            seconds=round(end - start, 3), lyrics=words, abc=abc,
                            frames=int(last - first))
            windows.append(window)
            items.append({"src": "artist", "name": f"{audio.stem}.w{index:03d}",
                          "prefix": np.asarray(prefix, dtype=np.int64),
                          "codec": codes[first:last].astype(np.int64)})
    if not items:
        raise ValueError(f"No trainable windows (skipped {skipped})")
    say(f"dataset: {len(items)} windows, skipped {skipped}")
    return items, windows, skipped


#: Bumped when the on-disk shape of a regulariser cache changes.
REG_CACHE_VERSION = 2


def write_regularizer_cache(path: Path, items) -> Path:
    """Ragged int sequences as flat arrays + offsets, and names as JSON.

    Deliberately NOT an object array. The first version wrote
    `np.savez(items=np.asarray(items, dtype=object))` and read it back with
    `allow_pickle=True`, which executes whatever `__reduce__` the file asks
    for — so a shared or downloaded `--reg-cache` was arbitrary code execution,
    reached even by `--plan-only`.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix_offsets, codec_offsets = [0], [0]
    prefixes, codecs = [], []
    for item in items:
        prefixes.append(np.asarray(item["prefix"], dtype=np.int64).reshape(-1))
        codecs.append(np.asarray(item["codec"], dtype=np.int64).reshape(-1))
        prefix_offsets.append(prefix_offsets[-1] + prefixes[-1].size)
        codec_offsets.append(codec_offsets[-1] + codecs[-1].size)
    empty = np.zeros(0, dtype=np.int64)
    np.savez(path,
             version=np.asarray(REG_CACHE_VERSION, dtype=np.int64),
             prefix_data=np.concatenate(prefixes) if prefixes else empty,
             prefix_offsets=np.asarray(prefix_offsets, dtype=np.int64),
             codec_data=np.concatenate(codecs) if codecs else empty,
             codec_offsets=np.asarray(codec_offsets, dtype=np.int64),
             meta=np.asarray(json.dumps(
                 [{"src": str(item["src"]), "name": str(item["name"])} for item in items])))
    return path


def read_regularizer_cache(path: Path) -> list[dict]:
    """The inverse, with `allow_pickle=False`. A legacy cache is refused, loudly."""
    with np.load(Path(path), allow_pickle=False) as blob:
        missing = {"version", "prefix_data", "prefix_offsets", "codec_data",
                   "codec_offsets", "meta"} - set(blob.files)
        if missing:
            raise ValueError(
                f"{Path(path).name} is not a v{REG_CACHE_VERSION} regulariser cache "
                f"(missing {sorted(missing)}). Delete it and let --reg-pack rebuild "
                f"it; the old format was a pickle and is no longer read.")
        version = int(blob["version"])
        if version != REG_CACHE_VERSION:
            raise ValueError(f"{Path(path).name} is version {version}, not "
                             f"{REG_CACHE_VERSION}; delete it and rebuild")
        prefix_data, prefix_offsets = blob["prefix_data"], blob["prefix_offsets"]
        codec_data, codec_offsets = blob["codec_data"], blob["codec_offsets"]
        meta = json.loads(str(blob["meta"]))
    items = []
    for index, row in enumerate(meta):
        items.append({
            "src": row["src"], "name": row["name"],
            "prefix": prefix_data[prefix_offsets[index]:prefix_offsets[index + 1]],
            "codec": codec_data[codec_offsets[index]:codec_offsets[index + 1]]})
    return items


def load_regularizer(path: Path, tokenizer, *, cap: int, validation: int,
                     cache: Path | None = None, say=print):
    """Minted base-dialect songs, so the AR stays inside YuE2's own dialect.

    Maestro mixes these against the artist at `artist_fraction`; without them
    an AR LoRA drifts out of the dialect and the decoder stops understanding
    it. The pack is a torch file; a converted copy is cached as .npz so a
    machine without torch can still train.
    """
    if cache is not None and Path(cache).is_file():
        items = read_regularizer_cache(cache)[: cap + validation]
        say(f"regularizer cached: {len(items)} songs")
        return items
    import torch                                                 # noqa: PLC0415

    with torch.serialization.safe_globals([np._core.multiarray._reconstruct, np.ndarray,
                                           np.dtype, np.dtypes.Int32DType,
                                           np.dtypes.Int64DType]):
        data = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(data, dict):
        data = data.get("records", data.get("items", data.get("data")))
    items, kept = [], {"minted": 0, "minted_val": 0}
    for index, record in enumerate(data):
        source = record.get("src")
        if source not in {"minted", "minted_val"}:
            source = "minted_val" if index % 20 == 0 else "minted"
        limit = validation if source == "minted_val" else cap
        if kept[source] >= limit:
            continue
        codes = np.asarray(record["codec"], dtype=np.int64).reshape(-1)
        if not codes.size or codes.min() < 0 or codes.max() >= CODEC_SIZE:
            raise ValueError("The regulariser pack carries invalid semantic codes")
        request = SongRequest(style=str(record["style"]), lyrics=str(record["lyrics"]),
                              cot="off", seed=1, id=f"m{index}")
        items.append({"src": source, "name": f"m{index}",
                      "prefix": np.asarray(token_prefixes(request, tokenizer), dtype=np.int64),
                      "codec": codes})
        kept[source] += 1
    say(f"regularizer: {kept['minted']} minted, {kept['minted_val']} held out")
    if cache is not None:
        write_regularizer_cache(cache, items)
    return items


def data_fingerprint(recordings, lyrics_sources, scores_dir) -> str:
    """One hash over everything that decided the dataset, for the sidecar."""
    digest = hashlib.sha256()
    for audio in recordings:
        digest.update(audio.name.encode())
        digest.update(str(audio.stat().st_size).encode())
        with audio.open("rb") as handle:
            digest.update(handle.read(1 << 20))
    for item in sorted(str(Path(p)) for p in (lyrics_sources or [])):
        digest.update(item.encode())
    if scores_dir is not None:
        for score in sorted(Path(scores_dir).glob("*.abc")):
            digest.update(score.read_bytes())
    return digest.hexdigest()


# ---------------------------------------------------------- window audio out --

def emit_windows(recordings, out: Path, *, window_seconds: float, hop_seconds: float,
                 codes_of, say=print) -> list[dict]:
    """Write each window as its own wav, so a transcriber can be pointed at it.

    The score dialect needs an ABC score PER WINDOW, and the thing that makes
    one is the engine's cover transcription (`yue2_run.py --source-audio
    --cover-task melody-vocal --transcribe-only`). Rather than pull SheetSage2
    into this trainer, hand that tool the exact audio each window trains on.
    """
    out.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg is not on PATH; it cuts the window audio")
    window_frames = int(round(window_seconds * FRAME_RATE))
    hop_frames = max(1, int(round(hop_seconds * FRAME_RATE)))
    # Window files are named by basename, because the per-window ABC scores are
    # looked up by that name. So the manifest carries the source's sha256 and a
    # window cut from DIFFERENT bytes is re-cut rather than silently reused.
    previous = {}
    if (out / "windows.json").is_file():
        try:
            for row in json.loads((out / "windows.json").read_text()):
                previous[row.get("file")] = row.get("source_sha256")
        except (json.JSONDecodeError, OSError):
            previous = {}
    manifest: list[dict] = []
    for audio in recordings:
        source_sha = file_digest(audio)
        codes = np.asarray(codes_of(audio)).reshape(-1)
        for index, (first, last) in enumerate(plan_windows(len(codes), window_frames,
                                                           hop_frames)):
            start, seconds = first / FRAME_RATE, (last - first) / FRAME_RATE
            target = out / f"{audio.stem}.w{index:03d}.wav"
            stale = target.is_file() and previous.get(target.name) != source_sha
            if stale:
                say(f"re-cutting {target.name}: it was cut from other bytes")
            if not target.is_file() or stale:
                done = subprocess.run(
                    [ffmpeg, "-nostdin", "-v", "error", "-y", "-ss", f"{start:.3f}",
                     "-t", f"{seconds:.3f}", "-i", str(audio), "-ac", "2",
                     str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if done.returncode:
                    raise RuntimeError(f"ffmpeg failed on {target.name}: "
                                       f"{done.stderr.decode('utf-8', 'replace')[-200:]}")
            manifest.append({"source": audio.name, "index": index, "file": target.name,
                             "source_sha256": source_sha,
                             "start": round(start, 3), "seconds": round(seconds, 3),
                             "frames": int(last - first)})
    (out / "windows.json").write_text(json.dumps(manifest, indent=2))
    say(f"windows: {len(manifest)} files in {out}")
    return manifest


# ------------------------------------------------------------------ training --

def adapter_key(layer: int, group: str, projection: str) -> str:
    """The one spelling `yue2_lora.read_adapter` normalises everything else to."""
    return f"layers.{layer}.{group}.{projection}"


def _targets(model):
    out = {}
    for index, layer in enumerate(model.model.layers):
        for group, names in (("self_attn", ATTN_PROJECTIONS), ("mlp", MLP_PROJECTIONS)):
            container = getattr(layer, group)
            for name in names:
                out[adapter_key(index, group, name)] = (container, name)
    return out


class _Slot:
    """Where a wrapped projection finds its current A and B."""
    __slots__ = ("a", "b")

    def __init__(self):
        self.a = self.b = None


_SLOT_ATTR = "_train_lora_slot"
_wrapped: dict[type, type] = {}


def _lora_class(base: type) -> type:
    if base in _wrapped:
        return _wrapped[base]

    class _LoRA(base):                                           # type: ignore[misc]
        def __call__(self, x, *args, **kwargs):
            out = super().__call__(x, *args, **kwargs)
            slot = object.__getattribute__(self, _SLOT_ATTR)
            delta = (x.astype(slot.a.dtype) @ slot.a.T) @ slot.b.T
            return out + delta.astype(out.dtype)

    _LoRA.__name__ = f"TrainLoRA{base.__name__}"
    _wrapped[base] = _LoRA
    return _LoRA


def _linear_shape(module):
    weight = module.weight
    scales = module.get("scales") if hasattr(module, "get") else None
    if scales is not None:
        return int(weight.shape[0]), int(scales.shape[1]) * int(module.group_size)
    return int(weight.shape[0]), int(weight.shape[1])


def install_adapters(model, rank: int, seed: int):
    """Wrap every AR target in place; return the slots and the parameter tree.

    Same trick as `yue2_lora.apply_adapters`: swap the projection's class
    rather than nest it in a wrapper, so not one parameter is renamed.
    """
    import mlx.core as mx                                        # noqa: PLC0415

    rng = np.random.default_rng(seed)
    slots, params = {}, {}
    for name, (container, attribute) in _targets(model).items():
        module = getattr(container, attribute)
        out_features, in_features = _linear_shape(module)
        slot = _Slot()
        object.__setattr__(module, _SLOT_ATTR, slot)
        module.__class__ = _lora_class(type(module))
        slots[name] = slot
        params[name] = {
            "A": mx.array((rng.standard_normal((rank, in_features))
                           / math.sqrt(in_features)).astype(np.float32)),
            "B": mx.zeros((out_features, rank), dtype=mx.float32),
        }
    return slots, params


def bind(slots, params):
    for name, slot in slots.items():
        slot.a, slot.b = params[name]["A"], params[name]["B"]


def save_adapter(params, path: Path, *, rank: int, dialect: str, trigger: str):
    """BF16 `lora_A`/`lora_B` pairs, the layout the runtime already reads."""
    import mlx.core as mx                                        # noqa: PLC0415

    flat = {}
    for name, pair in params.items():
        flat[f"{name}.lora_A"] = pair["A"].astype(mx.bfloat16)
        flat[f"{name}.lora_B"] = pair["B"].astype(mx.bfloat16)
    path.parent.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(path), flat, metadata={
        "format": "mlx", "branch": "ar", "rank": str(rank), "dialect": dialect,
        "trigger": trigger, "targets": "self_attn q,k,v,o + mlp gate,up,down"})


def sidecar_metadata(*, name: str, trigger: str, dialect: str, cot: str, style: str,
                     model_dir: Path, steps: int, rank: int, lr: float,
                     window_seconds: float, hop_seconds: float, windows, skipped,
                     fingerprint: str, metrics: dict, seed: int,
                     artist_fraction: float) -> dict:
    """What a listener has to know to use this file, and to trust its numbers."""
    revision = {}
    conversion = Path(model_dir) / "conversion.json"
    if conversion.is_file():
        try:
            raw = json.loads(conversion.read_text())
            revision = {key: raw[key] for key in ("format", "schema", "upstream")
                        if key in raw}
            identity = (raw.get("source") or {}).get("identity") or {}
            for key in ("repo", "repository", "revision", "commit"):
                if key in identity:
                    revision[key] = identity[key]
        except (json.JSONDecodeError, OSError):
            revision = {}
    return {
        "name": name, "kind": "yue2-ar-voice", "branch": "ar", "trigger": trigger,
        "dialect": dialect, "cot": cot, "style": style,
        "generate_with": ("--mode off" if dialect == "direct"
                          else f"--mode {cot} --abc-file <score.abc>"),
        "base": {"model_dir": str(model_dir), **revision},
        "training": {"steps": steps, "rank": rank, "lr": lr, "seed": seed,
                     "artist_fraction": artist_fraction,
                     "window_seconds": window_seconds, "hop_seconds": hop_seconds,
                     "windows": len(windows), "skipped": skipped},
        "data": {"fingerprint": fingerprint,
                 "recordings": sorted({window.source for window in windows}),
                 "windows_with_lyrics": sum(1 for w in windows if w.lyrics.strip()),
                 "windows_with_score": sum(1 for w in windows if w.abc)},
        "metrics": metrics,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ----------------------------------------------------------------- the CLI --

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yue2_train_voice.py",
        description="Train a YuE2 voice (AR LoRA) from recordings.")
    p.add_argument("--recordings", nargs="+", required=True,
                   help="a folder of audio, or the files themselves")
    p.add_argument("--lyrics", nargs="*", default=None,
                   help="a folder or files matching the recordings by stem; "
                        "'none' trains timbre only")
    p.add_argument("--trigger", default="", help="the word that turns the voice on")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--name", default=None, help="output basename (default: the trigger)")
    p.add_argument("--style", default="", help="style tags every window trains with")
    p.add_argument("--style-file", type=Path, default=None)
    p.add_argument("--dialect", choices=DIALECTS, default="direct")
    p.add_argument("--cot", choices=("melody", "full"), default="melody",
                   help="score dialect only: which planning instruction")
    p.add_argument("--scores", type=Path, default=None,
                   help="score dialect only: folder of <stem>.wNNN.abc")
    p.add_argument("--reg-pack", type=Path, default=None,
                   help="minted_regularizer_pack.pt — keeps the AR in YuE2's dialect")
    p.add_argument("--reg-cache", type=Path, default=None,
                   help="converted copy of the pack (.npz), written on first use")
    p.add_argument("--reg-cap", type=int, default=400)
    p.add_argument("--reg-validation", type=int, default=6)
    p.add_argument("--artist-fraction", type=float, default=0.5)
    p.add_argument("--steps", type=int, default=400)
    p.add_argument("--rank", type=int, default=32)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--sched-steps", type=int, default=3000)
    p.add_argument("--accumulation", type=int, default=2)
    p.add_argument("--max-len", type=int, default=2048)
    p.add_argument("--chunk", type=int, default=256)
    p.add_argument("--window-seconds", type=float, default=DEFAULT_WINDOW_SECONDS)
    p.add_argument("--hop-seconds", type=float, default=DEFAULT_HOP_SECONDS)
    p.add_argument("--eval-every", type=int, default=50)
    p.add_argument("--save-every", type=int, default=100)
    p.add_argument("--seed", type=int, default=4242)
    p.add_argument("--minutes", type=float, default=None, help="wall-clock budget")
    p.add_argument("--model-dir", type=Path,
                   default=ROOT / "mlx_models" / "yue2" / "generator")
    p.add_argument("--head", type=Path, default=None, help="tokenizer head (default: the pack's)")
    p.add_argument("--mert-dir", type=Path, default=None)
    p.add_argument("--codes-cache", type=Path, default=None,
                   help="where tokenised codes are kept (default: <out>/codes)")
    p.add_argument("--emit-windows", type=Path, default=None,
                   help="write the window audio here and stop")
    p.add_argument("--plan-only", action="store_true",
                   help="build the dataset, write the plan, train nothing")
    p.add_argument("--install", action="store_true",
                   help="also copy the result into the music LoRA folder's user/ dir")
    p.add_argument("--lora-dir", type=Path,
                   default=ROOT / "mlx_models" / "yue2-loras")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    log = (out / "train.log").open("a")

    def say(*parts):
        message = " ".join(str(part) for part in parts)
        print(message, flush=True)
        log.write(f"{time.strftime('%H:%M:%S')} {message}\n")
        log.flush()

    name = args.name or (args.trigger or "voice")
    style = args.style
    if args.style_file:
        style = Path(args.style_file).read_text(encoding="utf-8")
    style = " ".join(style.split())
    lyrics_sources = None if (args.lyrics in (None, ["none"], ["None"])) else args.lyrics

    recordings = resolve_recordings(args.recordings)
    say(f"recordings: {len(recordings)}")
    codes_cache = args.codes_cache or (out / "codes")
    head = resolve_head(args.head, args.lora_dir)
    say(f"tokenizer head: {head.name}")
    codes_of = (lambda audio: _codes_for(audio, codes_cache, head, args.mert_dir, say))

    if args.emit_windows:
        emit_windows(recordings, Path(args.emit_windows),
                     window_seconds=args.window_seconds, hop_seconds=args.hop_seconds,
                     codes_of=codes_of, say=say)
        return 0

    sys.path.insert(0, str(ROOT / "mlx_models" / "yue2" / "generator"))
    from yue2.tokenization_yue2 import YuE2TextTokenizer          # noqa: PLC0415
    tokenizer = YuE2TextTokenizer(str(Path(args.model_dir) / "qwen.tiktoken"))

    window_frames = int(round(args.window_seconds * FRAME_RATE))
    hop_frames = max(1, int(round(args.hop_seconds * FRAME_RATE)))
    items, windows, skipped = build_dataset(
        recordings, style=style, trigger=args.trigger, dialect=args.dialect,
        cot=args.cot, lyrics_sources=lyrics_sources, scores_dir=args.scores,
        window_frames=window_frames, hop_frames=hop_frames, tokenizer=tokenizer,
        codes_of=codes_of, say=say)

    if args.reg_pack or args.reg_cache:
        items += load_regularizer(args.reg_pack, tokenizer, cap=args.reg_cap,
                                  validation=args.reg_validation,
                                  cache=args.reg_cache, say=say)
    else:
        say("WARNING no regulariser: the AR can drift out of YuE2's dialect")

    fingerprint = data_fingerprint(recordings, lyrics_sources, args.scores)
    plan = {"windows": [vars(window) | {"lyrics": len(window.lyrics.split()),
                                        "abc": bool(window.abc)} for window in windows],
            "skipped": skipped, "fingerprint": fingerprint,
            "prefix_tokens": [len(item["prefix"]) for item in items if item["src"] == "artist"]}
    (out / "plan.json").write_text(json.dumps(plan, indent=2))
    longest = max(plan["prefix_tokens"])
    say(f"prefix tokens: min {min(plan['prefix_tokens'])} max {longest}")
    if longest + MIN_WINDOW_FRAMES >= args.max_len:
        raise SystemExit(f"--max-len {args.max_len} is too small for a {longest}-token prefix")
    if args.plan_only:
        say("PLAN ONLY — nothing trained")
        return 0

    return _train(args, items, windows, out, name, style, fingerprint, skipped, say)


def _train(args, items, windows, out: Path, name: str, style: str,
           fingerprint: str, skipped, say) -> int:
    import mlx.core as mx                                        # noqa: PLC0415
    import mlx.nn as nn                                          # noqa: PLC0415
    import mlx.optimizers as optim                               # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "yue2-mlx" / "src"))
    from lyra.ar import load_ar                                  # noqa: PLC0415

    groups = {"artist": [], "minted": [], "minted_val": []}
    for item in items:
        groups[item["src"]].append(item)
    rng = random.Random(args.seed)
    mx.random.seed(args.seed)

    model = load_ar(args.model_dir, precision="bf16", verify=False)
    model.freeze()
    mx.eval(model.parameters())
    slots, params = install_adapters(model, args.rank, args.seed)
    mx.eval(params)
    count = sum(value.size for pair in params.values() for value in pair.values())
    say(f"AR LoRA {count / 1e6:.1f}M params, rank {args.rank}, lr {args.lr}, "
        f"{len(groups['artist'])} artist windows, {len(groups['minted'])} minted")

    def loss_fn(parameters, ids, prefix_len):
        bind(slots, parameters)
        states, labels = supervised_span(prefix_len, int(ids.shape[0]))
        hidden = model.model(ids[None])[0][states]
        targets = ids[labels]
        total = mx.zeros((), dtype=mx.float32)
        for start in range(0, hidden.shape[0], args.chunk):
            logits = model.lm_head(hidden[start:start + args.chunk]).astype(mx.float32)
            total = total + nn.losses.cross_entropy(
                logits, targets[start:start + args.chunk], reduction="sum")
        return total / targets.shape[0]

    value_and_grad = mx.value_and_grad(loss_fn)
    optimizer = optim.AdamW(learning_rate=args.lr, betas=[0.9, 0.95], weight_decay=0.0)

    def ids_of(item, jitter):
        sequence, prefix_len = training_sequence(item["prefix"], item["codec"],
                                                 args.max_len, jitter)
        return mx.array(sequence, dtype=mx.int32), prefix_len

    def evaluate():
        scores = {}
        for tag, pool in (("minted_val", groups["minted_val"][:4]),
                          ("artist", groups["artist"][:8])):
            if not pool:
                continue
            values = []
            for item in pool:
                ids, prefix_len = ids_of(item, None)
                values.append(float(loss_fn(params, ids, prefix_len)))
            scores[tag] = round(sum(values) / len(values), 4)
        return scores

    history, started = [], time.time()
    baseline = evaluate()
    history.append({"step": 0, **baseline})
    say("EVAL step 0 " + " ".join(f"{k} {v:.3f}" for k, v in baseline.items()))
    deadline = started + args.minutes * 60 if args.minutes else None
    step = 0

    def checkpoint(step_number):
        save_adapter(params, out / f"{name}.step-{step_number}.safetensors",
                     rank=args.rank, dialect=args.dialect, trigger=args.trigger)
        (out / "progress.json").write_text(json.dumps(
            {"step": step_number, "history": history, "name": name}, indent=2))

    for step in range(1, args.steps + 1):
        factor = min(1, step / 50) * (0.2 + 0.8 * 0.5 * (
            1 + math.cos(math.pi * min(step, args.sched_steps) / args.sched_steps)))
        optimizer.learning_rate = args.lr * factor
        total, accumulated = 0.0, None
        for _ in range(args.accumulation):
            pool = groups["artist"] if (rng.random() < args.artist_fraction
                                        or not groups["minted"]) else groups["minted"]
            ids, prefix_len = ids_of(rng.choice(pool), rng)
            loss, grads = value_and_grad(params, ids, prefix_len)
            total += float(loss) / args.accumulation
            scaled = {k: {n: v / args.accumulation for n, v in pair.items()}
                      for k, pair in grads.items()}
            accumulated = scaled if accumulated is None else {
                k: {n: accumulated[k][n] + v for n, v in pair.items()}
                for k, pair in scaled.items()}
            mx.eval(accumulated)
        flat = [v for pair in accumulated.values() for v in pair.values()]
        norm = mx.sqrt(sum(mx.sum(g.astype(mx.float32) ** 2) for g in flat))
        scale = mx.minimum(mx.array(1.0), 1.0 / (norm + 1e-6))
        accumulated = {k: {n: v * scale for n, v in pair.items()}
                       for k, pair in accumulated.items()}
        params = optimizer.apply_gradients(accumulated, params)
        mx.eval(params, optimizer.state)
        if step <= 3 or step % 10 == 0:
            say(f"step {step} loss {total:.3f} len {ids.shape[0]} "
                f"{time.time() - started:.0f}s peak {mx.get_peak_memory() / 2 ** 30:.2f}G")
        if step % args.eval_every == 0 or step == args.steps:
            scores = evaluate()
            history.append({"step": step, **scores})
            say(f"EVAL step {step} " + " ".join(f"{k} {v:.3f}" for k, v in scores.items())
                + f" {time.time() - started:.0f}s")
        if step % args.save_every == 0 or step == args.steps:
            checkpoint(step)
        if deadline and time.time() > deadline:
            say(f"BUDGET reached at step {step}")
            history.append({"step": step, **evaluate()})
            checkpoint(step)
            break

    final = out / f"{name}.safetensors"
    save_adapter(params, final, rank=args.rank, dialect=args.dialect, trigger=args.trigger)
    metrics = {"history": history, "final": history[-1] if history else {},
               "minutes": round((time.time() - started) / 60, 1)}
    meta = sidecar_metadata(
        name=name, trigger=args.trigger, dialect=args.dialect, cot=args.cot, style=style,
        model_dir=args.model_dir, steps=step, rank=args.rank, lr=args.lr,
        window_seconds=args.window_seconds, hop_seconds=args.hop_seconds,
        windows=windows, skipped=skipped, fingerprint=fingerprint, metrics=metrics,
        seed=args.seed, artist_fraction=args.artist_fraction)
    final.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    say(f"SAVED {final} ({final.stat().st_size / 1e6:.1f} MB)")

    if args.install:
        user = Path(args.lora_dir) / "user"
        user.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final, user / final.name)
        shutil.copy2(final.with_suffix(".json"), user / f"{name}.json")
        say(f"INSTALLED {user / final.name}")
    say("TRAIN DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
