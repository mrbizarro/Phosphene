#!/usr/bin/env bash
# Hailuo H3 install: the MiniMax H3 licence must be accepted first (4.19.8).
#
#   bash scripts/pinokio/h3_licence_gate.sh        (from the app root)
#
# WHY. The MiniMax H3 Community License binds whoever downloads or runs H3, it
# covers only its Applicable Territory (worldwide except the EU, the UK, South
# Korea and the US), and it asks a product to bind and notify each user before
# giving access (s. V.2). The panel shows the licence screen and stores the
# acceptance (model_licences.py writes state/licence-h3-<version>.accepted);
# this step refuses to download until that marker matches the JSON record.
#
# Cached weights do not replace explicit acceptance of the current licence.
#
# The version below MUST equal model_licences.LICENCES["h3"]["version"]
# (test_model_licences.py holds them together). Pinokio stops an install only
# on "error:" / "errno " in the output, so the refusal is ONE "FATAL error:" line.
. scripts/pinokio/h3_roots.sh >/dev/null
H3_LICENCE_VERSION="2026-08-02"
STATE="${LTX_STATE_DIR:-$APP_ROOT/state}"
case "$STATE" in /*) ;; *) STATE="$APP_ROOT/$STATE" ;; esac
# Use the same read-only pair validation as the panel. Prefer its runtime.
PYTHON="$APP_ROOT/ltx-2-mlx/env/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3
if "$PYTHON" "$APP_ROOT/model_licences.py" "$STATE" h3 "$H3_LICENCE_VERSION" >/dev/null 2>&1; then
  echo "MiniMax H3 licence: accepted in Phosphene."
  exit 0
fi
echo "FATAL error: Open Phosphene, click Hailuo H3 in the engine switcher, read the MiniMax H3 licence and tick I accept, then run Install Hailuo H3 again."
exit 1
