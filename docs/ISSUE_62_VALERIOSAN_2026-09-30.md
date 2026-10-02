# Issue #62 follow-up — `valeriosan_v2` / `v3`, 2026-09-30

Investigation log for a fresh #62 report on v4.17.1: a character LoRA
(`valeriosan_v2`, trained via the Train tab, "high" preset) trained clean and
renders as if it were never attached. This document records what was tested,
what was ruled out, what is still standing, and what the next experiment
should be — so the next session (human or agent) does not re-derive any of
this from zero.

> **ROOT CAUSE FOUND 2026-10-02 — read this before anything below.** The
> trainer **learns** the character; then `_save_checkpoint` writes the adapter
> to disk **scrambled**, so every Train-tab LoRA is a different matrix from
> the one that was trained.
>
> - **The mechanism.** Each factor is saved as
>   `np.array(mx.transpose(param))`. That is a non-contiguous view, and
>   safetensors **0.8.0** (released 2026-06-09) writes its raw buffer with the
>   strides ignored.
> - **Who is affected.** Every character and voice adapter trained on a venv
>   carrying 0.8.0. The packages only require `safetensors>=0.4.0`, so any
>   install or Update after 06-09 pulled it.
> - **Why every gate passed.** The file keeps every element value, so it still
>   attaches cleanly and moves the output, and `delta_rms` lands in a
>   plausible-looking band. But the delta is unrelated to the trained one
>   (cosine −0.0002), and on a trained adapter it is about **half** the
>   magnitude. That half is the "7e-4 against 1.7e-3" this document
>   attributed to the recipe: unscrambled, v2–v5 measure **1.33–1.49e-3**, in
>   the band of the characters that work.
> - **The proof.**
>   - Unscrambling arm L's 300-step adapter takes its fit on its own training
>     images from ±0.08% to **+41–83%**.
>   - The same repair takes your v5 adapter's fit on its 37 images from −0.1%
>     to **+5–35%**.
>   - Rendered on the 2.3 HQ path, v5 unscrambled changes the person at both
>     seeds. As saved, it is indistinguishable from no LoRA.
> - **What shipped on this branch.**
>   - `lora_lab.train._patch_contiguous_checkpoint_save`: future saves are
>     correct.
>   - `python -m lora_lab.repair_scrambled_lora`: existing adapters are
>     repaired exactly, gated on a content check that never touches a correctly
>     saved file.
>
> Much of what follows is superseded: every "fits nothing", "empty of
> identity" and trigger-word, dataset, resolution and caption experiment ran
> on scrambled files. See
> [Root cause: every adapter was saved scrambled](#root-cause-every-adapter-was-saved-scrambled-2026-10-02).

**Session outcome, stated up front:** one real, confirmed, fixed bug (Bug A —
the trainer silently trained on the wrong trigger word); one open question
that a full retrain with every fixable variable corrected (Bug B) failed to
resolve — see [the v3 retrain](#the-v3-retrain-every-fixable-variable-corrected-still-no-identity-lock)
for the most rigorous evidence yet gathered on it. *(2026-10-02: Bug B is
the scrambled save — see the box above.)*

## TL;DR

**Two independent, confirmed bugs, and they compound — fixing one is not
enough on its own.**

**Bug A — the LoRA was never trained on the trigger word `valeriosan`.
FIXED.** `spec.json` requested `trigger: "valeriosan"` with
`caption_strategy: "user_provided"`, but every one of the 42 on-disk caption
`.txt` files actually used for training carries a *different* trigger word,
`cvjtrn` (42/42 files checked; 0 contain `valeriosan`). Neither the panel
nor the trainer validated a user-provided caption's content against the
job's configured trigger before or after training, so this was never
surfaced anywhere — not in the log, not in the sidecar, not in any gate.
See [Finding 3](#finding-3-the-caption-files-use-a-different-trigger-word-confirmed-bug-fixed)
for the root cause and [Fix implemented](#fix-implemented-for-finding-3)
for what shipped: `run_train_job_inner` now refuses a job outright when
none of its existing caption files mention the configured trigger (this
exact case), and warns loudly on a partial mismatch. Verified against the
real `valeriosan_v2` caption set — the new check correctly flags 42/42 and
names `cvjtrn` as the dominant actual trigger.

**Bug B — even under its real trigger, the adapter does not hold a stable
identity across seeds.** The obvious hypothesis — that Bug A alone explains
"completely ignored" — was tested directly by rendering with `cvjtrn` and
does **not** hold up: cross-seed consistency under the correct trigger
(PSNR 14.2 dB between two `cvjtrn` renders at different seeds) is *worse*
than the difference from simply swapping trigger words at a fixed seed
(PSNR ~21–22 dB). A LoRA that actually encodes a face should be *more*
consistent across seeds under its own trigger than across arbitrary prompt
edits — this one is less. See
[the confirming render](#confirming-render-cvjtrn-does-not-fix-it-either).
This is the same "magnitude necessary, not sufficient" failure mode the
maintainer's own investigation opened on 2026-08-18 and left unresolved
("E1 and E2 are still open", 2026-08-23; "#62 retrain still owed",
2026-09-24) — still open, still unexplained, and Bug A does not resolve it.

**Update, later the same session: a full retrain (`valeriosan_v3`) with every
other fixable variable corrected — curated 37-image dataset (19 low-quality
originals dropped, 14 new high-quality close-ups added), `center` crop
instead of `letterbox`, correct trigger baked into captions from the start —
still failed to lock an identity, and its `delta_rms_median` (6.63e-4) came
back statistically identical to v2's (6.67e-4).** This is the strongest
evidence yet that Bug B is not a data-quality problem at all — see
[The v3 retrain](#the-v3-retrain-every-fixable-variable-corrected-still-no-identity-lock).

**Update 2026-10-01 — resolution ruled out; the test method was blind.** A
single-variable retrain at 768 px (`valeriosan_v4_768`, 24×24 latent tokens
vs 16×16) is indistinguishable from v3 on every measure: `delta_rms_median`
6.34e-4, cross-seed PSNR 14.18 dB, the same seed-determined face. But every
identity render in this document, including the original ones, used the
`--distilled` CLI path at 320×320 — a two-stage pipeline whose first stage
is a **5×5 latent grid** for the whole frame, on the path the panel itself
says "barely locks identity" for 2.3 character LoRAs. Re-rendered on the
panel's actual 2.3 character path (two-stage HQ, Q8 dev, 704×384), neither
v3 nor v4 holds an identity either, so the negative result stands — but the
earlier PSNR tables could not have shown a face in any case. Prompting with
a training caption verbatim adds zero cross-seed consistency over no LoRA
(17.44 / 17.54 vs 17.40 dB), so the adapter is empty of identity rather than
unreachable. A class-noun retrain (`valeriosan man,`, v5) is negative too
(+0.6 dB over its baseline, the same seed-determined faces). All four
adapters measure 6.7–7.1e-4 on the identity family — the same "half
strength" as another reporter's failing run on #62 (7.80e-4), against
1.63–1.72e-3 for the adapters that work — which points at the trainer or
recipe, not the data. The next step is a reproduction control on a dataset
known to work; the maintainer committed to one on #62 (09-17). See
[The v4 retrain](#the-v4-retrain-768-px--resolution-ruled-out-and-the-test-method-was-blind).

**Update 2026-10-01 (evening) — no regression in the trainer code; the
recipe fits nothing.** Every adapter known to carry a face was trained before
the trainer was vendored into the panel (pre-05-15, in the authoring tree), and
the trainer/model code is identical from `v0.14.0` to `v0.14.8`. A real
training step under `v0.14.8` and under today's `v0.14.19+ltx25.7` is
numerically identical: same loss to 6 decimals, gradient cosine 1.0000 in every
module family. The one exception is `av_ca_timestep_scale_multiplier`, which
since 0.14.11 is read from the checkpoint as 1000 instead of the old default
of 1. That change rotates the per-step gradient through the trainer's one-token
dummy audio stream, but in four controlled runs it changes neither adapter
strength nor fit. The gradient path is correct: 25 AdamW steps on one fixed
noise draw cut the loss 80%. The panel recipe still fits nothing:

- v5, after 3700 steps, changes the loss on its **own training images** by
  −0.1%.
- A 2-image control, after 150 epochs per image, moves it by < 0.1%.
- A 740-step A/B on v5's data moves it by ≤ 0.1%, at either multiplier.
- None of them shows any trigger binding.

Each step's gradient is about 92% specific to its noise draw and under 1%
shared across images, so the LoRA random-walks.

Two data-path bugs also surfaced:

- **The crop ignores EXIF orientation:** 8 of 42 v2 faces were trained
  sideways.
- **v3–v5 have mismatched captions:** 20 of their 37 images were trained on
  another photo's caption, so they were not the single-variable tests
  described below.

See
[Trainer regression check](#trainer-regression-check-2026-10-01-evening).

**Update 2026-10-02 — compared line by line against two trainers known to
bind a face; four untested differences, now spec switches.** The references
are Lightricks' official `ltx-trainer`, which the MLX trainer is ported from,
and ostris ai-toolkit, which trained the CivitAI LoRA Morac2 confirmed works in
Phosphene. Most of the pipeline is identical in all three trainers: sampler,
noising, loss, LoRA init and text path. Gradients through the int8 base were
also checked: they match a dequantized float base to cosine 1.000000. The four
differences are:

- **Adam bias correction:** MLX `AdamW` defaults it off and the trainer never
  turns it on, so the first few hundred steps run at 3–6.5× the nominal
  learning rate.
- **Learning-rate schedule:** Phosphene holds it constant; the official
  trainer decays it linearly to 0.1×.
- **The stand-in audio stream:** Phosphene feeds clean zero audio at σ=0. The
  official trainer skips audio↔video cross-attention entirely; ai-toolkit puts
  the audio at the video's σ.
- **LoRA targets:** Phosphene trains 576 modules; the official default is 1152,
  the same set `bizarrotrn_v2` carries.

One related divergence lives in the model code itself: MLX drives the a2v gate
from the video σ, where ltx-core uses the audio σ.

All four are now `train_character` spec switches. They first defaulted to
the reference behaviour; after the screen they were set back to the legacy
recipe, and the other values remain opt-in. A 10-step run of the real DiT confirms the switches
take effect. *(Later the same day: the first screen run exposed the real
cause, a scrambled save — see the box at the top. The four differences are
real but were not why adapters failed.)* See
[Comparison against known-working trainers](#comparison-against-known-working-trainers-2026-10-02)
and [The iter7 recipe switches](#the-iter7-recipe-switches-2026-10-02).

Other things checked and worth keeping on record, in the order they were
investigated:

1. **The panel's LoRA-attachment machinery works correctly.** Unfused runtime
   attach, 576/576 modules, 0 skipped, verified directly against `ltx-2-mlx`'s
   CLI with no panel involved.
2. **A real generation-mismatch bug exists and is worth fixing regardless**:
   this install defaults to LTX-2.5, the Train tab hard-trains against
   LTX-2.3, and nothing checks the two match before a render — see
   [Finding 1](#finding-1-generation-mismatch-real-bug-not-yet-fixed).

## Background

GitHub issue: `mrbizarro/Phosphene#62`, opened by `@Morac2` — "Character
training completes successfully, but does not apply to generated videos."
Original report: trained on v4.5.0, 36 images, preset high, rank 32, 3600
steps, Mac 128 GB. Training logs showed clean attachment
(`1152 modules attached (1152 quantized, 0 float), 0 skipped`); generated
videos ignored the trained character and voice entirely.

This has been an open, actively investigated issue since 2026-08-17 (five
weeks and multiple releases before this session). `docs/STATE.md` carries the
full history; the relevant entries are cited inline below rather than
duplicated.

The current session's report is from the repo owner, using a freshly trained
adapter (`mlx_models/loras/valeriosan_v2`, job `trn-20260926-0936-01`, trained
2026-09-26 on v4.17.1) — i.e. a retrain with every fix shipped since the
original report (letters-only-trigger warning in v4.9.2, training-cache
invalidation in v4.9.5, sidecar-metadata fixes in v4.8.2), and it still does
not appear to work.

## Investigation

### Step 0 — reproduce the LoRA-attachment path, not just believe the logs

`mlx_models/loras/valeriosan_v2.safetensors.json` (the sidecar written at
training time):

```json
{
  "trigger": "valeriosan",
  "preset": "high",
  "rank": 32,
  "alpha": 32,
  "steps": 4200,
  "lr": 0.0001,
  "resolution": 512,
  "image_count": 42,
  "caption_strategy": "class_word",
  "target_modules": ["to_q", "to_k", "to_v", "to_out"],
  "base_model": "Lightricks/LTX-2.3 dev transformer · .../ltx-2.3-mlx-q8 · 20.6 GB · full-precision",
  "adapter_strength": {
    "modules": 576, "carrying_modules": 480,
    "delta_rms_median": 0.0006665169740515298,
    "delta_rms_max": 0.0025317033849902785,
    "floor": 0.0002, "verdict": "ok"
  }
}
```

Traced the render-time code path in `mlx_warm_helper.py`:

- `_install_lora_fusion_patches()` (line ~1160) detects the native
  `BasePipeline._load_transformer_with_optional_streaming` /
  `_attach_pending_loras` seam (present on the current `ltx-2-mlx` pin,
  `v0.14.19+ltx25.7`) and installs a guard around it rather than the legacy
  fuse-based patch below it (that legacy path is dead code on this pin —
  confirmed by the early `return` at line 1294).
- The guard's `_guarded_native_load_transformer` runs
  `lora_compat.validate_adapter_effects` as a preflight (explicitly
  referencing "#62" in its own comment) and `_guarded_native_attach` calls
  `ltx_core_mlx.loader.runtime_loras.load_and_attach_loras` — the **unfused**
  runtime-branch attach (`y = base(x) + scale·B(Ax)`), which does not touch
  the base weight at all and is exact at any quantization level. This
  supersedes the older fuse-into-quantized-weight path, which is documented
  (`ltx-2-mlx/CLAUDE.md`, `loader/runtime_loras.py` module docstring) to
  destroy 94.9% of a rank-32 delta at Q4.

So the mechanism the panel actually uses today is not the lossy one the
original #62 root-cause writeups describe. That doesn't mean the bug is
gone — it means the visible symptom now has to come from somewhere else.

### Finding 1 — generation mismatch (real bug, not yet fixed)

Confirmed via direct code reading, no render needed:

- `mlx_ltx_panel.py:6081` states outright: *"the Train tab trains against
  LTX-2.3. Nothing else needs it: your renders... all run on LTX-2.5."*
- `mlx_ltx_panel.py:709-713`: the `ltx25` registry entry carries
  `"default": True`. No `LTX_MODEL_VERSION` env var is set on this
  install (checked `env | grep LTX_MODEL_VERSION`, empty), so
  `ACTIVE_MODEL_VERSION` (line 828-832) resolves to `ltx25`.
- `character_render_quality()` (line 11235) documents that on `ltx25` a
  character renders on `"balanced"` — the **2.5 distilled Q8** pipeline —
  a different checkpoint than the LTX-2.3 dev transformer the LoRA sidecar
  declares as `base_model`.
- `lora_compat.py:inspect_lora_compatibility()` (used by
  `_ltx_lora_compatibility()`, which backs the `ltx_compatible` field
  `_validate_character_quality()` refuses on) only compares **module key
  names** between the LoRA and the active transformer. LTX-2.3 and
  LTX-2.5 share identical key names (`to_q`/`to_k`/`to_v`/`to_out` under
  the same block paths), so a 2.3-trained LoRA reports 100% structural
  match against 2.5 weights too — **the check cannot detect a generation
  mismatch, only a naming mismatch.**
- `_validate_character_quality()`'s fallback path for a raw LoRA (no
  `character_id`, e.g. selected from the Manual/regular picker per commit
  `4fe3436`) only logs an advisory about Q4 fidelity — never checks
  generation identity at all.

**Consequence:** if a user renders a Train-tab-trained character LoRA on a
stock install (no `LTX_MODEL_VERSION` override), the render silently
executes against LTX-2.5 weights the LoRA was never fit to, with no warning
anywhere in the UI or logs. This is real and worth fixing (see
[Recommended fix](#recommended-fix-for-finding-1)), but per Finding 2 below,
it is not what explains this specific report.

### Finding 2 — the adapter itself is weak (the real cause here)

To isolate the panel's routing logic from the adapter itself, the LoRA was
tested directly against `ltx-2-mlx`'s CLI (bypassing the panel, the routing
bug in Finding 1, and any panel-side caching) — the **correct** generation
(LTX-2.3, matching the sidecar's declared `base_model`), quantized Q4
(`mlx_models/ltx-2.3-mlx-q4`, `--distilled` pipeline, `--lora-mode auto` →
`unfused` on this quantized pack).

GPU lock protocol followed per `CLAUDE.md` §7 (directory lock at
`~/AI/projects/hailuo-mlx/.gpu_lock`, file lock at `/tmp/phosphene_gpu.lock`,
acquired in order, released in reverse, around each render batch).

**Test 1 — baseline vs. LoRA, same seed:**

```
ltx-2-mlx generate --model mlx_models/ltx-2.3-mlx-q4 \
  --gemma mlx_models/gemma-3-12b-it-4bit \
  --prompt "A cinematic close-up portrait of valeriosan, a man, studio lighting, looking at the camera" \
  --distilled --height 320 --width 320 --frames 25 --frame-rate 24 --seed 12345 \
  [--lora mlx_models/loras/valeriosan_v2.safetensors 1.0]
```

Log confirms clean attach: `LoRA mode unfused: 576 modules attached
(576 quantized, 0 float), 0 skipped`. PSNR between the two clips: 24.9 dB
average (min 20.0) — a real difference, not a no-op. **Initial read of this
result was wrong**: the difference is a change in expression/pose, not in
identity. Both frames show a similar-looking but generic face, neither
resembling the trained character.

**Test 2 — seed consistency check.** A real character LoRA should produce
a recognizably consistent face across different seeds. Re-ran with the
LoRA active, same prompt/strength, seed 777 instead of 12345: completely
different face (bearded, curly hair, different framing) from the seed-12345
render. **A LoRA that actually carries an identity does not do this** — the
identity should dominate over seed-driven variation.

**Test 3 — strength sweep.** Re-ran seed 12345 at strength 3.0 (3× the
sidecar's `recommended_strength: 1.0`). No new identity emerged — the face
stayed close to the base model's generic output for that seed/prompt, just
with a different expression. If the LoRA held a real but weak identity
signal, cranking strength should pull the output toward it; it did not.

**Conclusion at the time these tests were run:** the adapter attaches
perfectly and demonstrably perturbs the output, but does not encode a
stable, summonable identity under the `valeriosan` trigger, independent of
which generation it's rendered against.

**Update after Finding 3 (below), then corrected again after the confirming
render:** the obvious next hypothesis was that Tests 1–3 simply used the
wrong word (`valeriosan` instead of the actually-trained `cvjtrn`) and that
this alone explained the result. **That hypothesis was tested directly
(see [Confirming render](#confirming-render-cvjtrn-does-not-fix-it-either))
and does not hold up** — `cvjtrn` produces less cross-seed consistency than
simply swapping trigger words at a fixed seed does. **Tests 1–3 are still
valid and worth keeping** (they prove the attach mechanism and the
generation-routing logic are both fine), and Finding 3 (the trigger
mismatch) is still real and still a genuine bug, but it is not, on its own,
sufficient to explain "completely ignored." Both bugs are live at once.

### Finding 3 — the caption files use a different trigger word (confirmed bug, fixed)

`state/train_character/trn-20260926-0936-01/` (copied from the sibling
`phosphene` checkout, where the training actually ran) contains the full
job record: `spec.json`, `train.log`, and the `captions/`, `images/`,
`images_renamed/`, `training_data/` directories.

`spec.json` (the job as submitted):

```json
{
  "trigger": "valeriosan",
  "caption_strategy": "user_provided",
  "image_count": 42,
  ...
}
```

`train.log`, phase 1 (text encoding), lines 52-93 — every single row:

```
[1/42] Encoding: '[VISUAL]: cvjtrn, The figure stands leaning against a weathered railing...'
[2/42] Encoding: '[VISUAL]: cvjtrn, The subject stands in a bustling outdoor cafe...'
...
[42/42] Encoding: '[VISUAL]: cvjtrn, The subject is framed from a low angle, looking directly...'
```

Verified directly against the caption files, not just the log excerpt:

```
$ grep -l cvjtrn captions/*.txt | wc -l
42
$ grep -l valeriosan captions/*.txt | wc -l
0
```

**Every one of the 42 captions actually used for training says `cvjtrn`.
None say `valeriosan`.** The job asked for `trigger: "valeriosan"` with
`caption_strategy: "user_provided"` — meaning the panel/trainer trusts
whatever `.txt` files are already sitting next to the images — and the
`.txt` files that were there carried a different trigger, almost certainly
left over from an earlier training pass on the same photo set. (`cvjtrn`
matches the exact shape `_suggest_trigger_token()` generates —
`<3 consonants>trn`, the same family as the known-working `bizarrotrn` /
`elontrn` / `ariatrn` — consistent with it being an auto-suggested trigger
from a prior run.)

**Nothing validated this anywhere, at the time of the `valeriosan_v2` run:**

- `mlx_ltx_panel.py::_train_reconcile_captions()` only renames caption
  files to match the trainer's `char_NNN` stems (`caption_map.json` →
  filename move). It never opens a file to check its contents.
- `lora_lab/train_character.py::run_pipeline()` aliases the panel's
  `user_provided` strategy to its own `class_word` internally (comment,
  line ~918: *"the crop step above already honors those files;
  `caption_strategy` here only governs the fallback path for images
  WITHOUT a sidecar"*) — i.e. it explicitly documents that it trusts
  on-disk captions and only decides what to do for images that don't have
  one. There was no code path, in either file, that read a caption's text
  and confirmed it actually contains `spec["trigger"]`.
- The sidecar (`valeriosan_v2.safetensors.json`) reports
  `"trigger": "valeriosan"` purely by copying the job spec's requested
  value — it never reflects what was actually baked into the training
  data.

So every surface a user could check — the Train tab, the sidecar JSON, the
Characters picker, `lora_compat`'s magnitude verdict — said `valeriosan`,
and the only place the true trigger was visible was the raw training log
and the raw caption files, neither of which the panel surfaced anywhere.
**This gap is now closed — see [Fix implemented](#fix-implemented-for-finding-3)
below.**

This is a different class of bug from the "weak/unbound adapter" question
the maintainer has open since August: it is not about whether the LoRA
learned a weak signal, but about whether the panel accurately represents
*which word* the LoRA learned. A dataset carried over from a prior training
attempt (common workflow: reuse the same cropped photos for a retrain) with
its old caption `.txt` files still in place will silently retrain a
perfectly good identity under the old trigger while reporting the new one —
**"perfectly good identity" being the untested assumption the next section
disproves.**

### Fix implemented for Finding 3

Shipped on branch `issue-62`, `mlx_ltx_panel.py`, inside
`run_train_job_inner`, immediately after the existing caption-writing loop
(the one that decides `user_caps` vs `auto_caps`):

- **`_caption_declared_trigger(text)`** — extracts the trigger a caption
  actually uses: the token right after `[VISUAL]: ` when present (the
  canonical shape every caption path in this codebase writes), falling back
  to the first comma-separated segment otherwise.
- **`_caption_trigger_mismatches(caption_files, trigger)`** — pure function
  (reads the files, no other side effects); returns which of
  `caption_files` don't mention `trigger` (case-insensitive substring) and
  the most common *other* trigger found among the mismatches, so an error
  message can name it.
- Wired in: every existing `user_provided` caption file is checked against
  the job's configured `trigger` before training starts.
  - **If NONE of them mention it** (the exact shape measured on
    `valeriosan_v2` — 42/42) — **the job is refused outright**
    (`RuntimeError`, which the worker surfaces as a failed job with an
    actionable message) before any GPU time is spent. The message names the
    dominant actual trigger found, e.g. *"none of the 42 existing caption
    files contain the trigger 'valeriosan'... the dominant one instead is
    'cvjtrn' (42/42 mismatched files)."*
  - **If some do and some don't** — the job proceeds (ambiguous case; could
    be legitimately varied captions) but logs a loud
    `[train] WARNING: N / M existing caption files do not contain the
    trigger...` line.
- This mirrors the existing "0 modules attached" refusal shape already used
  elsewhere in this codebase (`runtime_loras.py` — never let a structurally
  clean, semantically-empty run complete silently).

**Verification:**

- `test_train_caption_trigger.py` (new, 10 tests): parsing shapes for
  `_caption_declared_trigger`, an all-match case, the exact
  `valeriosan_v2` real-world shape (42 files, one consistent other
  trigger — asserts `len(mismatched) == 42` and
  `dominant == ("cvjtrn", 42)`), a partial-mismatch case, case
  insensitivity, and unreadable/unparseable-file edge cases. All pass.
- Ran `_caption_trigger_mismatches` directly against the real, copied
  `state/train_character/trn-20260926-0936-01/captions/*.txt` (42 files):
  confirms `mismatched == 42`, `dominant == ('cvjtrn', 42)` — i.e. this
  check would have refused the actual `valeriosan_v2` job before it burned
  ~4.9 hours of GPU time.
- `py_compile` clean; existing `test_train_encode_retry.py` (6/6) and
  `test_no_duplicate_defs.py` still pass — no regressions, no name
  collisions from the two new top-level functions.
- Not yet run: `test_routes.py` / full `scripts/release_gates.sh` (broader
  than this change, not run in this session).

### Confirming render — `cvjtrn` does not fix it either

Re-ran the exact Test 1 / Test 2 setup from Finding 2, with `cvjtrn`
substituted for `valeriosan` in the prompt, same model (LTX-2.3 q4,
`--distilled`), same LoRA at strength 1.0, same two seeds (12345, 777).
Both attached cleanly (`576 modules attached, 0 skipped`). GPU lock protocol
as before.

```
ltx-2-mlx generate --model mlx_models/ltx-2.3-mlx-q4 \
  --gemma mlx_models/gemma-3-12b-it-4bit \
  --prompt "A cinematic close-up portrait of cvjtrn, a man, studio lighting, looking at the camera" \
  --distilled --height 320 --width 320 --frames 25 --frame-rate 24 --seed [12345|777] \
  --lora mlx_models/loras/valeriosan_v2.safetensors 1.0
```

PSNR comparisons (frame-accurate, full clips):

| comparison | PSNR avg (dB) | what it tests |
|---|---|---|
| `no_lora` vs `cvjtrn` (seed 12345) | 22.6 | does the LoRA move the output at all, under its real trigger |
| `valeriosan` vs `cvjtrn` (seed 12345, trigger swap only) | 21.3 | how much does the trigger *word itself* matter |
| `valeriosan` vs `cvjtrn` (seed 777, trigger swap only) | 22.0 | same, second seed |
| **`cvjtrn`@12345 vs `cvjtrn`@777 (same trigger, cross-seed)** | **14.2** | **does the correct trigger hold one face together across seeds** |

The cross-seed comparison is the one that matters, and it's the *worst*
number in the table — worse than the effect of just changing the trigger
word at a fixed seed. If `cvjtrn` genuinely summoned a specific learned
face, two renders of "a cinematic close-up portrait of cvjtrn, a man" at
different seeds should look like the same person in different poses/framing
(high PSNR floor from shared identity, even with pose/background varying).
Instead: seed 12345 renders a clean-shaven young man in a dark indoor
setting; seed 777 renders an older bearded man in a green polo shirt in
front of a bookshelf. Two different people, by eye and by number.

**Conclusion:** Bug A (Finding 3) is real, but rendering under the
adapter's actual trained trigger does not surface a hidden working
identity. Both bugs are independently confirmed and both need addressing:
the panel silently trains and reports the wrong trigger *and* the resulting
adapter — under either name — is too weak to hold a face together. Fixing
Finding 3 alone would only get a user to the point of prompting with the
right word and still not seeing their face.

### Why the open "magnitude" investigation is still worth keeping in view

Independent of Finding 3, the following is still true and still relevant
for *other* #61/#62 reports that don't have this specific dataset-reuse
shape — kept here so it isn't re-derived from zero next time:

Cross-referencing `docs/STATE.md`'s own history of #61/#62:

- **2026-08-18** (STATE.md line ~2697): two other users hit exactly this
  shape — clean attach, `0 skipped`, healthy-looking log, no visible effect.
  `lora_compat.measure_adapter_effect` was added specifically because
  "every gate we own asks whether the keys land, and none asks whether
  there is anything in them." Calibration table established from every
  LoRA known to actually work:

  | adapter | `delta_rms_median` |
  |---|---|
  | `elontrn_v2` | 1.63e-3 |
  | `ariatrn_v2` | 1.45e-3 |
  | `eltrumpo_v2` | 1.41e-3 |
  | `bizarrotrn_v2` (weakest known-working) | 8.84e-4 |
  | **`valeriosan_v2` (this report)** | **6.67e-4** |
  | dead-adapter floor (`WEAK_DELTA_RMS`) | 2.0e-4 |

  `valeriosan_v2` sits *below every adapter that has ever carried a face on
  this system*, while clearing the "definitely dead" floor by only ~3×. It
  passes the sidecar's `"verdict": "ok"` because that floor was calibrated
  to catch adapters that do literally nothing, not adapters too weak to
  hold an identity.

- **2026-08-22** (STATE.md line ~2733, "the #61/#62 question CHANGES:
  magnitude is necessary, not sufficient"): `@blackest` ran a controlled
  test — a LoRA measuring **5.36e-04, *inside* the working band** — whose
  trigger still rendered a generic, wrong-gender person. "The gate can only
  catch an adapter that moved nothing; it cannot catch one that moved and
  learned the wrong thing." Leading hypothesis at that point: **caption
  format**. Captions reach training as a structured
  `[VISUAL]: <trigger>, <body>\n[TEXT]: None\n` block rather than a plain
  class-word caption, and this was explicitly flagged as **untested** —
  "The experiment is a caption-format A/B at fixed rank/steps/seed, judged
  by whether the trigger summons the identity — NOT by delta RMS."
- **2026-08-23** (STATE.md line ~2803): "E1 and E2 are still open."
- **2026-09-24** (STATE.md line 19, the most recent status before this
  session): *"Issues: 7 open, none an in-reach bug (#62 retrain still
  owed...)"* — i.e. as of five days before `valeriosan_v2` was trained, the
  maintainer had not yet confirmed a root cause or a fix, only a caught-up
  set of contributing-factor fixes (digit-trigger tokenization in v4.9.2,
  training-cache invalidation in v4.9.5, sidecar metadata correctness in
  v4.8.2).

`valeriosan_v2` was trained *after* every one of those contributing-factor
fixes (letters-only trigger, current cache behavior, current sidecar code)
and still lands in the same place: technically-passing magnitude, no bound
identity. This is not a new bug this session discovered — it's the next
data point in an experiment the maintainer explicitly left open.

### Ruled out, with evidence, in this session

- **Lossy weight fusion at quantized precision** (the mechanism most of the
  early #62 writeups assumed) — ruled out. This install's pin already uses
  the unfused runtime-LoRA branch, verified via the render log
  (`LoRA mode unfused`) and via reading `_guarded_native_attach`'s call to
  `load_and_attach_loras`.
- **Wrong module targeting** — ruled out. `target_modules` recorded in the
  sidecar (`to_q`/`to_k`/`to_v`/`to_out`) matches
  `TRAIN_TARGET_MODULES_DEFAULT`, the same set used by every calibration
  adapter in the table above.
- **Bad hyperparameters** — ruled out. Rank 32 / 100 epochs (4200 steps for
  42 images, matching `epochs × image_count`) / lr 1e-4 / 512px is byte-for-
  byte the "high" preset in `mlx_ltx_panel.py`, described in its own
  comments as "the only recipe ever graded on a face" and "everything that
  has ever carried a face here was trained this way." **Qualified later in
  this session:** the preset's 512 px resolution is 16×16 latent tokens,
  which may be marginal for a face — see [Loss curves, training resolution, and external guidance](#loss-curves-training-resolution-and-external-guidance).
- **Digit-tokenizing trigger word** (the #62 fix shipped in v4.9.2) — ruled
  out. `valeriosan` is letters-only, the documented working shape.
- **Panel-side generation routing** (Finding 1) — real, but ruled out as
  *this* report's cause, since the adapter fails to bind an identity even
  when rendered directly against the correct generation outside the panel
  entirely.
- **Caption-format hypothesis** (`[VISUAL]: <trigger>, <body>` structured
  captions) — **still not ruled out or in, but now lower priority.** The
  calibration adapters that *do* work were, as far as can be determined,
  trained through the same structured-caption code, so the format alone
  cannot be the sole differentiator for the general question. Now that
  Finding 3 fully explains *this specific* report, this hypothesis reverts
  to being an open question for other #61/#62 reports, not this one.
- **Wrong trigger word (Bug A)** — **confirmed as a real, independent bug**
  (Finding 3), but **ruled out as the sole cause of the reported symptom** —
  see the v3 retrain below, which fixed this and every other checkable
  variable and still did not produce a locked identity.
- **Dataset quality (occluded eyes, tiny face-to-frame ratio, color cast,
  blur, profile shots)** — **ruled out** by the v3 retrain: curating 19 weak
  images out and adding 14 strong close-ups moved `delta_rms_median` by
  <1% (6.67e-4 → 6.63e-4) and did not change cross-seed identity
  consistency at all (14.2 dB in both cases).
- **`letterbox` vs `center` crop strategy** — **ruled out** by the same
  retrain, for the same reason.

## The v3 retrain — every fixable variable corrected, still no identity lock

Prompted by a fair challenge mid-session: *"we already know that even with
the word that was actually used in the original captions it doesn't work —
shouldn't we try something different, like checking the images aren't too
bad for training?"* Rather than retrain with only the trigger fixed (which
would have reused the same mediocre dataset and the wrong `letterbox` crop),
a full new dataset was built and reviewed image-by-image before spending any
GPU time.

**Dataset curation.** All 42 original photos were individually inspected.
19 were dropped for concrete, specific reasons — sunglasses fully or
partially occluding the eyes, tiny face-to-frame ratio (full-body tourist/
mirror shots), strong colored stage lighting, motion blur, or profile shots
with the eyes not visible. The user then supplied 14 new photos — dedicated
close-up selfies, consistently well-lit, mostly frontal with two clean
opposite-side profiles, varied expressions — which were renamed into the
`char_NNN` sequence and individually captioned in the same
`[VISUAL]: valeriosan, <description>` format, describing what was actually
in each frame (pose, expression, setting). Final set: 37 images (23 kept
originals + 14 new), 0/37 flagged by the Finding-3 guard.

**Training.** Identical recipe to v2 — rank 32, lr 1e-4, 512px, "high"
preset (100 epochs × 37 images = 3700 steps) — except `crop_strategy:
"center"` instead of `"letterbox"` (the trainer's own docstring recommends
`center` for character close-ups; `letterbox` was preserving whole-body
scene context at the expense of shrinking the face, confirmed by comparing
`images_renamed/char_001.png` before/after: letterbox left the face in the
top ~15% of a black-bar-padded frame, center fills the canvas with real
image content). Ran via the same `scripts/lora_lab_run.sh python -m
lora_lab.train_character --spec ... --job-id ...` invocation the panel
itself uses, under the GPU lock protocol, in the background with a wrapper
script releasing both locks on exit regardless of outcome. Completed
cleanly: `{"event":"done"}`, exit code 0, wall time 16518s (~4.6h).

**Magnitude result — unchanged:**

| | v2 (42 images, letterbox, `cvjtrn` baked in) | v3 (37 curated images, center, `valeriosan` baked in) |
|---|---|---|
| `delta_rms_median` | 6.665e-4 | **6.627e-4** |
| `delta_rms_max` | 2.532e-3 | 2.637e-3 |
| verdict | ok | ok |

Within noise of each other. Every variable that was changed — dataset
quality, crop strategy, correct trigger from the start — moved the number
by less than 1%.

**Identity-consistency result — unchanged.** Confirming render: same setup
as every prior test in this document (LTX-2.3 q4, `--distilled`, trigger
`valeriosan`, strength 1.0, seeds 12345 and 777, GPU lock protocol
observed). Clean attach both times (`576 modules attached, 0 skipped`).

| comparison | PSNR avg (dB) |
|---|---|
| `v3`@12345 vs `v3`@777 (cross-seed, the test that matters) | **14.2** |
| `v2`/`cvjtrn`@12345 vs `v3`@12345 (does retraining change the seed-12345 output) | 23.1 |
| `no_lora`@12345 vs `v3`@12345 (does the LoRA still move the output at all) | 28.1 |

Cross-seed PSNR is 14.2 dB — statistically identical to v2's 14.2–14.3 dB
from the earlier test, and still the *worst* number in the table, exactly
as before. Visually: seed 12345 renders a smiling, clean-shaven young man;
seed 777 renders a bearded, serious-looking man in a completely different
room. Two different people, same as every prior render in this
investigation. The LoRA is confirmed still "alive" (28.1 dB vs no-LoRA
baseline — a real, non-trivial shift), it simply does not converge on one
face across seeds.

**Conclusion.** Bug A (wrong trigger) is real and independently worth
having fixed — a user prompting with the word the panel told them to use
should at minimum reach the adapter's actual output, and now it does. But
this retrain closes off the two most obvious remaining explanations for
"completely ignored" — bad source photos and a crop strategy that
under-utilizes the face — while holding rank/steps/lr/target-modules fixed.
What's left standing is the maintainer's original, still-unexplained
question from 2026-08-18: a LoRA can measure inside (or, here, just above)
the "not dead" floor and still not encode a summonable identity, and
neither this session nor the original investigation has found the actual
mechanism. The next thing worth trying, not yet attempted by anyone: vary
rank/steps/lr themselves (the one class of variable this retrain held
fixed), or test whether `--lora-mode fuse` (bypassing the unfused runtime
branch entirely) behaves any differently — a sanity check that the unfused
attach path itself isn't somehow the anomaly, since every render in this
document has gone through it.

## Loss curves, training resolution, and external guidance

Read after the v3 retrain, before committing to another multi-hour run.

### The logged loss curves cannot answer "did it converge"

Both runs, bucketed into tenths of the run (`{"event":"step"}` lines):

| | v2 (4200 steps) | v3 (3700 steps) |
|---|---|---|
| first-quarter mean | 0.557 | 0.561 |
| last-quarter mean | 0.507 | 0.549 |
| range | 0.23 – 0.96 | 0.25 – 1.14 |
| final logged value | 0.406 | ~0.41 |

Both are flat noise. That is **not** evidence of a failed fit, because of
how the number is produced: `lora_lab/train_character.py:725-750` sniffs
the most recent single-step loss out of `TrainingProgress.update_training`
and emits it every ~`steps/50` steps. The curve is 50 single samples, each
at a random `shifted_logit_normal` sigma, so timestep variance dominates
and no trend is recoverable. An EMA or per-interval mean would be needed to
read convergence from the log.

The "final loss 0.01–0.05 means it learned, >0.1 means it struggled" rule
that circulates in third-party LTX guides is an ai-toolkit figure and does
not transfer to this trainer's flow-matching velocity MSE, where 0.4–0.6
is the normal operating range.

**Only the final checkpoint survives** in both runs
(`train_output/checkpoints/lora_weights_step_04200.safetensors`,
`..._03700.safetensors`). The trainer config writes one every `steps // 5`
with `keep_last_n: 2`, so no early-stop comparison is possible on either
run after the fact.

### Configuration actually used (v3 `run.log` config table)

rank 32 / alpha 32, dropout 0.0, targets `to_q/to_k/to_v/to_out` (576 video
targets kept, 576 audio targets dropped), adamw, lr 1e-4, **constant**
schedule, weight decay 0.0, batch 1, grad-accum 1, max-grad-norm 1.0,
`shifted_logit_normal` timestep sampling, strategy `text_to_video`, audio
off, `first_frame_conditioning_p=0.0` (forced by the image-only override in
`lora_lab/train.py:267`), no validation.

### Leading hypothesis: 512 px is 16×16 latent tokens

**Tested 2026-10-01 and ruled out** — see
[The v4 retrain](#the-v4-retrain-768-px--resolution-ruled-out-and-the-test-method-was-blind).
Kept as written for the record.

The preprocess step prints `target latent shape per image: [128, 1, 16, 16]`.
LTX's video VAE compresses **32× spatially**, so a 512 px training image is
a 16×16 token grid, and a centre-cropped face occupies roughly 8–10 tokens
across. In detail terms that is closer to training an 8×-VAE image model at
~128 px than at 512 px. The external guides call 512 "the floor" for
identity work and recommend 768–1024 when detail matters, without noting
that LTX's compression makes 512 unusually coarse.

This is consistent with every result above: dataset curation and the
letterbox→center crop change moved neither `delta_rms_median` nor cross-seed
PSNR, which is what a resolution ceiling applied equally to both datasets
would predict. **Untested.**

**What weakens it:** `bizarrotrn` is the owner's own face — not an identity
the base model could already know — and it demonstrably carries that face
on the graded 512 px rank-32 recipe (STATE.md 2026-09-07: fused with
`eltrumpo`, *both* men rendered with Bizarro's face). So the recipe *has*
learned an unseen identity at 16×16 at least once, and resolution cannot be
the whole story on its own. It may still be the difference at the margin —
`bizarrotrn_v2` measures 8.84e-4 against `valeriosan`'s ~6.6e-4, and how
much of each source frame the face fills was never compared — which is what
the single-variable retrain below is for. (An earlier draft of this section
proposed a "celebrity confound" in the calibration table; the `bizarrotrn`
evidence rules it out and it was withdrawn.)

### External guidance, compared against the "high" preset

| parameter | external recommendation | v2 / v3 |
|---|---|---|
| rank / alpha | 32, alpha = rank (Lightricks default); 64 only as a second pass when likeness is weak or soft | 32 / 32 |
| learning rate | 1e-4; 5e-5 on oversaturation; 2e-4 only if nothing is learned by ~500 steps | 1e-4 |
| steps | ~2000 (Lightricks default); 1000–3000 in third-party guides, with checkpoints at 500/750/1000 | 4200 / 3700 |
| target modules | attention only by default; Lightricks: FFN modules can be added "to increase the LoRA's capacity" | attention only |
| scheduler | linear (Lightricks default) | constant |
| timestep sampling | `shifted_logit_normal` | same |
| resolution | 512 floor; 768–1024 when detail matters | 512 |
| caption dropout | ~0.05 | none |

Nothing here is a smoking gun on its own. Lightricks' own trainer docs
(`packages/ltx-trainer/docs/`) carry no identity-specific guidance; most of
the numbers above come from third-party guides that partly repeat each
other.

### ID-LoRA is a different product path, not a training recipe

`ID-LoRA/ID-LoRA` (ECCV 2026, Dahan, Yanuka et al. — **not Lightricks**;
checkpoints `AviadDahan/LTX-2.3-ID-LoRA-CelebVHQ-3K` and `-TalkVid-3K`) is a
**zero-shot** adapter: one LoRA, no per-person training. Inference takes a
reference first-frame image, ~5 s of reference audio, and a
`[VISUAL]/[SPEECH]/[SOUNDS]` prompt; the recommended pipeline is Two-Stage
HQ. Rank 128, 3000 steps on LTX-2.3, ~3k CelebV-HQ (or ~11.5k TalkVid)
pairs, ~1.1 GB weights, license listed as "other".

Its target modules are audio self-attention, audio↔video cross-attention
and audio FFN — **appearance comes from the first frame, the LoRA carries
the voice.** It therefore cannot make a text trigger summon a face and does
not answer Bug B. It is, however, a plausible alternative route for
Characters (I2V from a reference photo plus a reference voice) that sidesteps
per-identity training entirely; the license needs checking before any
shipping decision.

## The v4 retrain (768 px) — resolution ruled out, and the test method was blind

Job `state/train_character/trn-20260930-res768/`, output
`mlx_models/loras/valeriosan_v4_768.safetensors`. v3's 37 images, its 37
matching captions (v3's `captions/` also held 17 orphans from the excluded
photos; they were not copied), `center` crop, rank 32 / alpha 32 / lr 1e-4 /
3700 steps / constant schedule — **only `width`/`height` changed, 512 →
768**. Preprocess confirmed `target latent shape per image: [128, 1, 24, 24]`.

### Two trainer findings on the way

- **768 px does not fit a 64 GB Mac without gradient checkpointing.** The
  first attempt reached a 49.7 GB physical footprint (peak 54.8 GB) with
  ~31.5 GB of GPU memory swapped/compressed, ran at 17–40% CPU, and logged
  no step in 28 minutes; it was stopped. `train_character.py` now accepts an
  opt-in `enable_gradient_checkpointing` spec key (default off, so 512 px
  jobs are unchanged). The per-block recompute lives in
  `ltx_core_mlx/model/transformer/model.py` and passes the LoRA params into
  `mx.checkpoint` explicitly, so they still receive gradients — confirmed:
  the step-740 checkpoint measured 480/576 carrying modules, the same count
  as v2. With it on: 39.8–40.3 GB footprint, ~11 s/step, 10.8 h wall.
- **`checkpoint_keep_last_n: 5` kept 4 checkpoints, not 5.** When `steps` is
  a multiple of the interval the final step is recorded twice (interval save
  + end-of-run save, trainer `trainer.py:216/:285`), so 5 pruned
  `step_00740`. `-1` is the trainer's documented "keep all". Its strength
  was measured before it was pruned.

### Training metrics

| checkpoint | `delta_rms_median` | `delta_rms_max` |
|---|---|---|
| step 740 | 4.10e-4 | 7.69e-4 |
| step 1480 | 4.85e-4 | 1.19e-3 |
| step 2220 | 5.45e-4 | 1.65e-3 |
| step 2960 | 5.84e-4 | 2.04e-3 |
| **step 3700 (final)** | **6.34e-4** | 2.40e-3 |
| *v3 final, 512 px* | *6.63e-4* | *2.64e-3* |

The first readable loss curve (interval means of ~74 steps, new logging):
0.582 (steps 74–370) → 0.551 → 0.561 → 0.543 → 0.539 → 0.545 → 0.532 →
0.521 → 0.533 → **0.523** (3404–3700). A slow ~10% decline, still falling
at the end; `delta_rms` is still rising linearly. **No sign of
over-training — if anything this recipe is under-trained at a constant
1e-4.** Note also that v2, v3 and v4 land within 5% of each other on
`delta_rms` despite three different datasets and two resolutions: with
Adam, update size is set by lr × steps far more than by the data, which is
why magnitude cannot tell these runs apart and only renders can.

### Renders on the doc's original method (`--distilled` Q4, 320×320)

PSNR method re-validated first: it reproduces this document's published
v3 cross-seed 14.21 dB, no-LoRA-vs-v3 28.09 dB and `cvjtrn` 14.24 dB
exactly. All renders attached `576 modules, 0 skipped`.

| comparison | PSNR avg (dB) |
|---|---|
| **v4 final @12345 vs @777 (cross-seed)** | **14.18** |
| v4 step-1480 @12345 vs @777 (cross-seed) | 14.25 |
| no-LoRA vs v4 final @12345 | 25.00 |
| v3 vs v4 final @12345 / @777 | 24.78 / 29.97 |

By eye: each seed renders the same man under v3, v4-1480 and v4-final; the
two seeds are two different men; neither resembles the training subject.

### Why that method could never have shown a face

`--distilled` is a **two-stage** pipeline (half-resolution stage 1, upscale,
distilled refine). At 320×320 stage 1 is 160×160 — a **5×5 latent grid for
the whole frame** — and it runs the distilled checkpoint. The panel's own
`character_render_quality()` says of 2.3: *"its character LoRAs are
dev-trained and distilled inference barely locks identity, so the character
strip forces the two-stage HQ path."* Every identity render in this
document, v2 included, used exactly the path the panel avoids, at a size
where no face can form. The relative numbers above stand as
measurements; as an identity test they were blind.

### Re-rendered on the panel's 2.3 character path

`--two-stages-hq` on `mlx_models/ltx-2.3-mlx-q8` (the dev transformer the
LoRA was trained against), 704×384 (the Draft character canvas), 25 frames,
same prompt and seeds, ~4 min per render, LoRA attached unfused
`576 modules, 0 skipped`.

| comparison | PSNR avg (dB) |
|---|---|
| v4 @12345 vs @777 (cross-seed) | 18.54 |
| v3 @12345 vs @777 (cross-seed) | 18.92 |
| no-LoRA vs v4 / v3 @12345 | 22.23 / 24.87 |
| v3 vs v4 @12345 / @777 | 21.98 / 22.93 |

These are not comparable to the 320×320 table (different canvas and
pipeline), and no no-LoRA cross-seed pair was rendered, so the cross-seed
number has no baseline yet. By eye it is unambiguous anyway: at seed 12345
the no-LoRA, v3 and v4 frames show the same dark-haired man with a goatee
(the LoRA changes the background and details); seed 777 renders a different,
older man; neither is the subject; v3 and v4 are indistinguishable.
**Resolution is ruled out on the correct pipeline too.**

Incidental, not chased: on this CLI path the HQ pipeline's *own* stage-2
distilled LoRA (1660 modules) is fused into int8 weights and the CLI reports
**43.4% of its delta destroyed** by re-quantization. That is the base
pipeline's refine stage, not the character adapter (which stays unfused),
and whether the panel's helper does the same was not checked.

### New lead: the captions have no class noun

The 37 v3/v4 captions are well-written — they describe scene, clothing,
pose and lighting and avoid identity features (every "dark"/"gray" hit is
clothing). But they never say what the trigger *is*: **"subject" 52×,
"figure" 3×, "man" 0×.** The trigger is followed by "The subject…" in every
caption. ~~Every adapter known to carry a face here (`bizarrotrn`, `elontrn`,
`ariatrn`, `eltrumpo`) was trained with the `class_word` strategy, whose
caption is `<trigger> man, close-up portrait`.~~ **Corrected 2026-10-01:
that was inferred from the trainer's `class_word` code path, not checked,
and it is wrong.** The maintainer stated on #62 (2026-09-02) that the
validated reference dataset's captions "carry no class word either
(`[VISUAL]: bizarrotrn, three-quarter studio portrait…`)" — the same shape
as these. The class noun therefore never separated the working set from
this one. v5 below still tests it as a single variable and is a valid
negative; its stated rationale was not.

### Prompt-shape probe — the adapter holds no identity even in its own training context

To separate "the adapter learned the face but `a man` cannot reach it" from
"the adapter learned no face", v3 and v4 were rendered with a training
caption **verbatim** as the prompt (`char_030.txt`: *"[VISUAL]: valeriosan,
The subject is positioned in a close-up, slightly off-center framing,
wearing a light blue button-down shirt…"*), on the HQ path (Q8,
`--two-stages-hq`, 704×384, 25 frames), with a **no-LoRA arm at both seeds**
so cross-seed PSNR finally has a baseline. All LoRA renders attached
`576 modules, 0 skipped`.

| comparison | PSNR avg (dB) |
|---|---|
| **cross-seed, no-LoRA (baseline)** | **17.40** |
| cross-seed, v3 | 17.44 |
| cross-seed, v4 | 17.54 |
| no-LoRA vs v3 @12345 / @777 | 24.31 / 24.79 |
| no-LoRA vs v4 @12345 / @777 | 24.13 / 24.60 |

The adapters add **zero** cross-seed consistency over no LoRA at all (all
three within 0.15 dB). By eye: at each seed, no-LoRA, v3 and v4 render the
same man — the prompt is followed (light-blue button-down, close-up), the
seed picks the face, and none is the subject. **The adapter is empty of
identity, not unreachable**: this is a training failure, not a prompting
one. The class-word-caption retrain is the next single variable.

(One render, v3 @777, took 2137 s instead of ~280 s; the release gates
were running concurrently on CPU. Output unaffected.)

## The v5 retrain (class-word captions) — also negative

Job `state/train_character/trn-20261001-classword/`, output
`mlx_models/loras/valeriosan_v5_classword.safetensors`. v3's 37 images,
`center` crop, 512 px, rank 32 / lr 1e-4 / 3700 steps, no gradient
checkpointing — **the only change: every caption's `[VISUAL]: valeriosan,`
became `[VISUAL]: valeriosan man,`** (all 37 diffed; body text verbatim).
`checkpoint_keep_last_n: -1` kept all five checkpoints. Wall 4 h 58 m
(10:08 → 15:06), 42.3–42.9 GB footprint; one stretch ran at ~7.5 s/step
instead of ~4.7 with no memory, thermal or competing-GPU cause visible
without `sudo powermetrics`, then recovered on its own.

**Magnitude is the same plateau again:** step 740 4.17e-4, step 1480
4.88e-4 (v4 at the same steps: 4.10e-4 / 4.85e-4), final **6.28e-4**. Four
datasets/captions/resolutions now land within 6% of each other — magnitude
is set by lr × steps under Adam, not by what was learned.

**Renders** — HQ path (Q8 `--two-stages-hq`, 704×384, 25 frames), each
prompt with its **own** no-LoRA cross-seed baseline. All LoRA renders
attached `576 modules, 0 skipped`.

| cross-seed PSNR (12345 vs 777), dB | no-LoRA | v3 | v4 | **v5** |
|---|---|---|---|---|
| prompt A — *"A cinematic close-up portrait of valeriosan, a man, …"* | 18.89 | 18.92 | 18.54 | **19.51** |
| prompt B — v5's own `char_030` caption verbatim | 17.44 | — | — | **17.67** |

v5 is the first adapter to score above its baseline at all (+0.6 dB on A,
+0.2 on B), but that is a nudge, not an identity. By eye, on both prompts:
at each seed v5 renders the **same man as no-LoRA** with small changes of
expression and lighting; the two seeds are two different men; neither is
the subject. **The class noun is ruled out as the missing piece on its
own.** (The no-LoRA prompt-A pair also gives the earlier HQ v3/v4 table its
missing baseline: 18.89 dB, so v3's 18.92 and v4's 18.54 were baseline
too.)

### What every failed run here has in common

Trigger fix (v2→v3), curated dataset + center crop (v3), 768 px (v4), class
noun (v5): four independent interventions, the same empty adapter. What
none of them varied: the trainer code and vendored pin on this branch, this
machine and its Q8 pack, the lr/steps/rank recipe, and the subject. The
adapters known to carry a face (`bizarrotrn`, `elontrn`, `ariatrn`,
`eltrumpo`) were trained earlier, elsewhere — and **the control that would
separate "this trainer cannot bind a face today" from "this subject/recipe
does not bind" has not been reported**: STATE.md's E2 (2026-08-22,
retraining `eltrumpo` with the then-current recipe on the owner's 64 GB M4
Max, the old file backed up to `mlx_models/loras/_backup_20260822/`) is
still listed open on 2026-08-23. Neither those datasets nor those adapters
exist on this machine, so the control cannot be run here.

### Cross-check against the #62 thread (read 2026-10-01)

The GitHub thread (56 comments) is further along than STATE.md, and two
things in it bear directly on this document:

- **The identity-family strength separates working from failing adapters,
  and all four runs here are on the failing side.** The maintainer's
  per-family measurement (`lora_compat.py`, video-attn identity family, 384
  modules) puts the characters that render reliably at **1.72e-03
  (`bizarrotrn_v2`) and 1.63e-03 (`ariatrn_v2`)**, and another reporter's
  failing High run at **7.80e-04** — "half the strength of a working
  adapter". Measured the same way here:

  | adapter | change | identity-family median |
  |---|---|---|
  | `valeriosan_v2` | stale trigger, letterbox | 7.04e-4 |
  | `valeriosan_v3` | correct trigger, curated, center | 7.11e-4 |
  | `valeriosan_v4_768` | v3 at 768 px | 6.76e-4 |
  | `valeriosan_v5_classword` | v3 with `valeriosan man,` | 6.73e-4 |

  Four datasets/captions/resolutions, one band — and the same band as the
  other reporter's run on a different machine and dataset. That is the
  pattern a trainer- or recipe-level cause would produce and a data-level
  one would not. (The earlier "magnitude cannot tell these runs apart" is
  still true *among* these runs; against the reference adapters it is the
  clearest signal there is.)
- **The reproduction control is in progress on the maintainer's side.** The
  08-31 test rendered the *existing* `eltrumpo_v2` (identity held, 2.5 Q8) —
  it shows the render path works, not that today's trainer can produce such
  an adapter. On 09-17 the maintainer committed to retraining the other
  reporter's full dataset on their own machine "and measure the adapter …
  I'll post the number here when it's done". As of 09-29 it has not been
  posted. That run, plus E2, is the experiment this document's next step
  names.

Also noted, not chased: the sidecar labels the training base
"full-precision", but the file is `ltx-2.3-mlx-q8/transformer-dev.safetensors`
at 20.6 GB — roughly one byte per parameter for a ~22B model, i.e. the int8
pack. Training a LoRA over a quantized base is normally fine (QLoRA), so this
is a labelling question first; it is listed because it is one of the things
every run here shares.

## Trainer regression check (2026-10-01, evening)

The question was whether a code change between the runs that produced working
character adapters and the runs reported in #62 broke training. It was answered
from the code history, plus numerical experiments on this machine (64 GB M4
Max). Every GPU run held both GPU locks per CLAUDE.md §7. Harness scripts, raw
results, the 2-image control set and every control adapter are in
`state/issue62_trainer_regression/` (gitignored).

**Verdict.** There is no regression in the vendored trainer or model code
between the pin that trained the working adapters and today's.

- A training step under `v0.14.8` and under `v0.14.19+ltx25.7` is numerically
  identical, except for `av_ca_timestep_scale_multiplier`. That value does not
  change adapter strength, and it does not change whether the trainer fits its
  data.
- The gradient path is correct.

What the experiments do show is that the panel's recipe, on this pipeline,
does not fit its own training images at all, at either multiplier:

- not the 37-image v5 set after 3700 steps;
- not a 740-step rerun of it;
- not a 2-image control after 150 epochs per image.

The adapters that carry a face came out of a different pipeline (see the
window below). On the evidence available, the vendored trainer has never been
shown to bind an identity.

### The regression window

| period | `ltx-2-mlx` pin | trainer / model code | adapters trained |
|---|---|---|---|
| ≤ 2026-05-15 | authoring tree (`~/AI/projects/lora-lab/`), before vendoring | — | `bizarrotrn_v2` and the other reference characters. `bizarrotrn_v2` has 2304 tensors = **1152 modules**, LoRA on all six attentions per block, so it predates the audio-target filter |
| 05-13 → 06-01 | `v0.14.0` | identical to `v0.14.8`. `git diff v0.14.0 v0.14.8` over `packages/ltx-trainer` and `ltx-core-mlx/.../transformer` is the version string only | the first vendored-trainer retrain of a reference character (05-18) came out **"worse"** than the original. Comment near `lora_lab/train_character.py:710`; attributed at the time to data-order RNG |
| 06-01 → 08-12 | `v0.14.8` | — | — |
| 08-12 → now | `v0.14.19+ltx25.x` | multiplier read from the checkpoint (1 → 1000) | #62 (v4.5.0), #61, the other reporter's runs, `valeriosan_v2`–`v5` |

Other recipe differences between the two pipelines:

- `_patch_lora_target_exclude_audio` (drops `audio_attn*` and
  `video_to_audio_attn`) has been in `lora_lab/train.py` since the trainer was
  vendored (`e9ce853`, 05-17). Every panel-trained adapter has 576 modules.
- The lora_lab CLI's own "high" preset, which is the authoring-tree recipe, is
  rank 32 / 5000 steps / **576 px** / class-word captions.
- The panel's "high" is rank 32 / 100 epochs / 512 px / user captions.

### Static diff, `v0.14.8` → `v0.14.19+ltx25.7`, as it applies to a 2.3 training run

- `trainer.py`: the conditions branch now reads
  `"video_prompt_embeds" not in conditions and feature_extractor is not None`.
  Phosphene trains without validation prompts, so `feature_extractor` is
  `None` and both versions take the same branch. The other changes (logging
  cadence, `transformer_file`, opt-in gradient checkpointing) have no effect
  on a default run.
- `model.py`, `transformer.py` and `feed_forward.py`: every LTX-2.5
  architecture flag defaults to its 2.3 value. In `utils/weights.py`,
  `derive_quant_params` gives bits 8 / group 64 for the Q8 dev, the same as
  before.
- `LTXModel()` became `LTXModel(LTXModelConfig.from_checkpoint_dir(...))`.
  Compared field by field against `ltx-2.3-mlx-q8/embedded_config.json`, the
  **only** difference is `av_ca_timestep_scale_multiplier` 1 → 1000 (#37,
  0.14.11). RoPE type, max positions, theta, eps and head counts are all
  identical.
- The installed site-packages equal the tag source, apart from the codec patch
  (`video_vae.py:487`, ffmpeg args only).
- The lora_lab changes over the same window do not touch training math: the
  sidecar, the strength report, target-module plumbing and the dev-transformer
  size guard.
- The training base is `ltx-2.3-mlx-q8/transformer-dev.safetensors`. It is
  **int8** (U32-packed weights, 8 bits, group 64), whatever the sidecar's
  "full-precision" label says.

### One training step, old code vs new code

`scripts/grad_ab.py` runs the real `LtxvTrainer` with the four
`lora_lab.train` patches and v5's config, with v5's adapter loaded into the
LoRA params. It records the loss and every LoRA gradient for 5 samples × 2
noise/sigma seeds. The `v0.14.8` arm imports `ltx-core-mlx`,
`ltx-pipelines-mlx` and `ltx-trainer` from `git archive v0.14.8`.

| comparison | loss | gradient cosine, mean / min | gradient norm ratio |
|---|---|---|---|
| `v0.14.8` vs current, both at multiplier 1 | identical to 6 decimals | **1.0000 / 1.0000** in every family | 1.000 |
| current, multiplier 1000 vs 1 | \|Δ\| ≤ 0.002 | 0.687 / −0.027 (`attn2` 0.724 / −0.398; `audio_to_video_attn` 0.518) | 1.71 (`attn2` 2.29) |

Cross-image gradient agreement under the two multipliers:

| mean pairwise cosine | multiplier 1 | multiplier 1000 |
|---|---|---|
| same image, different noise + sigma | +0.081 | +0.087 |
| different images | +0.011 | +0.003 |

**Reproducing this requires `LTX2_DIT_EVAL_EVERY=0` and
`LTX2_GEMMA_EVAL_EVERY=0`**, which `scripts/lora_lab_run.sh` exports for every
training run. Without them the DiT forces an `mx.eval` every 8 blocks inside
the autodiff graph. The footprint then reached 63 GB on this Mac and a step
took 65 s instead of 2.5 s. The values are unaffected.

#### Why the multiplier reaches training at all

The trainer feeds the joint model a dummy audio stream: a zero latent with
audio timesteps of 0 (`trainer.py`, `_build_loss_fn`). For a 1-frame image,
`compute_audio_token_count` returns **one token**, so `audio_to_video_attn` is
a softmax over a single key. Two consequences:

- Its `to_q`/`to_k` get exactly zero gradient. These are the 96
  non-carrying modules in every adapter here, verified zero in all five v5
  checkpoints.
- Its `to_v`/`to_out` learn a constant, text-independent vector that is added
  to every video token.

The multiplier feeds only the a2v/v2a gates (`t_emb_av_gate`). Measured on the
Q8 dev weights:

- the a2v gate changes 27–29% element-wise (cosine 0.96);
- the v2a gate changes 5%;
- mean |gate| is about 0.14 either way.

At render time the audio stream has about 26 real, noisy tokens for 25 frames.
That train/render mismatch exists at both multipliers.

### The gradient path is correct

`scripts/descent_check.py` holds one sample, sigma 0.6 and one noise draw
fixed. It uses the trainer's own `loss_fn`, `nn.value_and_grad`,
`clip_grad_norm(1.0)` and AdamW.

| lr | steps | loss | result |
|---|---|---|---|
| 1e-4 | 25 | **0.412 → 0.082 (−80%)** | monotone; same at multiplier 1000 and 1 |
| 1e-3 | 25 | +281% | diverges |

The LoRA after the 25 lr-1e-4 steps measures delta-RMS 2.9e-4. Nothing in the
loss, the targets or the backward pass is broken.

### The shipped recipe does not fit its own training images

`scripts/probe_fit.py` runs the trainer's own forward at a fixed sigma
(0.3 / 0.6 / 0.85 / 0.97) and fixed noise draws. It pairs training samples by
**filename**, because `PrecomputedDataset` discovers files with an unsorted
glob, and it switches the LoRA on and off and the trigger in and out of the
caption.

- **fit** = loss(no LoRA) − loss(LoRA), on the image the adapter was trained
  on.
- **binding** = how much of that fit disappears when the trigger is deleted.

A working character adapter must show both. These are render-free, take about
10 minutes, and measure the objective the trainer actually optimised.

| adapter | training | fit at σ 0.3 / 0.6 / 0.85 / 0.97 | trigger binding | identity-family RMS |
|---|---|---|---|---|
| `valeriosan_v5_classword` | 37 images, 3700 steps | −0.1% / −0.0% / −0.1% / −0.1% | ≈ 0 | 6.73e-4 |
| v5 data, multiplier 1000 | 740 steps (fresh run) | −0.02% / −0.11% / −0.05% / −0.00% | ≈ 0 | 4.52e-4 |
| v5 data, multiplier 1 | 740 steps (same init, order, noise) | −0.01% / −0.02% / +0.00% / −0.02% | ≈ 0 | 4.28e-4 |
| 2 images, multiplier 1000 | 300 steps = 150 epochs/image | −0.03% / −0.01% / +0.06% / +0.08% | ≈ 0 | 3.67e-4 |
| 2 images, multiplier 1 | 300 steps | −0.03% / −0.01% / +0.05% / −0.02% | ≈ 0 | 4.03e-4 |

The 740-step multiplier-1000 arm reproduces v5's own step-740 checkpoint
(4.27e-4), which validates the setup. Restoring the old multiplier gives
neither a stronger adapter nor a fit. It does change the trajectory: the two
740-step arms share init, data order and noise, and their deltas still overlap
only +0.17.

**The adapters move a lot and learn nothing.**

- 300 real steps produce about the delta of 25 coherent ones (3.7e-4 vs 2.9e-4).
- The 25 coherent steps cut their target loss by 80%.
- The 300 real steps move the two training images by under 0.1%.

The per-step gradients explain it. About 92% of a step is specific to its noise
draw, and well under 1% is shared across images, which is where an identity
would have to live. In the v5 checkpoints the two kinds of projection behave
differently across successive 740-step increments:

| projections | correlation of successive increments |
|---|---|
| `attn2.to_v` (carries text into the image) | cos 0.01–0.07 |
| `attn2.to_k` (carries text into the image) | −0.01 … 0.16 |
| `attn1.to_q`, `attn2.to_out` | ≈ 0.26, steady drift |

With batch 1 and a constant lr, Adam random-walks.

### Two data-path bugs found on the way, and what they do to v3–v5

1. **The crop ignores EXIF orientation.** `lora_lab/train_character.py:441`
   opens images with `Image.open(src).convert("RGB")`, with no
   `ImageOps.exif_transpose`. The panel only started normalising orientation at
   ingest in v4.17.0 (2026-09-29, `6cf2da2`). Every Train-tab run before that,
   and every hand-built dataset since, trains phone photos as stored. Measured
   from the crops (contact sheets: `results/sheet_v2.png`, `sheet_v5.png`):
   - `valeriosan_v2`: **8 of 42** crops rotated 90°, and the `letterbox` crop
     also shrinks most faces;
   - v3/v4/v5: 4 of 37.
2. **Captions pair with the wrong image when the source files are already
   named `char_NNN` with gaps.** `crop_and_caption` looks up
   `captions/char_{i:03d}.txt` (the new sequential stem) *before*
   `captions/{src.stem}.txt`, then writes the result back to the sequential
   stem.
   - The curated v3 set (sources `char_002, char_004, char_005, char_008, …`)
     trained **20 of 37 images on another photo's caption**. For example,
     image `char_004` got the caption of `char_002`, and `char_008` got that of
     `char_004`.
   - v4 and v5 copied v3's rewritten captions, so they trained on the same
     pairing. v5's log shows the duplicates this predicts: "Reusing cached
     encoding" at items 2, 5, 10, 11 and 25.
   - The "17 orphan captions" noted under v4 are actually the untouched
     originals for source stems ≥ 038.
   - Train-tab users are not exposed, because panel datasets are always
     dense: `/train/upload` names files `char_{n+1:03d}`, and
     `/train/remove-image` renumbers both images and captions.
     `valeriosan_v2` (dense, `char_001…042`) was paired correctly.

What this means for the rest of this document: v3, v4 and v5 were not the
clean single-variable tests they were described as. The claim that curated,
individually-written captions performed identically to auto-generated ones was
never actually tested. Neither bug explains the fit result, though: the 2-image
control uses two upright images with their own captions and still fits
nothing.

### What this leaves

- **No regression in the vendored trainer or model code.** `v0.14.0`,
  `v0.14.8` and the current pin compute the same training step, except for the
  multiplier.
- **The multiplier fix (1 → 1000) is correct for inference, and it does change
  training.** Per-step gradients rotate (cosine 0.69) and cross-image
  agreement drops 2–4× (0.017 → 0.009 on `lora_b` only, 0.011 → 0.003 over
  all kept modules). But it does not explain the "half-strength" adapters:
  at 740 steps, multiplier 1 is no stronger and fits no better.
- **The reference adapters came from a different pipeline**, the authoring
  tree before 05-15:
  - LoRA on all six attention families, including the audio stream, which
    gives the 1-token dummy audio a trainable path into the video;
  - 576 px, class-word captions, 5000 steps;
  - multiplier 1;
  - base weights unrecorded.

  The first retrain through the vendored trainer was already reported as
  "worse".
- **The reproduction control (1a below) can now be run without renders.**
  Retrain a reference dataset through the current panel trainer and run
  `probe_fit.py` on the result. A working adapter must show a clearly positive
  fit and trigger binding. It is also worth running `probe_fit.py` against a
  reference adapter (`bizarrotrn_v2` on its own data) on a machine that has
  both, to calibrate what "positive" looks like.

## Comparison against known-working trainers (2026-10-02)

The regression check above showed that the trainer code has not changed
between the pins. That leaves a different question: does the vendored trainer
do what the trainers that *do* bind a face do? This section answers it from the
source of two such trainers. Both are sparse clones in `reference_trainers/`,
which is gitignored:

| trainer | why it counts as working | snapshot |
|---|---|---|
| Lightricks `ltx-trainer` (`ltx-2-official/packages/ltx-trainer`) | the PyTorch reference `ltx_trainer_mlx` is ported from | `2d6e71c`, 2026-09-30 |
| ostris ai-toolkit (`ai-toolkit/extensions_built_in/diffusion_models/ltx2`) | trained the CivitAI LoRA (`training_info: step 3400`, `software: ai-toolkit 0.9.8`) that Morac2 reports working in Phosphene on #62, 08-19/08-22; Ostris publishes an LTX-2.3 character tutorial | `ecee894`, 2026-09-27 |

Sceneworks, the Mac trainer @saved-j reports a working character from, is
closed source and was not compared. The authoring tree that trained
`bizarrotrn_v2` is not on this machine.

### Identical in all three (ruled out)

- **Timestep sampler:** `timestep_samplers.py` is a line-for-line port of the
  official one. It is a shifted logit-normal with stretching and a 10% uniform
  fallback; the shift is 0.675 at 256 tokens.
- **Noising, target and loss:** `x_t = (1−σ)x₀ + σε`, the velocity target
  `ε − x₀`, and a masked MSE normalised by mask density.
- **LoRA initialisation and scale:** `mlx_lm` `LoRALinear` starts A uniform in
  ±1/√in and B at zero, with scale α/r = 1. That is PEFT's
  `init_lora_weights=True`.
- **Text conditioning:** training (`ltx_trainer_mlx/preprocess.py`) and
  Phosphene's own inference (`ltx_pipelines_mlx/utils/blocks.py::PromptEncoder`)
  build `GemmaFeaturesExtractorV2()` and load `connector.safetensors`
  identically. Neither passes a text mask to the DiT.
- **Data:** each latent pairs with its own condition by filename
  (`datasets.py`), and the VAE latents are per-channel normalised
  (`VideoEncoder.encode` → `normalize_latent`).
- **Gradients through the int8 base.** These are new and were measured:

  | setup | value |
  |---|---|
  | test chain | LoRA on an int8 `QuantizedLinear` (bits 8, group 64), then two more int8 layers |
  | compared with | the same weights dequantized into plain `nn.Linear` |
  | loss | identical |
  | `lora_a` / `lora_b` gradient cosine | 1.000000 |
  | `lora_a` / `lora_b` gradient norm ratio | 1.000000 |
  | device | GPU, which is what training uses |

  The quantized backward is not what corrupts training.

### Differences

| | Phosphene (MLX) | Lightricks official | ai-toolkit |
|---|---|---|---|
| Stand-in audio for image training | 1 all-zero token, **audio σ = 0**; a2v cross-attention runs | `audio=None`; ltx-core sets `run_a2v`/`run_v2a` false and **skips** it | 1 all-zero token at **audio σ = video σ** |
| a2v gate driven by | video σ (`model.py:547-585`) | audio σ (`transformer_args.py::_prepare_cross_attention_timestep`) | equal σ on both streams |
| Adam bias correction | **off**: `mlx.optimizers.AdamW` default, never set by `trainer.py:613` | on (torch `AdamW`) | on (bitsandbytes `AdamW8bit`) |
| LR schedule | constant | linear 1.0 → 0.1 (`t2v_lora.yaml`) | constant |
| LoRA targets | 576: q/k/v/out on `attn1`, `attn2`, `audio_to_video_attn` | 1152: q/k/v/`to_out.0` on all six attention families | 1632: every linear in the blocks, including `to_gate_logits` and both FFs (rank 48 on the CivitAI file) |
| Training resolution | 512 only (16×16 = 256 tokens) | user-set buckets | 512 / 768 / 1024 buckets (up to 1024 tokens) |
| Base weights | int8 | bf16 | qfloat8 (`quantize: true`) |
| Weight decay / caption dropout | 0 / none | 0.01 (torch default) / none | 1e-4 / 5% |
| Default steps | 100 × image count | 2000 | 3000 |

The bias-correction gap is concrete. Uncorrected, Adam's step is
`(1 − β₁ᵗ)/√(1 − β₂ᵗ)` times the corrected one:

| step | uncorrected ÷ corrected |
|---|---|
| 1 | 3.2× |
| 10–15 | 6.5× |
| 100 | 3.3× |
| 500 | 1.6× |
| 1000 | 1.26× |

So every lr in this document's tables is nominal. The descent check's "1e-4"
ran its 25 steps at roughly 5e-4, and its diverging "1e-3" at roughly 5e-3.

The a2v-gate divergence is in the model, not the trainer. It changes nothing
when the two streams share a σ, which is every T2V and I2V render. It does
change the result whenever they differ: the stand-in audio here, and probably
clean-audio A2V and lipdub. Fixing it means editing vendored `ltx-core-mlx`
under a pinned fork tag, with an output check on the A2V and lipdub lanes. The
training fixes below avoid that dependency instead, because neither
`skip_a2v` nor `matched_sigma` lets the two σ differ.

Ranked by how plausibly each explains "the adapter moves a lot and fits
nothing":

1. **Bias correction.** A recipe that runs at several times its nominal lr
   during the steps that set the adapter's direction would explain a lot.
2. **Constant lr.** Per-step gradients are ~92% noise draw. A decaying lr
   damps exactly that noise in the final iterate; a constant one keeps it.
3. **The stand-in audio.** Clean audio next to noisy video, plus a gate at the
   wrong σ, is a model state neither reference ever trains in. It adds a
   constant a2v term to every video token in all 48 blocks.
4. **Targets.** Fewer trainable modules, and the official default is exactly
   `bizarrotrn_v2`'s set.

Resolution and base precision rank lower. v4 ruled out 768 px, and ai-toolkit
also trains on an 8-bit base.

## The iter7 recipe switches (2026-10-02)

All four differences are now switches. They live in `lora_lab` and nothing in
the vendored `ltx-2-mlx` tree was edited.

- **Where:** `RECIPE_SWITCHES` and `resolve_recipe` in
  `lora_lab/train_character.py`, plus three patches in `lora_lab/train.py`.
- **How they are set:** a spec may carry each key, flat or under `advanced`,
  just like `rank`. A value outside the allowed set fails the job at plan time.
  It is never defaulted, so a typo cannot silently run the wrong arm.
- **Defaults:** the legacy recipe, set back after the
  [screen](#screen-results-with-the-save-fixed-2026-10-02). The reference
  values were the default for the first half of the day.
- **Legacy value:** the last allowed value of each switch reproduces every
  adapter trained before iter7.

| switch | reference value | default = legacy (≤ iter6) | patch |
|---|---|---|---|
| `adam_bias_correction` | `true` | `false` | `_patch_adam_bias_correction`: sets `bias_correction` on the AdamW the vendored `_init_optimizer` builds |
| `scheduler_type` | `linear` (1.0 → 0.1, the trainer's own linear defaults) | `constant` | config only |
| `image_audio` | `skip_a2v` | `clean_zero` | `_patch_image_only_audio`, described below |
| `lora_target_families` | `video` (576) | `video` | `_patch_lora_target_exclude_audio(enabled)`; `all_attention` = 1152 |

How `_patch_image_only_audio` works:

- It does not copy the vendored loss function. For the length of one loss
  evaluation it swaps a forwarding object in for `trainer._transformer`, and
  that object rewrites only the stand-in audio arguments.
- `value_and_grad` was built on the real model, so gradients are unchanged in
  kind.
- `skip_a2v` passes a `SKIP_A2V_CROSS_ATTN` + `SKIP_V2A_CROSS_ATTN`
  perturbation on every block. The block multiplies the cross-attention output
  by exactly 0, so values and gradients both match ltx-core's skip.
- `matched_sigma` sets the audio timesteps to the video σ, as ai-toolkit does.
- A run that trains real audio (`requires_audio`) is never touched.

**Why `all_attention` is opt-in rather than the default.** Under `skip_a2v`,
the audio families get no gradient and are saved as exact zeros. That is what
the official trainer does with images too, so arm C of the earlier plan was a
no-op as written. Paired with `matched_sigma` or `clean_zero`, they train on
the stand-in audio, which is the 2026-05-15 Aria v2 lip-sync breakage that
`_patch_lora_target_exclude_audio` was written for. `run_training` warns on the
`all_attention` + `skip_a2v` pairing.

What else changed:

- **Version:** `LORA_LAB_VERSION` is now `iter7`.
- **Records:** the sidecar gains a `recipe` block and the `plan` event carries
  the same dict.
- **Standalone CLI:** `python -m lora_lab.train` applies the bias correction
  and `skip_a2v` too; its yaml still owns the scheduler.

**Verified:**

- **`test_train_recipe_switches.py`, 15 tests, CPU-only so it needs no GPU
  lock.** It runs the real vendored `_build_loss_fn` on a tiny two-block
  `LTXModel` and checks:
  - under `skip_a2v`, every `audio_to_video_attn`, `audio_attn1/2` and
    `video_to_audio_attn` gradient is exactly 0, and `attn2` still trains;
  - under `clean_zero` and `matched_sigma` the a2v gradients are non-zero, and
    the two losses differ;
  - `matched_sigma` hands the model audio timesteps equal to σ;
  - a corrected first Adam step is lr-sized, against 3.16× uncorrected;
  - linear decay runs 1.0 → 0.55 → 0.1;
  - the target filter switches 24 ↔ 48 modules per two blocks.

  The existing trainer, caption, manifest, `lora_compat` and duplicate-def
  suites still pass.
- **A real 10-step run through `train_character.run_training`** on the 2-image
  control set, under both GPU locks. Scripts are
  `state/issue62_trainer_regression/scripts/smoke_recipe.py` and
  `run_smoke.sh`; each run took about a minute.

  | arm | `audio_to_video_attn` | attn1 / attn2 median ΔRMS | interval loss |
  |---|---|---|---|
  | reference values (then default) | **192 / 192 exactly zero** | 1.18e-5 / 9.86e-6 | 0.517 |
  | legacy (`false` / `constant` / `clean_zero`) | 96 / 192 zero (q/k: one-token softmax) | 7.90e-5 / 4.18e-5 | 0.518 |

  The legacy arm moved the adapter 4–7× further in the same 10 steps. That fits
  an uncorrected Adam step (3–6.5× here) and, in a 10-step run, a linear
  schedule that decays almost immediately. It shows the switches act; it says
  nothing yet about fit.
- **Not done: `release_gates.sh --fast` finished.** It exceeded a 10-minute cap
  on this machine and was stopped at `test_storyboard_assembly`, before the
  `test_train_*` suites; those were run directly instead.
  - 19 suites failed before the stop. None imports `lora_lab` or
    `train_character`.
  - The visible causes are `pytest` missing from the venv, the shipped
    `bizarrotrn` character not installed here, an incomplete HQ weight
    surface, and node harness failures.
  - A full gate run is still owed before any commit.

One more trainer defect surfaced on the way. `checkpoint_keep_last_n: 1`
deletes the run's final checkpoint. When `steps` is a multiple of the
checkpoint interval, the final path is recorded twice, and pruning to the last
entry unlinks the file just written. This is the same double-record as the
`keep_last_n: 5` off-by-one above. The panel default is 2, so no shipped run
has hit it.

## Root cause: every adapter was saved scrambled (2026-10-02)

### How it surfaced

Screen arm L ran the legacy recipe through the panel's own `run_training` on
the 2-image control. Its training loss (interval means) fell from 0.518 to
**0.167** over 300 steps. `probe_fit.py` then measured the saved adapter's fit
on those same two images at ±0.08%. The 10-01 control runs had logged the same
contradiction without anyone reading it (`run_queue.log`): first-quarter loss
0.386, last-quarter 0.243, and probe fit under 0.1%.

A model whose training loss drops by a third cannot have zero fit on its
training images — unless the file being probed is not the model that trained.

### Mechanism

- **The save.** `ltx_trainer_mlx/trainer.py:812-818` writes each factor in
  ComfyUI layout as `np.array(mx.transpose(param).astype(mx.float32))`.
  - The params are already float32, so `astype` is a no-op and the transpose
    stays a **view**.
  - numpy receives a correctly strided but non-C-contiguous array. Its values
    are right.
- **The writer.** `safetensors.numpy` **0.8.0** serializes
  `data_ptr=tensor.ctypes.data, data_len=tensor.nbytes` in `_flatten`. That is
  the raw buffer with the strides ignored. Its own docstring says tensors "need
  to be contiguous".
  - 0.4.5, 0.5.3, 0.6.2 and 0.7.0 all wrote `tobytes()`, a C-order copy, and
    were correct (checked in each wheel's `safetensors/numpy.py`).
- **The result on disk.**
  - `lora_A.weight` of shape `(r, in)` holds the bytes of `lora_a`'s
    `(in, r)`.
  - `lora_B.weight` of shape `(out, r)` holds the bytes of `lora_b`'s
    `(r, out)`.
  - With random factors at real shapes (4096×32), the file's delta has
    **0.9996×** the trained norm at cosine **−0.0002**: uncorrelated in
    direction.
  - On trained adapters, whose factors are co-adapted, the scrambled product
    is also smaller: `lora_compat` reads roughly **half** the trained
    strength. The next subsection gives the numbers.
- **Not affected.**
  - Training latents: the bf16 → f32 `astype` is a real copy. A fresh encode
    of a control image matches the saved latent at correlation 0.99992.
  - Conditions: the probe's sanity check shows a diff of 0.0.

### Why the code diff found nothing

| when | what |
|---|---|
| ≤ 05-15 | `bizarrotrn_v2` and the other reference characters trained in the authoring tree, on a pre-0.8.0 safetensors: correct files |
| 05-17 | trainer vendored, with the same `_save_checkpoint` |
| **06-09** | **safetensors 0.8.0 released.** `ltx-core-mlx` and `ltx-trainer` require only `safetensors>=0.4.0`, so every new install or Update picks it up |
| July | #35/#36: "near-zero LoRA" |
| 08-17 → | #61, #62 and every report in that thread |
| 09-25 | this machine's venv got 0.8.0 (dist-info mtime) |

The [regression check](#trainer-regression-check-2026-10-01-evening) was right
that the training math never changed. The regression is in a dependency's
writer, below the code it diffed.

### What it explains, and what it does not

**Explained:**

- clean attach, healthy `delta_rms`, an output that changes and no identity:
  every adapter in this document and in the thread;
- "magnitude is necessary, not sufficient" (@blackest, 5.36e-4);
- Morac2's raw ltx-2-mlx runs failing the same way, because they used the same
  save code;
- **voice LoRAs that do nothing**: `lora_lab/train_audio.py` saves through the
  same `LtxvTrainer`;
- `probe_fit` reading ~0 on every adapter while training loss fell.
- **The identity-family "half strength" against the characters that work.**
  `lora_compat.py`, identity-family median:

  | adapter | as saved | unscrambled |
  |---|---|---|
  | `valeriosan_v2` | 7.04e-4 | **1.35e-3** |
  | `valeriosan_v3` | 7.11e-4 | **1.49e-3** |
  | `valeriosan_v4_768` | 6.76e-4 | **1.33e-3** |
  | `valeriosan_v5_classword` | 6.73e-4 | **1.39e-3** |
  | screen arm L (300 steps) | 3.67e-4 | 6.86e-4 |
  | *`ariatrn_v2` / `bizarrotrn_v2` (thread, 09-10)* | | *1.63e-3 / 1.72e-3* |

  The other reporters' "half strength" numbers are the same artifact, e.g.
  @PiotrAstroCamp's 7.80e-4. The Train-tab recipe produces adapters in the
  working band; the save halved them on disk.

**Not changed:**

- **The other bugs this document found:** Bug A (wrong trigger), the EXIF crop
  and caption re-pairing.
- **The iter7 recipe switches.** They were chasing the wrong cause; the screen
  below now decides whether they help.

### Evidence

`probe_fit.py`, basic arms; cells are fit at σ 0.3 / 0.6 / 0.85 / 0.97:

| adapter | probed on | as saved | unscrambled |
|---|---|---|---|
| screen arm L (legacy recipe, 300 steps) | 2-image control | −0.03 / −0.01 / +0.06 / +0.08% | **+67.2 / +82.5 / +77.5 / +41.2%** |
| `valeriosan_v5_classword` (3700 steps) | its own 37 images | −0.1 / −0.0 / −0.1 / −0.1% | **+18.3 / +35.5 / +26.5 / +5.4%** |

Trigger binding stays near zero even unscrambled (v5: +0.00002 to −0.0044).
Deleting `valeriosan ` from a caption does not undo the fit, so v5 learned the
images as an always-on adapter rather than one keyed to its trigger. That is a
recipe question, and with the save fixed the screen can measure it.

**Render** (`state/issue62_trainer_regression/renders/grid_v5_unscrambled.png`):
2.3 HQ path, Q8 dev, 704×384, 25 frames, the Test 1 prompt, seeds 12345 and
777, attached unfused (576 modules).

- **No LoRA vs v5 as saved:** near-identical frames at each seed. The
  scrambled adapter barely moves the output, as every render in this document
  found.
- **v5 unscrambled:** a different man from the base model's, at both seeds,
  with short dark hair and a stubbly beard, in casual phone-selfie framing like
  the training photos. The two seeds now resemble each other.
- Likeness to the subject is for the owner to judge from the grid.

### Fix and repair (shipped on `issue-62`)

- **`lora_lab.train._patch_contiguous_checkpoint_save`.**
  - It wraps the trainer module's `save_safetensors` so every tensor is passed
    through `np.ascontiguousarray`. That is a no-op on contiguous arrays.
  - It is applied unconditionally by `train_character.run_training`,
    `train_audio`, and the `lora_lab.train` CLI. It is not a recipe switch.
  - `test_train_checkpoint_layout.py` runs the real `_setup_lora` and
    `_save_checkpoint` on a tiny `LTXModel`. Patched, the reloaded file equals
    the in-memory factors bit for bit. Unpatched, it is scrambled (|cos| < 0.5,
    skipped on a safetensors that honours strides).
- **`python -m lora_lab.repair_scrambled_lora FILE ... [--out F | --in-place]`.**
  - Unscrambling is a pure reinterpretation, so the repair is exact:
    `A = file_A.reshape(in, r).T`, `B = file_B.reshape(r, out).T`.
  - Applied to a correctly saved file it would scramble it, so it is gated on a
    content check rather than on dates. In a real adapter the rank rows of
    `|lora_A|` share an input-channel magnitude profile; the scramble destroys
    it.
  - It scores both readings and acts only when one clears a noise floor and
    beats the other by 3×.
  - `--in-place` keeps `<file>.scrambled.bak` and notes the repair in the
    sidecar.
  - Report-only is the default. `test_repair_scrambled_lora.py` (5 tests) pins
    exact repair, never touching a correct file, refusing to guess on noise,
    and the backup and sidecar behaviour.

Detector scores, read-only, on every adapter on this machine (median |A|
row-correlation, as-is / unscrambled reading):

| file | as-is | unscrambled | verdict |
|---|---|---|---|
| `valeriosan_v2` / `v3` / `v4_768` / `v5_classword` | 0.0014–0.0020 | 0.0360–0.0406 | scrambled |
| 10-01 control adapters (`tiny_*`, `ab_*`) | 0.0009–0.0012 | 0.0118–0.0168 | scrambled |
| screen arm L, as saved | 0.0012 | 0.0118 | scrambled |
| the two unscrambled copies | 0.0118 / 0.0362 | 0.0010 / 0.0018 | ok |
| DoctorDiffusion Colorizer (PyTorch, third-party) | 0.0256 | 0.0032 | ok |

No file under `mlx_models/` was modified. Repaired copies live in
`state/issue62_trainer_regression/adapters/`.

**Not done:**

- **The upstream fix in `ltx-2-mlx`'s `_save_checkpoint`:**
  `np.ascontiguousarray`, or `mx.save_safetensors`. Users of the raw trainer
  stay affected until then.
- **Detection in `lora_compat`**, at measure or attach time, so the panel tells
  a user their adapter needs the repair.
- **Running the repair on voice adapters:** it uses the same transform, but no
  voice adapter exists on this machine to check the detector on.
- **Pinning `safetensors<0.8`:** unnecessary with the patch, though it would
  also have prevented this.

## Screen results with the save fixed (2026-10-02)

**Setup.**

- **Data and recipe:** the 2-image control, 300 steps per arm, rank 32,
  lr 1e-4, through `train_character.run_training`.
- **Shared between arms:** the LoRA init (`--init-seed 42`), the data order and
  the noise draws.
- **Scoring:** `probe_fit.py`, basic arms, samples 0 and 1, seeds 1–3, each arm
  scored under the objective it trained on.
- **Arm L:** the stopped first run's adapter, unscrambled. The repair is exact,
  so that is the trained adapter.
- **Tooling:** `scripts/run_screen.sh` and `scripts/summarize_screen.py`.

Each cell is fit / trigger binding:

| arm | recipe | σ 0.3 | σ 0.6 | σ 0.85 | σ 0.97 | mean fit |
|---|---|---|---|---|---|---|
| L | legacy (no bias correction, constant lr, clean-zero audio) | +67.2% / +0.0000 | +82.5% / −0.0001 | +77.5% / +0.0009 | +41.2% / +0.0021 | **67.1%** |
| A | + bias correction, linear decay | +13.9% / −0.0000 | +33.3% / −0.0002 | +39.4% / +0.0002 | +31.2% / +0.0056 | 29.4% |
| B | A + `skip_a2v` (the reference defaults, since reverted) | +12.6% / −0.0000 | +30.9% / +0.0001 | +36.5% / +0.0002 | +24.6% / +0.0003 | 26.1% |
| B′ | A + `matched_sigma` | +12.7% / −0.0000 | +32.0% / +0.0002 | +36.8% / −0.0004 | +28.1% / +0.0010 | 27.4% |
| C | B′ + `all_attention` (1152) | +12.8% / −0.0001 | +31.0% / +0.0002 | +38.2% / −0.0010 | +28.4% / −0.0013 | 27.6% |

**What this shows.**

- **Every arm fits** once the save is correct.
- **The optimizer switches are the only ones that move fit:** bias correction
  plus linear decay take it from 67% to ~29% at 300 steps, through a lower and
  falling effective lr.
- **The audio-stream and target switches change nothing measurable on top**
  (26–28%).
- **Trigger binding is ≈ 0 in every arm.** Deleting `valeriosan ` from the
  caption does not undo the fit, so no recipe here makes the adapter keyed to
  its trigger. Every caption in the set contains the trigger, which leaves the
  trainer no signal to separate the trigger from the rest of the caption.
  Binding probably needs regularisation data or caption dropout; it is not one
  of these switches.
- **Arm C trains the audio families,** `audio_attn2` most of all (median ΔRMS
  1.1e-4). That is the May lip-sync risk, now confirmed to be live under
  `matched_sigma`.

**Recommendation.** Nothing here justifies the iter7 defaults over the legacy
recipe. Legacy is the one that produced the adapters that render a learned
person once saved correctly (v5, above); iter7 halves fit at equal steps with
no gain in binding. **Done 2026-10-02:** the defaults are the legacy values
again, and the switches stay for experiments. The real decision belongs to a
full-length retrain judged by HQ renders, not to this control.

## Recommended fixes

**For the scrambled save (the actual root cause, 2026-10-02), in priority
order:**

1. ~~Make every saved tensor contiguous.~~ **Done** on this branch:
   `_patch_contiguous_checkpoint_save`. It ships with the next release, and
   until then **every Train-tab adapter is broken on arrival**.
2. **Upstream the one-line fix** to `ltx-2-mlx`'s
   `ltx_trainer_mlx/trainer.py::_save_checkpoint`, as
   `np.ascontiguousarray(...)` around the transposed factor. Fix the
   `mrbizarro/ltx-2-mlx` fork tag and file it at `dgrauet/ltx-2-mlx`. It
   affects every user of the raw `ltx-2-mlx train` command on safetensors
   ≥ 0.8.0, not just Phosphene.
3. **Tell #62.** Every reporter's existing adapters are almost certainly
   repairable without retraining:
   `python -m lora_lab.repair_scrambled_lora mlx_models/loras/<name>.safetensors --in-place`
   reports first and refuses a file it cannot classify. Voice adapters
   (`*.audio.safetensors`) use the same transform but are untested here.
4. **Panel detection.** Have `lora_compat`'s measurement call
   `repair_scrambled_lora.scramble_scores` and badge a scrambled adapter the
   way WEAK and DEAD are badged, with the repair command as the action.
5. **Re-grade every recipe decision taken since August** against unscrambled
   adapters, including the iter7 switches and the "High is the only graded
   recipe" advice. All of it was judged on files that could not carry an
   identity.

**For Finding 3 (root cause of this report) — DONE, see
[Fix implemented](#fix-implemented-for-finding-3).** Item 1 below shipped
(the refuse/warn check). Items 2 and 3 are follow-ups not yet done:

1. ~~At `/train/start` validation time... refuse (or warn loudly)...~~
   **Shipped** — implemented at the point captions are read for training
   (`run_train_job_inner`, after the reconcile step) rather than at
   `/train/start`, since that's the earliest point the actual caption
   *content* (not just file existence) is available; functionally
   equivalent — the refusal still lands before any GPU time is spent.
2. **Not yet done.** Still worth surfacing the *actual* trigger the
   captions used in the sidecar and the Train tab's completion summary,
   for the partial-mismatch case that only warns rather than refuses.
3. **Not yet done.** Still worth considering whether dataset directories
   should be cleared of stale `captions/*.txt` when a job is resubmitted
   with a new trigger for the same image set, rather than relying on the
   new check to catch it every time.

**For Finding 1 (generation mismatch, independent, still worth fixing):**

1. Extend `_ltx_lora_compatibility()` / `inspect_lora_compatibility()` (or
   a new check alongside it) to compare a LoRA sidecar's declared
   `base_model` generation against `ACTIVE_MODEL_VERSION`, not just module
   key names.
2. Have `_validate_character_quality()` refuse or loudly warn on a
   generation mismatch, for both the `character_id` path and the raw-LoRA-
   in-`loras[]` path (currently only the latter is silent; the former's
   `ltx_compatible` check is structural-only and would also pass a
   cross-generation LoRA today).

**For the trainer (from the
[regression check](#trainer-regression-check-2026-10-01-evening) and the
[trainer comparison](#comparison-against-known-working-trainers-2026-10-02);
item 4 implemented as an iter7 default, the rest not yet):**

1. `crop_and_caption`: call `ImageOps.exif_transpose` before cropping. Panel
   uploads are normalised at ingest since v4.17.0, but the trainer should not
   depend on how a dataset reached the disk.
2. `crop_and_caption`: look up the source-stem caption
   (`captions/{src.stem}.txt`) **before** the sequential stem, or consult the
   sequential stem only when `caption_map.json` maps a file to it. The current
   order silently re-pairs any dataset whose files are already named
   `char_NNN` with gaps.
3. Report **fit** next to delta-RMS at the end of every run. That is
   `probe_fit.py`'s measurement on a few training images at fixed sigma and
   noise, taking about a minute. Delta-RMS has now been shown four times not to
   separate a working adapter from an empty one; fit on the training images is
   the quantity the optimiser was supposed to move.
4. Image-only training feeds the joint model a one-token, all-zero audio
   stream. The `audio_to_video_attn` LoRA then learns a constant
   text-independent bias that does not exist at render. Consider skipping A2V
   cross-attention for image-only training: `PerturbationType.SKIP_A2V_CROSS_ATTN`
   already exists in the block and is the MLX equivalent of upstream training
   with `audio=None`. Unvalidated, and not shown to cause the failure.
   **Done 2026-10-02** as `image_audio: skip_a2v`, the iter7 default, together
   with Adam bias correction and the linear schedule; see
   [The iter7 recipe switches](#the-iter7-recipe-switches-2026-10-02). Still
   ungraded by fit.
5. **The a2v gate's σ (model, not trainer).** `LTXModel` drives the a2v gate
   from the video σ, but ltx-core drives it from the audio σ. The legacy
   default (`clean_zero`) still reaches it in training; `skip_a2v` and
   `matched_sigma` do not. Clean-audio A2V and
   lipdub renders probably still do. The fix belongs in vendored
   `ltx-core-mlx`: a fork-tag move, with an output check on those lanes. Not
   done.
6. **`checkpoint_keep_last_n: 1` deletes the final checkpoint.** It is the
   same double-record as the `keep_last_n: 5` off-by-one. Dedupe the path in
   `_save_checkpoint`, or never prune the file just written. Not done; the
   panel uses 2.

## Proposed next experiment

Both cheap-then-expensive experiments proposed earlier in this document have
now been run: the single-render confirmation (ruled out "wrong trigger word
alone") and the full curated retrain (ruled out "dataset quality" and "crop
strategy" — see
[The v3 retrain](#the-v3-retrain-every-fixable-variable-corrected-still-no-identity-lock)).
The 768 px resolution retrain has also been run and ruled resolution out
(see [The v4 retrain](#the-v4-retrain-768-px--resolution-ruled-out-and-the-test-method-was-blind)).
**Every experiment below must be judged on the panel's 2.3 character path
(`--two-stages-hq`, Q8, ≥704×384), never `--distilled` at 320×320**, and
should include a no-LoRA cross-seed pair so the cross-seed PSNR has a
baseline. What's left, in priority order:

0. ~~**Prompt-shape probe.**~~ **Done 2026-10-01** — the adapter is empty
   of identity even prompted in its own training context; see
   [the probe](#prompt-shape-probe--the-adapter-holds-no-identity-even-in-its-own-training-context).
1. ~~**Class-word caption retrain.**~~ **Done 2026-10-01 — negative**; see
   [The v5 retrain](#the-v5-retrain-class-word-captions--also-negative).
1a. **Reproduction control (new top priority).** Retrain a dataset whose
   adapter is *known* to carry a face (`eltrumpo` or `bizarrotrn`) with the
   current trainer and pin, unchanged recipe, and render it on the HQ path
   with a no-LoRA cross-seed baseline. If it fails too, the trainer/pin
   regressed and every recent #62 report is explained by that; if it works,
   the cause is specific to this subject or dataset. Needs the owner's
   datasets — and STATE.md's unreported E2 may already hold the answer.
   The maintainer committed on #62 (2026-09-17) to exactly this kind of run
   on another reporter's dataset; its number is the first thing to ask for.
   **Revised by the
   [regression check](#trainer-regression-check-2026-10-01-evening):** the
   trainer/model code has not changed except for the multiplier, which does
   not explain the failure. A failing reproduction would therefore point at the
   vendored *pipeline and recipe*, compared with the pre-05-15 authoring-tree
   recipe, rather than at a pin regression. Judge it with `probe_fit.py`
   (fit and trigger binding, about 10 minutes, no renders) before spending
   render time.
1b. **The iter7 recipe-switch screen (runnable now, no owner datasets).** It
   grades the four differences from the
   [trainer comparison](#comparison-against-known-working-trainers-2026-10-02)
   by fit and trigger binding.
   - **First pass:** the 2-image control. Each 300-step run is about 25 min of
     training plus a ~10 min `probe_fit.py`.
   - **Confirmation:** repeat on v5's data at 740 steps (~1 h each).
   - **Baseline:** the legacy arm is already measured — `tiny1000` and
     `tr1000` in the regression check, fit under 0.1%.
   - **How to run an arm:** `smoke_recipe.py --steps 300 --set key=value ...`
     inside `run_smoke.sh`, with a cap above the run length. Then point
     `probe_fit.py` at the saved adapter, as `run_queue2.sh`'s `ptiny1000` arm
     does.

   | arm | `adam_bias_correction` | `scheduler_type` | `image_audio` | `lora_target_families` |
   |---|---|---|---|---|
   | A | true | linear | clean_zero | video |
   | B (reference values) | true | linear | skip_a2v | video |
   | B′ | true | linear | matched_sigma | video |
   | C | true | linear | matched_sigma | all_attention |

   *(2026-10-02: the first run of this screen exposed the
   [scrambled save](#root-cause-every-adapter-was-saved-scrambled-2026-10-02).
   It was stopped and re-run with the save fixed. Every arm can now fit, so the
   screen compares the size of the fit and of the trigger binding, rather than
   looking for the one arm that fits at all. Results are in
   [Screen results](#screen-results-with-the-save-fixed-2026-10-02).)* C carries
   the May lip-sync risk, so render it with dialogue before shipping anything
   trained that way.
2. **Recipe variables, judged by fit rather than by delta-RMS.** Each of these
   can be screened in about 1 h with a 740-step run plus `probe_fit.py`.
   - **Effective batch size:** `gradient_accumulation_steps` 4–8. Each step's
     gradient is about 92% noise-draw-specific, and the shared signal is under
     1%.
   - **The audio-family targets the reference adapters had:** now
     `lora_target_families: all_attention` (arm C of 1b).
   - **576 px**, the authoring-tree resolution.
   - **Learning rate:** 2e-4.

   v4's loss and delta-RMS were still moving at 3700 steps, but those numbers
   are no longer evidence that anything was being learned.

Retained from earlier, lower priority now:

1. **A rank/steps/lr sweep.** The one class of variable never varied across
   v2 or v3 — both used the exact "high" preset (rank 32, lr 1e-4, 100
   epochs). Worth testing whether a different rank (e.g. 64, if the panel's
   ceiling allows) or a different learning rate moves `delta_rms` off its
   apparent ~6.6e-4 plateau, and separately whether identity consistency
   tracks magnitude at all once magnitude is actually varied (this session
   only ever saw magnitude held effectively constant).
2. **`--lora-mode fuse` as a control arm.** Every render in this document —
   v2, v3, and the original bug report — went through the unfused runtime
   branch. Worth one render with `--lora-mode fuse` (lossy at Q4, but a
   different code path) purely to rule out an unfused-branch-specific bug,
   since nothing in this investigation has varied that.
3. **The caption-format A/B** flagged in the maintainer's 2026-08-22
   investigation — plain `<trigger> man` vs the structured
   `[VISUAL]: <trigger>, <body>` both datasets in this document used — is
   now lower priority given curated, individually-written captions (v3)
   performed identically to auto-generated ones (v2); the format itself
   looks unlikely to be the answer, but it has still never been directly
   tested. *(2026-10-01 evening: that comparison was confounded. v3 trained 20
   of its 37 images on another photo's caption; see the
   [regression check](#trainer-regression-check-2026-10-01-evening).)*

The three retained items are multi-hour-or-more GPU commitments; none
started, pending owner confirmation on priority.

## Artifacts from this session

**Renders** — `/tmp/lora_test_out/` (not preserved across reboots — copy out
if needed):

With the (now known to be wrong) `valeriosan` trigger:

- `no_lora.mp4` / `no_lora_f12.png` — baseline, seed 12345, no LoRA.
- `with_lora.mp4` / `with_lora_f12.png` — seed 12345, LoRA strength 1.0.
- `with_lora_seed777.mp4` / `with_lora_seed777_f12.png` — seed 777, LoRA
  strength 1.0.
- `with_lora_strength3.mp4` / `with_lora_strength3_f12.png` — seed 12345,
  LoRA strength 3.0.

With the confirmed-actual `cvjtrn` trigger (v2 adapter):

- `cvjtrn_seed12345.mp4` / `cvjtrn_seed12345_f12.png` — seed 12345, LoRA
  strength 1.0.
- `cvjtrn_seed777.mp4` / `cvjtrn_seed777_f12.png` — seed 777, LoRA
  strength 1.0.

With the v3 adapter (correct trigger, curated dataset, center crop):

- `v3_seed12345.mp4` / `v3_seed12345_f12.png` — seed 12345, LoRA strength
  1.0.
- `v3_seed777.mp4` / `v3_seed777_f12.png` — seed 777, LoRA strength 1.0.

All eight generated via direct `ltx-2-mlx` CLI calls against
`mlx_models/ltx-2.3-mlx-q4`, bypassing the panel entirely, under the GPU
lock protocol in `CLAUDE.md` §7.

**Training job record** — `state/train_character/trn-20260926-0936-01/`
(copied into this checkout's own `state/` from the sibling `phosphene`
clone, where the job actually ran, so the sibling checkout is left
untouched):

- `spec.json` — the job as submitted (trigger `valeriosan`, `user_provided`
  captions, rank 32 / 4200 steps / 512px).
- `train.log` — full run log; phase 1 (lines 52-93) shows every caption
  used, all under `cvjtrn`.
- `captions/char_NNN.txt` (42 files) — the actual captions consumed;
  confirmed 42/42 contain `cvjtrn`, 0/42 contain `valeriosan`.
- `caption_map.json` — original upload filename → `char_NNN` stem mapping
  (useful if tracing which original photo/caption pair came from where).
- `images/`, `images_renamed/`, `cropped/`, `training_data/` — the source
  and processed photos themselves, for any future dataset-quality review.

**v3 training job record** — `state/train_character/trn-20260930-retrain02/`
(built fresh in this checkout, not copied):

- `spec.json` — the job as run (trigger `valeriosan`, `center` crop, rank 32
  / 3700 steps / 512px, 37 images).
- `run.log` — full run log; `{"event":"done"}`, exit 0, `training_wall_seconds`
  16518.3.
- `run_retrain.sh` — the launch wrapper (GPU-lock acquire/release, exact
  `lora_lab_run.sh` invocation the panel itself uses).
- `images/`, `captions/` — the final curated 37-image set actually trained
  on.
- `excluded/images/`, `excluded/captions/` — the 19 images dropped during
  curation, moved aside rather than deleted, with the reason for each
  recorded in this document's curation table.
- `images_renamed/`, `cropped/`, `training_data/` — the trainer's own
  processed output, useful for visually confirming the `center` crop result
  (compare against `trn-20260926-0936-01/images_renamed/char_001.png` for
  the `letterbox` version of a similar frame).

Output adapter: `mlx_models/loras/valeriosan_v3.safetensors` +
`.safetensors.json` sidecar (`delta_rms_median` 6.627e-4, verdict `ok`).

**v4 (768 px) training job record** — `state/train_character/trn-20260930-res768/`:

- `spec.json` — v3's spec with `width`/`height` 768, `checkpoint_keep_last_n`
  5 (kept 4 — see the off-by-one above) and `enable_gradient_checkpointing`.
- `run.log` — the completed run (interval-mean loss, `{"event":"done"}`,
  `TRAINING_EXIT_CODE=0`); `run_attempt1_no_gradckpt_thrashed.log` — the
  stopped first attempt.
- `run_retrain.sh` — launch wrapper (GPU locks released on exit).
- `train_output/checkpoints/lora_weights_step_{01480,02220,02960,03700}.safetensors`.

Output adapter: `mlx_models/loras/valeriosan_v4_768.safetensors` + sidecar
(`training_resolution` [768, 768], `delta_rms_median` 6.34e-4, verdict `ok`).

**v4 and HQ renders** — `/tmp/lora_test_out/` (same reboot caveat):

- `v4_768_seed{12345,777}`, `v4_768_s1480_seed{12345,777}` — `--distilled`
  Q4 320×320, `.mp4` + `_f12.png` + `.log`.
- `hq_v4_seed{12345,777}`, `hq_v3_seed{12345,777}`, `hq_nolora_seed12345` —
  `--two-stages-hq` Q8 704×384, same file set.
- `grid_v3_vs_v4.png` (distilled: v3 | v4-1480 | v4, rows = seeds),
  `grid_hq.png` (HQ: no-LoRA | v3 | v4, rows = seeds),
  `grid_nolora_v4_train.png` (no-LoRA, v4, two training crops).
- `ps_{nolora,v3,v4}_seed{12345,777}` — prompt-shape probe (`char_030.txt`
  verbatim as the prompt), `--two-stages-hq` Q8 704×384; `grid_promptshape.png`
  (no-LoRA | v3 | v4, rows = seeds).

**v5 (class-word) training job record** — `state/train_character/trn-20261001-classword/`:
`spec.json` (v3's, plus `checkpoint_keep_last_n: -1`), `captions/` (v3's 37
with `valeriosan man,`), `run.log`, `run_retrain.sh`, and all five
`train_output/checkpoints/lora_weights_step_{00740,…,03700}.safetensors`.
Output adapter `mlx_models/loras/valeriosan_v5_classword.safetensors`
(`delta_rms_median` 6.28e-4, verdict `ok`).

**v5 renders** — `/tmp/lora_test_out/`: `hq_v5_seed{12345,777}`,
`hq_nolora_seed777` (prompt A); `psm_{v5,nolora}_seed{12345,777}` (v5's
`char_030` caption verbatim); `grid_v5.png` (rows = prompt A / B; columns =
no-LoRA@12345 | v5@12345 | no-LoRA@777 | v5@777).

**Trainer regression check (2026-10-01, evening)**:
`state/issue62_trainer_regression/` (gitignored, about 3.6 GB).

- `scripts/`:
  - `grad_ab.py`: one training step, old vs current code, and multiplier 1 vs
    1000.
  - `compare_ab.py`: compares the step outputs.
  - `descent_check.py`: the gradient-path check.
  - `probe_fit.py`: fit and trigger binding.
  - `train_ab.py`: short real-trainer runs.
  - `run_queue2.sh`: the GPU-lock wrapper. It exports
    `LTX2_DIT_EVAL_EVERY=0` and `LTX2_GEMMA_EVAL_EVERY=0` as
    `scripts/lora_lab_run.sh` does, and runs a 52 GB footprint watchdog.

  The scripts' `T` constant points at the job scratch directory they were run
  from. Repoint it at this folder before reuse. The `v0.14.8` arm expects
  `git -C ltx-2-mlx archive v0.14.8 packages/ltx-core-mlx/src
  packages/ltx-pipelines-mlx/src packages/ltx-trainer/src` extracted under
  `$T/old/`.
- `results/`:
  - `ab_{old,cur}.{json,npz}`: losses, per-module gradient norms, and full
    gradients for 56 kept modules.
  - `descent_check.jsonl`.
  - `probe_{v5,tiny1000,tiny1,tr1000,tr1}.json`: one row per sample × sigma ×
    seed × arm.
  - `run_queue.log`, `run_queue2.log`.
  - `sheet_v2.png`, `sheet_v5.png`: the training crops, with the sideways
    faces.
- `tiny_dataset/`: the 2-image positive-control set. It is v5's
  `char_024`/`char_029` latents, conditions, crops and their own captions.
- `adapters/`:
  - `tiny_avca{1000,1}_step300.safetensors`;
  - `ab_avca{1000,1}_step740.safetensors`, which share LoRA init, data order
    and noise seed and differ only in the multiplier.

**Trainer comparison and iter7 switches (2026-10-02)**:

- `reference_trainers/` (gitignored), sparse clones read for the comparison:
  - `ltx-2-official/`: Lightricks/LTX-2 `2d6e71c`. It holds `ltx-trainer`
    plus ltx-core's transformer, loader, text encoders and components.
  - `ai-toolkit/`: ostris/ai-toolkit `ecee894`. It holds
    `extensions_built_in/diffusion_models`, `toolkit`, `config/examples` and
    the job-UI defaults.
  - Refresh either with `git -C <dir> pull`.
- Code:
  - `lora_lab/train_character.py`: `RECIPE_SWITCHES`, `resolve_recipe`,
    `LORA_LAB_VERSION = "iter7"`, and the sidecar `recipe` block.
  - `lora_lab/train.py`: `_patch_adam_bias_correction`,
    `_patch_image_only_audio`, and the now-switchable
    `_patch_lora_target_exclude_audio(enabled)`.
- Test: `test_train_recipe_switches.py` (15 tests, CPU).
- `state/issue62_trainer_regression/scripts/` (gitignored):
  - `smoke_recipe.py`: a few real steps through `run_training` on the 2-image
    control set, then per-family zero counts read from the saved adapter.
  - `run_smoke.sh`: the GPU-lock wrapper, with a 52 GB footprint watchdog and
    a hard time cap.
  - The 10-step smoke adapters themselves were written to the session's
    scratch directory and are not kept.

**Scrambled-save root cause (2026-10-02)**:

- **Code:**
  - `lora_lab/train.py::_patch_contiguous_checkpoint_save`, applied in
    `train_character.run_training`, `train_audio.py` and the `lora_lab.train`
    CLI.
  - `lora_lab/repair_scrambled_lora.py`: detector plus exact repair.
- **Tests:** `test_train_checkpoint_layout.py` (3) and
  `test_repair_scrambled_lora.py` (5).
- **`state/issue62_trainer_regression/scripts/`:**
  - `unscramble_lora.py`: the bare transform, without the detector.
  - `run_screen.sh`: the screen queue. Both GPU locks, a watchdog, and each
    arm probed under its own training objective.
  - `run_probe.sh`: a single locked probe.
  - `run_validate.sh`: the unscrambled-v5 renders plus probe.
  - `summarize_screen.py`: the fit and binding table.
  - `probe_fit.py` gained `--image-audio` and `--targets`, and its `T` now
    points here.
  - `smoke_recipe.py` gained `--init-seed`, so every arm shares one LoRA init.
- **`state/issue62_trainer_regression/adapters/`:**
  `valeriosan_v5_classword_unscrambled.safetensors` and
  `screen_L_step300_unscrambled.safetensors`.
- **`state/issue62_trainer_regression/results/`:**
  - `screen_L_as_saved_scrambled.json`
  - `screen_L_unscrambled.json` (= `screen_L.json`)
  - `probe_v5_unscrambled.json`
  - `screen_{A,B,Bp,C}.json`
- **`state/issue62_trainer_regression/renders/`:**
  `hq_v5fixed_seed{12345,777}.mp4`, their `_f12.png` frames and logs, and
  `grid_v5_unscrambled.png` (no LoRA | v5 as saved | v5 unscrambled | training
  crop). The no-LoRA and as-saved frames (`hq_nolora_*`, `hq_v5_*`) were copied in from `/tmp/lora_test_out`.
- **Logs:** `screen_batch1.log` is the stopped first run; `screen_batch2.log`
  and `screen_batch3.log` are the re-run; `validate.log`.
