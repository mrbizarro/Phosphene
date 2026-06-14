#!/bin/bash
# ---------------------------------------------------------------------------
# validate_ideogram_fresh.sh — FROM-ZERO Ideogram release gate.
#
# Reproduces a brand-new user's very first Ideogram render: DELETE the model,
# then download + render with NO Hugging Face token (the un-gated mirror) on the
# real cache the panel uses. Catches the things that have shipped broken before:
#   - model gated / needs a token        → download must work token-less
#   - mflux / ideogram CLI missing        → mflux-generate-ideogram4 must exist
#   - render crash / garbled output       → must produce a real PNG
#
# RUN THIS BEFORE PROMOTING ANY IDEOGRAM-AFFECTING RELEASE TO PUBLIC.
# Exit 0 = PASS. Non-zero = do NOT ship.
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HFH="$ROOT/cache/HF_HOME"
VENV="$ROOT/ltx-2-mlx/env"
MIRROR="cocktailpeanut/ideogram-4-fp8"
OUT="/tmp/validate_ideo_fresh.png"
CAP="/tmp/validate_ideo_caption.json"

echo "== 1. mflux + ideogram CLI present? =="
[ -x "$VENV/bin/mflux-generate-ideogram4" ] || { echo "FAIL: mflux-generate-ideogram4 missing — install.js didn't bundle mflux"; exit 2; }
"$VENV/bin/mflux-generate-ideogram4" --help >/dev/null 2>&1 || { echo "FAIL: ideogram CLI not runnable"; exit 2; }
echo "  ok"

echo "== 2. DELETE the model (force a fresh download) =="
rm -rf "$HFH/hub/models--ideogram-ai--ideogram-4-fp8" \
       "$HFH/hub/models--cocktailpeanut--ideogram-4-fp8"
echo "  deleted"

echo "== 3. fresh render: un-gated mirror, NO HF token =="
cat > "$CAP" <<'J'
{"high_level_description":"A bold typographic poster reading FRESH INSTALL on a deep purple background.","compositional_deconstruction":{"background":"A deep purple to black gradient background.","elements":[{"type":"text","bbox":[360,80,560,920],"text":"FRESH INSTALL","desc":"a huge bold white headline, centered","color_palette":["#FFFFFF"]},{"type":"text","bbox":[600,250,680,750],"text":"no token needed","desc":"a small cyan subtitle, centered","color_palette":["#43E0FF"]}]}}
J
rm -f "$OUT"
export HF_HOME="$HFH"
unset HF_TOKEN HUGGING_FACE_HUB_TOKEN          # a brand-new user has no token
unset HF_HUB_OFFLINE                            # must be allowed to download
export HF_HUB_ENABLE_HF_TRANSFER=1
export SSL_CERT_FILE="$VENV/lib/python3.11/site-packages/certifi/cacert.pem"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
T0=$(python3 -c 'import time;print(int(time.time()))')
"$VENV/bin/mflux-generate-ideogram4" \
  --model "$MIRROR" --base-model ideogram4 \
  --prompt-file "$CAP" --output "$OUT" \
  --width 1280 --height 720 --preset V4_TURBO_12 --seed 7 2>&1 | tail -25
RC=${PIPESTATUS[0]}
T1=$(python3 -c 'import time;print(int(time.time()))')

echo "== 4. verify output =="
if [ "$RC" -ne 0 ]; then echo "FAIL: render exited $RC"; exit 3; fi
if [ ! -f "$OUT" ]; then echo "FAIL: no PNG produced"; exit 3; fi
DIMS=$("$VENV/bin/python" -c "from PIL import Image;im=Image.open('$OUT');print(f'{im.width}x{im.height}')" 2>/dev/null)
SZ=$(stat -f%z "$OUT" 2>/dev/null)
echo "  PNG: $DIMS, ${SZ} bytes, $(( T1 - T0 ))s total (incl. fresh download)"
[ "${SZ:-0}" -gt 50000 ] || { echo "FAIL: PNG suspiciously small"; exit 3; }
echo ""
echo "PASS — fresh, token-less Ideogram install renders. Eyeball $OUT for legible text."
