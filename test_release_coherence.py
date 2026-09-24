#!/usr/bin/env python3
"""Coherence gates on the repo's own documentation and agent-rules files.

These are not tests of the panel. They are tests of the four things that have
each shipped wrong at least once, are invisible to every other gate, and are
cheap to assert:

1. The README's "Current release:" banner drifted to v4.1.1 while VERSION said
   4.8.1 — the first line a new user reads, naming a release from seven
   releases ago.
2. `.clinerules` / `.cursorrules` / `.windsurfrules` were 96 KB *copies* of
   CLAUDE.md frozen at 2026-05-18, silently feeding every non-Claude agent a
   three-month-old manual. `AGENTS.md` / `GEMINI.md` / `QWEN.md` were already
   symlinks; these are now too, and this test keeps them that way.
3. CLAUDE.md's pin row claimed the installed packages report
   `0.14.19+ltx25.4` while `_LTX_EXPECTED_VERSION` and the checkout are both
   `+ltx25.6`. That exact claim is what a reader would copy into
   `_LTX_EXPECTED_VERSION`, producing a VERSION SKEW on every render. The
   assertion is deliberately narrow: `ltx25.4` is real history and is
   discussed at length in that same row, so only the stale *"packages report"*
   phrasing is forbidden.
4. `docs/RELEASE_CHECKLIST.md` ended with `git push origin dev:main`, which is
   not the promote ritual and would publish the whole dev history to public
   `main` in one irreversible command. The real ritual is a curated snapshot
   commit; that command must never reappear in the file.

Run: `ltx-2-mlx/env/bin/python -m unittest test_release_coherence -v`
"""

import os
import re
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))

# The three agent-rules files converted from stale copies to symlinks, plus the
# three that were already symlinks. All six must point at CLAUDE.md.
RULES_FILES = (
    ".clinerules",
    ".cursorrules",
    ".windsurfrules",
    "AGENTS.md",
    "GEMINI.md",
    "QWEN.md",
)


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


class ReadmeVersionBanner(unittest.TestCase):
    """The README banner names the release the VERSION file says we are."""

    def test_readme_current_release_matches_version_file(self):
        version = _read("VERSION").strip()
        self.assertTrue(version, "VERSION file is empty")

        readme = _read("README.md")
        m = re.search(r"Current release:\s*v?([0-9][0-9A-Za-z.\-+]*?)\.?\*", readme)
        self.assertIsNotNone(
            m,
            "README.md has no 'Current release: vX.Y.Z' banner — if the banner "
            "was intentionally restructured, update this test with it.",
        )
        self.assertEqual(
            m.group(1),
            version,
            "README says 'Current release: v{}' but VERSION says {}. The banner "
            "is the first line a new user reads; bump it with the release."
            .format(m.group(1), version),
        )


class ReadmeManualInstall(unittest.TestCase):
    """The README's Manual install runs on a fresh machine (Codex INST-04).

    It created the venv with `uv venv --seed` and then called `./env/bin/uv`,
    which `--seed` never installs (pip, setuptools, wheel only) — so every
    package step failed and the reader was left with a venv holding a Python
    and nothing else. It also named a stale engine tag and skipped the
    transformers cap and the dependency pass install.js runs.
    """

    def _section(self):
        readme = _read("README.md")
        start = readme.index("### Manual install")
        end = readme.index("\n## ", start) if "\n## " in readme[start:] else len(readme)
        return readme[start:end]

    def test_no_venv_local_uv_or_pip(self):
        sec = self._section()
        self.assertNotRegex(sec, r"(?m)^\s*\S*env/bin/uv\b",
                            "Manual install calls env/bin/uv — `uv venv --seed` does not put uv in the venv")
        self.assertNotRegex(sec, r"(?m)^\s*\S*env/bin/pip\b",
                            "Manual install mixes plain pip into a uv-managed install")

    def test_pins_match_the_installer(self):
        sec = self._section()
        pin = re.search(r'LTX_PIN="([^"]+)"', _read("scripts/pinokio/ltx_checkout.sh")).group(1)
        self.assertIn(f"git checkout {pin}", sec)
        self.assertIn("'transformers>=5.0.0,<5.13.0'", sec)
        # The dependency pass (no --no-deps) must precede the --reinstall pass.
        dep = re.search(r"uv pip install --python env/bin/python \\\n\s*--build-constraints", sec)
        rei = re.search(r"--reinstall --no-deps \\\n\s*--build-constraints", sec)
        self.assertTrue(dep and rei and dep.start() < rei.start(),
                        "vendored packages need a dependency pass, then the --reinstall --no-deps pass")


class AgentRulesAreSymlinks(unittest.TestCase):
    """No agent reads a frozen copy of the manual."""

    def test_all_rules_files_are_symlinks_to_claude_md(self):
        for name in RULES_FILES:
            path = os.path.join(REPO, name)
            with self.subTest(rules_file=name):
                self.assertTrue(
                    os.path.lexists(path), "{} is missing".format(name)
                )
                self.assertTrue(
                    os.path.islink(path),
                    "{} is a regular file, not a symlink — a copy of CLAUDE.md "
                    "goes stale the moment CLAUDE.md changes. Fix with: "
                    "rm {} && ln -s CLAUDE.md {}".format(name, name, name),
                )
                self.assertEqual(
                    os.readlink(path),
                    "CLAUDE.md",
                    "{} points at {!r}, not CLAUDE.md".format(
                        name, os.readlink(path)
                    ),
                )

    def test_rules_files_resolve_to_the_real_manual(self):
        claude = _read("CLAUDE.md")
        for name in RULES_FILES:
            with self.subTest(rules_file=name):
                self.assertEqual(
                    _read(name),
                    claude,
                    "{} does not read back as CLAUDE.md".format(name),
                )


class ClaudeMdPinClaim(unittest.TestCase):
    """The pin row must not tell a reader the wrong reported version."""

    # Narrow on purpose: `ltx25.4` is a real, discussed part of the pin's
    # history in that same table row. Only the stale *claim about what the
    # installed packages report* is a regression.
    STALE_CLAIM = "report `0.14.19+ltx25.4`"

    def test_no_stale_packages_report_claim(self):
        claude = _read("CLAUDE.md")
        self.assertNotIn(
            self.STALE_CLAIM,
            claude,
            "CLAUDE.md claims the installed packages report "
            "`0.14.19+ltx25.4`. `_LTX_EXPECTED_VERSION` in mlx_warm_helper.py "
            "and the pin in scripts/pinokio/ltx_checkout.sh are the truth — "
            "state what they state, or every render logs VERSION SKEW.",
        )

    def test_reported_version_agrees_with_expected_version_constant(self):
        helper = _read("mlx_warm_helper.py")
        m = re.search(
            r'^_LTX_EXPECTED_VERSION\s*=\s*["\']([^"\']+)["\']',
            helper,
            re.MULTILINE,
        )
        self.assertIsNotNone(
            m, "could not find _LTX_EXPECTED_VERSION in mlx_warm_helper.py"
        )
        expected = m.group(1)
        claude = _read("CLAUDE.md")
        self.assertIn(
            "report **`{}`**".format(expected),
            claude,
            "CLAUDE.md does not state that the packages report {!r}, which is "
            "what _LTX_EXPECTED_VERSION says. Move the two together."
            .format(expected),
        )


class ReleaseChecklistPromoteRitual(unittest.TestCase):
    """The checklist must never hand anyone the history-leaking branch push."""

    FORBIDDEN = "git push origin dev:main"

    def test_checklist_does_not_instruct_a_branch_push_to_main(self):
        checklist = _read("docs/RELEASE_CHECKLIST.md")
        self.assertNotIn(
            self.FORBIDDEN,
            checklist,
            "docs/RELEASE_CHECKLIST.md contains {!r}. Public main is a chain "
            "of curated single-parent snapshot commits; that command would "
            "publish the entire dev history irreversibly. Describe the "
            "read-tree / commit-tree / leak-verify / push-one-commit ritual "
            "instead.".format(self.FORBIDDEN),
        )

    def test_checklist_documents_the_snapshot_ritual(self):
        checklist = _read("docs/RELEASE_CHECKLIST.md")
        for token in ("read-tree", "commit-tree"):
            self.assertIn(
                token,
                checklist,
                "docs/RELEASE_CHECKLIST.md no longer describes the snapshot "
                "promote (missing {!r}). Removing the dangerous command is "
                "only half the fix — the real ritual has to be written down."
                .format(token),
            )


if __name__ == "__main__":
    unittest.main()
