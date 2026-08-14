# LTX-2.5 character quality unlock receipts

## Contract

- `BOOT.ltx.character.tokens` is the shared source for both character quality
  surfaces. LTX-2.5 publishes `draft`, `pro`, `high`, and `high720`; LTX-2.3
  continues to publish only `draft` and `pro`.
- The default remains `pro`, which resolves to the graded q8 + distilled
  `balanced` pipeline at 1024×576.
- `high` resolves to the real `high` HQ cell at 1024×576 (`~4 min / 5s`).
  `high720` resolves to `high_720p` at 1280×704 (`~8 min / 5s`). Both labels
  come from the same `LTX_TIERS` cells the main quality strip renders.
- Draft/Pro require the q8 pack. High/High 720p require the q8 pack and the
  active generation's HQ add-on. Both character surfaces call the main strip's
  `ltxCellNeedsInstall()` / `ltxCellInstallLabel()` helpers, attach
  `.needs-install`, and route clicks to the Models modal.
- Job params record both meanings: `quality_choice` is the four-token UI value;
  `quality` is the actual pipeline quality. This distinguishes Pro from High
  even though both use a 1024×576 canvas.

## Legacy sidecar choice

There is no feature-schema or release field that reliably distinguishes a
pre-unlock bare `quality=high` sidecar from a new one. The accepted migration
choice is therefore:

- on LTX-2.5, a bare legacy `quality=high` now reopens as the first-class High
  chip;
- a sidecar that already carries explicit `quality_choice=pro` remains Pro;
- `quality=high_720p` maps to `high720`, while legacy `balanced` / `standard`
  continue to reopen as Pro.

New sidecars always carry `quality_choice`, so the ambiguity ends at this
feature boundary.

## Rendered DOM receipt

The real `renderCharacterStrip()` and `charactersRenderChips()` functions were
executed in Node against the LTX-2.5 bootstrap payload. Four buttons render in
each surface, and Pro is the sole active default:

```json
{
  "main_character_strip": [
    {"token": "draft", "active": false},
    {"token": "pro", "active": true},
    {"token": "high", "active": false},
    {"token": "high720", "active": false}
  ],
  "characters_tab": [
    {"token": "draft", "active": false},
    {"token": "pro", "active": true},
    {"token": "high", "active": false},
    {"token": "high720", "active": false}
  ]
}
```

With the q8 pack present and the HQ add-on missing, both DOM fragments render
exactly two `.needs-install` chips: `high` and `high720`. Draft and Pro remain
ordinary selectable q8-distilled chips.

## High 720p job-dict receipt

A real local `/queue/add` handler invocation used `quality_choice=high720`, face
strength `0.9`, and voice strength `1.25`. Synthetic safetensors headers kept
the receipt model-free while exercising the real character compatibility and
LoRA-stack construction:

```json
{
  "http_status": 200,
  "quality_choice": "high720",
  "quality": "high_720p",
  "canvas": [1280, 704],
  "pipeline": "hq",
  "dispatch_action": "generate_hq",
  "character_id": "bizarrotrn",
  "character_strength": 0.9,
  "character_voice_strength": 1.25,
  "lora_strengths": [0.9, 1.25]
}
```

The HQ dispatch copies the complete `params.loras` list into `hq_loras`, so the
face and voice adapters reach the two-stage pipeline in the same order and at
the same strengths. Existing no-voice gating is unchanged.

## Gates

```text
test_character_roundtrip.py
  35 tests, OK
  Includes all four payload -> job params/sidecar -> real JS reload paths.

scripts/assert_registry.py
  169 passed, 0 failed, 0 known defects

scripts/assert_schedules.py
  45 passed, 0 failed
  Includes character High for both generations and character High 720p on 2.5.

test_lora_compat.py
  9 tests, OK

test_storyboard.py
  55 tests, OK

test_storyboard_planner.py
  109 tests, OK, 1 opt-in live-model test skipped

Python py_compile, extracted inline JavaScript `node --check`, and
`git diff --check`
  PASS
```

`assert_schedules.py` now loads the checked-in pure-Python scheduler source with
minimal import stubs. The earlier exact command aborted before assertions when
MLX tried to enumerate a sandbox-hidden Metal device, despite this gate using
only sigma lists. No schedule logic is stubbed; the constants and functions
under test still come from the vendored `scheduler.py`.

No GPU render was launched for these receipts. No version, changelog, release
metadata, or remote branch was changed.
