# shellcheck shell=bash
# Resolve the Hailuo H3 checkout and weights roots EXACTLY as the panel does.
# SOURCE it (`. scripts/pinokio/h3_roots.sh`) from the app root; every
# install_h3.js step does, because Pinokio runs each message element in its own
# shell and nothing exported in one survives to the next.
#
# WHY IT EXISTS (Codex INST-09, 2026-09-24). The panel honours LTX_H3_ROOT and
# LTX_H3_MODELS (mlx_ltx_panel.py H3_ROOT / H3_MODELS) and so does the sidebar
# (pinokio.js h3Path) — docs/H3_ENGINE.md recommends them for sharing one 75 GB
# tree between installs. install_h3.js did not: it cloned, installed and
# downloaded into the literal default paths, so "Repair Hailuo H3" repaired a
# second checkout the panel never uses and could pull ~75 GB of duplicate
# weights, while the engine the menu had diagnosed stayed broken.
#
# Semantics match the panel: unset = the default under the app root; a
# relative value is relative to the app root (the panel's cwd); an absolute
# one is used as-is. Spaces are fine — every consumer quotes these.
#
# Exports: APP_ROOT H3_CHECKOUT H3_MODELS_ROOT H3_LAYOUT H3_FLAT LTX_H3_MODELS
#   H3_LAYOUT  where the component dirs live: <root>/models (what upstream's
#              download_selected.py --root writes) or <root> itself for the
#              flat tree the panel also accepts (_h3_model_roots()).
#   H3_FLAT    1 when an existing flat tree was found — the downloader cannot
#              write that layout, so the install leaves it alone rather than
#              duplicating it under models/.
APP_ROOT="$(pwd)"
_h3_abs() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$APP_ROOT" "$1" ;; esac; }
H3_CHECKOUT="$(_h3_abs "${LTX_H3_ROOT:-minimax-h3-mlx}")"
H3_MODELS_ROOT="$(_h3_abs "${LTX_H3_MODELS:-mlx_models/hailuo-h3}")"
H3_DIT_REL='deepbeep-pruned-bf16/MiniMax-H3-FL2VA-pruned_bf16.safetensors'
if [ -f "$H3_MODELS_ROOT/$H3_DIT_REL" ] && [ ! -d "$H3_MODELS_ROOT/models" ]; then
  H3_LAYOUT="$H3_MODELS_ROOT"; H3_FLAT=1
else
  H3_LAYOUT="$H3_MODELS_ROOT/models"; H3_FLAT=0
fi
# Absolute from here on, so h3_build_q8.sh (which runs with cwd = the
# checkout) reads the same tree rather than resolving a relative value again.
LTX_H3_MODELS="$H3_MODELS_ROOT"
export APP_ROOT H3_CHECKOUT H3_MODELS_ROOT H3_LAYOUT H3_FLAT LTX_H3_MODELS
echo "H3 checkout: $H3_CHECKOUT"
echo "H3 weights:  $H3_MODELS_ROOT"

# What a FLAT tree still lacks, one relative path per line ("" = complete).
# Same three things the panel's h3_paths() requires. The downloader cannot
# write a flat tree, so an incomplete one has to be SAID, not skipped: before
# this, Install/Repair printed "not downloading" over a tree missing its VAE
# and H3 stayed unavailable no matter how often it was clicked (Codex, 4.16.0).
h3_flat_missing() {
  local c="${LTX_H3_COMPACT_DIR:-ddalcu-q8}" f
  [ -f "$H3_MODELS_ROOT/$H3_DIT_REL" ] || echo "$H3_DIT_REL"
  for f in text_encoder.safetensors video_vae.safetensors audio_vae.safetensors; do
    [ -f "$H3_MODELS_ROOT/$c/$f" ] || [ -f "$H3_MODELS_ROOT/ddalcu-q8/$f" ] || echo "$c/$f"
  done
  [ -f "$H3_MODELS_ROOT/upstream-meta/FL2VA/text_encoder/config.json" ] \
    || echo "upstream-meta/FL2VA/text_encoder/config.json"
}

# The install's weights step. Run from the H3 checkout (RH3 cds there).
h3_fetch_weights() {
  local m
  if [ "$H3_FLAT" != 1 ]; then
    .venv/bin/python scripts/download_selected.py --root "$H3_MODELS_ROOT"
    return $?
  fi
  m="$(h3_flat_missing)"
  if [ -n "$m" ]; then
    echo "error: the flat H3 weights tree at $H3_MODELS_ROOT is missing:" $m \
      "- complete it, or unset LTX_H3_MODELS so Install can download into its own folder."
    return 1
  fi
  echo 'Flat H3 weights tree complete - nothing to download.'
}
