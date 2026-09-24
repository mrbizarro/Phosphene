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
| LoRA pack | `mlx_models/yue2-loras/` | `LTX_MUSIC_LORAS` |

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
lyrics text in the browser but sends none of it; it passes `--instrumental`
(see **LoRA inference**, below, for the two recipes that flag selects).

Score modes are **Melody + chords** (`full`, default), **Melody only**
(`melody`) and **No score** (`off`). YuE2 writes a sheet-music plan before
the song. Draft maps to 8 synthesis steps; Final maps to 32 (default).
Seed -1 chooses a random seed. All seven `music_*` fields survive the
form-urlencoded queue path and are persisted in the job.

## Music Studio — the song as a thing you come back to

Every song YuE2 writes keeps its **artifacts** — the plan (the ABC score and
the tokenised prompt), the semantic tokens, the latents and the solver noise —
under `state/music/<job-id>/`. That folder is what makes a song something you
can return to rather than a file you got once. Nothing in it is shown in the
gallery; the song's sidecar names it, and the studio reads the sidecar.

### Three tasks, one form

| Task | What happens | Needs |
|---|---|---|
| **Write a song** | style + lyrics → plan → song | the music pack |
| **Cover a song** | a recording is transcribed to a score (SheetSage2), then that score is realised under *your* style and lyrics | the pack + the 2.8 GB cover models |
| **Get the score** | transcription only — the recording's sheet music, no song | the cover models |

**Write lyrics** asks the local Gemma 3 (the same model the prompt enhancer
uses) for section-tagged lyrics from a one-line concept, in the exact format
YuE2 reads: `[Verse]`, `[Chorus]`, one sung line per row, a bare tag for an
instrumental passage. It is a draft to edit, not a result.

### The Song card

Under the player, for songs only: the title, the style, badges (cover, new
take, re-rolled, restyled, instrumental, cut at max length), the **sheet music
drawn from the score** (abcjs, vendored), the lyrics, and where the song came
from. And four ways to get another song from it:

| Verb | Restarts from | Keeps | Changes | Cost |
|---|---|---|---|---|
| **New take** | the saved plan | score, words, style | the performance (semantic stage re-run with a new seed) | a song minus the planning |
| **Re-roll the sound** | the saved semantic tokens | score, words, style, *the performance* | the recording of it (new solver noise through the NAR and the decoder) | roughly a third of a song |
| **New style on this score** | the saved `score.abc` | the score | the words and the style, taken from the composer | a full song minus the planning |
| **Cover it** | the audio itself | the tune, as heard | everything else | transcription + a song |

**Edit the score** opens the ABC in a text box; *Preview* redraws it, *Render
this score* queues a song from the edited text under the composer's style and
lyrics (or the song's own, if the boxes are empty). The edited ABC is written
under `state/music/edits/` and the worker refuses any other path.

A song made before v4.16 has no artifacts, and its buttons say so instead of
failing after a click. Every variation names its parent in the sidecar
(`lineage.parent`, `lineage.variation`) and is filed beside it as
`<parent>_take.wav`, `<parent>_sound.wav`, `<parent>_restyle.wav`.

### Advanced

- **Takes** — queue N songs from one form, each with its own seed (a fixed seed applies to the first only).
- **Text guidance** (`cfg_scale`) — 1.0 is the engine's default and is sent as *unset*, so a sidecar reading `null` means "the default was used".
- **Precision** — `bf16` / `8bit` / `4bit` for the composer weights. Every song so far shipped on 8-bit; bf16 needs roughly 8 GB more memory.
- **Voice & style LoRA** — adapters from `mlx_models/yue2-loras/` (and your own from its `user/` folder), each with its own 0–1.5 strength. See **LoRA inference**.

### Routes

`POST /music/variation` (`path`, `kind` ∈ take/sound/restyle, optional `style`,
`lyrics`, `seed`, `quality`, `title`), `GET /music/lora/status`,
`POST /music/lora/fetch`, `POST /music/transcribe` (`path`,
`task`), `GET /music/score?path=…` or `?job=…`, `POST /music/score/render`
(`path`, `abc`, …), `POST /music/lyrics` (`concept`, `style`, `seconds`,
`language`). All of them take output paths, never state paths, and refuse
anything that is not one of this panel's songs.

## LoRA inference

The engine has no adapter code, and the engine directory is replaced on every
sync, so Phosphene's LoRA support lives in **`scripts/music/yue2_lora.py`** and
is applied to the models `lyra` hands back. Nothing is merged into the base
weights: the AR model runs 8-bit by default, and merging would mean either
BF16-only (5 GB more resident) or a dequantise/requantise per song. The
arithmetic is the published one — `y = W x + strength · B (A x)`, `lora_A:
[rank, in]`, `lora_B: [out, rank]`, BF16, no alpha unless the file carries one.

**How it attaches without renaming anything.** `lyra.nar.load_nar()` validates
the shared AR model by walking `tree_flatten(ar_model.parameters())` and
demanding the exact upstream tensor names. A wrapper module holding the
original linear as a child would rename all 196 of them and the acoustic model
would refuse to load. So the projection object is kept and its **class** is
swapped for a generated subclass that adds the delta after the base call; the
matrices hang off the instance through `object.__setattr__`, the way `lyra`
itself keeps runtime constants off the parameter tree. Quantised bases work
unchanged and removal is one `__class__` assignment back.

`LoRARuntime` wraps `YuE2Pipeline._load_model`, which is the only hook that
sees all three models a song uses — the planning AR, the BF16 conditioning AR
built for the acoustic stage, and the acoustic model itself, which is loaded
halfway through the song.

| Mode | AR/NAR deltas | Decoder `vae2llm` / `llm2vae` |
|---|---|---|
| `joint` (default) | added at strength | untouched, even when the file carries them |
| `separate` | added at strength | REPLACED by a convex blend of every separate companion, weighted by strength |

A separate NAR file is a complete decoder companion, not an independent style
delta: summing several would double the shared decoder adaptation, so their
relative strengths form a convex blend. `nar_lora_joint_v9` *does* ship full
`vae2llm`/`llm2vae` tensors; in `joint` mode they are read, ignored, and the
sidecar records `decoder_io_ignored: true`.

Key spellings accepted: the Mothersuperior `layers.{i}.….lora_A` layout, an
optional `model.` prefix, PEFT / AI-Toolkit `base_model.model.` prefixes,
`.lora_A.weight` and `.lora_A.default.weight`, kohya `lora_down` / `lora_up`,
and `.A` / `.B`. A PEFT `alpha` scalar is folded into B as `alpha / rank`.

### The pack

`scripts/pinokio/music_lora_fetch.py` downloads two pinned adapters and the
real-audio tokenizer head into `mlx_models/yue2-loras/` (~300 MB, CC BY-NC 4.0,
checksummed, a `NOTICE` beside them). It is **not** part of
`MUSIC_INSTALL_STEPS`: a user who never asks for an instrumental never pays
for it.

| File | Repo · revision | What |
|---|---|---|
| `ar_lora_inst_v3abc.bf16.safetensors` | `Mothersuperior/YuE2-instrumental-cot-full-loras` · `947f2f4b` | AR · rank 64 · 196 targets |
| `nar_lora_joint_v9.bf16.safetensors` | `Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4` · `e2e63d85` | acoustic · rank 32 · 196 targets + decoder I/O |
| `tokenizer/tokenizer_head_joint_v9.bf16.safetensors` | same repo · `e2e63d85` | the real-audio tokenizer head, 85.6 MB |
| `tokenizer/sem_nbr_{cos,idx}.npy` | same repo · `e2e63d85` | training-side neighbour tables, not read at inference |

The head lives under `tokenizer/` on purpose: `pack_adapters()` globs the root
and `user/`, so a head beside the adapters would be offered in the studio's
LoRA picker as if it were one. `lora_problems()` still speaks only about the
adapters, so an installed pack is not declared broken by a new optional
download; `head_problems()` is its own gate and `--check` reports both.

**Usable and whole are two questions** (Codex review, 2026-09-22).
`lora_problems()` answers the first — can the picker offer these adapters —
and `pack_problems()` answers the second: is there anything left to download,
head included. `POST /music/lora/fetch` gates on the second, so an
interrupted run can be finished from the UI. A pinned file counts only at its
pinned SIZE; the SHA-256 is checked by the download, which copies to a
`.partial` sibling and `os.replace`s it into place only once it passes, so a
name on disk is always a verified file. `--check --deep` hashes them anyway.

`mlx_models/yue2-loras/user/` is scanned for any `*.safetensors`, so a
community or AI-Toolkit adapter is usable the moment it is copied in. The
picker is a directory listing; there is no registry to register with.

```sh
ltx-2-mlx/env/bin/python scripts/pinokio/music_lora_fetch.py --root mlx_models/yue2-loras --check
yue2-mlx/.venv/bin/python scripts/music/yue2_lora.py <file.safetensors>   # inspect one
```

### Instrumental, the two recipes

`--instrumental` picks its recipe from one fact: whether the AR adapter is on
disk.

- **With the pack** — Maestro's recipe. The instrumental AR adapter at
  strength 1.0, `cot=full` forced, the lyrics replaced by `[instrumental]`
  (bare section tags, optionally timed — `[chorus 0:15-0:40]` — pass through
  lowercased; anything else becomes `[instrumental]`, because sung prose fed
  to this adapter is what produces a vocal-like track), other LoRAs paused,
  stock acoustic decoder.
- **Without it** — the tag skeleton that shipped in v4.16, which was validated
  by ear. A queued song never fails because an optional download is missing.

The sidecar records which one ran, under `instrumental_recipe`.

### Measured

Four 30-second songs on an M4 Max 64 GB, 8-bit AR, Final (32 steps), one arm
per pair carrying the adapter, everything else — prompt, seed, steps,
precision — held fixed. Vocal content is the RMS of demucs `htdemucs_ft`'s
vocals stem relative to the mix RMS.

**Does the instrumental adapter remove the singing?** Yes — but only where
there was singing to remove. On an instrumental *prompt* the base model
already writes no vocal line, so that pair cannot discriminate (both stems are
silence at ~-67 dB). On a prompt that names a singer, with `[instrumental]` as
the lyrics and the seed held, the difference is the whole point:

| Pair · seed | Arm | vocals RMS | vocals ÷ mix | dB vs mix | vocal peak |
|---|---|---|---|---|---|
| vocal prompt · 909090 | AR adapter @1.0 | 0.000237 | 0.0012 | **-58.53** | 0.002 |
| vocal prompt · 909090 | none | 0.068147 | 0.3489 | **-9.15** | 0.682 |
| instrumental prompt · 424242 | AR adapter @1.0 | 0.000031 | 0.0003 | -69.66 | 0.0009 |
| instrumental prompt · 424242 | none | 0.000024 | 0.0005 | -66.29 | 0.0003 |

**49.4 dB** less vocal content at the same seed and the same words.

**`nar_lora_joint_v9` on a sung song** (same seed, same lyrics): it loads,
synthesises and decodes cleanly, and it changes the audio (different SHA-256),
but the vocal share barely moves — 0.4888 with, 0.5000 without (-6.22 dB vs
-6.02 dB). Expected: it was trained beside `tokenizer_head_joint_v9`, and
without that head it is reading a token dialect it was not adapted to.

**What an adapter costs.** Per-stage, from the sidecars:

| Stage | Base | With adapter | Delta |
|---|---|---|---|
| AR score planning | 112.5 tok/s | 93.4 tok/s | -17% |
| AR semantic | 114.0 tok/s | 95.4 tok/s | -16% |
| Acoustic (NAR adapter) | 13.36 s | 14.96 s | +12% |
| MLX peak | 10.48 GiB | 10.61 GiB (AR) / 10.55 GiB (NAR) | +0.13 / +0.07 |

Wall-clock per song is dominated by how long a score the planner writes, not
by the adapter: the four 30-second songs ran 29.4 s to 63.5 s, and the longest
was a *base* arm whose score came out three times longer.

### Flags and wiring

Runner: `--lora PATH[:STRENGTH]` (repeatable), `--lora-mode joint|separate`,
`--lora-trigger WORD` (repeatable, added to the style once), `--lora-dir`.
Panel: `music_loras` on the form is `id:strength` pairs, each resolved inside
the LoRA folder before a job exists — a form can name a file, never a path.
`GET /music/lora/status` and `POST /music/lora/fetch` mirror the cover pack's
pair, and `/status.music.loras` carries the same block for the first paint.
The studio shows a **Voice & style LoRA** section (multi-select, 0–1.5
strength each) and a one-line download affordance under the Instrumental pill.

### What is not done

- **`separate` bundles are implemented but untested against a real artist
  bundle** — none is published. The decoder-blend arithmetic is covered by
  tests, not by audio.
- **The `_comfyui` adapter layout** (block-diagonal fused qkv / gate_up) is
  not read. The plain layout is.

## The real-audio tokenizer head — YuE2 can listen now

YuE2 writes from words. `tokenizer_head_joint_v9` is the piece that lets a
REAL recording in: an 8-layer transformer that reads MERT-v2-FullSong features
and predicts YuE2's own 32768-way semantic codes. `scripts/music/
yue2_tokenizer.py` is the MLX port. Two things follow from it — a recording can
be re-synthesised through YuE2's decoder, and a recording can be the prefix a
new song continues from.

### The architecture, and where every number came from

`Linear(1024→512)` · learned `pos [1,512,512]` · 8 × `TransformerEncoderLayer(
512, 8 heads, ffn 2048, norm_first, gelu)` · `LayerNorm(512)` ·
`Linear(512→32768)`. 103 BF16 tensors.

None of the preprocessing is guessed. Mothersuperior's `prep_real.py` and
`joint_v6.py` were fetched and read first, and every step below is theirs:

| Stage | What runs |
|---|---|
| audio | mono, 24 kHz, float32; `scipy.signal.resample_poly` by default (what `prep_real.py` used), ffmpeg as the fallback and the `--resampler ffmpeg` alternative — the metadata records which one ran |
| chunking | 30 s chunks; a chunk under 1 s is dropped; each encoded on its own, so MERT attention never crosses a chunk boundary |
| features | `hidden_states[20]`, concatenated in time order, linearly resampled to `round(seconds × 25)` and rounded through float16 — the head was trained on fp16-stored features |
| norm | per **track**, per channel: `(x − mean) / (std + 1e-5)`, numpy's population std, before any windowing |
| window | 512 frames, the learned `pos` sliced (never interpolated) for a short tail |
| tiling | hop 256; a final window pulled back to the end of the track; each window contributes its centre, trimming 128 frames off every interior edge |
| decode | plain `argmax` — no neighbour smoothing; the `sem_nbr_*` tables the repo ships are a training loss (`soft_ce`), not an inference step |

### The two things a port gets silently wrong

Both have identical shapes whether they are right or wrong, so both were
settled numerically rather than argued.

**The fused q,k,v projection.** PyTorch stores one `in_proj_weight [1536,512]`
whose row blocks are q, then k, then v. Any permutation loads without
complaint. Against a torch `TransformerEncoderLayer` built from these exact
tensors, on one random 512-frame window in FP32:

| split order | max-abs-diff |
|---|---|
| **q,k,v** | **3.910e-05** (relative 3.40e-06, argmax agreement 1.0000) |
| q,v,k / k,q,v / k,v,q / v,q,k / v,k,q | 15.6 / 17.5 / 15.2 / 16.0 / 15.1 |

A 137-frame window gives 3.076e-05, so the `pos` slice is right too.

**The MERT layer.** The head's metadata says "layer 20". transformers collects
a hidden state only AFTER each conformer block — `MERT2Model._encode()` appends
inside the loop and never appends the pre-layer subsampled embedding — so
`hidden_states[20]` is the output of `layers[20]`. `lyra.mert`'s `layer_weights`
vector is one entry LONGER (25 for 24 blocks) because its index 0 IS that
pre-layer embedding. The one-hot goes at **21**. Against the real HF model on
2 s of audio:

| `layer_weights` index | max-abs-diff vs `hidden_states[20]` |
|---|---|
| 19 | 7.3160 |
| 20 | 12.180 |
| **21** | **1.135e-04** (relative 3.75e-06) |
| 22 | 19.839 |

Code statistics do **not** separate these: layer 19 produces 594 unique codes
with repeat 0.072, layer 20 produces 565 with repeat 0.087. Both look healthy.
Only the reference comparison decides it.

### Precision and the resampler, measured

FP32 throughout, rather than the training-time `torch.autocast` bfloat16:
more accurate and reproducible. On 30 s of a real recording the two paths
agree on **97.07 %** of frames, and polyphase-vs-ffmpeg resampling on
**94.67 %**. Layer 19 instead of 20 agrees on 25.33 % — a different song.

### The round trip

```sh
yue2-mlx/.venv/bin/python scripts/music/yue2_tokenizer.py encode SONG.mp3 \
    --start 60 --seconds 30 --out codes.npy \
    --roundtrip out.wav --model-dir mlx_models/yue2/generator \
    --vae-dir mlx_models/yue2/vae --seed 4242 \
    [--lora mlx_models/yue2-loras/nar_lora_joint_v9.bf16.safetensors --lora-mode joint]
```

`encode` alone writes `codes.npy` (int32, 25 per second) and a sidecar with the
head identity, the feature summary and the code statistics. `--roundtrip` also
synthesises them through the NAR and the VAE decoder and measures the result
against the SAME excerpt of the original.

Measured on seconds 60–90 of a real recording, seed 4242, 32 ODE steps:

| Arm | log-mel | chroma r | synthesis | MLX peak |
|---|---|---|---|---|
| stock decoder | **7.935 dB** | **0.8832** | 13.50 s | 10.48 GiB |
| `nar_lora_joint_v9`, `joint` | **7.861 dB** | 0.8171 | 14.76 s | 10.55 GiB |
| `nar_lora_joint_v9`, `separate` | 10.389 dB | 0.8314 | 14.76 s | 10.55 GiB |

The controls are what make those numbers mean something:

| Control | log-mel | chroma r |
|---|---|---|
| the excerpt against itself | 0.000 dB | 1.0000 |
| a DIFFERENT 30 s of the same record | 13.399 dB | 0.7020 |
| white noise at the same level | 26.283 dB | 0.1844 |
| silence | 101.507 dB | — |

So the round trip lands well inside "the same passage" and nowhere near "a
different passage of the same song". `joint` is a hair closer spectrally than
the stock decoder and a little further away harmonically; `separate` — which
replaces the decoder's `vae2llm`/`llm2vae` outright — is clearly worse here,
which is what Maestro's own semantics predict for a file that is not an artist
bundle. The encode itself is **2.96 s for 30 s** of audio.

Code statistics for that excerpt: 750 frames, 565 unique codes, top code
0.9 % of frames, repeat 0.087, mean run 1.09 — a live stream, not a collapsed
one.

### `--audio-prompt`: a song that continues a recording

```sh
yue2-mlx/.venv/bin/python scripts/music/yue2_run.py ... \
    --audio-prompt TAKE.wav --audio-prompt-seconds 10 [--audio-prompt-start 60] \
    --lora-dir mlx_models/yue2-loras
```

The recording's first seconds become codes; the codes are appended to the
request prefix as ordinary codec tokens, so the AR model prefills on them
exactly as it would on its own output and writes on from there. The codes stay
at the head of the token stream; the plan keeps its canonical prefix, which is
what `synthesize()` validates against the request. Listening happens **before**
the generator is built and the MERT frontend is dropped afterwards — same
order, same reason, as the cover path.

Two honest edges: the repetition-penalty window and the minimum-length counter
both restart at the boundary, because `generate_tokens()` takes no prior
history. The model sees the prompt; the sampler does not.

Measured, 10 s prompt, `cot=off`, seed 4242, budget 30 s:

| | audio | wall | ended naturally | chroma r vs the prompt |
|---|---|---|---|---|
| with the prompt | 39.52 s (250 prompt + 738 written) | 37.59 s (3.96 s listening) | yes | **0.5827** |
| same seed, no prompt | 30.00 s | 30.18 s | no (hit the budget) | 0.1011 |

The prompted song's first 10 s reconstruct the source — 10.503 dB log-mel,
chroma 0.8752, against 13.399 dB / 0.702 for a different excerpt. The two
generated token streams agree on **0.14 %** of frames: it is a different song,
not a perturbed one. MLX peak 10.48 GiB on both arms.

The sidecar records `audio_prompt`: path, seconds, start, frames, head version
and sha256, MERT layer, resampler, code statistics, listening time and how many
frames the model wrote.

### What is not done here

- **No panel surface.** This is runner-and-CLI only; the studio has no
  "continue this recording" control yet.
- **Instance norm is per track, so the same seconds encode differently
  depending on what they were cut out of** — the same 10 s scored inside a 30 s
  track agrees with its standalone encode on 34.40 % of frames. That is
  faithful to the training recipe, and it means a prompt should be cut once and
  reused, not re-derived from different excerpts.
- **`sem_nbr_cos.npy` / `sem_nbr_idx.npy` are downloaded but unused.** They are
  the training soft-CE neighbour tables.
- **No training.** Adapting the head to a voice is the Maestro "My Music" road
  and is not implemented; see `docs/STATE.md` for the estimate.

## Train a voice

```sh
yue2-mlx/.venv/bin/python scripts/music/yue2_train_voice.py \
    --recordings RECORDINGS/ --lyrics LYRICS/ --trigger myvoice \
    --out runs/myvoice --style "the style every window trains with" \
    --reg-pack minted_regularizer_pack.pt \
    --steps 400 --rank 32 --lr 1e-4 --dialect direct
```

Out come `runs/myvoice/myvoice.safetensors` and `myvoice.json`. Drop both in
`mlx_models/yue2-loras/user/` (or pass `--install`) and the studio's LoRA
picker offers it; on the runner it is
`--lora myvoice.safetensors:1.0 --lora-trigger myvoice`.

**What is being trained.** An AR-branch LoRA, all 196 projections, one rank —
the same objective as Mothersuperior's `ar_lora.py` and Maestro's
`artist_training.py`. The recordings become YuE2 semantic codes through
`tokenizer_head_joint_v9`, and the AR model is taught to *write* that code
stream from style + lyrics:

```
sequence = token_prefixes(SongRequest(style, lyrics, …)) + [code + CODEC_OFFSET …]
loss     = cross-entropy on the CODE TAIL ONLY
```

The prefix comes from the engine's own `token_prefixes`, so training sees the
byte-identical thing inference builds. **No conversion step anywhere** — this
is the whole reason it does not sound robotic. A voice converter resynthesises
a stranger's performance frame by frame and inherits their phrasing, vibrato
onset and consonant attack (`notes/yue2/MAESTRO_VOICE_STUDY.md` §4); this
model sings its own performance.

### Windows, and why the lyrics are sliced

A recording is cut into `--window-seconds` windows (default 20.48 s = Maestro's
512 frames) at `--hop-seconds`, and **each window gets the lyrics that were
sung inside it**. That is not a detail. If the prefix carries the whole song's
words while the codes are one slice of it, the pair is a lie: the model learns
"this style → this singer's codes" and never learns "these words → these
sounds", so it reproduces trained fragments instead of pronouncing new lyrics.

Timings come from a `<stem>.lines.json` sidecar beside the recording —
`{"lines": [{"text": …, "onset": seconds, "end": seconds, "section": "[Verse]"}]}`
— which is what a forced aligner or a whisper pass with word timestamps
produces. Without one, the sheet is cut by the window's share of the
recording, which is cruder but still keeps each prefix roughly honest about
its own codes. A window whose lyrics cannot be located trains on an empty
lyric block rather than on somebody else's line.

The trainer is pure: it never runs demucs, whisper or SheetSage2. Sidecars come
in from outside, and `--emit-windows DIR` writes the window audio so those
tools can be pointed at exactly the seconds each window trains on.

### Two dialects

| `--dialect` | prefix | generate with |
|---|---|---|
| `direct` (default) | `cot="off"` — no score | `--mode off` |
| `score` | `cot="melody"`, with the window's real ABC score inside the prefix | `--mode melody --abc-file SCORE.abc` |

`SongRequest` refuses `abc` with `cot="off"`, so a `direct` voice cannot be
driven by a score in Direct mode. It **can** be driven by one in `--mode
melody`, and measured, that is the best way to use it (see below) — the claim
in `MAESTRO_VOICE_STUDY.md` §6.1 that the two are mutually exclusive is a
Maestro UI convention, not an engine constraint. The `score` dialect trains the
prefix the model will actually be given. To build it, transcribe each window
first:

```sh
python scripts/music/yue2_train_voice.py --recordings SONG.wav --out runs/x \
    --emit-windows windows/
for w in windows/*.wav; do
  python scripts/music/yue2_run.py --model-dir … --vae-dir … \
      --transcribe-only --source-audio "$w" --cover-task melody-vocal \
      --cover-seconds 21 --transcription-cache mlx_models/yue2-cover \
      --output "scores/$(basename "$w" .wav).abc"
done
python scripts/music/yue2_train_voice.py … --dialect score --scores scores/
```

### The regulariser

`--reg-pack` is Maestro's pinned `minted_regularizer_pack.pt` (101.9 MB, codes
only): base-dialect songs mixed against the artist at `--artist-fraction`, so
the AR stays inside YuE2's own dialect instead of drifting somewhere the
decoder cannot follow. `--reg-cache` writes a converted `.npz` on first use, so
a machine without torch can still train. Without it the trainer says so and
carries on; the held-out minted cross-entropy in the log is how you check the
drift.

### The sidecar

`<name>.json` records the trigger, the dialect and the generation flags that
match it, the base model and its upstream commit, steps / rank / lr / seed /
window geometry, a fingerprint of the training data, how many windows carried
lyrics and how many carried a score, and the whole loss curve.

### Measured: strength is not optional, and neither is the window

One recording (≈6 min of a separated vocal), 35 windows, rank 32, lr 1e-4, 400
steps, 21.8 min on an M4 Max. Scored on lyrics the model had never seen, by
whisper on the take's full mix: how many of 8 planted words are heard, and word
error rate against the sheet. `sim` is resemblyzer cosine against two reference
passages by the same singer.

| | strength 1.0 | strength 0.7 |
|---|---|---|
| whole-song lyrics, random code window | 0/8, WER 1.000 | 4/8, WER **1.000** |
| **per-window lyrics** | 0/8, WER 0.896 | **8/8, WER 0.214** |

The base model scores 7/8 at WER 0.325, so the best cell is **more accurate at
the given words than the stock model**, at voice similarity 0.8205 against
stock's 0.7718.

Both ingredients matter and neither substitutes for the other. At strength 1.0
the adapter overrides the lyric conditioning completely and nothing is
pronounced, whichever way it was trained. At 0.7 the whole-song recipe sings
*more* words (243 vs 180) and still scores WER 1.000 — it has learned to sing,
not to sing what it is given. 0.5 is too far the other way: lyrics survive but
the voice does not (sim 0.7272, register −6.25 st).

**So: apply a trained voice at about 0.7**, and treat 1.0 as the setting that
silences the lyrics. The number is one recording deep — re-measure on yours.

### Measured: give it a score

With a supplied score (`--mode melody --abc-file`), the same `direct`-dialect
adapter at strength 1.0 does better than it ever does in Direct mode:

| same score, same lyrics, same seed | voice sim | register | in-tune folded | in-tune absolute | robot words |
|---|---|---|---|---|---|
| stock, no LoRA | 0.7302 | −9.15 st | 96.1 % | **1.3 %** | 7/8 |
| trained voice @1.0 | **0.8161** | +2.65 st | 83.1 % | **76.6 %** | **6/8** |

Read the two octave columns. Stock follows the written pitch classes almost
perfectly while singing them **an octave down** — 1.3 % of notes land in the
written octave. The trained voice sings the score in the octave it is written
in (76.6 %), because the voice it learned lives there. And it holds 6/8 lyrics
and 0.8161 voice similarity *at the same time*, which Direct mode never manages
at full strength. A supplied score anchors the melody, which leaves the lyric
conditioning intact.

### Measured: the `score` dialect, and the octave it learned

Trained on 13 windows whose prefixes carried SheetSage2's `melody-vocal`
transcription of the same seconds, then handed the same score as above:

| same score, lyrics, seed | voice sim | register | in-tune folded | in-tune absolute | robot words |
|---|---|---|---|---|---|
| `direct` voice @1.0 (above) | **0.8161** | +2.65 st | 83.1 % | **76.6 %** | 6/8 |
| `score` voice, step 100 | 0.7774 | −9.35 st | **97.4 %** | 3.9 % | 5/8 |
| `score` voice, step 400 | 0.8004 | −11.15 st | 76.6 % | 1.3 % | 6/8 |

**It follows the pitch classes and drops the octave**, like the stock voice
does — and the cause is measurable. SheetSage2 writes the melody **+12.7 st
above what was sung** (median over the 13 windows; 9 of them sit at +12.1 to
+12.9). Every training pair therefore said "a written note means sing it an
octave lower", and a score written at pitch is then sung an octave down.

So **today the `direct` dialect under a score is the better recipe**, and
`--dialect score` should not be used on SheetSage2 transcriptions as they come.
The fix is mechanical and not yet in the trainer: transpose each window's score
down to the octave that was sung (or estimate the written-vs-sung offset per
window and correct it) before training. The `score` curve also memorises far
faster — artist CE 0.021 at step 400 against the `direct` run's 0.753 — because
a unique score in each prefix is a near-perfect key to that window's codes; it
needs more windows than one recording gives.

### What this can and cannot do

- **Can**: move vocal identity a long way from a prompt alone, with no
  converter in the chain, and keep the singer's register.
- **Can**: pronounce lyrics it has never seen more accurately than the base
  model — at strength ~0.7, trained on windows whose prefixes told the truth
  about their codes. At 1.0 it pronounces nothing.
- **Can**: follow a supplied score, in the written octave, better than the
  stock voice does.
- **Cannot yet**: train usefully in the `score` dialect on raw SheetSage2
  transcriptions — they are written an octave above what was sung (above).
- **Cannot**: be trusted to generalise from one song. Maestro wants 2–50 songs
  and at least one held out; one track gives a memorisation check, not a
  generalisation check.
- **Cannot**: change timbre the way the expensive half of Maestro's recipe
  does. That is head + NAR adaptation with a decoded-waveform loss, and it
  needs an MLX VAE *encoder*, a differentiable NAR path and the unpublished
  minted anchor corpus — see `docs/STATE.md`.
- **No panel surface.** CLI plus the LoRA picker; the studio has no training
  screen.

## Outputs and the video handoff

The runner writes **48 kHz stereo PCM-24 WAV** and its `.wav.json` sidecar.
Audio cards use the existing outputs stream, with native audio controls.
The info panel shows style, lyrics, seed, actual length, natural ending
versus the ceiling, and the ABC score. The sidecar also records the job id,
engine pin and weight-source revisions. Delete/Trash carries it with the WAV.

**Music video** loads a song into the Music video pane and switches to that
mode. It does not submit a render. Settings → Storage can remove the music
weights; doing so hides Compose and leaves Music video available.

## Music video — the song and the pictures

The Audio tab's second mode turns a song plus a set of tagged pictures into a
storyboard. The song is dropped in or picked from the library; each picture
carries a role — **Singer** (filmed singing), **Instrument** and **Room**
(B-roll) — and an optional line of its own; one "Look" line goes on every
shot.

`POST /music/video/plan` reads the beat (`storyboard_edit.beat_map`), works
out the song's sections, and writes an ordinary storyboard board.

**Where the song's structure comes from — strongest first, and each rung is
only reached when the one above it is silent:**

| Rung | Field | What it is |
|---|---|---|
| 1 | `sections` | A JSON list of `{start, end, kind: vocal\|instrumental, label?}` in seconds — the structure somebody **measured**. Sorted, gaps closed, clamped to the song, and the first section is always pulled to 0:00 (the film plays from 0:00, so a later start would put every mouth on the wrong words). Overlaps, a missing `kind` and an end before its start are refused by name. |
| 2 | `lyrics` / `score_abc` | The same two texts a YuE2 sidecar carries, in the request — for a song that was re-cut, or whose sidecar belongs to an earlier take. |
| 3 | the sidecar | A YuE2 song's sections are arithmetic rather than analysis: `score_abc` carries `M:`/`Q:` headers and `% section` comments, so bars × tempo is the boundary and the lyrics' bracket tags say which sections anybody sings in. |
| 4 | the classifier | A song from anywhere else is split into 8-bar downbeat phrases and classified on its 1–4 kHz band energy. |

Vocal sections become **singing shots** — mode `a2v`, alternating Singer
pictures, `frames` the largest LTX cell inside a 10–20 s window, and
`audio_start_time` set to that shot's own place in the song. Everything else
becomes **B-roll** — a still with a slow move on it, 3–7 s, cut on the
downbeats. The shots tile the song with no gaps, and every length is the one
that will really render (a frame count on the 8k+1 grid), so the clip placed
at 1:42 in the finished film is the clip that was rendered against 1:42 of
the song.

**The duration axis is the panel's table cut down to the delivery canvas.**
`LTX_LENGTHS` marks the 20 s / 481-frame cell as Quick-only (640×480 holds;
1024×576 dies around frame 454, issue #46), and the plan route reads the
board's final pass — Settings → `storyboard_final_quality`, `standard` out of
the box — so at Standard the longest planned shot is 10 s and a note says
why. The quality used is echoed back as `quality` and stored on the board.
Before this, a long vocal section produced 481-frame a2v shots on a Standard
board: an hour of render each, none of which could finish.

**Casting the B-roll.** Each picture may carry `use` and `weight` beside its
`role` and `prompt`:

* `"use": "open"` takes the film's **first** B-roll shot, `"close"` its
  **last**, and `"any"` (the default) takes its turn in the rotation. Several
  of each are placed in the order given, so the last picture pinned `close` is
  the last shot of the film. A pin moves who is in frame and nothing about the
  clock — durations, `film_start` and every `audio_start_time` are untouched.
* `"weight": 1..20` (default 1) buys a picture that many turns in the
  rotation, spread rather than clumped: everybody once, then everybody with
  weight ≥ 2 again, and so on.

**Casting the singing.** The rotation alternates so the angle changes; which
picture **leads** it is decided by face size. A picture may carry
`"face_frac": 0..1` — how much of the frame the face fills — and the biggest
measured face opens the rotation, because a singing shot is a close-up and a
mouth at a quarter of the frame is mush where at half of it it is legible.
The number is never invented from the picture: the cheap proxies (aspect
ratio, short side) cannot tell a 1:1 crop of a face from a 1:1 crop of a
room, so a picture nobody measured keeps the place it was given rather than
being sorted by a made-up value. (The panel's captioner already looks at
every upload and is where this can be filled in later.) **A pin always
wins** — a `use` pin or a per-shot image pin is the caller saying which
picture goes where, and the face rule does not overrule it.

**A picture for one shot.** A `shots` row may carry `"image"` beside (or
instead of) its `prompt`: `[{"n": 7, "image": "/…/panel_uploads/face2.png"}]`
recasts that one shot. Same containment rule as everything else, and like the
`use` pins it moves who is in frame and nothing about the clock. A pin naming
a picture that is not in the cast is a note, not a refusal.

**A line for one shot.** `shots` is either `[{"n": 24, "prompt": "…"}]` or
`{"24": "…"}` and replaces the direction of that shot alone — the picture's
own `prompt` stays the default everywhere else it is cast, which is what lets
the last shot say "the stage lights slowly fading down to black" while the
same wide is used three times earlier without a fade. Each shot row comes
back with `prompt_override` so a caller re-planning can see which lines it
already owns. A line written for a shot number the plan does not have is a
note, not a refusal.

Every path a picture or a song takes in is the same containment rule the
whole panel uses — it resolves inside the outputs folder or the uploads
folder, or it is not used — and the refusal now names both folders and
`POST /upload` (multipart, field `image` or `audio`), which returns the path
to use.

From there it is an ordinary board: Render all, re-roll a shot, edit the
prompts. **Film with the song** (`POST /music/video/film`) exports it with
the song as the bed in `replace` mode — every clip's own audio is dropped and
the song plays unbroken, which the lip-sync survives because each singing
shot was rendered against its own segment of that file. The auto-editor is
off there on purpose: the board's cuts are already on the grid, and a second
pass over them would slide the singing shots off their own words.

### The a2v lane — what makes a mouth actually move

Three things decide whether a singing shot lip-syncs, and only one of them is
the sampler. The numbers below were measured on ONE shot at one seed with the
image, the audio window and the prompt held fixed, scored as the per-second
correlation between the inner-lip aperture and the vocal-band energy of the
same audio (`~/AI/projects/phosphene/notes/yue2/MAESTRO_LIPSYNC_STUDY.md`).

**1. Audio guidance has a different meaning on each lane, and the panel's old
default switched it off.** The Q4 distilled pipeline multiplies the audio
tokens by `audio_conditioning_scale`, so **1.0 is the identity — audio fully
on**. The Q8 two-stage pipeline has no such parameter at all: the helper
reroutes the slider onto the stage-1 guider's `modality_scale`, whose term is
`(value - 1) × (cond - uncond)`, so **1.0 makes it exactly zero** and the
vendored engine's own default is **3.0**. The panel stamped 1.0 on every a2v
job, so every Q8 a2v clip rendered at the default had audio guidance off in
all but name: **-0.065**, which is *worse* than the same clip scored against
deliberately wrong audio. At 3.0, nothing else changed: **+0.128**, and
positive seconds rose from 44% to 67%.

The form no longer sends a number unless you move the slider — it starts on
**Auto** and the panel fills in the lane's own default at queue time, which is
the only place that knows which pipeline will run (including the Q8→Q4
fallback when the Q8 files are missing). A value you set is passed through
untouched on both lanes, so a saved job or board keeps exactly what it had.

**2. The model should listen to the vocal, not the band.** Drums, bass and
guitar reach the audio encoder as energy that can be read as syllables, so the
model hedges — the mouth moves a little, all the time, on everything.
Conditioning on a separated vocal stem was the only candidate with a positive
zero-lag correlation (**+0.156**) and **doubled** how much the mouth moved.

Two fields, both optional: `audio_stem` names a stem you already have, and
`audio_stem_auto` asks the panel to make one with demucs. Whichever is used,
**the original song is muxed back over the finished clip** — the video stream
is copied, not re-encoded — so nothing the audience hears changes. The
separation is cached by path + size + mtime, so a twelve-shot film separates
once. demucs is an OPTIONAL extra (`scripts/pinokio/a2v_stems_deps.sh`, into
the engine venv); without it the auto option degrades to the full mix with a
note naming the installer, and never fails a queued job. `POST
/music/video/plan` takes `vocal_stem` and rides it onto every singing shot.

**3. The prompt must not say that nothing moves.** LTX reads a stillness word
as "nothing in the scene moves" — *including the singer's lips* — and returns
a freeze frame with a closed mouth. The planner used to write two of them
mechanically on every shot ("the frame never moves - no pan, no push-in, no
reframing" and "with no new movement of any kind"), and `storyboard.py`
*skipped* prompt validation for a2v, so the audio-driven mode got less
checking than every other one. The law now lives in `storyboard.py`, where
both planners and the validator read the same copy:

* a stillness phrase on an a2v shot is a **blocking validation error**, with
  the phrase named;
* a clause about the **camera** is deleted on the way out (an audio-driven
  shot does not want a restraint sentence at all) and a clause about a
  **person** is rewritten in place so it keeps its subject;
* the creative direction is capped at **40 words** — more set-dressing makes
  a more static clip and buries the vocal verb — and the cap falls on the
  direction only, never on the face law or the style;
* a literal sync contract is appended **last, after every polish pass**, so a
  re-roll or an enhance cannot paraphrase it away: *"The singer lip-syncs
  every vocal syllable to the supplied soundtrack…"* — "lip-syncs" rather
  than "sings", because a generic vocal verb is read as unconstrained
  performance;
* a window with **no vocal in it** (read off the sections) gets the opposite
  contract instead — *"the mouth stays at rest with relaxed closed lips"* —
  because saying nothing is not neutral: the model keeps the mouth working
  through an instrumental bar.

`compose_shot_prompt` is the last line of defence: every shot passes through
it on the way to a render, so a board planned before this law existed still
reaches the model with the contract on it.

The planner is `music_video.py`, pure and panel-free; the tests are
`test_music_video.py`, `test_music_video_routes.py`,
`test_music_video_minshot.py` and `test_a2v_sync_fixes.py`.

## License and attribution

Weights use CC BY-NC 4.0 plus the upstream individual-creator permission:
individual creators may monetize their songs; companies need a license for
the weights. Read [the full current model license](../LICENSES/YuE2-MODEL_LICENSE.txt).
The port and upstream code are Apache-2.0; Oobleck VAE and SnakeBeta are MIT.
[The notice](../LICENSES/NOTICE-yue2.md) identifies the source and conversion.
BF16 weights are byte-identical to their source; 8-bit AR is a quantization.
The Compose footer credits Multimodal Art Projection and vanch007.

## Validation and troubleshooting

`test_yue2_lora.py` covers the adapter contract: one canonical spelling for
every key layout, shape validation against the model that will run it, the
wrapper's arithmetic against `W x + s·B(A x)` written out by hand on a plain
*and* a quantised base, the parameter tree surviving a wrap unchanged (the
`load_nar` guard), `joint` vs `separate` decoder I/O, the instrumental recipe,
and the form → argv round trip. It loads no weights.

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

### When another render holds the GPU

A song queued while a command-line job holds `/tmp/phosphene_gpu.lock` (or the lab's
`.gpu_lock` directory) no longer fails with "Retry when it has finished": it waits in
the queue with the phase **Waiting for the GPU**, Stop ends the wait, and only a wait
longer than `PHOSPHENE_MUSIC_GPU_WAIT_S` (default 3600 s) refuses, naming the lock.
By default the panel only reads these locks. A panel that shares the machine with
command-line GPU jobs can set `PHOSPHENE_MUSIC_TAKES_GPU_LOCK=1`: it then creates the
file lock atomically while a song renders, so those jobs queue behind it, and removes
only the lock it wrote.
