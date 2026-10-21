#!/usr/bin/env bash
#
# release_gates.sh — run every static/gate check in one command.
#
# This is the mechanical half of docs/RELEASE_CHECKLIST.md. It does NOT replace
# the checklist: the from-zero install, the eyeball on the Ideogram render, the
# smoke renders, the weight-mirror publish and the update-path gate all need a
# human and a real machine. What this script does is make sure that nothing
# that CAN be checked by a command is ever skipped because it was tedious.
#
#   bash scripts/release_gates.sh          # everything
#   bash scripts/release_gates.sh --fast   # skip the two slowest gates
#
# Exit 0 only if every gate passed. Any FAIL -> non-zero.
#
# A gate may also report SKIP. A SKIP is a WARNING, never a pass: it means the
# gate could not run (no rendered clip yet, pytest missing from the venv). The
# summary prints skips loudly and tells you what to do about them. Do not
# promote on a table full of skips.

set -u
set -o pipefail

cd "$(dirname "$0")/.." || exit 2
REPO="$PWD"

FAST=0
for arg in "$@"; do
    case "$arg" in
        --fast) FAST=1 ;;
        -h|--help)
            sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "release_gates.sh: unknown argument: $arg" >&2
            echo "usage: bash scripts/release_gates.sh [--fast]" >&2
            exit 2
            ;;
    esac
done

# The repo venv. Every python gate runs through it when it exists — the root
# suites import the panel, which needs the venv's deps.
VENV_PY="$REPO/ltx-2-mlx/env/bin/python"
if [ ! -x "$VENV_PY" ]; then
    echo "WARNING: $VENV_PY not found; falling back to python3." >&2
    echo "         The root test suites import the panel and will likely fail." >&2
    VENV_PY="$(command -v python3)"
fi

LOGDIR="$(mktemp -d "${TMPDIR:-/tmp}/phosphene-gates.XXXXXX")"

# HERMETIC, like conftest.py does for pytest (4.17.5): the unittest sweep does
# not load conftest, and a full run used to leave an 810 MB Hugging Face
# download in the system temp dir every time. Every gate gets its own TMPDIR
# under LOGDIR (removed below, the logs kept) and an offline Hugging Face hub.
GATE_TMP="$LOGDIR/tmp"
mkdir -p "$GATE_TMP"
export TMPDIR="$GATE_TMP/"
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
trap 'rm -rf "$GATE_TMP"' EXIT

# ...AND THE TEST SWEEPS NEVER SEE THE INSTALL'S OWN state/, mlx_outputs/ OR
# panel_uploads/ (issue #90). conftest.py sandboxes those three for pytest,
# but the root sweep runs through `python -m unittest`, which never loads
# conftest — and most root suites import the panel without setting
# LTX_STATE_DIR themselves. So every gate run read the OPERATOR's
# panel_settings.json and eta_calibration.json (a gate result that depended
# on whose Mac ran it) and wrote storyboards, outputs, usage-log lines and
# analytics install steps INTO the install it ran in. Each suite now gets a
# fresh, empty tree of its own, set unconditionally: a caller's LTX_STATE_DIR
# is just as likely to be somebody's real state. check_output_codec is NOT
# sandboxed — its whole job is to read the real mlx_outputs/.
# sandbox_env <name>: point the three dirs at a fresh tree for one unittest
# gate. pytest_env: clear them, so conftest.py builds its own sandbox (it
# keeps a caller's value, and test_state_sandbox asserts it is conftest's).
sandbox_env() {
    local box="$GATE_TMP/sandbox/$(echo "$1" | tr -c 'A-Za-z0-9_.-' '_')"
    mkdir -p "$box/state" "$box/mlx_outputs" "$box/panel_uploads"
    export LTX_STATE_DIR="$box/state"
    export LTX_OUTPUT_DIR="$box/mlx_outputs"
    export LTX_UPLOADS_DIR="$box/panel_uploads"
    # Licence screens accepted, as conftest.py does for pytest (4.19.8): a
    # suite that queues an H3 / music / Ideogram job tests that job, not the
    # licence gate - the gate has its own suite on a fresh state dir.
    "$VENV_PY" -c 'import sys; sys.path.insert(0, "."); import model_licences as m; [m.accept(sys.argv[1], k, "gate-sandbox") for k in m.LICENCES]' "$box/state"
}
pytest_env() {
    unset LTX_STATE_DIR LTX_OUTPUT_DIR LTX_UPLOADS_DIR
}

# Parallel arrays: gate name, result, log path.
NAMES=()
RESULTS=()
LOGS=()

PASS_N=0
FAIL_N=0
SKIP_N=0
INNER_SKIPS=()

# run_gate <name> <command...>
#   Records PASS on exit 0, FAIL otherwise. Output goes to a per-gate log,
#   echoed immediately only when the gate fails.
run_gate() {
    local name="$1"; shift
    local log="$LOGDIR/$(echo "$name" | tr -c 'A-Za-z0-9_.-' '_').log"
    printf '  %-46s ' "$name"
    if "$@" >"$log" 2>&1; then
        # A suite can pass while some of its tests SKIPPED (a pack this Mac
        # does not have, an opt-in live test). That is not a pass for what
        # those tests cover, so the count goes on the row and the reasons
        # (unittest -v "skipped '...'", pytest -rs "SKIPPED [n] ...", the
        # registry gate's "SKIP  ...") go in the summary, instead of hiding
        # inside a green line.
        local nskip
        nskip=$(grep -Eo 'skipped=[0-9]+|[0-9]+ skipped' "$log" | tail -1 | grep -Eo '[0-9]+')
        if [ -n "$nskip" ] && [ "$nskip" != "0" ]; then
            printf 'PASS  (%s test(s) skipped)\n' "$nskip"
            NAMES+=("$name"); RESULTS+=("PASS ($nskip test(s) skipped)"); LOGS+=("$log")
            while IFS= read -r why; do
                INNER_SKIPS+=("$name: $why")
            done < <(grep -Eo "skipped ['\"].*['\"]\$|^SKIPPED \[[0-9]+\] .*|^SKIP  .*" "$log" | sort -u)
        else
            printf 'PASS\n'
            NAMES+=("$name"); RESULTS+=("PASS"); LOGS+=("$log")
        fi
        PASS_N=$((PASS_N + 1))
    else
        printf 'FAIL\n'
        NAMES+=("$name"); RESULTS+=("FAIL"); LOGS+=("$log")
        FAIL_N=$((FAIL_N + 1))
        echo "    ---- $name output (last 25 lines) ----"
        tail -25 "$log" | sed 's/^/    /'
        echo "    ---- full log: $log ----"
    fi
}

# mark_skip <name> <reason>
mark_skip() {
    local name="$1"; local reason="$2"
    printf '  %-46s SKIP  (%s)\n' "$name" "$reason"
    NAMES+=("$name"); RESULTS+=("SKIP: $reason"); LOGS+=("-")
    SKIP_N=$((SKIP_N + 1))
}

# ---------------------------------------------------------------------------
echo "== compile =="
# ---------------------------------------------------------------------------
run_gate "py_compile (panel, image engine, helper)" \
    "$VENV_PY" -m py_compile mlx_ltx_panel.py image_engine.py mlx_warm_helper.py civitai_client.py usage_stats.py train_support.py version_check.py

# ---------------------------------------------------------------------------
echo
echo "== node gates =="
# ---------------------------------------------------------------------------
if command -v node >/dev/null 2>&1; then
    run_gate "check_ltx_pin.js"         node scripts/check_ltx_pin.js
    run_gate "check_pinokio_scripts.js" node scripts/check_pinokio_scripts.js
    run_gate "check_post_update.js"     node scripts/check_post_update.js
    # The extracted-frontend lint (docs/ARCHITECTURE.md): no-undef and
    # no-redeclare over webapp/js modules + the inline block, plus the
    # cross-file duplicate-publish check. Needs the dev-only eslint from
    # package.json — a missing install is a loud skip, not a pass.
    if [ -d node_modules/eslint ]; then
        run_gate "lint_webapp.mjs" node scripts/lint_webapp.mjs
    else
        mark_skip "lint_webapp.mjs" "eslint not installed — run: npm install"
    fi
else
    mark_skip "check_ltx_pin.js"         "node not on PATH"
    mark_skip "check_pinokio_scripts.js" "node not on PATH"
    mark_skip "check_post_update.js"     "node not on PATH"
    mark_skip "lint_webapp.mjs"          "node not on PATH"
fi

# ---------------------------------------------------------------------------
echo
echo "== registry / schedules =="
# ---------------------------------------------------------------------------
run_gate "assert_registry.py"  "$VENV_PY" scripts/assert_registry.py
run_gate "assert_schedules.py" "$VENV_PY" scripts/assert_schedules.py

# ---------------------------------------------------------------------------
echo
echo "== render-level codec gate =="
# ---------------------------------------------------------------------------
# check_output_codec.py exit semantics (see its main()):
#   0 = the produced file matches what was requested
#   1 = a real codec failure  -> DO NOT PROMOTE
#   2 = inconclusive: no ffprobe, or no panel-rendered clip to check
# 2 is a SKIP with a warning, not a pass — the whole point of this gate is that
# something actually looked at a rendered file.
if [ "$FAST" = "1" ]; then
    mark_skip "check_output_codec.py" "--fast"
else
    printf '  %-46s ' "check_output_codec.py"
    codec_log="$LOGDIR/check_output_codec.log"
    "$VENV_PY" scripts/check_output_codec.py >"$codec_log" 2>&1
    codec_rc=$?
    case "$codec_rc" in
        0)
            printf 'PASS\n'
            NAMES+=("check_output_codec.py"); RESULTS+=("PASS"); LOGS+=("$codec_log")
            PASS_N=$((PASS_N + 1))
            ;;
        2)
            printf 'SKIP  (nothing to check — render the smoke first)\n'
            NAMES+=("check_output_codec.py")
            RESULTS+=("SKIP: no clip / no ffprobe (exit 2)")
            LOGS+=("$codec_log")
            SKIP_N=$((SKIP_N + 1))
            tail -6 "$codec_log" | sed 's/^/    /'
            ;;
        *)
            printf 'FAIL\n'
            NAMES+=("check_output_codec.py"); RESULTS+=("FAIL"); LOGS+=("$codec_log")
            FAIL_N=$((FAIL_N + 1))
            tail -25 "$codec_log" | sed 's/^/    /'
            echo "    ---- full log: $codec_log ----"
            ;;
    esac
fi

# ---------------------------------------------------------------------------
echo
echo "== root test sweep (unittest; pytest-style files through pytest) =="
# ---------------------------------------------------------------------------
# A root file with a module-level `def test_*` is pytest-style (wholly, or
# mixed with TestCase classes). `python -m unittest` collects ZERO tests from
# those functions and prints "Ran 0 tests / OK" — the same false green the
# scripts/ sweep below guards against. Until issue #90 only test_music_engine
# was routed through pytest; 17 other root suites (366 tests) were reported
# PASS while not one of their assertions ran, and test_storyboard_planner
# silently dropped its module-level test. Every such file now goes through
# pytest, and without pytest it is a loud SKIP, never a pass.
HAVE_PYTEST=0
"$VENV_PY" -c "import pytest" >/dev/null 2>&1 && HAVE_PYTEST=1
for t in test_*.py; do
    [ -e "$t" ] || continue
    mod="${t%.py}"
    if [ "$FAST" = "1" ] && [ "$mod" = "test_storyboard_editor_ui" ]; then
        mark_skip "$mod" "--fast"
        continue
    fi
    if grep -Eq '^(async )?def test_' "$t"; then
        if [ "$HAVE_PYTEST" = "1" ]; then
            pytest_env
            run_gate "$mod" "$VENV_PY" -m pytest -q -rs -p no:cacheprovider "$t"
        else
            mark_skip "$mod" "pytest-style suite and pytest is not installed"
        fi
        continue
    fi
    sandbox_env "$mod"
    run_gate "$mod" "$VENV_PY" -m unittest -v "$mod"
done

# ---------------------------------------------------------------------------
echo
echo "== scripts/ test sweep (pytest) =="
# ---------------------------------------------------------------------------
# Three of these are pytest-style (module-level `def test_*`, no TestCase):
#   scripts/test_convert_ltx_mlx.py
#   scripts/test_ltx_pack_diff.py
#   scripts/test_pack_release.py
# `python -m unittest` collects ZERO tests from them and prints "Ran 0 tests /
# OK" — a green line that asserts nothing. They MUST go through pytest, and if
# pytest is not installed we say so loudly rather than paint a false green.
PYTEST_ONLY="scripts/test_convert_ltx_mlx.py scripts/test_ltx_pack_diff.py scripts/test_pack_release.py"

if "$VENV_PY" -c "import pytest" >/dev/null 2>&1; then
    pytest_env
    run_gate "pytest scripts/" "$VENV_PY" -m pytest -q -rs -p no:cacheprovider scripts/
else
    echo
    echo "  ############################################################"
    echo "  #  LOUD SKIP: pytest is NOT installed in the repo venv.    #"
    echo "  #  These three suites are pytest-style and are therefore   #"
    echo "  #  NOT BEING RUN AT ALL:                                   #"
    for f in $PYTEST_ONLY; do
        printf '  #    %-53s#\n' "$f"
    done
    echo "  #  Under 'python -m unittest' they report 'Ran 0 tests OK', #"
    echo "  #  which is a lie, not a pass. Install pytest:              #"
    echo "  #    ltx-2-mlx/env/bin/python -m pip install pytest         #"
    echo "  ############################################################"
    echo
    mark_skip "pytest scripts/" "pytest not installed — 3 suites unrun"
fi

# ---------------------------------------------------------------------------
echo
echo "============================================================"
echo " RELEASE GATES SUMMARY"
[ "$FAST" = "1" ] && echo " (--fast: test_storyboard_editor_ui and check_output_codec skipped)"
echo "============================================================"
i=0
while [ "$i" -lt "${#NAMES[@]}" ]; do
    printf ' %-46s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}"
    i=$((i + 1))
done
if [ "${#INNER_SKIPS[@]}" -gt 0 ]; then
    echo "------------------------------------------------------------"
    echo " Tests SKIPPED inside passing suites (not verified on this Mac):"
    for s in "${INNER_SKIPS[@]}"; do
        printf '   %s\n' "$s"
    done
fi
echo "------------------------------------------------------------"
printf ' PASS %d   FAIL %d   SKIP %d\n' "$PASS_N" "$FAIL_N" "$SKIP_N"
echo " logs: $LOGDIR"
echo "============================================================"

if [ "$FAIL_N" -gt 0 ]; then
    echo
    echo "DO NOT PROMOTE — $FAIL_N gate(s) failed."
    exit 1
fi

if [ "$SKIP_N" -gt 0 ]; then
    echo
    echo "All run gates passed, but $SKIP_N were SKIPPED. A skip is not a pass."
    echo "Read the skip reasons above before promoting."
fi

echo
echo "This script is NOT the whole checklist. Still required by hand:"
echo "  * the FROM-ZERO install (docs/RELEASE_CHECKLIST.md, top)"
echo "  * the UPDATE-PATH gate (previous tag -> Update -> code AND weights)"
echo "  * the Ideogram fresh render + eyeball, and the T2V/I2V smoke renders"
exit 0
