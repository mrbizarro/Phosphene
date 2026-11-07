"""The ONE place a link from Phosphene to phosphene.video gets its UTM tags.

Why: every visit, download and sale on the website should say where it came
from, without knowing who the person is. A link from the Studio carries

    utm_source=app
    utm_medium=mac-app | pinokio     (from distribution.py: where this runs)
    utm_campaign=<purpose>           (one of CAMPAIGNS)
    utm_content=<placement>          (optional: settings, banner, pro-modal ...)

and NOTHING else: no install id, no email, no version, no licence. The tags say
which button was pressed in which kind of install, never by whom.

Code that builds a link calls site_link() here. The browser page has the same
function (siteLink in webapp/js/boot.js), fed from BOOT.site_utm, which comes
from utm_base() below; test_site_links.py checks the two campaign lists match.

GitHub (README, release notes) uses a different source and medium
(utm_source=github, utm_medium=readme|release); github_link() builds those for
the tests and for anyone writing a template. Machine-to-machine URLs (licence
API, update feed, relay, reports, bench schema id) are NEVER tagged: they are
not links a person follows.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import distribution

SITE = "https://phosphene.video"
SITE_HOST = "phosphene.video"

SOURCE_APP = "app"
SOURCE_GITHUB = "github"
MEDIUM_MAC_APP = distribution.MAC_APP    # "mac-app"
MEDIUM_PINOKIO = distribution.PINOKIO    # "pinokio"
MEDIUM_README = "readme"
MEDIUM_RELEASE = "release"

#: The purposes the owner listed, then the few short specific ones the Studio
#: needs. Lower-case words and hyphens only. A new link either fits one of
#: these or adds a new short name HERE and in webapp/js/boot.js (the test
#: fails until both lists agree).
CAMPAIGNS = (
    # the agreed list
    "get-pro", "upgrade", "pricing", "docs", "download", "support",
    "whats-new", "remote", "changelog", "star",
    # specific short names
    "home", "website", "benchmarks", "acceptable-use", "privacy",
    # GitHub only (readme / release templates)
    "install", "release-body",
)

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content")


def medium(env=None) -> str:
    """'mac-app' inside the Mac app, 'pinokio' everywhere else."""
    return MEDIUM_MAC_APP if distribution.is_mac_app(env) else MEDIUM_PINOKIO


def utm_base(env=None) -> dict:
    """What the page needs to tag its own links (served as BOOT.site_utm)."""
    return {"source": SOURCE_APP, "medium": medium(env)}


def _check(name: str, what: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(f"{what} must be lower-case words joined by hyphens: {name!r}")
    return name


def tag_url(url: str, source: str, medium_: str, campaign: str, content: str | None = None) -> str:
    """Add the four UTM tags to `url`, MERGING with any query it already has:
    an existing utm_* tag is replaced (never doubled), every other parameter
    and the #fragment are kept. Only phosphene.video links may be tagged."""
    _check(source, "utm_source")
    _check(medium_, "utm_medium")
    _check(campaign, "utm_campaign")
    if campaign not in CAMPAIGNS:
        raise ValueError(f"unknown utm_campaign {campaign!r}; add it to site_links.CAMPAIGNS")
    if content is not None:
        _check(content, "utm_content")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in (SITE_HOST, "www." + SITE_HOST):
        raise ValueError(f"only https://{SITE_HOST} links are tagged: {url!r}")
    keep = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in UTM_KEYS]
    tags = [("utm_source", source), ("utm_medium", medium_), ("utm_campaign", campaign)]
    if content:
        tags.append(("utm_content", content))
    query = urlencode(keep + tags)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", query, parts.fragment))


def site_link(path: str, campaign: str, content: str | None = None, env=None) -> str:
    """A tagged link from the Studio to the website.
    site_link("/pricing/", "get-pro", "pro-modal") ->
    https://phosphene.video/pricing/?utm_source=app&utm_medium=pinokio&utm_campaign=get-pro&utm_content=pro-modal
    `path` may carry its own query and #fragment; both are kept."""
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")
    return tag_url(SITE + path, SOURCE_APP, medium(env), campaign, content)


def github_link(path: str, medium_: str, campaign: str, content: str | None = None) -> str:
    """A tagged link from the README (medium 'readme') or a GitHub release
    body (medium 'release') to the website."""
    if medium_ not in (MEDIUM_README, MEDIUM_RELEASE):
        raise ValueError(f"GitHub links use utm_medium readme or release, not {medium_!r}")
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")
    return tag_url(SITE + path, SOURCE_GITHUB, medium_, campaign, content)
