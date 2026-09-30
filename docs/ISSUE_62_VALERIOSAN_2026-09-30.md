# Issue #62 follow-up — `valeriosan_v2`, 2026-09-30

Investigation log for a fresh #62 report on v4.17.1: a character LoRA
(`valeriosan_v2`, trained via the Train tab, "high" preset) trained clean and
renders as if it were never attached. This document records what was tested,
what was ruled out, what is still standing, and what the next experiment
should be — so the next session (human or agent) does not re-derive any of
this from zero.

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
  has ever carried a face here was trained this way."
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
- **The actual cause of this report** — **not ruled out; confirmed.** See
  Finding 3. The trained trigger (`cvjtrn`) and the reported/prompted
  trigger (`valeriosan`) are different tokens.

## Recommended fixes

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

## Proposed next experiment

The cheap single-render confirmation (re-rendering with `cvjtrn`) has now
been run — see [Confirming render](#confirming-render-cvjtrn-does-not-fix-it-either)
— and ruled out "wrong trigger word" as the sole explanation. What's left,
in priority order:

1. **Retrain, not just re-render.** The one thing not yet tried: a fresh
   training run on the same 42-image dataset with captions that actually
   contain the intended trigger (fixing Finding 3 at the data level, not
   just relabeling), same rank/steps/lr. If this still produces a weak
   (`delta_rms` in the same ~6-7e-4 range) or seed-inconsistent adapter,
   that's strong evidence the dataset itself — not the caption bug — is the
   limiting factor. This is the natural next step now that a same-trigger
   render has already ruled out the cheap explanation, and it's the
   experiment `docs/STATE.md`'s "#62 retrain still owed" note has been
   waiting on. Multi-hour GPU commitment (~4.9 h based on the original
   run's `training_wall_seconds`); not started, pending owner confirmation.
2. **The caption-format A/B** flagged in the maintainer's 2026-08-22
   investigation and never run — `[VISUAL]: <trigger>, <body>` vs a plain
   `<trigger> man` caption, fixed rank/steps/seed/trigger, judged by whether
   the trigger summons a consistent identity across seeds (not by
   `delta_rms`). Can be folded into the retrain in (1) as a second arm
   rather than run separately. Multi-hour GPU commitment; still not
   started.

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

With the confirmed-actual `cvjtrn` trigger:

- `cvjtrn_seed12345.mp4` / `cvjtrn_seed12345_f12.png` — seed 12345, LoRA
  strength 1.0.
- `cvjtrn_seed777.mp4` / `cvjtrn_seed777_f12.png` — seed 777, LoRA
  strength 1.0.

All six generated via direct `ltx-2-mlx` CLI calls against
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
