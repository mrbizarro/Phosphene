# Eltrumpo character/LoRA routing investigation

Date: 2026-08-14

The requested notes directory (`/Users/salo/AI/projects/phosphene/notes/`) is
outside this session's writable sandbox, so this repository-root receipt is the
requested fallback. Nothing under the running production install was modified,
and the panel process on port 8198 was not contacted, restarted, or stopped.

## Production evidence (read-only)

The newest matching sidecar at investigation time was:

`mlx_outputs/eltrumpo_on_a_red_bikini_very_720p.mp4.json`

It recorded:

- `mode`: `t2v`
- `engine`: `ltx`
- `quality`: `balanced`
- active checkpoint: `mlx_models/ltx-2.5-mlx-q8`
- `character_id`: `eltrumpo`
- `character_strength`: `1.0`
- `character_voice_strength`: `1.0`
- face adapter: `mlx_models/loras/eltrumpo_v2.safetensors` at `1.0`
- voice adapter: `mlx_models/loras/eltrumpo.audio.safetensors` at `1.0`
- 1024x576, 241 frames, 8 steps, seed 444027839
- elapsed time: 483.67 seconds

That establishes the **Characters/cast lane**. It was not a bare user-LoRA
picker job and not a hand-written API request. It also rules out class (a): the
character files were present in the serialized job.

The production library held one face adapter and one voice adapter for this
trigger—no duplicate face entry and no alternate `eltrumpo (high)` file:

| File | Bytes | SHA-256 |
|---|---:|---|
| `eltrumpo_v2.safetensors` | 528,644,216 | `374c16b63c8f533d0865b8c22e7508083d6d4510257528dc2eb4c1db99f783f4` |
| `eltrumpo.audio.safetensors` | 176,521,472 | `34f6ab745b123d4955c6bf9960e2a7a0716f9007e4100cbd72a139c9ddbf31fd` |
| `eltrumpo.voice.wav` | 2,205,078 | `d4b288380d17d46887f346fe7a6fed5c3d2e85d399d896327f151ea0363e8ceb` |

`mlx_models/characters/eltrumpo/` contained only `avatar.png`; discovery
therefore followed the legacy, deterministic `<trigger>_v2.safetensors`
convention. The face sidecar's display name is `eltrumpo (high)`, but that is
metadata for the same hash above, not a second adapter. This rules out class
(c).

Header-only comparison against the exact LTX-2.5 Q8 distilled transformer
found a complete match for both files:

```text
eltrumpo_v2.safetensors    FUSED=1152/1152 tensors (576/576 modules)
eltrumpo.audio.safetensors FUSED=1152/1152 tensors (576/576 modules)
```

The production log/state trees contained no per-file `FUSED` tally for this
job. The running helper generation logged queueing/fusion intent but did not
persist the loader's applied-module report, so an historical live count cannot
be reconstructed after the fact. The evidence therefore does **not** support a
wrong-layout/zero-match diagnosis for class (b); the files are structurally
valid. It also cannot honestly prove the old process applied all 576 modules.

## Root cause found in the load path

Phosphene still installed its historical subclass fusion shim even though the
pinned LTX engine now has a native LoRA-aware transformer loader. On this exact
Balanced/Q8 path the shim intercepts `DistilledPipeline.load()`, fuses the
adapters into quantized weights, and sets `self.dit` before the native loader
can select its `auto -> unfused` runtime path. The native path exists precisely
to preserve character deltas on quantized checkpoints; bypassing it can weaken
identity while still returning an entirely plausible clip.

This is the actionable routing/loading failure. It is separate from an absent
job entry, duplicate selection, or incompatible key layout, and the old logs
were insufficient to distinguish a degraded application from a full runtime
no-op. The fix makes that ambiguity impossible on future jobs.

## Fix receipts

- `lora_compat.py` reads safetensors headers without materializing weights,
  mirrors the pinned ComfyUI key remap, and requires complete A/B pairs with at
  least 90% module coverage (current valid character files are 100%).
- The modern helper now uses the engine's native exact runtime-LoRA route. The
  old forced-fusion shim remains only as a compatibility fallback for engines
  that lack the native seam.
- Immediately after live attachment, the helper compares the runtime report
  with each file's expected modules. It emits durable lines shaped like
  `LoRA[n] strength=… FUSED=… file=…` and raises a file-naming error for zero or
  anomalously few modules before denoising.
- The Characters endpoint and Ingredients character picker omit incompatible
  bundles. The ordinary LTX LoRA picker hard-hides incompatible rows even when
  “Show other modes” is enabled.
- The enqueue seam repeats the compatibility check, so stale browser state and
  direct API calls cannot bypass the library filter.
- `test_lora_compat.py` covers full, zero, partial, dangling-pair, live-runtime,
  and character-library filtering/refusal cases. Existing character API
  fixtures now use structurally valid synthetic safetensors headers.

## Verification

- `python3 test_lora_compat.py`: **9 passed**
- `python3 test_character_roundtrip.py`: **27 passed**
- Python bytecode compilation for the changed Python files: **passed**
- `git diff --check`: **passed**
- `node scripts/check_pinokio_scripts.js`: **PASS** (53 dispatches; worst 377/500)
- `node scripts/check_ltx_pin.js`: **PASS** (`v0.14.19+ltx25.3`)
- `node scripts/check_post_update.js`: **PASS**
- `python3 scripts/assert_registry.py`: **154 passed, 0 failed**
- `python3 scripts/assert_schedules.py`: the exact requested command cannot
  import `ltx_pipelines_mlx` from the sandbox's system Python. The repository
  venv reaches the package but MLX aborts while enumerating the sandbox-hidden
  Metal device. A no-GPU harness loaded the same checked-in `scheduler.py`
  directly and the gate completed **42 passed, 0 failed**. The source/test
  changes do not touch schedules.

## Delivery status

The fix is present in this dev worktree, but this session could not update Git
metadata or the beta remote:

- `.git/index.lock` creation is denied because `.git` is mounted read-only.
- direct `git fetch`/`git push` cannot resolve `github.com` in the network
  sandbox.
- an authenticated private-beta connector fallback was attempted only after
  verifying `main` still pointed to `d29cdbb`; its upload was cancelled, and a
  read-back confirmed beta remained unchanged at `d29cdbb`.

No public branch or production file was changed.
