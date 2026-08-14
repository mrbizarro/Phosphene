# `/prompt/enhance` empty-response debug record

Date: 2026-08-14

## Outcome

The helper IPC did not drop a successful reply, and trigger-token restoration
was never reached. The active LTX-2.5 registry selection made the shared
`LTX_GEMMA` environment variable resolve to the vendored Gemma 4 render text
tower. The prompt enhancer then tried to build `GemmaLanguageModel` from that
root. The tower correctly rejected the request because it has no language-model
head or KV cache; prompt rewriting still requires the installed Gemma 3 root.

The client had a 120-second deadline. Loading the wrong tower consumed that
deadline before the helper returned its structured error. The panel caught the
error and called `_json(..., 500)`, but the client socket was already gone, so
`end_headers()` raised `BrokenPipeError`. That ordering explains the observed
empty body: the response serializer did not discard JSON; it tried to write the
JSON error after the caller had timed out.

## Evidence

- The restricted execution sandbox could not connect to the live loopback
  listener even though a read-only `lsof` check showed the production panel on
  `127.0.0.1:8198`. The same sandbox rejected a test `bind()` on port 8262 with
  `EPERM`, so no production process was modified, signalled, or restarted.
- Read-only inspection of the running panel's actual stdout log recovered the
  request traceback. `WarmHelper._dispatch_run_event()` raised the helper's
  explicit error:

  ```text
  Prompt enhancement is not available on the vendored Gemma 4 tower
  (it builds the text-encoder path only — no lm_head, no KV cache).
  Point the enhancer at a Gemma 3 root.
  ```

- The next traceback frame was the handler's attempted JSON 500 response,
  ending in `BrokenPipeError` while flushing headers to the expired client.
- Both model roots are present in the read-only production install: Gemma 3 is
  approximately 7.5 GB and Gemma 4 is approximately 6.3 GB. The Gemma 3 root
  contains both required safetensor shards, tokenizer files, and config. This
  was a resolver regression, not a missing download.
- `f1d2139` introduced the version-aware render-encoder seam:
  `env["LTX_GEMMA"] = text_encoder_dir()`. That is correct for LTX-2.5 render
  conditioning, but `get_gemma_lm()` also consumed `LTX_GEMMA`, incorrectly
  coupling the generative enhancement lane to the active render generation.

## Fix

1. `WarmHelper._ensure()` now exports two independent paths:
   - `LTX_GEMMA`: active generation's render text encoder (Gemma 4 for LTX-2.5).
   - `LTX_ENHANCE_GEMMA`: the installed generative Gemma 3 root.
2. `mlx_warm_helper.py:get_gemma_lm()` loads `LTX_ENHANCE_GEMMA` and retains a
   Gemma 3 Hugging Face fallback for standalone helper use.
3. `/prompt/enhance` passes an enhance-only 90-second helper deadline. A silent
   helper is killed and converted to a JSON 500 before the normal 120-second UI
   or curl deadline can expire.
4. Malformed helper terminal events (non-dict results or non-string enhanced
   prompts) are also converted to JSON 500 responses instead of raising after
   the handler's exception boundary.

## Regression coverage

`test_prompt_enhance_endpoint.py` is an executable stdlib regression test. On a
normal host it starts the real panel on `127.0.0.1:8262`, with isolated state,
output, upload, and model directories under a temporary repo-root scratch
directory. A newline-JSON helper double verifies that the render lane receives
Gemma 4 while enhancement receives Gemma 3, then exercises:

- a successful enhanced-prompt JSON body;
- a structured helper failure with a non-empty JSON 500 body;
- a malformed successful event with a non-empty JSON 500 body;
- a silent helper with a bounded, non-empty JSON timeout body.

The current sandbox cannot bind loopback, so that instance case reports a
precise skip here. Its companion test directly executes the same real
`Handler.do_POST` plus `WarmHelper` subprocess round-trip without a socket; all
four success/error/malformed/timeout assertions pass. No model weights or GPU
are used.

Run:

```bash
python3 test_prompt_enhance_endpoint.py
```

## Verification record

Passed in the shared dev worktree:

- `node scripts/check_pinokio_scripts.js`
- `node scripts/check_ltx_pin.js`
- `node scripts/check_post_update.js`
- `python3 scripts/assert_registry.py` — 154 passed, 0 failed, 0 pinned defects
- `python3 test_prompt_enhance_endpoint.py` — restricted-sandbox handler/IPC
  path passed; the real port-8262 case skipped only because `bind()` returned
  `EPERM`
- Python compile checks for the panel, helper, and regression test
- `git diff --check`

`python3 scripts/assert_schedules.py` could not import the vendored package from
the system interpreter. The repository-venv invocation reached `mlx.core`, but
this sandbox exposes an empty Metal device list and MLX aborted in its native
device constructor before the gate imported the scheduler. This is an
execution-environment block, not a failed schedule assertion; the gate must be
re-run in the normal Pinokio terminal before promotion.

Git finalization is also environment-blocked in this session: the shared
worktree's `.git` directory is mounted read-only (`FETCH_HEAD: Operation not
permitted`), while a disposable writable clone cannot resolve `github.com`.
Consequently the requested rebase, commits, and beta push cannot be performed
from this sandbox unless those restrictions are lifted.
