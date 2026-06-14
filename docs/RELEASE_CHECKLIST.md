# Release checklist — DON'T ship broken

Hard rule: **a release is not validated until it has been run FROM ZERO** — the
state a brand-new user is in when they click Install in Pinokio. "It works on my
machine" is not validation; my machine has every model cached, a token saved, and
every dep already installed. New users have none of that. Most of the things that
have shipped broken (gated model needing a token, mflux not bundled, the missing
upscaler "mosaic", the scary pip dependency block) were invisible on a warm dev
box and only showed up from zero.

Run these BEFORE promoting `dev`/`beta` → public `main`. All must pass.

## 1. Ideogram — from-zero render gate (the one that keeps biting us)

```
bash scripts/validate_ideogram_fresh.sh
```

It **deletes the Ideogram model** and re-renders with the un-gated mirror and
**no Hugging Face token** — exactly a new user's first render. It fails loudly if:
- `mflux-generate-ideogram4` is missing (install.js didn't bundle mflux),
- the model can't download without a token (gating regressed),
- the render crashes or produces no/empty PNG.

Exit 0 = pass. Then **eyeball `/tmp/validate_ideo_fresh.png`** — the text must be
legible (an auto-check can't tell crisp typography from a mosaic).

## 2. Install scripts produce no scary errors

The mflux pack must install via **uv, not plain pip** (mlx-vlm is `--no-deps`, so
plain pip dumps an "ERROR: dependency resolver…" block that makes the update look
broken). Grep to confirm no plain-pip mflux install slipped back in:

```
grep -n "pip install 'mflux" install.js update.js   # expect NOTHING (all uv)
node -c install.js && node -c update.js              # syntax
```

## 3. Video modality smoke (when LTX/ltx-2-mlx is touched)

Render T2V + I2V at Q4 and confirm no mosaic (frames, not just exit code). The
mosaic was a missing `spatial_upscaler_x2_v1_1.safetensors` in the Q4 download —
if you touch `required_files.json` / `install.js` / `update.js` model lists,
re-confirm a fresh Q4 install pulls it.

## 4. Version + compile

```
ltx-2-mlx/env/bin/python -m py_compile mlx_ltx_panel.py image_engine.py
cat VERSION                                          # bumped for this release
```

---

When all pass, promote (`git push origin dev:main` + `gh release create`). If
any fails, **do not ship** — that's the whole point of this file.
