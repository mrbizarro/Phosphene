# Speech — text to speech in preset or character voices

**Audio → Speech** turns a script into one WAV, in one of nine preset voices (with a delivery
the user describes in words) or in a saved character's own voice, cloned from its voice sample.
The WAV lands in Outputs and goes straight to Lip-sync ("Use this audio for…"). It is a free
feature, an optional install of ~6 GB, and it runs on every Mac Phosphene supports (16 GB up).

User doc: `webapp/docs/speech.md`. HTTP reference: `docs/API.md` → Speech. Agent door:
`docs/AGENT_API.md` → Speech for agents.

## Choosing the model (2026-10-22)

The bar: weights and code under licences that allow free redistribution and use from an MIT
app with no non-commercial trap; native on Apple Silicon (MLX preferred); named preset voices
AND cloning from a short clip; emotion that can be directed; fits a 16 GB Mac beside the panel.

| Candidate | Licence (code / weights) | Mac route | Presets · clone · emotion | Verdict |
|---|---|---|---|---|
| **Qwen3-TTS 12 Hz 1.7B** (Qwen team) | Apache-2.0 / Apache-2.0 | MLX via mlx-audio (MIT); 8-bit conversions by mlx-community | 9 named speakers · clone from ~3 s (Base) · free-text delivery instruction (CustomVoice) · 10 languages | **Chosen.** The only candidate that clears every bar at once. |
| Chatterbox (Resemble AI) | MIT / MIT | MLX via mlx-audio | one stock voice · clone · an "exaggeration" knob | Runner-up for cloning; no named presets, emotion is a single intensity knob. |
| Kokoro-82M | Apache-2.0 / Apache-2.0 | MLX via mlx-audio | many presets · **no cloning** · no emotion control | Rejected: no character voice. |
| Sesame CSM-1B | Apache-2.0 / Apache-2.0 | MLX via mlx-audio | context-conditioned · clone · — | Rejected: the weights repo is gated (HF login + terms) — no one-click install. |
| F5-TTS | MIT / **CC-BY-NC-4.0** | f5-tts-mlx | clone | Rejected: non-commercial weights. |
| Dia 1.6B / Dia2 2B (Nari Labs) | Apache-2.0 / Apache-2.0 | MLX via mlx-audio | random voice per seed · clone · nonverbal tags; English only | Rejected: no stable named voices, English only. |
| Orpheus 3B (Canopy) | Apache-2.0 finetune of **Llama-3.2-3B** | MLX weights | 8 voices · emotion tags | Rejected: Llama 3.2 Community Licence underneath, gated repo, 3B. |
| Zonos v0.1 / ZONOS2 (Zyphra) | Apache-2.0 | v0.1 needs espeak-ng (GPL) phonemes; ZONOS2 MLX is ~4 shards | clone · emotion vector | Rejected: GPL system dependency (v0.1), size (ZONOS2). |
| Fish Audio S1 / Drama 3 | **Fish Audio Research Licence** | mlx-speech | clone · rich tags | Rejected: non-commercial. |
| KugelAudio-0-open | MIT | — | multi-speaker | Rejected: 18.7 GB of weights, no MLX route; no 16 GB Mac. |

Licences were read from each
Hugging Face model card and repository on 2026-10-22; the pins are in
`scripts/speech/speech_manifest.json`.

### The audition

Same three lines, same machine (M4 Max 64 GB, GPU, mlx 0.32.4, mlx-audio 0.5.8):

| Voice | Similarity to the reference | Words heard right | Pitch (ref ~108 Hz) | Time per line (6–10 s of audio) | MLX peak |
|---|---|---|---|---|---|
| Qwen3-TTS Base, clip + its words (in-context) | **0.90 · 0.92 · 0.90** | 3/3, WER 0 | 86–103 Hz | 1.5–2.2 s | 8.3–9.1 GB* |
| Qwen3-TTS Base, clip only (speaker embedding) | 0.90 · 0.88 · 0.89 | 3/3, WER 0 | 85–90 Hz | 1.2–2.1 s | 6.7–7.3 GB |
| Chatterbox (fp16), clip | 0.92 · 0.95 · 0.93 | 3/3, WER 0 | 91–119 Hz | 2.7–4.2 s | 5.0–5.3 GB |
| Qwen3-TTS CustomVoice presets (not clones; a floor) | 0.49–0.61 | 3/3, WER 0 | 141–188 Hz | 1.1–3.4 s | 6.5–6.9 GB |

\* the audition ran both clone modes in one process with MLX's default cache; the panel's runner
caps the cache at 256 MB (see Memory below).

Chatterbox matches the timbre about 0.03 closer, at roughly twice the time and with no named
presets and no worded delivery — a second engine and a second 2.6 GB download for that margin is
not worth it in v1; it is the first candidate if users ask for closer clones. Through the panel's
own doors (`/speech/generate`, the character's untrimmed 23.7 s training sample, no words given)
the Bizarro line scored 0.85; with the clip's words the same voice scores 0.90–0.92, which is why
the form offers a words field for characters too.

Similarity is the cosine similarity of Resemblyzer speaker embeddings between each line and the
14 s reference clip (the sample character Bizarro's voice); "words heard right" is Whisper
large-v3-turbo's transcript against the script; pitch is the median F0 (pYIN).

## Engine and install

| Item | Default | Override |
|---|---|---|
| Engine venv (Python 3.12) | `speech-mlx/.venv` | `LTX_SPEECH_ROOT` |
| Weights | `mlx_models/speech/{customvoice,base}` | `LTX_SPEECH_MODELS` |
| Known-good packages | `scripts/speech/speech_constraints.txt` (mlx-audio 0.5.8, mlx 0.32.4, every package exact) | — |
| Weight pins | `scripts/speech/speech_manifest.json` (repo, 40-hex revision, size + sha256 per file) | — |
| Runner | `scripts/speech/speech_run.py` | owned by the panel |

Its own venv on purpose: mlx-audio needs mlx ≥ 0.32 and LTX holds 0.31.1 (CLAUDE.md §4); the
H3 and music venvs are optional, and Speech must work without either.

**Install** (`POST /speech/install`, the card on Audio → Speech): `speech_setup.sh preflight`
(16 GB memory, 2 GB free for the engine) → `venv` (uv, Python 3.12) → `packages` (uv, `-c` the
constraints file, an import probe, then a marker in the venv holding the constraints' sha256 —
the panel calls the engine ready only when that marker matches, so a venv left half-installed by
a Stop, or made stale by an Update that moves the constraints, is offered the install again) → `speech_fetch.py` (each file at its pinned revision through
`hf_hub_download`, sha256-verified, moved into place; it checks free space against the bytes
still to fetch, minus what a stopped install already staged, plus 2 GB; the two checkpoints share the speech
tokenizer byte for byte, so the second copy is a hard link). Every step is idempotent: Install
again resumes, files that verify are kept, a half-downloaded file resumes from the staging
folder. Paths are passed relative to the app folder (uv 0.11.23 splits a `-c` path at spaces —
the Mac app lives under "Application Support"). A failure prints one `FATAL error:` line, which
the card shows verbatim. Progress: `/status.speech_install` (`bytes_done` counts hard links once).

On disk: 5.5 GB of weights + ~0.45 GB of packages. Nothing is re-hosted; `pack_source.json`
records the sources, revisions and hashes.

## How it plugs in

```
POST /queue/add (mode=speech, speech_*)          POST /speech/generate (agent JSON)
  → make_job → speech_engine.speech_params (allowlist) + validate_speech (refusals, consent)
  → worker_loop → run_job_inner → run_speech_job_inner
  → lab GPU locks (maintainer's Mac only, same rule as music) → park LTX helper (< 48 GB Macs)
  → speech-mlx/.venv/bin/python scripts/speech/speech_run.py   (env: speech_engine.child_env)
  → [speech] stage/line/done lines → Now card
  → mlx_outputs/speech_<stamp>_<slug>.wav + .wav.json
```

- **Env.** `child_env()` drops `PYTORCH_ENABLE_MPS_FALLBACK`, `PYTORCH_MPS_FAST_MATH`,
  `PYTHONPATH`, `PYTHONHOME`, `VIRTUAL_ENV`; sets `HF_HUB_OFFLINE=1` (weights are local, a render
  never downloads) and the resolved ffmpeg on PATH (Speed uses `atempo`). The runner pops the two
  MPS vars again before importing anything (phosphene-release §0).
- **Voices.** `preset` → CustomVoice checkpoint, `--voice` + `--style` (the instruction). `character`
  → Base checkpoint with the character's `voice_sample`; `clip` → Base with the uploaded clip. Either
  clone takes the clip's words when given (`--ref-text`: in-context cloning, the model continues the
  recording — closer than the speaker embedding alone; the UI keeps a character's words per browser).
- **Script.** One generation per non-empty line (lines over ~400 characters split at sentence
  ends), joined with 0.35 s of silence, silence-trimmed, peak-normalised to −1 dBFS, 24 kHz mono.
- **Reference clip.** The runner converts it with ffmpeg to a 24 kHz mono WAV of at most 20 s
  (any container a user drops — .m4a, .mov — reads the same; a long clip only slows the clone).
- **Stop** kills the runner's process group (`STATE.speech_pgid`); nothing is published. A kill
  nobody asked for (macOS reclaiming memory) is a failure that says so, not a quiet "stopped".
- **Memory guard.** Before each line the runner checks free memory (`vm_stat`); under 0.6 GiB it
  stops with exit 3 and a sentence telling the user to close apps.
- **Sidecar.** `engine: "speech"`, model, voice, voice_kind, instruct, text, audio_seconds, seed,
  peaks, realtime_factor, params — and for clones `voice_consent` (see below).

## The safety rule

Cloning a voice requires the requester to confirm that the voice is their own or that they have
the speaker's permission. Phosphene's acceptable-use policy (phosphene.video/acceptable-use)
forbids impersonation.

- `validate_speech()` refuses a `character` or `clip` job without `speech_voice_consent` — the UI
  checkbox, `voice_consent: true` on the agent door — and nothing is queued (400).
- `run_speech_job_inner()` refuses it again (a replayed or hand-edited queue entry skips make_job);
  only the boolean `True` the allowlist writes counts — a stored `"false"` is a refusal.
- The confirmation is written into the WAV's sidecar (`voice_consent.confirmed`, `at`, the
  statement), so the file carries the record of what authorised it.
- The UI box sits next to a link to the policy, is never stored, and clears whenever the voice
  changes (another character, another clip, another voice kind).
- There are no celebrity presets: the presets are exactly the model's nine fictional speakers
  (`test_speech.py` pins the list). The agent doc tells agent authors never to set the flag on
  their own initiative.

## Memory and speed

Measured 2026-10-23 on an M4 Max 64 GB through the runner exactly as the panel spawns it
(cache capped at 256 MB). "Footprint" is the process's lifetime peak physical footprint
(`proc_pid_rusage` → `ri_lifetime_max_phys_footprint`: what Activity Monitor counts, Metal buffers
included); "MLX peak" is `mx.get_peak_memory()`.

| Job | Audio | Load | Generate | Realtime | MLX peak | Footprint |
|---|---|---|---|---|---|---|
| Preset (Aiden, Dramatic), 1 line | 7.8 s | 2.2 s | 1.6 s | 4.9x | 6.7 GB | 6.5 GB |
| Preset (Ryan), 5 lines | 34.0 s | 1.2 s | 6.7 s | 5.1x | 7.1 GB | 6.8 GB |
| Clone, speaker embedding (23.7 s sample) | 8.4 s | 1.6 s | 2.0 s | 4.1x | 6.9 GB | 7.4 GB |
| Clone with the clip's words (14 s clip) | 8.8 s | 1.2 s | 2.5 s | 3.5x | 8.8 GB | 9.2 GB |
| Clone with the clip's words, 5 lines | 31.2 s | 1.2 s | 8.2 s | 3.8x | 9.1 GB | 9.1 GB |

Through the panel (queue, sidecar, everything): 3.5–5.5 s per 7–11 s line.

The peak does not grow with the script (lines are generated one at a time) — it is the model, the
speech-tokenizer decode, and for in-context clones the reference's codes. **Worst case 9.2 GB**,
under the ~10.7 GiB of GPU working set macOS gives a 16 GB Mac, with the LTX helper parked first
(< 48 GB Macs) and the runner's free-memory guard as the backstop. Not yet run on a physical
16 GB Mac: a browser with many tabs open there is the risk the guard exists for.

Floor: **16 GB** (`SPEECH_MIN_RAM_GB`), the same everywhere a Mac is told yes or no (the panel,
`speech_setup.sh preflight`, the install card). On Macs under 48 GB the LTX helper is parked
before a line so its resident weights are not competing.

The estimate (`speech_engine.estimate`) prices audio at 2.8 words/s and wall time as a cold load
plus a per-second rate measured here, scaled by the chip table; Speed changes the file's length,
not the time to make it (tempo is applied afterwards). It is always labelled an estimate.

## Validation

- `test_speech.py`: the allowlist and clamps, refusals, the consent rule at both gates and in the
  sidecar, the preset list, the runner env and argv, the progress protocol, the manifest pins and
  the offline check, the worker against a fake runner, the agent door's mapping, routes, UI, docs.
- Real lines and a lip-sync clip rendered through the panel's own doors (2026-10-22).

## Credits and licences

Qwen3-TTS by the Qwen team, Alibaba Cloud — Apache-2.0
(https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice). 8-bit MLX conversions by
mlx-community (Apache-2.0). mlx-audio by Prince Canuma — MIT (https://github.com/Blaizzy/mlx-audio).
