# YuE2 — Phosphene's music engine

YuE2 by Multimodal Art Projection writes vocals and an arrangement together
from lyrics and a style description. It sits beside LTX and Hailuo H3 as a
peer engine. Open **Audio → Compose**: Compose is always there. Until the
engine is installed the form is greyed and one button, **Install music engine
(YuE2, ~11 GB)**, installs it from the panel; pressing **Compose** does the
same. The sidebar entry **Install the music engine** in Pinokio still works and
runs the same steps. Settings → Models and the header engine picker (YuE2 ·
11 GB) open the same install card.

## Installing from the panel

`POST /music/install` (`kind=install|repair`) starts a background task that
runs `MUSIC_INSTALL_STEPS` in `mlx_ltx_panel.py`: the six `shell.run` commands
of `install_music.js`, character for character, from the app folder
(`test_music_engine.py` compares the two lists). Progress rides
`/status.music_install` (`state` idle/running/stopping/done/failed/stopped,
`step_index`, `step_label`, `percent`, `bytes_done`/`bytes_total` while the
weights download, `last_line`, a short `log`). The byte counter reads the pack
folder, so with hf_xet it moves as each large file lands.

- **Environment:** the panel's own (HF_HOME, SSL certs, `LTX_MUSIC_*`) minus
  the LTX venv activation, with Pinokio's `bin/miniforge/bin`,
  `bin/miniconda/bin` and `bin/homebrew/bin` in front of PATH — a panel started
  outside Pinokio's shell has no `uv` otherwise.
- **Stop** (`POST /music/install/stop`) SIGTERMs the step's process group and
  SIGKILLs it after 8 s. **Install again resumes**: the clone and venv are kept
  and `music_fetch.py` keeps every file whose sha256 matches.
- **Guards:** one install at a time (409); refused while a song is being
  written; a song is refused while the install runs; each step is registered
  with the orphan reaper (`state/music_install_running.json`), and the panel
  stops the install when it exits.
- **Disk:** `music_preflight.sh` (14 GB free) and `music_fetch.py` (space for
  the missing bytes) refuse with a sentence the card shows verbatim.
- Nothing restarts: `/status.music` flips to available and Compose unlocks on
  the next poll.

## Memory, length and estimates

The floor is **24 GB unified memory** (`MUSIC_MIN_RAM_GB`). Peaks
measured 11.0 GB on this integration's runs (see below). Installation
requires **14 GB free** on the weights volume; the pack occupies about
10.5 GB. Repairs verify existing files and need space only for missing bytes.

Length follows the lyrics. **Max length** is a ceiling, not a requested song
length: it truncates a longer song and never stretches a shorter one. The
form offers 30–360 seconds in 15-second steps, initially 4:00. The runner's
8–360-second clamp also applies to API requests.

The estimate prices a song that runs to Max length, fitted to receipts
taken on an M4 Max 64 GB (8-bit AR): **Final** (32 steps) rendered 177.8 s of
audio in 180.2 s, 120 s in 113.8 s and 60 s in 58.5 s; **Draft** (8 steps)
rendered 120 s in 64.2 s. Score planning and resident loads add about 12 s.
The model is `12 s + seconds × 0.93` (Final) or `× 0.43` (Draft), scaled for
other chips by the LTX speed table. It is always labelled an estimate: most
songs end sooner, and a cold first load is not in the fit. Every run peaked at 11.0 GB of process
footprint (MLX peak 10.5 GiB). The constants sit in one block beside
`music_paths()` in `mlx_ltx_panel.py`.

## How it plugs in

```
POST /queue/add (mode=music, engine=music, music_*)
  → make_job → worker_loop → run_job_inner → run_music_job_inner
  → refuse if a command-line GPU lock exists → park LTX helper
  → yue2-mlx/.venv/bin/python scripts/music/yue2_run.py
  → [music] stdout progress → Now card
  → mlx_outputs/music_<stamp>_<slug>.wav + .wav.json
```

Music shares the queue's GPU mutex, which already serialises every job in
the panel. The panel never creates, deletes or reclaims a shared lock file. It
only reads the two paths command-line GPU jobs use (`/tmp/phosphene_gpu.lock`
and `~/AI/projects/hailuo-mlx/.gpu_lock`) and refuses, naming the path, while
one exists. The LTX helper is killed to release its resident weights and
returns lazily on the next LTX job. Stop sends SIGTERM to the owned music
process group, then SIGKILL after eight seconds if the group is still active.
If a Stop lands while the song is being written, the job's files are removed,
so "Nothing was saved" stays true. `state/music_running.json` registers the
runner with the existing orphan reaper.

### Why a separate venv

The pinned MLX port needs **Python 3.12 and mlx 0.32.2**. LTX's Python 3.11
venv remains pinned to mlx 0.31.1. Install and Update use the music checkout's
lockfile with `uv sync --frozen --no-dev`; music is torch-free.

## Paths and installation

| Item | Default | Override |
|---|---|---|
| Engine checkout | `yue2-mlx/` | `LTX_MUSIC_ROOT` |
| Engine venv | `yue2-mlx/.venv/` | follows engine root |
| Weights | `mlx_models/yue2/` | `LTX_MUSIC_MODELS` |
| Phosphene runner | `scripts/music/yue2_run.py` | owned by the panel |
| Engine revision | `scripts/music/engine_pin.txt` | change only after validation |

Set root overrides in Pinokio's `ENVIRONMENT` file; the menu and panel read
the same names. The engine directory is ignored by git. Install clones
`vanch007/mlx-Yue`, fetches the pinned SHA and checks it out detached with
`--force`. Update repeats that pin and frozen sync only for an installed
engine, then runs the offline pack check. It does not download music weights.
Repair rebuilds a missing/broken Python 3.12 venv and keeps intact weights.

`scripts/pinokio/music_fetch.py` is the single downloader and verifier. It
inherits `HF_HOME`, stages downloads under `.staging/`, checks manifest
SHA-256s and moves files into place. Weights come straight from the public
repos the pinned port documents, each at a pinned revision: the MLX generator
from `vanch007/mlx-Yue2-3B` (its BF16 tensors are byte-identical to
`m-a-p/YuE2-3B`) and the decoder from `m-a-p/YuE2-Vae` (the original file).
Phosphene re-hosts nothing. A receipt, `pack_source.json`, records source
repositories, revisions and every file hash.

The generator directory must contain exactly its `conversion.json` files
map, the manifest, and at most a README. Only the declared `licenses/`
ancestors are allowed. HF's `.cache/` belongs in staging, never in generator;
a repair moves a leftover cache there. The VAE contains its model, config,
manifest and notices. Its model has a pinned SHA-256 as well as a manifest
hash. The current model permission and Phosphene notice live at the pack
root so they do not violate the generator's strict tree.

```sh
yue2-mlx/.venv/bin/python scripts/pinokio/music_fetch.py --check
yue2-mlx/.venv/bin/python scripts/pinokio/music_fetch.py --min-free-gb 14
```

`--check` is offline, prints missing/corrupt/unexpected entries, and exits
0/1. Panel status uses sizes and the manifest shape instead of hashing
10.5 GB on every poll. Install, Repair and Update perform full verification.

## Composing

Write section tags on their own lines, words beneath them, and leave a
blank line between sections:

```
[Verse]
The station lights are fading
Your footsteps find the street

[Chorus]
Carry the morning home
```

`[Pre-Chorus]`, `[Bridge]` and `[Outro]` are also section markers. A section
with no words is instrumental. Style is prose: genre, instruments, singer,
language and tempo in one description. The Instrumental pill keeps the
lyrics text in the browser but sends none of it; it passes `--instrumental`.

Score modes are **Melody + chords** (`full`, default), **Melody only**
(`melody`) and **No score** (`off`). YuE2 writes a sheet-music plan before
the song. Draft maps to 8 synthesis steps; Final maps to 32 (default).
Seed -1 chooses a random seed. All seven `music_*` fields survive the
form-urlencoded queue path and are persisted in the job.

## Outputs and the video handoff

The runner writes **48 kHz stereo PCM-24 WAV** and its `.wav.json` sidecar.
Audio cards use the existing outputs stream, with native audio controls.
The info panel shows style, lyrics, seed, actual length, natural ending
versus the ceiling, and the ABC score. The sidecar also records the job id,
engine pin and weight-source revisions. Delete/Trash carries it with the WAV.

**Drive video** loads a song into the existing Audio → Video workflow and
switches to that mode. It does not submit a render. Its normal video memory
and model requirements still apply. Settings → Storage can remove the music
weights; doing so hides Compose and leaves Drive video available.

## License and attribution

Weights use CC BY-NC 4.0 plus the upstream individual-creator permission:
individual creators may monetize their songs; companies need a license for
the weights. Read [the full current model license](../LICENSES/YuE2-MODEL_LICENSE.txt).
The port and upstream code are Apache-2.0; Oobleck VAE and SnakeBeta are MIT.
[The notice](../LICENSES/NOTICE-yue2.md) identifies the source and conversion.
BF16 weights are byte-identical to their source; 8-bit AR is a quantization.
The Compose footer credits Multimodal Art Projection and vanch007.

## Validation and troubleshooting

`test_music_engine.py` covers status, pack checks, the real HTTP handler's
form parsing, argv/provenance, progress, WAV MIME/ranges/gallery/Trash,
process ownership, and an unchanged Video engine switcher. It uses fake
packs and a stub child; it never loads weights. Run it through pytest with
MLX forced to CPU, then run `bash scripts/release_gates.sh --fast`.

A complete **from-zero Pinokio install, local ETA/memory measurements and
the owner's listening gate remain pending**. No music inference was run for
this integration. A passing fake-pack test does not validate song quality.

- **Weights present, engine unavailable:** choose Repair in Pinokio. It
  restores the interpreter and pinned code without downloading intact weights.
- **Missing file or hash mismatch:** run Repair; `--check` names the file.
- **Unexpected generator entry:** move only the named extra entry out of
  `generator/`; do not put notes or HF cache directories in that tree.
- **GPU busy:** the refusal names a lock file. Let that job finish; if nothing
  is running, the lock was left by a command-line job and can be deleted.
- **Stopped:** no partial WAV is published; the next job starts a fresh child.
