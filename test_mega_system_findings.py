"""Regression receipts for the 2026-09-29 "system" flow-review package (SYS-*).

Full findings: ~/AI/projects/phosphene/review-2026-09-29-ux/report_system.txt
(outside this repo — PM hub, not shipped). These are lightweight source-level
checks in the style of test_docs_and_shortcuts.py: grep-gate the fix so the
regression can't silently come back, without standing up a whole browser for
every one of ~35 findings. Each test names its finding ID in its docstring.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
QUEUE_JS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
HEALTH_JS = (ROOT / "webapp" / "js" / "health.js").read_text(encoding="utf-8")
CHAR_JS = (ROOT / "webapp" / "js" / "characters.js").read_text(encoding="utf-8")
SETTINGS_JS = (ROOT / "webapp" / "js" / "settings.js").read_text(encoding="utf-8")
STAGE_JS = (ROOT / "webapp" / "js" / "stage.js").read_text(encoding="utf-8")
PREVIEW_JS = (ROOT / "webapp" / "js" / "preview.js").read_text(encoding="utf-8")
MAIN_JS = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
PINOKIO_JS = (ROOT / "pinokio.js").read_text(encoding="utf-8")
# The panel's server source now spans the panel file and the modules split out
# of it (train_support.py holds the Train helpers these asserts look for).
PANEL_PY = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8") + "\n" + (
    ROOT / "train_support.py").read_text(encoding="utf-8")


def test_sys08_settings_has_a_models_destination():
    """SYS-08: 'Settings -> Models' pointed nowhere -- Settings had no Models
    section. Assert the Settings modal now has one that opens the real
    Models modal, so every existing 'Settings -> Models' string in error
    copy resolves to something real."""
    settings_modal = HTML[HTML.index('id="settingsModal"'):HTML.index('id="modelsModal"')]
    assert "<h3>Models</h3>" in settings_modal
    assert "openModelsModal()" in settings_modal


def test_sys09_toast_accepts_string_kind_shorthand():
    """SYS-09: health.js called phosToast(message, 'warn') / (message, 'error')
    -- a bare string where phosToast expected an options object. `opts.kind`
    on a string is undefined (no throw), so these silently rendered as
    neutral info toasts. phosToast must now accept the string shorthand."""
    assert "if (typeof opts === 'string') opts = { kind: opts };" in QUEUE_JS
    assert "warn: 'warning'" in QUEUE_JS
    assert "error: 'danger'" in QUEUE_JS


def test_sys09_toast_wraps_instead_of_truncating():
    """SYS-09: .phos-toast-msg was white-space:nowrap + ellipsis inside a
    480px cap -- 20+ literals over 70 chars were cut off mid-sentence.
    Must wrap (multi-line) instead of truncating to one line."""
    m = re.search(r"\.phos-toast \.phos-toast-msg\s*\{([^}]*)\}", CSS)
    assert m, "expected a .phos-toast .phos-toast-msg rule"
    body = m.group(1)
    assert "nowrap" not in body
    assert "text-overflow: ellipsis" not in body


def test_sys09_toast_has_a_close_control():
    """A duration:0 (persistent) toast had no way to be dismissed -- no close
    button existed at all. Every toast now gets one."""
    assert "phos-toast-close" in QUEUE_JS
    assert ".phos-toast-close" in CSS


def test_sys09_warning_kind_has_real_styling():
    """kind:'warning' toasts (5 existing call sites in stage.js/queue.js)
    rendered with the neutral info icon and no border tint -- only
    success/danger were ever styled. Warning needs its own look."""
    assert ".phos-toast.phos-toast-warning" in CSS
    assert "ph-warning-fill" in QUEUE_JS


def test_sys10a_offline_banner_icon_is_inline_svg_not_a_network_image():
    """SYS-10a: the offline banner used <img src="/assets/favicon-64.png">
    -- while offline, that request also fails, so the icon showed a
    broken-image glyph. Must use the already-inlined SVG sprite instead."""
    assert "bar.innerHTML =" in QUEUE_JS
    banner_html = QUEUE_JS[QUEUE_JS.index("bar.innerHTML ="):QUEUE_JS.index("document.body.appendChild(bar)")]
    assert 'src="/assets/favicon-64.png"' not in banner_html
    assert "#ph-warning-fill" in banner_html


def test_sys10a_generate_disabled_while_offline():
    """SYS-10a: Generate did nothing while offline (uncaught fetch failure,
    no toast, Now stuck on Idle). genBtn must now be gated + the submit
    path must catch the fetch failure instead of leaving it uncaught."""
    assert "_applyOfflineGate" in QUEUE_JS
    assert "offlineBlocked" in QUEUE_JS
    # The submit handler must catch api('/queue/add', ...) failing.
    submit_region = QUEUE_JS[QUEUE_JS.index("await api('/queue/add','POST',fd);"):]
    assert submit_region.startswith("await api('/queue/add','POST',fd);\n  } catch (e) {")


def test_sys10b_training_upload_reports_skipped_and_failed_files():
    """SYS-10b: HEIC files were dropped from the picker with zero message,
    and a 413/error on file N was overwritten by file N+1's progress text
    before anyone could read it. Both must now reach a persistent summary."""
    assert "const rejected = all.filter" in CHAR_JS
    assert "let failed = [];" in CHAR_JS
    assert "skipped: `" in CHAR_JS
    assert "train-status-warn" in CHAR_JS
    assert ".train-status-warn" in CSS


def test_sys10c_voice_before_images_has_an_inline_error():
    """SYS-10c: dropping a voice clip before any training image produced
    'nothing on the voice card' -- the only feedback lived in #trainStatus,
    far from the voice card and unstyled. Must show inline on the card."""
    assert "trainVoiceInlineError" in HTML
    assert "_trainVoiceInlineError" in CHAR_JS
    assert ".train-voice-inline-error" in CSS


def test_sys10d_and_sys37_stop_disabled_when_idle():
    """SYS-10d / SYS-37: Stop was red and clickable with nothing running --
    clicking it was a silent no-op. All six copies (one per tab) must ship
    `disabled` by default and be toggled from poll()."""
    assert HTML.count('class="danger js-stop-btn" disabled') == 6
    assert "stopActive" in QUEUE_JS
    assert "js-stop-btn" in QUEUE_JS
    assert "button.danger:disabled" in CSS


def test_sys03_train_tab_gates_the_whole_body_under_min_ram():
    """SYS-03: a Mac under TRAIN_MIN_RAM_GB could prepare a whole dataset,
    caption it, and start a 41 GB download before Start refused. The whole
    Train tab body must gate at the entry instead."""
    assert 'id="trainGateCard"' in HTML
    assert "function trainApplyRamGate" in CHAR_JS
    gate_fn = CHAR_JS[CHAR_JS.index("function trainApplyRamGate"):
                       CHAR_JS.index("function trainInit")]
    assert "ram < minRam" in gate_fn
    # Codex UI-7: gated by a class on the section (panel.css hides the
    # rest), never by overwriting each child's own `hidden`.
    assert "section.classList.toggle('train-ram-gated', gated)" in gate_fn
    assert "child.hidden = gated" not in gate_fn
    # Called at the top of trainInit, before anything else wires up.
    init_fn = CHAR_JS[CHAR_JS.index("function trainInit"):CHAR_JS.index("function trainInit") + 400]
    assert "if (trainApplyRamGate()) return;" in init_fn
    # Also re-checked every poll, not just on tab-open.
    assert "trainApplyRamGate" in QUEUE_JS
    # Backend supplies the exact numbers the gate compares (no hardcoded
    # threshold duplicated client-side).
    routes_queue_py = (ROOT / "panel" / "routes_queue.py").read_text(encoding="utf-8")
    assert '"ram_gb": round(P.SYSTEM_RAM_GB, 1)' in routes_queue_py
    assert '"train_min_ram_gb": P.TRAIN_MIN_RAM_GB' in routes_queue_py


def test_sys04_preflight_discloses_one_total_and_downloads_sequentially():
    """SYS-04: the 41 GB cost was disclosed piecemeal (two separate
    Download buttons, no total) and the further 30 GB Q8-to-use-it cost
    wasn't mentioned on this card at all. Must show one total + a
    sequential "Download all" + the Q8 disclosure."""
    assert "Get ready to train: ~${totalGb" in CHAR_JS
    assert "async function trainInstallAll(keys)" in CHAR_JS
    assert "30 GB" in CHAR_JS and "Q8 pack too" in CHAR_JS
    # The sequential download must watch the SPECIFIC key just installed,
    # not "everything missing" -- otherwise step 1 of N never resolves
    # until the last item finishes.
    install_fn = CHAR_JS[CHAR_JS.index("async function trainInstall(key, onDone)"):]
    assert "(d2.required || []).find(m => m.key === key)" in install_fn


def test_sys12_bug_context_scrubs_home_paths_from_every_log_line():
    """SYS-12, privacy: the bug-report textarea pre-fills from a raw log
    tail, which carries /Users/<username>/... paths -- the username is
    often the person's real name -- straight into a public GitHub issue.
    Must scrub every line, not just truncate to one like the telemetry
    scrubber does."""
    import sys as _sys
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    import mlx_ltx_panel as P

    scrubbed = P._bug_report_scrub_line(
        "error writing /Users/jane.example/Desktop/panel_uploads/photo.jpg: no space")
    assert "jane.example" not in scrubbed
    assert "<path>" in scrubbed
    # Multi-line tails must keep every line (the telemetry scrubber
    # truncates to the first line, which would gut a 50-line diagnostic).
    routes_meta_py = (ROOT / "panel" / "routes_meta.py").read_text(encoding="utf-8")
    assert "[P._bug_report_scrub_line(line) for line in tail]" in routes_meta_py


def test_sys12_crash_zip_and_count_filter_to_phosphenes_own_crashes():
    """SYS-12: 'Include latest crash reports' zipped the 5 newest .ips from
    ANY process on the Mac (Safari, Mail, a game) into a Phosphene bug
    report -- an unrelated app's crash tells the maintainer nothing and is
    an unnecessary privacy cost. Must filter to Phosphene's own process
    names, and the displayed count must match what actually gets zipped.
    Codex UI-6 tightened the filter from a file-name prefix to a positive
    identification read out of each report (test_codex_4170_ui pins the
    behaviour); both handlers still share ONE filter."""
    routes_meta_py = (ROOT / "panel" / "routes_meta.py").read_text(encoding="utf-8")
    assert routes_meta_py.count("crash_count = len(_phosphene_crash_reports(diag))") == 1
    assert routes_meta_py.count("ips = _phosphene_crash_reports(diag)[:5]") == 1


def test_sys12_crash_checkbox_defaults_off():
    """SYS-12: 'Include latest crash reports' was pre-ticked next to a
    Submit button someone could click fast. Zipping real crash logs into a
    public issue should be opt-in per report."""
    bug_modal = HTML[HTML.index('id="bugModal"'):HTML.index('id="bottomPane"')]
    assert '<input id="bugCrashCheck" type="checkbox">' in bug_modal
    assert '<input id="bugCrashCheck" type="checkbox" checked>' not in bug_modal
    assert "document.getElementById('bugCrashCheck').checked = false;" in SETTINGS_JS


def test_sys40_bug_modal_offers_a_non_github_path():
    """SYS-40: 'Report a bug' assumed a GitHub account and English. Must
    offer a copy-anywhere path and a link to somewhere non-English
    creators already are."""
    assert "function copyBugDiagnostics" in SETTINGS_JS
    assert "onclick=\"copyBugDiagnostics()\"" in HTML
    assert "x.com/PhospheneAI" in HTML


def test_sys41_train_start_counts_only_past_the_ram_refusal():
    """SYS-41: train_start fired before the RAM refusal, so the fleet's
    train-start count included refused clicks. Must fire only past it,
    with a paired train_refused for the refused clicks."""
    routes_train_py = (ROOT / "panel" / "routes_train.py").read_text(encoding="utf-8")
    refusal_idx = routes_train_py.index("TRAIN_MIN_RAM_GB} GB of memory")
    start_call_idx = routes_train_py.index('P._analytics_feature("train_start")')
    assert start_call_idx > refusal_idx
    assert 'P._analytics_feature("train_refused", "ram")' in routes_train_py
    assert '"train_refused"' in PANEL_PY


def test_sys21_enhance_button_is_inline_flex_and_version_neutral():
    """SYS-21: #enhanceBtn inherited .ghost-btn's display:block (the file's
    own comment elsewhere documents this exact bug class), so the icon+
    label wrapped to two lines in a one-line-tall button. Tooltip also
    named LTX 2.3 while the active engine has been 2.5 since v4.0."""
    assert "#enhanceBtn {" in CSS
    enhance_rule = CSS[CSS.index("#enhanceBtn {"):CSS.index("#enhanceBtn {") + 200]
    assert "inline-flex" in enhance_rule
    enhance_btn_tag = HTML[HTML.index('id="enhanceBtn"') - 20:HTML.index('id="enhanceBtn"') + 200]
    assert "LTX 2.3" not in enhance_btn_tag
    assert 'title="Use Gemma to rewrite your prompt in the style LTX was trained on"' in HTML


def test_sys21_avoid_toggle_has_a_resting_border():
    """SYS-21: "Avoid +" (.ct-link) had border:1px solid TRANSPARENT, so it
    read as plain text next to the always-bordered No-music/No-voice
    pills right beside it."""
    ct_link_rule = CSS[CSS.index(".ct-link {"):CSS.index(".ct-link:hover")]
    assert "border: 1px solid transparent" not in ct_link_rule
    assert "border: 1px solid var(--border)" in ct_link_rule


def test_sys38_version_pill_hides_build_internals_at_every_width():
    """SYS-38: branch + short SHA + commit date used to show inline at
    >=1440px ("HEAD - 70665d3 (2026-09-28)") -- developer detail already
    duplicated in the pill's own tooltip."""
    assert "#versionPill .vp-detail { display: none; }" in CSS
    # Must NOT be scoped inside the narrow-width media query only.
    media_block = CSS[CSS.index("@media (max-width: 1400px) {"):
                       CSS.index("@media (max-width: 1400px) {") + 300]
    assert "vp-detail" not in media_block


def test_sys39_model_tag_tooltip_clarifies_clip_vs_engine():
    """SYS-39: the Now-bar model tag follows the SELECTED clip (deliberate
    -- see boot.js's own comment, fixing a worse mis-attribution bug), but
    with no hint of that, switching selection read as "the engine
    changed". Tooltip must say which case it's in."""
    fn = (ROOT / "webapp" / "js" / "boot.js").read_text(encoding="utf-8")
    fn = fn[fn.index("function updateModelCredit"):]
    assert "This clip was rendered with" in fn
    assert "Current engine:" in fn


def test_sys18_pinokio_q8_menu_entry_gated_by_ram():
    """SYS-18: the Pinokio sidebar's Q8 download entry was offered on
    every Mac regardless of RAM -- 30 GB of disk/bandwidth for a feature
    the panel's own tier table refuses under 48 GB. Same threshold as the
    existing h3Capable()/musicCapable() pattern."""
    pinokio_js = (ROOT / "pinokio.js").read_text(encoding="utf-8")
    assert "function q8Capable()" in pinokio_js
    assert "48 * 1000 * 1000 * 1000" in pinokio_js
    assert "if (!q8_ready && q8Capable())" in pinokio_js


def test_sys30_reset_and_install_qwen_copy():
    """SYS-30: "Reset" sat one click from "Update" with no explanation of
    what it does (reset.js deletes the engine). "Reinstall image engines"
    showed on a fresh install that never had the pack, implying it broke."""
    pinokio_js = (ROOT / "pinokio.js").read_text(encoding="utf-8")
    assert pinokio_js.count('text: "Reset engine (keeps models & outputs)"') == 3
    assert 'text: "Reset"' not in pinokio_js.replace(
        'text: "Reset engine (keeps models & outputs)"', '')
    assert "Install/repair image engines" in pinokio_js


def test_sys15_delta_rms_numbers_move_behind_a_details_disclosure():
    """SYS-15: the Train tab's live preset note led with raw delta_rms
    numbers -- lab jargon a creator can't act on -- while they were still
    choosing a preset. Outcome language up front; numbers under a
    disclosure for anyone who wants them (e.g. filing a bug report)."""
    fn = CHAR_JS[CHAR_JS.index("function trainUpdatePresetNote"):
                 CHAR_JS.index("function trainDisableSelectAbove")]
    lead = fn[fn.index("el.innerHTML =\n    '<strong>High is the only recipe"):
              fn.index("<details")]
    assert "delta_rms" not in lead
    assert "<details class=\"h3-diag\">" in fn
    assert "delta_rms" in fn  # still present, just moved


def test_sys15_dev_transformer_blurb_is_outcome_language():
    """SYS-15/SYS-42: 'the dev transformer has the right flow-matching
    schedule for LoRA-from-images training' was lab jargon in the Train
    tab's model-download card."""
    assert "flow-matching schedule" not in PANEL_PY
    assert "looks fine in the log and does nothing" in PANEL_PY


def test_sys33_troubleshooting_covers_the_previously_missing_sections():
    """SYS-33: docs existed but missed several top fleet failures, and
    error cards never linked to the sections that DID exist."""
    troubleshooting = (ROOT / "webapp" / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
    for anchor in ("{#training-failed}", "{#offline}", "{#queue-paused}"):
        assert anchor in troubleshooting
    assert "openDocs('troubleshooting','queue-paused')" in HTML
    assert "docsAnchor: 'memory'" in QUEUE_JS
    assert "docsAnchor: 'gpu-watchdog'" in QUEUE_JS
    assert "docsAnchor: 'training-failed'" in QUEUE_JS
    assert "openDocs('troubleshooting','${docsAnchor}')" in QUEUE_JS


def test_sys16_first_run_card_exists_and_is_gated_on_the_activation_flag():
    """SYS-16 (priority item): no first-run path -- a fresh 8 GB Mac and a
    64 GB Studio saw the same empty prompt. BOOT.first_run must reuse the
    SAME flag render_completed's first_render prop already tracks (has
    this install ever finished a render), not a second flag that could
    drift from it."""
    assert '"first_run": not bool(get_settings().get("analytics_first_render_reported"))' in PANEL_PY
    assert 'id="firstRunCard"' in HTML
    # Three starter prompts, each queues directly (one click, per the fix).
    assert HTML.count("first-run-prompt-btn") >= 3
    assert 'onclick="useFirstRunPrompt(this)"' in HTML
    assert "downloadSampleCharacter(); return false;" in HTML


def test_sys16_first_run_card_hides_on_first_finished_render_or_dismiss():
    """SYS-16: must disappear the moment a render finishes in this session
    (not wait for a page reload) and on manual dismiss, and must be
    re-checked every poll like the other gates in this file."""
    fn = QUEUE_JS[QUEUE_JS.index("function applyFirstRunCard"):
                  QUEUE_JS.index("async function dismissFirstRunCard")]
    assert "everFinished" in fn
    assert "j.status === 'done'" in fn
    assert "applyFirstRunCard(s)" in QUEUE_JS
    assert "first_run_card_dismissed" in PANEL_PY


def test_sys16_starter_prompt_queues_at_a_safe_default():
    """SYS-16: 'each one click to queue at this Mac's safe settings' --
    must force Balanced (not whatever quality/mode happened to be
    selected before) and actually submit, not just fill the field."""
    fn = QUEUE_JS[QUEUE_JS.index("function useFirstRunPrompt"):]
    assert "setQuality('balanced')" in fn
    assert "form.requestSubmit()" in fn


def test_sys16_train_start_analytics_carries_ram_for_per_tier_funnels():
    """SYS-16: 'track first_queue against first_render per tier' -- the
    reviewer's own finding is that activation is lowest on the machines
    that struggle most, so the funnel needs a hardware signal on
    install_step (first_queue), matching what render_completed
    (first_render) already carries."""
    fn = PANEL_PY[PANEL_PY.index("def _analytics_install_step("):
                  PANEL_PY.index("def _analytics_update_outcome(")]
    assert 'props["ram_gb"] = int(round(SYSTEM_RAM_GB))' in fn
    assert "ram_gb" in (ROOT / "docs" / "ANALYTICS.md").read_text(encoding="utf-8")[
        (ROOT / "docs" / "ANALYTICS.md").read_text(encoding="utf-8").index("install_step` (v4.16.0)"):]


def test_sys25_workflow_tabs_have_tab_semantics():
    """SYS-25: the workflow tab strip was a plain row of <button>s -- 0
    role=tablist, 0 role=tab, 0 aria-selected. A screen reader had no way
    to know it was a tab strip or which tab was active."""
    nav_idx = HTML.index('id="workflowTabs"')
    nav = HTML[nav_idx:HTML.index('</nav>', nav_idx)]
    assert 'role="tablist"' in HTML[nav_idx:nav_idx + 60]
    assert nav.count('role="tab"') == 7
    assert nav.count('aria-selected=') == 7
    editor_js = (ROOT / "webapp" / "js" / "editor.js").read_text(encoding="utf-8")
    assert "b.setAttribute('aria-selected'" in editor_js


def test_sys25_prompt_textarea_has_a_label():
    """SYS-25: #prompt had no label or aria-label (placeholder only) --
    a placeholder is not an accessible name."""
    assert 'aria-label="Describe your shot"' in HTML


def test_sys25_hidden_toggle_checkbox_gets_a_visible_focus_ring():
    """SYS-25: 'No music'/'No voice' checkboxes are visually 0x0 (opacity:0,
    width/height:0), so a keyboard Tab put the focus ring on nothing
    visible. :focus-within on the wrapping pill fixes it."""
    assert ".toggle-pill:focus-within" in CSS


def test_sys25_icon_only_header_buttons_have_static_aria_labels():
    """SYS-25: icon-only buttons had `title` only (tips.js sets it lazily
    on hover/focus) -- no static aria-label for immediate screen-reader
    announcement."""
    for marker in ('id="bugBtn"', 'id="settingsBtn"', 'id="starLink"',
                   'class="phosphene-x-link"'):
        idx = HTML.index(marker)
        tag = HTML[idx:HTML.index(">", idx)]
        assert "aria-label=" in tag, f"missing aria-label near {marker}"


def test_sys24_generate_button_meets_contrast_floor():
    """SYS-24: white-on-#2f81f7 (the shared --accent token) measured
    3.75:1 on the Generate button -- below the 4.5:1 AA floor for normal
    text. Scoped to #genBtn specifically."""
    def _contrast(hex1, hex2):
        def lum(h):
            r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
            def f(x):
                x /= 255
                return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
        l1, l2 = lum(hex1), lum(hex2)
        l1, l2 = max(l1, l2), min(l1, l2)
        return (l1 + 0.05) / (l2 + 0.05)

    assert "#genBtn { background: #1f6feb" in CSS
    assert _contrast("ffffff", "1f6feb") >= 4.5
    # The hover state must not regress below the resting state's contrast
    # (button.primary:hover brightens toward --accent-bright, which is
    # WORSE against white text).
    m = re.search(r"#genBtn:hover:not\(:disabled\) \{ background: #([0-9a-f]{6})", CSS)
    assert m, "expected a #genBtn hover rule"
    assert _contrast("ffffff", m.group(1)) >= 4.5


def test_sys24_train_video_link_is_not_invisible_when_visited():
    """SYS-24: this is a same-page href="#" link, so after the first click
    it's :visited and inherits the browser's dull default -- reported as
    'dark blue on navy, nearly invisible'. Must set explicit color for
    both states."""
    assert ".train-go-video-link,\n    .train-go-video-link:visited {" in CSS


def test_sys35_a_finished_training_announces_itself_with_a_working_action():
    """SYS-35: the only pointer from a finished training back to Video was
    a link at the bottom of the Train tab -- invisible to someone watching
    the Now card for their training to finish. Must announce right where
    completion is already detected, with a real 'make a clip' action."""
    assert "_trainAnnounceReady(j)" in QUEUE_JS
    assert "async function _trainAnnounceReady(job)" in CHAR_JS
    fn = CHAR_JS[CHAR_JS.index("async function _trainAnnounceReady"):
                 CHAR_JS.index("function trainUseInVideo")]
    assert "trainUseInVideo(newest.path, newest.trigger, 't2v')" in fn
    assert "duration: 0" in fn  # persistent, not a 3-4s toast someone can miss


def test_sys26_upload_keeps_unicode_filenames():
    """SYS-26: /upload sanitized with [^A-Za-z0-9._-]+ -> "_", ASCII-only --
    a Japanese filename テスト画像_猫.jpg was stored as ____.jpg and
    "Recent uploads" listed it the same way. Must keep the actual
    characters via the shared _unicode_slug."""
    import sys as _sys
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    import mlx_ltx_panel as P

    orig = P.Path("テスト画像_猫.jpg")
    stem = P._unicode_slug(orig.stem, max_chars=120, fallback="upload")
    assert stem == "テスト画像_猫"
    routes_files_py = (ROOT / "panel" / "routes_files.py").read_text(encoding="utf-8")
    assert 'safe_name = P.re.sub(r"[^A-Za-z0-9._-]+"' not in routes_files_py
    assert "P._unicode_slug(_orig.stem" in routes_files_py


def test_sys26_descriptive_filename_and_sb_slug_keep_non_latin_scripts():
    """SYS-26/VC-13/FILM-45: _descriptive_filename and _sb_slug were both
    ASCII-only sluggers -- a Japanese or Chinese prompt/title extracted
    zero words and fell to a bare fallback ("t2v", "shot"), so every such
    render/shot in one session shared the same filename stem."""
    import sys as _sys
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    import mlx_ltx_panel as P

    assert P._descriptive_filename("", "夕暮れの港で彼は笑う", fallback="t2v") == "夕暮れの港で彼は笑う"
    assert P._descriptive_filename("", "Une femme élégante marche", fallback="t2v") == "Une_femme_élégante_marche"
    assert P._unicode_slug("Café été") == "Café_été"
    assert P._sb_slug("夕暮れの港", words=6) != "shot"
    assert P._sb_slug("", words=6) == "shot"


def test_sys27_speech_detection_and_extraction_cover_cjk_and_french_quotes():
    """SYS-27/FILM-45 (i18n bullet: CJK quotes in speech detection): 「」
    『』«» were invisible to storyboard.py's speech detector and span
    extractor -- a Japanese or French dialogue line's no_voice defaulted
    to "on" (silent) and the duration-fit check never ran for it."""
    import sys as _sys
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    import storyboard as sb

    ja = "夕暮れの港で、彼は「今日は大漁だ」と笑う。"
    assert sb.shot_speech_problem(ja) is None  # speech IS present -- no problem
    assert sb.spoken_spans(ja) == ["今日は大漁だ"]
    fr = "Elle dit «je reviendrai demain sans faute» avec un sourire."
    assert sb.shot_speech_problem(fr) is None
    assert sb.spoken_spans(fr) == ["je reviendrai demain sans faute"]
    # A bare one-character interjection must not false-positive as
    # multi-word speech (mirrors the existing "2+ words" guard for
    # space-separated scripts).
    assert sb._SPOKEN_WORDS_RE.search("彼は「ん」とうなずいた。") is None
    # English straight-quote path must be unaffected.
    assert sb.shot_speech_problem("A woman says 'I will be right back' now.") is None


def test_sys27_cjk_word_counting_does_not_undercount_speech_duration():
    """SYS-27/FILM-45: len(text.split()) treated a whole CJK sentence as
    ONE word (no whitespace between characters), so a shot with real
    dialogue was fit to a duration sized for one word -- badly too short.
    Must count CJK characters individually."""
    import sys as _sys
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    import storyboard as sb

    six_char_phrase = "今日は大漁だ"
    assert len(six_char_phrase.split()) == 1  # the bug, if using split()
    # 4.17 merge: the board package fixed the same FILM-45 counting as
    # storyboard.spoken_word_count; one implementation ships.
    assert sb.spoken_word_count(six_char_phrase) == 6
    assert sb.spoken_word_count("hello there friend") == 3
    panel_py_snip = PANEL_PY[PANEL_PY.index("_spans = storyboard.spoken_spans"):
                             PANEL_PY.index("_spans = storyboard.spoken_spans") + 400]
    assert "storyboard.spoken_word_count(x)" in panel_py_snip


def test_sys27_fullwidth_colon_in_location_parsing():
    """SYS-27 (i18n bullet: "：" in location parsing): a Japanese
    "場所：説明" line used ASCII partition(":") only and lost its
    intended name/description split. Fixed server-side (authoritative)
    and client-side (the live-preview mirror)."""
    import sys as _sys
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    import mlx_ltx_panel as P

    parsed = P._sb_parse_locations("港：夕暮れの漁港、静かな波の音")
    assert len(parsed) == 1
    assert parsed[0]["name"] == "港"
    assert parsed[0]["description"] == "夕暮れの漁港、静かな波の音"
    storyboard_js = (ROOT / "webapp" / "js" / "storyboard.js").read_text(encoding="utf-8")
    assert "raw.indexOf('：')" in storyboard_js


def test_sys41_test_rig_flag_fixes_fleet_pollution_at_the_source():
    """SYS-41: the fleet numbers that steer UX were polluted by the owner's
    own test rigs (~410 installs, 64 GB, skewing non-activation from ~32%
    to ~54%), found only by an ad hoc heuristic in sys-fleet/q.py. Must be
    fixed at the source: a flag any dev/gate/clean-room run can set, tagged
    onto every event, documented for future queries to filter on."""
    import os as _os
    import sys as _sys
    from pathlib import Path as _Path
    from unittest import mock as _mock

    _root = _Path(__file__).resolve().parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    import mlx_ltx_panel as P

    assert P._analytics_is_test_rig() is False
    with _mock.patch.dict(_os.environ, {"PHOSPHENE_TEST_RIG": "1"}):
        assert P._analytics_is_test_rig() is True
    # Wired into the single capture choke point, after clean_props (so the
    # whitelist pass cannot drop it).
    capture_fn = PANEL_PY[PANEL_PY.index("def _analytics_capture("):
                           PANEL_PY.index("def _analytics_capture(") + 1200]
    assert 'payload["props"]["test_rig"] = True' in capture_fn
    assert "PHOSPHENE_TEST_RIG" in (ROOT / "docs" / "ANALYTICS.md").read_text(encoding="utf-8")


def test_sys19_health_chip_colors_by_pressure_level_not_used_ratio():
    """SYS-19: the health chip colored by pressure_pct (a USED ratio --
    active+wired+compressed / total), and macOS routinely sits at 75-90%
    "used" by that measure while idle, so the chip read amber/red almost
    all day on 8-16 GB Macs. Must color by the OS's own pressure level
    (kern.memorystatus_vm_pressure_level), and treat elevated pressure as
    normal while a render is actively running."""
    assert '"pressure_level"' in PANEL_PY
    assert "kern.memorystatus_vm_pressure_level" in PANEL_PY
    # plan_memory_policy's existing pressure_pct threshold must be
    # untouched -- this is additive, not a semantic change to a value
    # that also gates real render behavior.
    assert 'pressured = pressure >= 82 or swap_gb >= 4.0' in PANEL_PY
    mem_block = QUEUE_JS[QUEUE_JS.index("const m = s.memory;"):
                          QUEUE_JS.index("const m = s.memory;") + 1400]
    assert "m.pressure_level" in mem_block
    assert "rendering" in mem_block
    assert "if (m.pressure_pct > 90)" not in mem_block


def test_sys19_helper_row_renamed_to_engine_in_plain_language():
    """SYS-19: popover rows read as internal process language ("helper
    idle") rather than what a creator actually wants to know (can I
    render right now)."""
    assert ">Engine</span>" in HTML
    assert "engine ready" in QUEUE_JS
    assert "engine resting" in QUEUE_JS


def test_sys19_popover_closes_before_its_modals_open():
    """SYS-19: clicking the Tier or Models row inside the still-open health
    popover opened that modal UNDERNEATH the popover."""
    assert "closeHealthPop," in HEALTH_JS
    settings_js = (ROOT / "webapp" / "js" / "settings.js").read_text(encoding="utf-8")
    preview_js = (ROOT / "webapp" / "js" / "preview.js").read_text(encoding="utf-8")
    tier_fn = settings_js[settings_js.index("function openTierModal()"):]
    models_fn = preview_js[preview_js.index("function openModelsModal()"):]
    assert "closeHealthPop()" in tier_fn[:300]
    assert "closeHealthPop()" in models_fn[:300]


def test_sys29_update_banner_and_pill_self_restart_instead_of_asking():
    """SYS-29: two update mechanisms -- the banner's Update did a git pull
    then told the user to go click Stop then Start in Pinokio themselves,
    right next to panelRestart() (the one-click self-restart already used
    for the stale-process case). Must finish what it started."""
    fn = HEALTH_JS[HEALTH_JS.index("function _ubRestartState"):
                   HEALTH_JS.index("async function _ubSaveSetting")]
    assert "panelRestart()" in fn
    assert "requiresFullUpdate" in fn
    pill_fn = HEALTH_JS[HEALTH_JS.index("async function versionPillClick"):
                        HEALTH_JS.index("async function versionPillClick") + 1200]
    assert "await panelRestart();" in pill_fn


def test_sys17_models_card_and_row_order_fixed():
    """SYS-17: "Models ready · 3/12" read as 25% ready (the denominator
    counts every optional add-on); duplicate "Manage models" links; the
    modal listed the previous-generation pack first and the actual
    renderer 8th; descriptions were ellipsis-truncated."""
    settings_js = (ROOT / "webapp" / "js" / "settings.js").read_text(encoding="utf-8")
    assert "Ready to render ✓" in settings_js
    assert "Manage models →" not in settings_js
    preview_js = (ROOT / "webapp" / "js" / "preview.js").read_text(encoding="utf-8")
    assert "const rank = (r) =>" in preview_js
    assert "white-space: normal" in CSS
    models_sub_rule = CSS[CSS.index(".models-list li .meta .sub"):
                           CSS.index(".models-list li .meta .sub") + 200]
    assert "ellipsis" not in models_sub_rule


def test_sys22_shot_setup_jump_opens_closed_details_then_scrolls():
    """SYS-22: Quality/Length lives inside a <details> that starts closed;
    a plain scrollIntoView on hidden content is a silent no-op. Verified
    live in a real browser session (see PR/commit notes) -- this pins the
    two things that made the live bug: opening before scrolling, and NOT
    using 'smooth' (which didn't complete reliably right after a layout
    change in the same gesture)."""
    fn = QUEUE_JS[QUEUE_JS.index("function scrollToShotSetup"):
                  QUEUE_JS.index("function applyPackIncompleteGate")]
    assert "details.open = true" in fn
    assert "behavior: 'smooth'" not in fn
    assert "behavior: 'auto'" in fn
    assert 'onclick="scrollToShotSetup()"' in HTML


def test_sys23_images_and_train_tabs_get_the_same_gutter_as_video():
    """SYS-23: .form-pane's own padding was zeroed and moved onto
    individual direct children -- #genForm (Video) got padding: 0 18px
    back, #studioSection (Images) and #trainSection (Train) never did."""
    gutter_block = CSS[CSS.index(".form-pane > #genForm { padding: 0 18px; }"):
                        CSS.index(".form-pane > #genForm { padding: 0 18px; }") + 700]
    assert "#studioSection { padding: 0 18px; }" in gutter_block
    assert "#trainSection { padding: 0 18px; }" in gutter_block
    assert "#firstRunCard { margin: 0 18px 14px; }" in gutter_block


def test_sys36_train_dataset_copy_no_longer_states_a_hard_max():
    """SYS-36: the counter/hint contradicted itself -- "0 / 50 images" at
    rest, "4 / 500 images" once JS took over (TRAIN_MAX_IMAGES is 500,
    the static markup said 50). Must not assert a specific max at all
    (20-50 is the real sweet spot; 500 is a technical ceiling)."""
    assert "0 / 50 images" not in HTML
    assert "Drop 15-50 images" not in HTML
    assert "20–50 is the sweet spot" in HTML
    assert "n} / ${TRAIN_MAX} images" not in CHAR_JS


def test_breaker_notice_and_train_gate_card_respect_the_hidden_attribute():
    """Caught in a live browser check, not by source review alone: giving
    .breaker-notice / .train-gate-card a bare `display: flex` rule beats
    the UA stylesheet's `[hidden] { display: none }` at equal specificity
    (whichever rule is later in the cascade wins) -- so
    breakerNotice.hidden = true / trainGateCard.hidden = true left both
    visibly flex'd anyway. This is the exact bug class the review's own
    VC-07/H3-02 findings are about ("CSS lets hidden controls show").
    This codebase already has the fix pattern once
    ([data-spicy-only][hidden] { display: none !important; }); both new
    cards need the same one."""
    assert ".breaker-notice[hidden] { display: none !important; }" in CSS
    assert ".train-gate-card[hidden] { display: none !important; }" in CSS
    # Order matters for readability but not correctness here (!important
    # wins regardless of order against a non-important rule) -- still,
    # keep the guard present for both.
    for selector in (".breaker-notice", ".train-gate-card"):
        rule_start = CSS.index(f"{selector} {{")
        # The plain (non-!important) display:flex rule must exist too --
        # this test protects the co-existence, not just the guard alone.
        assert "display: flex" in CSS[rule_start:rule_start + 100]


def test_sys32_breaker_reason_reaches_status_and_the_ui():
    """SYS-32: the circuit breaker's reason lived only in a push() log line
    (Logs tab). Must be in STATE, in the /status payload, and rendered as a
    persistent notice above Generate."""
    assert '"paused_reason"' in PANEL_PY
    assert 'STATE["paused_reason"] = (' in PANEL_PY
    routes_queue_py = (ROOT / "panel" / "routes_queue.py").read_text(encoding="utf-8")
    assert '"paused_reason": P.STATE.get("paused_reason")' in routes_queue_py
    assert 'P.STATE["paused_reason"] = None' in routes_queue_py
    assert "breakerNotice" in HTML
    assert "breakerNotice" in QUEUE_JS


def test_sys07_watchdog_and_oom_get_actionable_copy_and_retry_smaller():
    """SYS-07: the #1 (Metal watchdog) and #3 (jetsam OOM) fleet failures
    asked for a crashlog instead of saying what to change. Must lead with
    what happened + what to try, offer a real "Retry smaller", and demote
    the crashlog ask behind Details."""
    sigkill_region = QUEUE_JS[QUEUE_JS.index("if (rawLower.includes('sigkill'))"):
                               QUEUE_JS.index("if (rawLower.includes('sigsegv')")]
    assert "ran out of memory" in sigkill_region
    assert "smaller: true" in sigkill_region
    sigabrt_region = QUEUE_JS[QUEUE_JS.index("if (rawLower.includes('sigabrt'))"):]
    assert "isWatchdog" in sigabrt_region
    assert "ran out of GPU time" in sigabrt_region
    assert "smaller: true" in sigabrt_region
    # The crashlog ask must be demoted to a details/summary disclosure, not
    # inline in the headline hint.
    assert "details: '" in QUEUE_JS or 'details: "' in QUEUE_JS
    assert "Details for troubleshooting</summary>" in QUEUE_JS
    # The server-side half: /queue/retry must accept and act on smaller=1.
    routes_queue_py = (ROOT / "panel" / "routes_queue.py").read_text(encoding="utf-8")
    assert '"smaller"' in routes_queue_py
    assert "ltx_fit_canvas" in routes_queue_py


def test_sys07_m1_m2_32gb_long_clip_gets_a_real_refusal_not_silence():
    """SYS-07 (priority item): on 32 GB M1/M2 Macs, a long T2V/I2V clip must
    either refuse with a real suggestion or default lighter -- not render
    silently into a watchdog kill. This project chose an explicit refusal
    over a silent downgrade (the app's own "no silent downgrades" rule),
    checked against model_frames (post Long-Clip-Boost/windows) so a clip
    already chunked down small isn't refused for a workload it no longer
    runs in one GPU command buffer."""
    import sys as _sys
    from pathlib import Path as _Path
    from unittest import mock as _mock

    _root = _Path(__file__).resolve().parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    import mlx_ltx_panel as P

    with _mock.patch.object(P, "_HW_CHIP_FAMILY", "M1 Max"), \
         _mock.patch.object(P, "SYSTEM_RAM_GB", 32.0):
        assert P.m1_m2_long_clip_watchdog_refusal("t2v", "balanced", 241) is not None
        assert "watchdog" in P.m1_m2_long_clip_watchdog_refusal("t2v", "balanced", 241).lower()
        # Short clips on the same Mac are unaffected.
        assert P.m1_m2_long_clip_watchdog_refusal("t2v", "balanced", 121) is None
        # HQ two-stage lane is a different denoise shape -- not refused.
        assert P.m1_m2_long_clip_watchdog_refusal("t2v", "high", 241) is None
    # A fast, unaffected chip is never refused, same RAM.
    with _mock.patch.object(P, "_HW_CHIP_FAMILY", "M4 Max"), \
         _mock.patch.object(P, "SYSTEM_RAM_GB", 32.0):
        assert P.m1_m2_long_clip_watchdog_refusal("t2v", "balanced", 241) is None
    # The same chip at a RAM size the reports weren't about is unaffected.
    with _mock.patch.object(P, "_HW_CHIP_FAMILY", "M1 Max"), \
         _mock.patch.object(P, "SYSTEM_RAM_GB", 64.0):
        assert P.m1_m2_long_clip_watchdog_refusal("t2v", "balanced", 241) is None
    # Wired into the real T2V/I2V dispatch path, after windows/Long Clip
    # Boost have had a chance to shrink model_frames.
    assert "m1_m2_long_clip_watchdog_refusal(mode, quality, model_frames)" in PANEL_PY


def test_sys11_base_pack_gates_generate_before_queueing():
    """SYS-11: a half-installed BASE pack left Generate enabled -- the job
    queued and failed instantly with a Retry button that could never work.
    Must gate client-side, before anything is queued, like the existing
    q8-lane gate does."""
    gate_region = QUEUE_JS[QUEUE_JS.index("function applyPackIncompleteGate"):]
    assert "if (!s.base_available)" in gate_region.split("let cell")[0]
    assert "genBtn.disabled = true" in gate_region.split("let cell")[0]


def test_sys11_incomplete_model_failure_offers_models_not_a_dead_retry():
    """SYS-11: a plain Retry on an incomplete-model failure re-queues the
    identical job, which fails again the same way. Must swap Retry for an
    action that actually fixes it."""
    assert "action: 'models'" in QUEUE_JS
    assert "action === 'models'" in QUEUE_JS
    assert "openModelsModal()" in QUEUE_JS


def test_sys11_banner_copy_names_only_what_is_actually_missing():
    """SYS-11: 'Base models needed' always said BOTH base and encoder were
    required even when only one pack had a file missing (Gemma 4 present,
    still named as required). Must filter by what's actually in
    base_missing."""
    assert "const packNeeded = (p) =>" in SETTINGS_JS
    assert "needBase" in SETTINGS_JS and "needEnc" in SETTINGS_JS
    assert '"local_dir": r.get("local_dir")' in PANEL_PY


def test_sys32_breaker_reason_clears_on_resume():
    """Functional check: post_queue_resume must clear paused_reason so a
    stale breaker notice doesn't survive past the fix + Resume click."""
    import sys as _sys
    from pathlib import Path as _Path
    from unittest import mock as _mock

    _root = _Path(__file__).resolve().parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    import mlx_ltx_panel as P
    from panel import routes_queue

    routes_queue.P = P

    class _H:
        def _json(self, payload, status=200):
            self.payload, self.status = payload, status

    fake_state = {"paused": True, "paused_reason": "3 renders failed: boom", "queue": []}
    with _mock.patch.object(P, "STATE", fake_state), \
         _mock.patch.object(P, "persist_queue", lambda: None):
        routes_queue.post_queue_resume(_H(), "/queue/resume", {}, "")
        assert fake_state["paused"] is False
        assert fake_state["paused_reason"] is None


# ---------------------------------------------------------------------------
# Follow-up round (coordinator: "the owner said 'ship everything'")
# ---------------------------------------------------------------------------

def test_sys13_images_auto_engine_names_the_default_and_its_footprint():
    """SYS-13: the Images tab's 'Auto' engine option just said "(use
    Settings)" -- it never named which engine Auto resolves to or what it
    costs on a Compact Mac. Must name the default (FLUX.2 klein) and its
    first-download size."""
    idx = HTML.index('<option value="auto">Auto')
    tag = HTML[idx:HTML.index("</option>", idx)]
    assert "FLUX.2" in tag
    assert "fits every Mac" in tag
    assert "GB" in tag


def test_sys14_images_header_leads_with_outcome_not_jargon():
    """SYS-14: the Images tab header was 'Reference image(s) — for
    image-to-image (Reference Edit)' -- jargon before a user has decided
    to use a reference at all. Must lead in plain words."""
    assert "<h2>Make or change a picture" in HTML


def test_sys14_missing_reference_error_offers_the_text_only_escape_hatch():
    """SYS-14: submitting an engine that composes against a photo with zero
    references said only 'Pick at least 1 reference image' -- no path
    forward for someone who wants to type a prompt instead. Must name the
    text-only engine and how to switch to it."""
    assert "composes against a photo" in STAGE_JS
    assert "switch Engine" in STAGE_JS
    assert "Ideogram 4" in STAGE_JS


def test_sys13_low_memory_heads_up_reads_free_ram_against_the_engine_need():
    """SYS-13: on a <=24 GB Mac, an image engine that technically 'fits' by
    the hard gate could still starve the render if too much RAM is already
    used. A soft heads-up, not a hard block, using ram_need_gb from
    get_image_engine_status() against LAST_STATUS.memory."""
    assert "engInfo.ram_need_gb" in STAGE_JS
    assert "LAST_STATUS.memory" in STAGE_JS
    assert "softNotice = `Heads up:" in STAGE_JS


def test_sys18_character_q8_nudge_names_the_floor_instead_of_a_dead_link():
    """SYS-18 (second half): q8_character_install_copy() offered an
    'Install Q8 (...) ->' link on every Mac, including ones the panel's own
    SYSTEM_CAPS['allows_q8'] gate already refuses Q8 on -- run_job_inner
    would refuse the render regardless, so the link only wasted a 30+ GB
    download. Below the RAM floor it must explain why and drop the CTA,
    using the same gate the Pinokio-side q8Capable() menu fix and the High
    quality chip (SYSTEM_CAPS['allows_q8']) already use."""
    fn = PANEL_PY[PANEL_PY.index("def q8_character_install_copy("):
                  PANEL_PY.index("def character_render_quality(")]
    assert 'if not SYSTEM_CAPS.get("allows_q8"):' in fn
    assert "min_ram_gb_for(\"allows_q8\")" in fn
    assert "doesn't have enough memory for it" in fn
    # The gated branch must not offer the same dead CTA the ungated one does.
    gated_branch = fn[fn.index('if not SYSTEM_CAPS.get("allows_q8"):'):
                       fn.index("return (\n        f\"<b>Trained characters need")]
    assert "openModelsModal" not in gated_branch


def test_sys25_mode_bar_gets_tab_semantics_via_observer_not_new_call_sites():
    """SYS-25 (second bar): the mode-bar pill group (#modeGroup: T2V / I2V /
    FFLF / Extend) had 0 role=tab / aria-selected, distinct from the
    workflow tabs (#workflowTabs) SYS-25's first pass already covered. Must
    not require editing the ~6 existing .active toggle call sites spread
    across boot.js/characters.js/engines.js/settings.js -- a MutationObserver
    keeps aria-selected synced to whichever site changes .active."""
    idx = HTML.index('id="modeGroup"')
    tag = HTML[HTML.rindex("<div", 0, idx):HTML.index(">", idx) + 1]
    assert 'role="tablist"' in tag
    assert "function _wireModeGroupTabSemantics()" in MAIN_JS
    assert "new MutationObserver" in MAIN_JS
    assert "setAttribute('role', 'tab')" in MAIN_JS
    assert "_wireModeGroupTabSemantics();" in MAIN_JS[MAIN_JS.index("// ====== Init ======"):]


def test_sys28_settings_leads_with_models_not_codec_presets():
    """SYS-28: Settings used to open on Output format's codec presets ("it
    opens on codec presets with pix_fmt=yuv420p * crf=18 lines"). Models
    (added for SYS-08) must be the first section, with the disk-facing
    Storage/Model-files rows grouped right after it rather than split apart
    by Output format and Memory/speed in between."""
    modal = HTML[HTML.index('id="settingsModal"'):HTML.index('id="tierModal"')]
    for marker in ("<h3>Models</h3>", "<h3>Storage</h3>", "<h3>Model files</h3>",
                   "<h3>Memory / speed</h3>", "<h3>Output format</h3>",
                   "<h3>API tokens</h3>"):
        assert marker in modal, f"missing {marker}"
    order = [modal.index(m) for m in (
        "<h3>Models</h3>", "<h3>Storage</h3>", "<h3>Model files</h3>",
        "<h3>Memory / speed</h3>", "<h3>Output format</h3>", "<h3>API tokens</h3>")]
    assert order == sorted(order), "Models/Storage/Model files must lead, in that order"


def test_sys28_hf_token_copy_does_not_contradict_the_models_modal():
    """SYS-28: the HF-token hint claimed 'Control LoRAs' need a token, while
    CURATED_LORAS['union-control'] (mlx_ltx_panel.py) is documented as
    Lightricks' one official UN-GATED IC-LoRA, downloaded token-less at
    install time. The two surfaces must not disagree."""
    hint = HTML[HTML.index('id="hfTestResult"'):HTML.index("</div>", HTML.index('id="hfTestResult"'))]
    assert "Motion Track Control" in hint
    assert "un-gated" in hint
    assert 'CURATED_LORAS["union-control"]' in PANEL_PY
    assert "Un-gated official " in PANEL_PY
    assert "weight — no HF token needed." in PANEL_PY


def test_sys28_live_preview_note_explains_a_missing_decoder_in_settings_too():
    """SYS-28: the Live-preview select read 'On' whenever the saved
    preference was on, even with the 22 MB TAE decoder missing from disk --
    preview.js already explains this case on the main screen
    (preview_state.reason === 'missing_decoder'); Settings had no
    equivalent."""
    assert 'id="settingsLivePreviewNote"' in HTML
    assert "ps.reason === 'missing_decoder'" in SETTINGS_JS
    assert "needs a 22 MB add-on" in SETTINGS_JS


def test_sys24_font_size_floor_is_11px_everywhere_in_panel_css():
    """SYS-24: panel.css had 105 literal font-size declarations under 11px
    (36 of 120 visible text nodes were under 11px, 18 under 10px). Every
    literal sub-11px font-size must be gone, and the --fs-2xs token (10px)
    that fed many of them raised to the same floor."""
    assert not re.search(r"font-size:\s*(?:[6-9](?:\.\d+)?|10(?:\.\d+)?)px", CSS)
    assert "--fs-2xs: 11px;" in CSS


def test_sys42_models_modal_row_hides_repo_path_behind_details():
    """SYS-42: a Models-modal row's always-visible line was
    'dgrauet/ltx-2.3-mlx-q4 -> mlx_models/ltx-2.3-mlx-q4' -- a repo id and a
    filesystem path ahead of the one thing a non-engineer can act on. The
    outcome line (name/status/blurb) must lead; repo_id/local_dir move
    behind a closed <details>."""
    fn = PREVIEW_JS[PREVIEW_JS.index("async function refreshModelsModal"):
                     PREVIEW_JS.index("async function startDownload")]
    row_tpl = fn[fn.index("return `\n      <li"):fn.index("}).join('');")]
    assert '<details class="model-row-details">' in row_tpl
    # The outcome line (statusText/blurb) must appear before the details
    # disclosure that carries repo_id -> local_dir.
    assert row_tpl.index("${statusText}") < row_tpl.index('class="model-row-details"')
    assert ".model-row-details" in CSS
