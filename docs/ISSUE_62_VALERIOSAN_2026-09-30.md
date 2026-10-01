# Issue #62 follow-up — `valeriosan_v2` / `v3`, 2026-09-30

Investigation log for a fresh #62 report on v4.17.1: a character LoRA
(`valeriosan_v2`, trained via the Train tab, "high" preset) trained clean and
renders as if it were never attached. This document records what was tested,
what was ruled out, what is still standing, and what the next experiment
should be — so the next session (human or agent) does not re-derive any of
this from zero.

**Session outcome, stated up front:** one real, confirmed, fixed bug (Bug A —
the trainer silently trained on the wrong trigger word); one open question
that a full retrain with every fixable variable corrected (Bug B) failed to
resolve — see [the v3 retrain](#the-v3-retrain-every-fixable-variable-corrected-still-no-identity-lock)
for the most rigorous evidence yet gathered on it.

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
unreachable. New lead: the captions carry **no class noun** ("subject" 52×,
"man" 0×), unlike the `class_word` captions behind every adapter known to
work. See
[The v4 retrain](#the-v4-retrain-768-px--resolution-ruled-out-and-the-test-method-was-blind).

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
caption. Every adapter known to carry a face here (`bizarrotrn`, `elontrn`,
`ariatrn`, `eltrumpo`) was trained with the `class_word` strategy, whose
caption is `<trigger> man, close-up portrait` — the same shape as the render
prompt (`… of valeriosan, a man, …`). So the trigger was learned in a
context the render prompt never reproduces, and is never bound to the class
noun the render uses. This is the caption-format A/B the maintainer flagged
on 2026-08-22, narrowed to a concrete, checkable difference. **Untested.**

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
1. **Class-word caption retrain, single-variable (now top priority).** v3's images, crop and
   512 px recipe, captions rewritten as `valeriosan man, <body>` (or the
   trainer's own `class_word` strategy), judged on the HQ path. 512 px fits
   without gradient checkpointing (~4.6 h on this Mac). Set
   `checkpoint_keep_last_n: -1`.
2. **Learning rate / steps.** The v4 loss and `delta_rms` were both still
   moving at 3700 steps; external guidance allows 2e-4 when a concept is
   not picked up after ~500 steps. Only worth it after (0)/(1), since
   magnitude alone does not separate these runs.

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
   tested.

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
