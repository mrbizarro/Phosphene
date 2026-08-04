"""Security helpers shared by the panel and its subprocess backends.

Keep this module stdlib-only and free of panel imports.  It is intentionally
small enough to unit-test without importing the 35k-line HTTP application.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Inline CSS and JavaScript are part of the single-file panel today. The only
# external active resource is the exact Chart.js 4.4.1 path used by the
# opt-in stats dashboard; the tag also carries a SHA-384 integrity value.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "base-uri 'none'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "frame-src 'none'; "
        "form-action 'self'; "
        "script-src 'self' 'unsafe-inline' "
        "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' data: blob: https:; "
        "font-src 'self' data:; "
        "connect-src 'self'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "serial=(), bluetooth=()"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _split_authority(value: str) -> tuple[str, int | None] | None:
    """Parse an HTTP authority without accepting userinfo or paths."""
    value = (value or "").strip()
    if not value:
        return "", None
    if any(ch.isspace() for ch in value) or any(ch in value for ch in "/?#"):
        return None
    try:
        parsed = urlsplit("//" + value)
        if parsed.username is not None or parsed.password is not None:
            return None
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    return host, port


def is_trusted_loopback_request(
    host_header: str,
    origin_header: str,
    server_port: int,
) -> bool:
    """Validate Host and Origin for the loopback-only HTTP panel.

    Empty Host remains supported for HTTP/1.0/local tooling.  Browser origins,
    when present, must be the *same* host and port as the request rather than
    merely another loopback service.  That closes cross-port localhost CSRF
    while retaining curl and other non-browser API clients.
    """
    authority = _split_authority(host_header)
    if authority is None:
        return False
    host, host_port = authority
    if host not in LOOPBACK_HOSTS | {""}:
        return False
    if host_port is not None and host_port != server_port:
        return False

    origin = (origin_header or "").strip()
    if not origin:
        return True
    if origin.lower() == "null":
        return False
    try:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
        origin_host = (parsed.hostname or "").lower()
        origin_port = parsed.port or 80
    except ValueError:
        return False
    if origin_host not in LOOPBACK_HOSTS or origin_port != server_port:
        return False
    return not host or origin_host == host


_SAFE_ENV_NAMES = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "USER",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "XDG_CACHE_HOME",
        "PYTHONUTF8",
        "PYTHONUNBUFFERED",
        "TOKENIZERS_PARALLELISM",
        # Non-secret Phosphene feature/runtime overrides consumed by child
        # helpers. Do not allow the whole PHOSPHENE_* namespace because it
        # also contains the dedicated repo-statistics credential.
        "PHOSPHENE_FFMPEG_DIR",
        "PHOSPHENE_HIDREAM_TIMEOUT_S",
        "PHOSPHENE_T2V_TWO_STAGE",
    }
)

_SAFE_ENV_PREFIXES = (
    "LTX_",
    "MLX_",
    "METAL_",
    "MFLUX_",
    "HF_HUB_",
    "PYTORCH_MPS_",
    "OMP_",
    "VECLIB_",
)

_SECRET_ENV_SUFFIXES = ("_TOKEN", "_KEY", "_SECRET", "_PASSWORD")


def sanitized_subprocess_env(
    source: Mapping[str, str] | None = None,
    *,
    extra: Mapping[str, str] | None = None,
    allow_secrets: tuple[str, ...] = (),
) -> dict[str, str]:
    """Build an environment without ambient developer/cloud credentials.

    AI packages and optional engine repositories are a large supply-chain
    boundary.  They receive only operating/runtime configuration by default;
    callers must name and inject each credential required for their task.
    """
    src = os.environ if source is None else source
    allowed_secret_names = set(allow_secrets)
    env = {
        key: value
        for key, value in src.items()
        if (
            key in allowed_secret_names
            or (
                not key.upper().endswith(_SECRET_ENV_SUFFIXES)
                and (
                    key in _SAFE_ENV_NAMES
                    or any(key.startswith(prefix) for prefix in _SAFE_ENV_PREFIXES)
                )
            )
        )
    }
    if extra:
        env.update({key: str(value) for key, value in extra.items()})
    return env


def validated_bfl_base_url(value: str) -> str:
    """Return the canonical credential-bearing BFL API URL or raise."""
    try:
        parsed = urlsplit((value or "").strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("bfl_base_url is malformed") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "api.bfl.ml"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        raise ValueError("bfl_base_url must be https://api.bfl.ml/v1")
    return "https://api.bfl.ml/v1"
