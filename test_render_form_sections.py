#!/usr/bin/env python3
"""The render form is three named, closed-by-default sections.

Owner, 2026-09-18: "The H3 layout is kind of a bit too much. … you have a pill
for speed, quality, length, and all that. That should be inside one section that
you can compress and decompress, like we have LoRAs and Customize, because it is
very messy like that. I don't think it is easy to navigate between the options …
it should be closed by default. … Think about what you can separate and what
should be grouped together."

What this pins:
  1  the form keeps FOUR things in the open — engine (header), mode (bar), the
     composer (reference + prompt), Generate (footer) — and everything else
     lives in a disclosure that ships CLOSED: Shot setup, After the render,
     Advanced, LoRAs. No `open` attribute on any of them.
  2  WHICH control is in WHICH section, by id. A control that drifts back out
     to the top level, or into the wrong section, fails here.
  3  the order Speed → Quality inside Shot setup (test_h3_tristep_draft pins the
     same thing from the other side, because Fast overrules Steps).
  4  nothing was dropped or renamed: every form field name the old layout posted
     is still in the markup, and the H3 fields are still read by the panel (a
     field that isn't in make_job's allowlist silently no-ops on /queue/add).
  5  the cell's honest notes (#ltxTierNote / #h3TierNote) stay OUTSIDE the
     disclosures — a warning about the render you are about to queue must not
     be foldable.
  6  the three summary lines exist and are written from one entry point, so a
     closed section still says what it is hiding.
  7  the markup is balanced. The old form carried a stray `</div>` that closed
     .quick-settings early, which is why Orientation and Seed were siblings of
     the form rather than children of the block that styled them.
"""
from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
CJS = (ROOT / "webapp" / "js" / "characters.js").read_text(encoding="utf-8")
LJS = (ROOT / "webapp" / "js" / "loras.js").read_text(encoding="utf-8")
QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
PANEL = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")

FORM = HTML[HTML.index('<form id="genForm">'):HTML.index("</form>")]


def block(details_id: str) -> str:
    """The inner HTML of <details id=...>. None of the form's sections nest a
    <details>, so the first close is the right one."""
    start = HTML.index('<details id="%s"' % details_id)
    return HTML[start:HTML.index("</details>", start)]


SHOT = block("shotSetupDetails")
FINISH = block("finishDetails")
ADVANCED = block("customizeDetails")


class SectionsAreClosedByDefault(unittest.TestCase):
    def test_the_four_disclosures_exist_with_their_labels(self):
        for did, title in (
            ("shotSetupDetails", "Shot setup"),
            ("finishDetails", "After the render"),
            ("customizeDetails", "Advanced"),
        ):
            self.assertIn('<details id="%s"' % did, HTML)
            self.assertIn('<span class="cz-title">%s</span>' % title, HTML)
        self.assertIn('<details id="lorasDetails" class="loras-section">', HTML)

    def test_none_of_them_ships_open(self):
        for did in ("shotSetupDetails", "finishDetails", "customizeDetails",
                    "lorasDetails"):
            tag = re.search(r"<details id=\"%s\"[^>]*>" % did, HTML)
            self.assertIsNotNone(tag, did)
            self.assertNotIn(" open", tag.group(0), did + " must ship closed")

    def test_each_section_carries_a_summary_line(self):
        for meta in ("shotSetupSummary", "finishSummary", "customizeSummary"):
            self.assertIn('class="cz-meta" id="%s"' % meta, HTML)

    def test_the_word_customize_is_gone_from_the_form(self):
        # The grab-bag was split; the id survives for every caller, the label
        # does not.
        self.assertNotIn('<span class="cz-title">Customize</span>', HTML)


class WhatIsInEachSection(unittest.TestCase):
    def test_shot_setup_holds_the_shape(self):
        for cid in (
            'id="h3SpeedRow"',          # H3 Fast | Best
            'id="schedPresetRow"',      # LTX Tuned | Fast draft
            'id="qualityLabelName"',    # Quality label (both engines)
            'id="qualityGroup"',        # LTX canvas
            'id="ltxAxes"',             # LTX length
            'id="qualityGroupCharacter"',
            'id="h3TierGroup"',         # H3 canvas + length
            'id="h3PrimaryControls"',
            'id="h3StepsRow"',          # H3 steps
            'id="h3LoraSlotRow"',       # the one adapter slot
            'id="aspectRow"',           # LTX orientation
            'id="h3OrientationRow"',    # H3 orientation
            'id="quickMetricsRow"',     # seed
        ):
            self.assertIn(cid, SHOT, cid + " left Shot setup")

    def test_speed_still_comes_before_quality(self):
        self.assertLess(SHOT.index('id="h3SpeedRow"'),
                        SHOT.index('id="qualityLabelName"'))

    def test_after_the_render_holds_what_happens_to_the_file(self):
        for cid in ('id="h3ExportRow"', 'id="h3UpscaleGroup"',
                    'id="h3FaceFixAfter"', 'id="upscaleGroup"',
                    'id="upscaleMethodRow"', 'id="open_when_done"'):
            self.assertIn(cid, FINISH, cid + " left After the render")

    def test_advanced_holds_the_overrides(self):
        for cid in ('id="dimsRow"', 'id="durationRow"', 'id="accelRow"',
                    'id="stgRow"', 'id="temporalRow"', 'id="windowsRow"',
                    'id="i2vAudioModeSection"'):
            self.assertIn(cid, ADVANCED, cid + " left Advanced")

    def test_the_sections_do_not_overlap(self):
        for cid in ('id="dimsRow"', 'id="upscaleGroup"', 'id="open_when_done"'):
            self.assertNotIn(cid, SHOT)
        for cid in ('id="h3StepsRow"', 'id="quickMetricsRow"', 'id="dimsRow"'):
            self.assertNotIn(cid, FINISH)
        for cid in ('id="h3StepsRow"', 'id="h3ExportRow"', 'id="upscaleGroup"'):
            self.assertNotIn(cid, ADVANCED)

    def test_the_shot_list_sits_with_the_prompt_not_inside_a_section(self):
        # Per-window prompts is prompt CONTENT (one line per 5 s window), and a
        # user who has just chosen a 15 s length has to be able to see it.
        self.assertNotIn('id="h3WindowPromptsRow"', SHOT)
        self.assertLess(HTML.index('id="h3WindowPromptsRow"'),
                        HTML.index('<details id="shotSetupDetails"'))
        self.assertLess(HTML.index('id="prompt" class="composer-prompt"'),
                        HTML.index('id="h3WindowPromptsRow"'))

    def test_the_honest_notes_are_not_foldable(self):
        for cid in ('id="ltxTierNote"', 'id="h3TierNote"', 'id="engineRowNote"'):
            self.assertIn(cid, HTML)
            for sec in (SHOT, FINISH, ADVANCED):
                self.assertNotIn(cid, sec, cid + " must stay visible")


class NothingWasDroppedOrRenamed(unittest.TestCase):
    # Every field the form posted before the layout pass. The list is the point:
    # a moved row that loses its hidden input posts nothing and fails silently.
    FIELDS = (
        "preset_label", "mode", "quality", "quality_choice", "ltx_length",
        "accel", "temporal_mode", "upscale", "schedule_preset", "prompt",
        "negative_prompt", "no_music", "no_voice", "hdr", "engine",
        "h3_tier", "h3_quality", "h3_length", "h3_upscale", "h3_steps",
        "h3_turbo", "h3_tristep", "h3_lora_slot", "h3_chain_prompts",
        "h3_orientation", "aspect", "seed", "loras", "steps", "width",
        "height", "frames", "stg_scale", "window_prompts",
        "window_invariants", "upscale_method", "audio", "open_when_done",
        "image", "i2v_reference_mode", "start_image", "end_image",
        "keyframe_count", "character_id", "character_strength",
        "character_voice_strength", "stop_comfy",
    )

    def test_every_field_is_still_in_the_form(self):
        names = set(re.findall(r'name="([a-z0-9_]+)"', FORM))
        for f in self.FIELDS:
            self.assertIn(f, names, f + " is no longer posted by #genForm")

    def test_the_h3_fields_are_still_read_by_the_panel(self):
        # A form field that isn't in make_job's allowlist silently no-ops on
        # /queue/add — the known trap. This is the cheap standing guard.
        for f in ("h3_tier", "h3_quality", "h3_length", "h3_upscale",
                  "h3_steps", "h3_turbo", "h3_tristep", "h3_lora_slot",
                  "h3_chain_prompts", "h3_orientation"):
            self.assertIn('"%s"' % f, PANEL, f + " is not read server-side")

    def test_the_engine_fold_hooks_survived_the_move(self):
        # The per-engine fold rules key off these attributes, not off position.
        self.assertGreater(SHOT.count("data-h3-only"), 3)
        self.assertGreater(SHOT.count("data-ltx-only"), 2)
        self.assertIn("data-h3-only", FINISH)
        self.assertIn("data-ltx-only", ADVANCED)


class TheSummariesAreWrittenFromOnePlace(unittest.TestCase):
    def test_the_three_writers_exist(self):
        for fn in ("function updateShotSetupSummary",
                   "function updateFinishSummary",
                   "function updateAdvancedSummary"):
            self.assertIn(fn, CJS)

    def test_the_old_entry_point_fans_out(self):
        # 23 call sites across five modules call updateCustomizeSummary after
        # every state change; it keeps its name and updates all three.
        body = CJS[CJS.index("function updateCustomizeSummary"):]
        body = body[:body.index("\nfunction updateShotSetupSummary")]
        for fn in ("updateShotSetupSummary()", "updateFinishSummary()",
                   "updateAdvancedSummary()"):
            self.assertIn(fn, body)

    def test_the_writers_are_exported(self):
        self.assertIn("updateShotSetupSummary, updateFinishSummary, "
                      "updateAdvancedSummary,", CJS)

    def test_a_closed_shot_setup_still_names_the_shape(self):
        body = CJS[CJS.index("function updateShotSetupSummary"):
                   CJS.index("function updateFinishSummary")]
        for needle in ("quality_label", "length_label", "h3TriStepOn",
                       "h3CellEta", "ltxCellEta", "seed "):
            self.assertIn(needle, body)

    def test_an_import_opens_the_closed_lora_picker(self):
        self.assertIn("_det.open = true", LJS)

    def test_every_control_in_a_closed_section_refreshes_its_summary(self):
        # A closed section's line is the only thing the user sees, so each
        # setter that changes what it says has to rewrite it. setH3Upscale and
        # setH3Orientation did not (the export target was stale in the old
        # Customize summary too), and #seed had no listener at all.
        ejs = (ROOT / "webapp" / "js" / "engines.js").read_text()
        for fn in ("function setH3Upscale", "function setH3Orientation",
                   "function setH3Steps"):
            body = ejs[ejs.index(fn):]
            body = body[:body.index("\n}\n") + 3]
            self.assertIn("updateCustomizeSummary", body, fn + " leaves it stale")
        qjs = (ROOT / "webapp" / "js" / "queue.js").read_text()
        self.assertIn("document.getElementById('seed')?.addEventListener('input'",
                      qjs)


class TheChromeIsStyled(unittest.TestCase):
    def test_the_primary_section_is_marked_in_the_engine_colour(self):
        self.assertIn('class="customize-section shot-setup"', HTML)
        self.assertIn(".customize-section.shot-setup", CSS)
        self.assertIn("--eng-accent", CSS.split(".customize-section.shot-setup")[1][:400])

    def test_the_moved_rows_got_their_margins_rehomed(self):
        for rule in (".cz-body .h3-speed", ".cz-body .eng-primary",
                     ".cz-body > #quickMetricsRow",
                     ".cz-body > #aspectRow.mode-only.show",
                     ".quick-settings > .engine-hint"):
            self.assertIn(rule, CSS)

    def test_the_chip_rows_answer_to_the_panes_width_not_the_windows(self):
        # auto-fit tracks, so a dragged-in pane wraps the chips instead of
        # squeezing them. Identical to the fixed grids at full width.
        for rule in (".cz-body .quality-strip { grid-template-columns: "
                     "repeat(auto-fit, minmax(90px, 1fr)); }",
                     ".cz-body .pill-group.cols-4 { grid-template-columns: "
                     "repeat(auto-fit, minmax(62px, 1fr)); }",
                     ".quick-settings .qs-label { flex-wrap: wrap;"):
            self.assertIn(rule, CSS)
        self.assertIn("repeat(auto-fit, minmax(90px, 1fr))",
                      (ROOT / "webapp" / "js" / "engines.js").read_text())

    def test_the_shot_list_block_got_its_own_chrome(self):
        self.assertIn("#genForm > .h3-winprompts {", CSS)
        self.assertIn(".cz-label .toggle-pill {", CSS)


class _Balance(HTMLParser):
    WATCH = ("div", "details", "form", "summary")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.bad: list[tuple] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.WATCH:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag not in self.WATCH:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                if i != len(self.stack) - 1:
                    self.bad.append(("crossed", tag, self.getpos()[0],
                                     self.stack[-1]))
                del self.stack[i:]
                return
        self.bad.append(("no open", tag, self.getpos()[0]))


class TheMarkupIsBalanced(unittest.TestCase):
    def test_no_stray_or_crossed_div(self):
        p = _Balance()
        p.feed(HTML)
        self.assertEqual(p.bad, [], "unbalanced tags in webapp/index.html")
        self.assertEqual(p.stack, [], "unclosed tags in webapp/index.html")

    def test_orientation_and_seed_are_inside_the_quick_settings_block(self):
        # They used to be siblings of the form: the stray </div> closed
        # .quick-settings before them, so .quick-settings' own label styles
        # never applied. Both now sit inside Shot setup, inside that block.
        qs = HTML.index('<div class="quick-settings">')
        self.assertLess(qs, HTML.index('<details id="shotSetupDetails"'))
        self.assertIn('id="aspectRow"', SHOT)
        self.assertIn('id="quickMetricsRow"', SHOT)


class BatchIsASection(unittest.TestCase):
    """Owner, 2026-09-18: "batch is kind of hidden, and it should probably be
    re-taught in a way that is more user-friendly and also more highlighted. It
    should be a section, actually, after the render and setup."

    It was a 60-px pill in the queue strip opening a modal with one textarea and
    the line "split prompts with --- on its own line".
    """

    def test_it_is_a_closed_section_after_after_the_render(self):
        self.assertIn('<details id="batchDetails"', HTML)
        self.assertNotIn('<details id="batchDetails" open', HTML)
        self.assertLess(HTML.index('<details id="finishDetails"'),
                        HTML.index('<details id="batchDetails"'))
        self.assertLess(HTML.index('<details id="batchDetails"'),
                        HTML.index('<details id="customizeDetails"'))

    def test_the_modal_is_gone_and_the_pill_opens_the_section(self):
        self.assertNotIn("batchModal", HTML)
        self.assertNotIn("batchModal", QJS)
        self.assertIn("getElementById('batchDetails')", QJS)

    def test_both_modes_exist_and_seeds_mode_randomises(self):
        batch = block("batchDetails")
        self.assertIn('data-batch-mode="prompts"', batch)
        self.assertIn('data-batch-mode="seeds"', batch)
        # N takes of one prompt must not come back as N identical clips.
        self.assertIn("fd.set('seed', '-1')", QJS)
        # One function builds the list the count shows AND the jobs it posts.
        self.assertIn("function batchPrompts()", QJS)

    def test_the_section_says_how_many_jobs_before_you_commit(self):
        self.assertIn('id="batchSummary"', HTML)
        self.assertIn('id="batchCount"', HTML)
        self.assertIn('id="batchTotal"', HTML)
        self.assertIn("function updateBatchSummary", QJS)
        # ...and it is refreshed from the same entry point as the others.
        self.assertIn("updateBatchSummary()", CJS)


class TheShipReviewFindings(unittest.TestCase):
    """Codex ship review of 92e3033..bca2aa0, 2026-09-18 — the four findings
    that live in this file's surfaces. Each one is a real user path, so each
    one gets a line here that fails if it comes back."""

    def test_takes_mode_refuses_a_prompt_that_contains_the_separator(self):
        # /queue/batch splits `prompts` on a line of three dashes. Repeating a
        # prompt that CONTAINS such a line turned 3 advertised takes into 6 jobs
        # holding fragments. The list must come back empty instead.
        self.assertIn("BATCH_SEP_RX", QJS)
        i = QJS.index("function batchPrompts()")
        body = QJS[i:i + 400]
        self.assertIn("BATCH_SEP_RX.test(main)", body)
        # ...and the refusal has to be said out loud, in both places a user looks.
        self.assertIn("line of three dashes, which Batch uses", QJS)
        self.assertIn("Batch uses that to", QJS)

    def test_the_batch_row_listens_to_the_prompt_it_repeats(self):
        # Queue stayed disabled while the user typed the very prompt it wanted.
        i = QJS.index("// \"Same prompt, N takes\" reads #prompt")
        body = QJS[i:i + 420]
        self.assertIn("getElementById('prompt')", body)
        self.assertIn("addEventListener('input'", body)
        self.assertIn("updateBatchSummary", body)

    def test_the_batch_estimate_helpers_are_published(self):
        # Private, they threw on first call and the catch swallowed it, so an
        # H3 batch showed no total at all (lint_webapp no-undef).
        EJS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")
        self.assertIn("h3CellEtaMin, h3FaceFixOn,", EJS)



    """Owner, 2026-09-18, on After the render: "What is this? It's not good.
    Normalize the design. The pills are okay. The rest is pretty okay, but it's
    weird: the other stuff that is in there doesn't look related to the design
    at all."

    The cause was in the CSS, not the markup: the segmented trough was only ever
    applied to `.pill-group.cols-2` and `.cols-4`, so the three-option Export
    row had no container at all while the two-option METHOD row under it did.
    """

    def test_every_pill_group_count_gets_the_same_trough(self):
        i = CSS.index(".pill-group.cols-2,")
        rule = CSS[i:CSS.index("}", i)]
        for cols in ("cols-2", "cols-3", "cols-4", "cols-5"):
            self.assertIn(".pill-group." + cols, rule)
        self.assertIn("background: rgba(140, 160, 220, 0.04)", rule)

    def test_a_checkbox_row_wears_the_sections_chrome(self):
        i = CSS.index("#genForm .cz-body .check {")
        rule = CSS[i:CSS.index("}", i)]
        self.assertIn("background: rgba(140, 160, 220, 0.04)", rule)
        self.assertIn("border: 1px solid var(--ph-border-soft)", rule)

    def test_the_export_note_sits_under_the_pills_it_explains(self):
        finish = block("finishDetails")
        self.assertLess(finish.index('id="h3ExportNote"'),
                        finish.index('id="h3FaceFixAfterRow"'))


class OneControlScale(unittest.TestCase):
    """Owner, 2026-09-18, with a screenshot of the LoRAs header: "all the
    buttons are different. What the fuck? You need to really clean up the
    visuals so it looks professional in general."

    Measured in that row before the fix: the rescan icon 28 px, Browse CivitAI
    43 px (an inline-styled <svg> inflated its line box), Check for updates
    31 px; the footer chips 27 px at 11 px with a pill radius; the batch button
    35 px at 12.5 px. This pins the scale, not the look: heights and radii come
    from one place, and no button carries its geometry inline.
    """

    SCALED = ("#genForm .loras-summary .loras-icon-btn",
              "#genForm .loras-summary .loras-browse-btn",
              "#genForm .batch-queue-btn",
              ".form-action-footer .queue-strip .qchip")

    def test_the_small_action_buttons_share_one_rule(self):
        i = CSS.index("#genForm .loras-summary .loras-icon-btn,")
        rule = CSS[i:CSS.index("}", i)]
        for sel in self.SCALED:
            self.assertIn(sel, rule)
        self.assertIn("height: 30px", rule)
        self.assertIn("border-radius: 6px", rule)
        self.assertIn("font-size: 12px", rule)

    def test_no_button_carries_its_geometry_inline(self):
        # `style="margin-right:6px;vertical-align:-2px"` on nine icons was what
        # made one button 12 px taller than its neighbour.
        self.assertNotIn("vertical-align:-2px", HTML)

    def test_one_filled_primary_per_header_row(self):
        i = CSS.index("#genForm .loras-summary .loras-browse-btn:not(.is-ghost),")
        rule = CSS[i:CSS.index("}", i)]
        self.assertIn("background: var(--accent)", rule)
        # ...and the others are the hairline
        j = CSS.index("#genForm .loras-summary .loras-browse-btn.is-ghost,")
        ghost = CSS[j:CSS.index("}", j)]
        self.assertIn("background: transparent", ghost)

    def test_generate_and_stop_are_one_pair(self):
        i = CSS.index(".form-action-footer .actions .primary,")
        self.assertIn("border-radius: 6px", CSS[i:CSS.index("}", i)])


class AClosedSectionCostsOneRow(unittest.TestCase):
    """A fold that is closed must cost a row, and an empty fold must not exist.

    Measured on the live panel after the first pass: the closed LoRAs summary
    still stood 252 px tall, because every button inside the render form is
    full-width by default and the header's three buttons each took a line of
    their own; and on H3 the Advanced row opened onto NOTHING, since every
    control in it is an LTX override that H3 ignores.
    """

    def test_the_loras_header_buttons_keep_their_own_width(self):
        self.assertIn(".loras-summary .loras-browse-btn { width: auto; flex: 0 0 auto; }", CSS)

    def test_advanced_is_hidden_on_h3_not_labelled_empty(self):
        i = CJS.index("function updateAdvancedSummary")
        body = CJS[i:i + 1400]
        self.assertIn("customizeDetails", body)
        self.assertIn("adv.hidden = true", body)
        self.assertIn("adv.hidden = false", body)
        # The inputs stay in the DOM: hiding the container must not change what
        # the form posts (verified live: 59 FormData keys on both engines).
        self.assertNotIn("disabled = true", body)


if __name__ == "__main__":
    unittest.main()
