# v4.0.3 side-pair receipts

Scope: clone `phosphene-side`, branch `side/v403-pair`. No running panel or
other checkout was modified or contacted; port 8198 was not used.

## Task 1 — Settings is the only Spicy/NSFW gate

Implementation:

- The CivitAI `Show NSFW` control is fail-closed in the static page markup via
  `data-spicy-only hidden`.
- `spicyModeEnabled()` is the one client predicate used to render the control
  and decide whether either initial search or `Load more` may submit
  `nsfw=true`. Engine selection only changes the LoRA family.
- Modal open waits for `/settings` before its first search, removing the stale
  checkbox/request race.
- `spicy_mode_enabled()` is the server predicate. With Settings off,
  `_civitai_search()` forces the upstream request to `nsfw=false` and filters
  NSFW-flagged response cards as defense in depth.

Executable rendered-page and submit-boundary receipt:

```text
$ PYTHONDONTWRITEBYTECODE=1 python3 test_spicy_contract.py
test_all_four_engine_setting_states ... ok
test_hidden_checked_control_cannot_submit_nsfw ... ok
test_static_markup_starts_hidden ... ok
test_off_forces_safe_query_and_filters_nsfw_response ... ok
Ran 4 tests
OK
```

The real page markup/functions are extracted from `page()` after its server-side
bootstrap/profile/engine substitutions, then run in Node against a DOM shim.
Observed state matrix:

| Engine | Settings | `Show NSFW` | Forced checked state after render |
|---|---:|---|---|
| LTX | on | visible | preserved |
| LTX | off | hidden | cleared |
| Hailuo H3 | on | visible | preserved |
| Hailuo H3 | off | hidden | cleared |

The submit receipt force-checks the hidden box with Settings off and executes
the real `civitaiSearch()` function: its captured URL contains no `nsfw=true`.
The Python boundary receipt independently sends `nsfw=True` while Settings is
off: the captured CivitAI parameters contain `nsfw=false`, and an NSFW response
card is removed.

## Task 2 — H3 Turbo adapter default

Audit note: this branch did not actually resolve LightX2V v0.1; it still used
the older Larry ckpt500-EMA adapter plus a separate AdaLN time embedder. The
implementation was moved directly to the requested LightX2V contract:

- Preferred: `lightx2v_v1.0_768p_ourlayout.safetensors`.
- Fallback only when preferred is absent:
  `lightx2v_v0.1_ourlayout_alpha8.safetensors`.
- Explicitly never selected:
  `minimax_h3_fl2v_turbo_4step_v0.1.safetensors` (raw, alpha not folded) or
  the retired `minimax_h3_turbo_4step_ema_ckpt500.safetensors`.
- Runner argv is `--lora <resolved-path>:1.0`; the Larry-only
  `--lora-adaln` companion path is removed.
- Sidecars now record adapter version, fallback state, and scale.
- The old Larry wall clocks remain historical evidence, but no longer mark
  LightX2V v1.0 estimates as measured.

Executable resolver/dispatch receipt:

```text
$ PYTHONDONTWRITEBYTECODE=1 python3 test_h3_turbo_adapter.py
test_installer_records_exact_publication_todo ... ok
test_unpublished_repack_fails_closed ... ok
test_alpha_folded_v01_is_the_only_fallback ... ok
test_raw_v01_and_old_adapter_are_never_selected ... ok
test_v1_is_preferred_when_both_exist ... ok
Ran 5 tests
OK
```

`install_h3.js` intentionally does not download the raw upstream file. The
runner-layout repack does not yet exist as a digest-pinned release asset, so the
installer records the exact publication contract instead of half-wiring it:

```text
target release asset: lightx2v_v1.0_768p_ourlayout.safetensors
source repo: lightx2v/Minimax-h3-Turbo (Apache-2.0)
source file: minimax_h3_fl2v_turbo_4step_v1.0_768p_bf16.safetensors
source SHA-256: 1bdabc2e9fce20b1db563b96bcf6e46adcad4c1964f423676436bf266cc7416c
release-asset SHA-256: must be recorded before enabling the fetch
```

The UI and `/h3/turbo/install` fail closed with this publication requirement.
The changelog credits the selection to core_tan's public LoRA testing plus the
owner's visual review.

## Gates

```text
$ node scripts/check_pinokio_scripts.js
53 dispatches across 33 shell.run steps in 10 scripts
RESULT: PASS

$ node scripts/check_ltx_pin.js
RESULT: PASS

$ node scripts/check_post_update.js
RESULT: PASS

$ LTX_MODELS_DIR=/Users/salo/pinokio/api/phosphene.git/mlx_models \
  LTX_Q8_LOCAL=/Users/salo/pinokio/api/phosphene.git/mlx_models/ltx-2.3-mlx-q8 \
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/assert_registry.py
154 passed, 0 failed, 0 known defect(s) pinned
```

The registry gate was first run exactly as listed in the task. It reported 17
missing-pack failures because this isolated clone intentionally contains no
`mlx_models` weights. The passing rerun used only the panel's supported path
overrides to inspect the permitted production model tree read-only; no running
panel, process, or port was contacted.

Additional static/contract gates:

```text
python3 -m py_compile mlx_ltx_panel.py test_spicy_contract.py test_h3_turbo_adapter.py
node --check install_h3.js
node --check <extracted inline panel script>
python3 test_lora_compat.py  # 9 tests, OK
```

`scripts/assert_schedules.py` was not run: this clone has no LTX venv and the
gate requires that venv plus Metal, as expected in the task instructions. No
GPU work was performed.

## Repository boundary

No push was attempted. Port 8198 was never contacted. The running install and
other checkouts were not modified. The requested granular commits could not be
created because this session mounts `.git` read-only; both commit attempts
failed before staging with `Unable to create '.git/index.lock': Operation not
permitted`. All source changes remain intact and unstaged in this clone.
