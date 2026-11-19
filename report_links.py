"""Where "report" and "issues" links in the Studio point - ONE switch.

The H3 and MiniMax-H3-Image licences require a working way to report
suspected misuse (MiniMax H3 Community License s. V.5), and the bug button,
the crash hints and the licence screens all send people somewhere to report.
Until 16 Nov 2026 every one of them opened a GitHub issue page on
mrbizarro/Phosphene; that account has been hidden since then and every such
link answers 404.

TARGET picks the place for all of them at once:

  "github" - the GitHub issue form (flip back to this when the GitHub
             repo is reachable again);
  "gitlab" - the GitLab copy's issue form (gitlab.com/phosphene1/phosphene).
             Its Issues feature was OFF on 17 Nov 2026 (the page answered
             404 to a visitor), so it only works once the owner turns Issues
             on for that project;
  "mail"   - an email to support@phosphene.video (works today, needs no
             account anywhere).

Stdlib only; the panel, model_licences and the routes import it.
"""
from __future__ import annotations

from urllib.parse import quote

# THE switch. One word; everything below follows it.
TARGET = "mail"

GITHUB_REPO = "https://github.com/mrbizarro/Phosphene"
GITLAB_REPO = "https://gitlab.com/phosphene1/phosphene"
SUPPORT_EMAIL = "support@phosphene.video"

# A mailto: link with a long body is cut or refused by some mail apps
# (about 2,000 characters is the safe common size); the issue forms take
# far more (the bug route already trims its body to ~6 KB for GitHub).
MAIL_BODY_MAX = 1800


def target() -> str:
    return TARGET if TARGET in ("github", "gitlab", "mail") else "mail"


def issue_url(title: str = "", body: str = "", labels: str = "") -> str:
    """A pre-filled new report at the current target."""
    t = target()
    if t == "github":
        url = f"{GITHUB_REPO}/issues/new?title={quote(title)}"
        if body:
            url += f"&body={quote(body)}"
        if labels:
            url += f"&labels={quote(labels)}"
        return url
    if t == "gitlab":
        url = f"{GITLAB_REPO}/-/issues/new?issue%5Btitle%5D={quote(title)}"
        if body:
            url += f"&issue%5Bdescription%5D={quote(body)}"
        return url
    if len(body) > MAIL_BODY_MAX:
        body = body[:MAIL_BODY_MAX] + "\n\n...(shortened for email)"
    url = f"mailto:{SUPPORT_EMAIL}?subject={quote(title)}"
    if body:
        url += f"&body={quote(body)}"
    return url


def misuse_url() -> str:
    """The misuse-report link the H3 licences require (s. V.5)."""
    return issue_url("Misuse report: ")

