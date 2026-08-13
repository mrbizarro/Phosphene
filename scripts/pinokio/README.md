# `scripts/pinokio/` — payloads too big to hand Pinokio directly

Every file here is a step body lifted out of `install.js` / `install_h3.js`
because **Pinokio 8.0.x wedges when a single dispatch is large** — one CPU core
at 100%, no child process ever spawns, and the install is dead until the user
force-quits.

## What Pinokio actually dispatches

`pinokiod/kernel/shells.js` `launch()`:

| `message` | what happens |
|---|---|
| an **array**, no `chain` param | each element is launched in its **own shell** — so an array is already safe and the *element* is the unit |
| an **array** with `chain` | `Shell.build()` joins them into **one** dispatch |
| a **string** | **one** dispatch, as-is |

and `shell.js:1911` writes whichever it is in a single unchunked call:

```js
this.ptyProcess.write(`${this.exec_cmd || this.cmd}${this.EOL}`)
```

The same file's `Shell.emit()` (shell.js:665-711) chunks anything over **1024**
chars into 256-byte pieces with a 10 ms gap — so the kernel already knows large
pty writes are unsafe. The `shell.run` path just doesn't do it.

## Why v3.6.2's fix wasn't enough

@Morac2 bisected the ceiling in #50: **764 chars hangs, ~373 passes**. v3.6.2
adopted "no generated shell line over 350" and the issue was closed. The rule
was right; the *measurement* was wrong. Every long payload in this repo is
written `[ ... ].join("\n")` — deliberately a **string**, so an `if/else` runs
in one shell — and those are exactly the dispatches nobody counted. The venv
step's source lines were all under 110 chars and it dispatched **1,417** in one
go:

| report | finding | their fix |
|---|---|---|
| #50 @Morac2 | 764-char line hangs, ~373 passes | shortened the line (v3.6.2) |
| #50 @davidaircloud | lines short, joined message still **1,417** — still hangs | moved the body to a `.sh`, dispatched `bash ../x.sh` |
| #56 @itrejomx | same stall before `uv venv` | split the payload into smaller steps |

A local pty probe (bash `--noprofile --norc` — Pinokio's own shell and args —
with one unchunked `os.write`) completes a 1,475-byte payload fine, so this is
**not** a kernel tty limit. It is Pinokio's terminal layer above the pty, and
dispatch size is the only lever we have from this side.

## The rule

`scripts/check_pinokio_scripts.js` enforces it against the **loaded modules**,
modelling `launch()` exactly:

> **no single dispatch over 500 chars**

500 sits above the largest dispatch that has demonstrably shipped and worked in
the field (498) and well under the smallest that has demonstrably hung (764).
Anything bigger belongs here, behind one short `bash …` line.

## House rules for files here

- Dispatched as `bash <path>` — **never** `./<path>`, so no execute bit is
  needed and nothing depends on git preserving the mode.
- Preserve the semantics the inline payload had. These were all `"\n"`-joined
  strings, which ran **without** `set -e`: the step's exit code was the last
  command's, and an intermediate failure did not stop the step. Keep it that
  way unless you mean to change it, and say so if you do.
- Values that belong to the release — the vendored pin SHA above all — stay in
  the `.js` beside the comment block that documents them and arrive here as
  arguments. Never inline a SHA in this directory.
- `{{cwd}}` is substituted by Pinokio in the *message*, so anything path-shaped
  must be passed in as an argument rather than written here.

## What is here

| file | dispatched by | what it does |
|---|---|---|
| `ltx_checkout.sh` | `install.js`, `scripts/post_update.sh` | the vendored engine pin move — the ONE implementation, and the pin literal itself (its header argues the exception to the rule above) |
| `ltx_venv.sh` | `install.js` | the venv build that dispatched 1,417 chars and hung Pinokio 8.0.x |
| `ltx25_weights.sh` | `install.js` | LTX-2.5 base + Gemma 4 encoder (27.47 GB), the default generation. Fatal on failure: a panel that boots into a generation with no weights cannot render |
| `q8_weights.sh` | `download_q8.js` | the optional LTX-2.5 Q8 pack (30.02 GB) — what trained characters and voices need. NOT the High add-on, which is a separate 29.5 GB click |
| `mflux_pack.sh` | `install_qwen.js` | the image-engine pack (Ideogram 4 + Qwen-Edit) |
| `h3_preflight.sh` | `install_h3.js` | Hailuo H3 disk/RAM preflight |
| `h3_build_q8.sh` | `install_h3.js` | the H3 q8 DiT build |
