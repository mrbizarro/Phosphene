"""CivitAI client: search, host allowlist, download with progress and cancel.

Moved verbatim out of mlx_ltx_panel.py (the body of each function is unchanged
except that panel globals are now spelled `P.<name>`, the same mechanical
rename the panel/routes_*.py modules use). `P` is the RUNNING panel module,
assigned by mlx_ltx_panel at wiring time; it is never imported here because the
panel usually runs as __main__ and importing it by name would execute the whole
file a second time. Every cross-reference to panel state and to this module's
own functions goes through `P.<name>`, so a test that patches
`P.ROOT` or `P._civitai_opener` still reaches the code that uses it.

The names below stay importable from mlx_ltx_panel (it re-exports them), so
panel/routes_loras.py and the tests are unchanged.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

P = None  # the running mlx_ltx_panel module; assigned at wiring time


CIVITAI_API_BASE = "https://civitai.com/api/v1"


CIVITAI_USER_AGENT = "phosphene/1.0 (https://github.com/mrbizarro/phosphene)"


# 2026-05-31 security fix (deep-review). The CivitAI download path takes a
# `download_url` from model metadata (attacker-influenceable) and sends the
# user's API token in BOTH the URL (`?token=`) and an `Authorization: Bearer`
# header. Two holes were closed:
#   1. The old host gate was `netloc.endswith("civitai.com")` — which also
#      matches `evilcivitai.com`. Now an EXACT host allowlist.
#   2. Python's default HTTPRedirectHandler re-attaches the Authorization
#      header across redirects to ANY host, so a crafted/compromised download
#      that 30x-redirects off-domain would exfiltrate the token. The handler
#      below strips Authorization on any cross-host redirect.
def _civitai_host_allowed(netloc: str) -> bool:
    """True only for civitai.com itself or a real *.civitai.com subdomain."""
    host = (netloc or "").split("@")[-1].split(":")[0].strip().lower()
    return host == "civitai.com" or host.endswith(".civitai.com")


def _civitai_opener():
    """A urllib opener that strips the Authorization header on any cross-host
    redirect, so the CivitAI token never travels to a host the user didn't
    authenticate to. Built lazily so urllib.request is imported before the
    handler subclass is defined (no module-load import-order coupling)."""
    import urllib.request
    import urllib.parse

    class _AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
            if new_req is None:
                return None
            try:
                # Keep Authorization only while staying inside the civitai.com
                # domain (civitai.com -> images.civitai.com is legit). Strip it
                # the moment a redirect leaves the domain — e.g. to a pre-signed
                # CDN (which needs no auth anyway) or to an attacker's host.
                new_netloc = urllib.parse.urlparse(newurl).netloc
                if not P._civitai_host_allowed(new_netloc):
                    hdrs = {k: v for k, v in new_req.header_items()
                            if k.lower() != "authorization"}
                    new_req = urllib.request.Request(
                        new_req.full_url, headers=hdrs, method=new_req.get_method())
            except Exception:
                # Reasoning about the redirect failed — fail closed.
                try:
                    new_req.remove_header("Authorization")
                except Exception:
                    pass
            return new_req

    return urllib.request.build_opener(_AuthStrippingRedirectHandler())


def _civitai_request(path: str, params: dict | None = None,
                     timeout: float = 20.0) -> dict:
    """Minimal stdlib HTTP client for the CivitAI API. No third-party
    deps so we don't pin requests / httpx into the install. Returns the
    decoded JSON body or raises RuntimeError on non-2xx."""
    import urllib.parse, urllib.request
    url = f"{CIVITAI_API_BASE}{path}"
    if params:
        # urlencode handles the URL-quoting (spaces → %20 etc.) so
        # baseModels=LTXV 2.3 makes it through unmangled.
        url = url + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": CIVITAI_USER_AGENT,
    })
    api_key = P._active_civitai_key()
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    # CivitAI's API throws the occasional 502/503/504 on a perfectly good
    # request (owner hit "HTTP error 503" mid-search). Three attempts with a
    # short back-off turn that into a pause instead of a dead search, and
    # the message a user finally sees says what to do, not a status code.
    import urllib.error
    last: Exception | None = None
    for attempt in range(3):
        try:
            with P._civitai_opener().open(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (500, 502, 503, 504, 429) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if exc.code in (500, 502, 503, 504):
                raise RuntimeError("CivitAI is busy right now (it answered "
                                   f"{exc.code}). Try again in a few seconds.") from exc
            if exc.code == 429:
                raise RuntimeError("CivitAI is rate-limiting this Mac — wait a minute "
                                   "and search again.") from exc
            if exc.code in (401, 403):
                raise RuntimeError("CivitAI refused the request (401/403) — check the "
                                   "CivitAI API key in Settings.") from exc
            raise RuntimeError(f"CivitAI answered HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError("Could not reach civitai.com — check the connection "
                               "and try again.") from exc
        except Exception as exc:                               # noqa: BLE001
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
    raise RuntimeError(f"{type(last).__name__}: {last}")


# Sub-families for the image context. Each entry maps a UI family-pill id
# to the list of CivitAI baseModel strings it covers. The "all" pill is
# the union of every list below — built dynamically so adding a new
# family stays a one-line edit. Probed against the live CivitAI API
# 2026-05-20; CivitAI's taxonomy uses different names than what their
# own model-card UI shows.
#
# Flux LoRAs are intentionally excluded — Phosphene's image engines are
# Qwen-Image-Edit-2511 (via mflux) and HiDream-O1-Image-Dev. There's no
# Flux engine exposed today, so surfacing Flux LoRAs in the browser
# would just create install candidates that no engine can run.
_CIVITAI_IMAGE_FAMILIES: dict[str, list[str]] = {
    "qwen":    ["Qwen 2", "Qwen"],   # Qwen-Image-Edit-2511 + Qwen-Image (2509)
    "hidream": ["HiDream-O1"],       # HiDream-O1-Image-Dev
}


# Sub-families for the VIDEO context — one per video ENGINE. Same mechanism
# the image context uses, and for a stronger reason: LTX and H3 don't merely
# prefer different LoRAs, they cannot load each other's at all (different
# module tree, different runner, different loader). The family pill is
# therefore not a convenience filter here, it is the lane selector, and
# openCivitaiModal() preselects it from the ACTIVE engine so an LTX user's
# default view is byte-for-byte what it was before H3 LoRAs existed.
#
# Both strings probed against the live CivitAI API rather than guessed:
#   "LTXV 2.3"    verified 2026-05-20 — "LTX 2.3", "LTX-Video 2.3" and
#                 "LTXV2.3" all return zero.
#   "MiniMax H3"  verified 2026-08-09 — "MiniMax", "Hailuo" and "Hailuo H3"
#                 all return zero; "MiniMax H3" is the only spelling with hits.
_CIVITAI_VIDEO_FAMILIES: dict[str, list[str]] = {
    "ltx": ["LTXV 2.3"],
    "h3":  ["MiniMax H3"],
}


# family-map per context, so the `family` query param means the same thing on
# both surfaces and adding a family stays a one-line edit in ONE of the two
# dicts above.
_CIVITAI_FAMILIES_BY_CONTEXT: dict[str, dict[str, list[str]]] = {
    "video": _CIVITAI_VIDEO_FAMILIES,
    "image": _CIVITAI_IMAGE_FAMILIES,
}


_CIVITAI_BASE_MODELS_BY_CONTEXT = {
    # Video LoRAs — the default. Union of every video family; the `family`
    # query param on /civitai/search narrows it to one engine's lane.
    "video": [bm for bms in _CIVITAI_VIDEO_FAMILIES.values() for bm in bms],
    # Image LoRAs — union of every family in _CIVITAI_IMAGE_FAMILIES.
    # Sub-filtering happens via the `family` query param on /civitai/search.
    "image": [bm for bms in _CIVITAI_IMAGE_FAMILIES.values() for bm in bms],
}


def _civitai_search(query: str = "", nsfw: bool = False,
                    cursor: str = "", limit: int = 20,
                    context: str = "video", family: str = "") -> dict:
    """Search LoRAs. Returns the trimmed shape:
        { "items": [{
            "id", "name", "creator", "description", "tags", "downloads",
            "rating", "nsfw", "preview_url", "version_id",
            "download_url", "trigger_words", "recommended_strength",
            "size_kb", "base_model"
          }, ...],
          "total_pages", "current_page" }
    Each item carries everything the front end needs to render a card
    AND everything the download endpoint needs to write a sidecar — no
    second round-trip required to install."""
    # Spicy mode gate (v2.0 — server-side authoritative). When the user
    # hasn't enabled Spicy mode in Settings, force nsfw=False regardless
    # of what the client sent. This keeps NSFW results out of casual /
    # kid-accessible installs even if someone fiddles the client param.
    spicy_on = P.spicy_mode_enabled()
    if not spicy_on:
        nsfw = False
    # CivitAI's /models endpoint uses cursor-style pagination
    # (`nextCursor` in the response → pass back as `cursor` on the next
    # request). Page numbers are deprecated for this endpoint. We expose
    # the same cursor flow to the panel UI so it can render a "Load more"
    # affordance without faking pagination on top of cursors.
    # Context-aware baseModels (2026-05-18). The Images workflow needs to
    # see Flux / Qwen / HiDream LoRAs, NOT LTX-2.3 video LoRAs. Unknown
    # contexts silently fall back to the video default so any caller that
    # forgets to set context still gets sensible results.
    base_models_filter = _CIVITAI_BASE_MODELS_BY_CONTEXT.get(
        context, _CIVITAI_BASE_MODELS_BY_CONTEXT["video"]
    )
    # Sub-family narrowing. The browser exposes pills per context — "LTX-2.3"
    # / "Hailuo H3" on video, "Qwen-Image" / "HiDream" on image — so users can
    # scope the result set to the engine they're about to use. family=""
    # (or missing, or "all") leaves the full context list active.
    fam = (family or "").strip().lower()
    _fam_map = _CIVITAI_FAMILIES_BY_CONTEXT.get(context) or {}
    if fam and fam in _fam_map:
        base_models_filter = _fam_map[fam]
    # The CivitAI API accepts multi-value baseModels as repeated query
    # params (`baseModels=X&baseModels=Y`), not comma-separated. Passing
    # a list here triggers `urlencode(..., doseq=True)` in _civitai_request
    # to emit the repeated form.
    params: dict[str, object] = {
        "types": "LORA",
        "baseModels": base_models_filter,
        "limit": limit,
        "sort": "Most Downloaded",
        "nsfw": "true" if nsfw else "false",
    }
    q = query.strip()
    if q:
        # CivitAI's text search (`query`) is a different backend from the
        # browse listing and it does NOT honour `baseModels`: the two
        # together return an empty page for every word ("turbo" → 0 while
        # "Minimax H3 Turbo Loras" sits on page one of the un-queried list).
        # So a typed search drops the server-side base filter, nudges the
        # search with the engine's own word (the backend ranks "minimax
        # turbo" all-H3), and the base-model filter is applied HERE on the
        # results, paging until the page is full or the well runs dry.
        _hint_words = {"h3": "minimax", "ltx": "ltx", "qwen": "qwen",
                       "hidream": "hidream"}
        # One engine → one hinted search. "All" → one hinted search per
        # engine in the context, merged (a bare "anime" search fills its
        # pages with SDXL LoRAs and the base filter leaves nothing).
        _hints = ([_hint_words.get(fam, "")] if fam
                  else [_hint_words.get(k, "") for k in (_fam_map or {}).keys()] or [""])
        params.pop("baseModels", None)
        params["limit"] = 50
        _want = {bm.lower() for bm in base_models_filter}
        raw_items: list[dict] = []
        _seen: set = set()
        raw = {}
        for _hint in _hints:
            _p = dict(params)
            _p["query"] = (f"{_hint} {q}" if _hint and _hint not in q.lower() else q)
            if cursor:
                _p["cursor"] = cursor
            for _page in range(4):
                raw = P._civitai_request("/models", params=_p)
                for m in (raw.get("items") or []):
                    _bm = str(((m.get("modelVersions") or [{}])[0]).get("baseModel") or "").lower()
                    if _bm in _want and m.get("id") not in _seen:
                        _seen.add(m.get("id"))
                        raw_items.append(m)
                _next = (raw.get("metadata") or {}).get("nextCursor")
                if len(raw_items) >= limit or not _next:
                    break
                _p["cursor"] = _next
            if len(raw_items) >= limit:
                break
        raw = {**raw, "items": raw_items[:limit]}
    else:
        if cursor:
            params["cursor"] = cursor
        raw = P._civitai_request("/models", params=params)
    items = []
    for m in (raw.get("items") or []):
        versions = m.get("modelVersions") or []
        if not versions:
            continue
        v = versions[0]
        files = v.get("files") or []
        # Pick the first .safetensors file. CivitAI sometimes hosts
        # multiple file variants (pruned, full, etc.); prefer the one
        # marked `primary: true` if present, otherwise fall back to the
        # first .safetensors.
        primary = next((f for f in files if f.get("primary") and
                        f.get("name", "").lower().endswith(".safetensors")),
                       None) or next((f for f in files if
                       f.get("name", "").lower().endswith(".safetensors")),
                       None)
        if not primary:
            continue
        images = v.get("images") or []
        # Prefer the first image-type preview if any exist (some LTX LoRAs
        # ship both video MP4s AND still images). Fall back to the first
        # entry of any type. We pass the type back to the client so the
        # renderer can pick <img> vs <video> appropriately — for LTX
        # video LoRAs the previews are usually MP4s, which would just
        # 404 inside an <img> tag.
        preview_obj = (
            next((img for img in images if img.get("type") == "image" and img.get("url")), None)
            or next((img for img in images if img.get("url")), None)
        )
        preview = preview_obj.get("url") if preview_obj else None
        preview_type = preview_obj.get("type") if preview_obj else None
        creator = (m.get("creator") or {}).get("username") or "unknown"
        # CivitAI puts trigger words on the version (`trainedWords`).
        trigger = list(v.get("trainedWords") or [])
        items.append({
            "id": m.get("id"),
            "version_id": v.get("id"),
            "name": m.get("name") or f"model-{m.get('id')}",
            "creator": creator,
            "description": (m.get("description") or "")[:600],
            "tags": list(m.get("tags") or []),
            "downloads": (m.get("stats") or {}).get("downloadCount"),
            "rating": (m.get("stats") or {}).get("rating"),
            "nsfw": bool(m.get("nsfw")),
            "preview_url": preview,
            "preview_type": preview_type,        # "image" | "video" | None
            "filename": primary.get("name"),
            "size_kb": primary.get("sizeKB"),
            "download_url": primary.get("downloadUrl"),
            "trigger_words": trigger,
            "recommended_strength": 1.0,    # CivitAI doesn't expose this; default
            "base_model": v.get("baseModel"),
            "civitai_url": f"https://civitai.com/models/{m.get('id')}",
        })
    # Defense-in-depth: when Spicy mode is off, also drop any items the
    # API returned with nsfw=true. CivitAI's `nsfw=false` request param
    # filters by image-tier (Soft/Mature/X) but model cards have a
    # separate boolean we re-check here so nothing slips through.
    if not spicy_on:
        items = [it for it in items if not it.get("nsfw")]
    metadata = raw.get("metadata") or {}
    return {
        "items": items,
        "next_cursor": metadata.get("nextCursor"),
        "has_more": bool(metadata.get("nextCursor")),
    }


# ---- CivitAI download progress + cancel (4.18.0) ------------------------------
# The browser's Install button used to say "Downloading…" for the whole of a
# 300 MB-2 GB download with no number and no way out. The download still runs
# inside its own POST (the server is threaded); the browser names it with a
# random token, polls GET /civitai/download/state for bytes, and can POST
# /civitai/download/cancel. A cancel stops the stream at the next chunk and
# the existing .partial cleanup removes what had arrived — nothing half-
# downloaded is ever registered as a LoRA.
CIVITAI_DL: dict[str, dict] = {}


CIVITAI_DL_LOCK = threading.Lock()


_CIVITAI_DL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class CivitaiDownloadCancelled(RuntimeError):
    """The person pressed Cancel; the partial file is already gone."""


def civitai_dl_token(raw) -> str:
    t = str(raw or "").strip()
    return t if _CIVITAI_DL_TOKEN_RE.match(t) else ""


def civitai_dl_begin(token: str, name: str) -> None:
    if not token:
        return
    with CIVITAI_DL_LOCK:
        # A crashed tab never polls its entry away; keep the table small.
        if len(CIVITAI_DL) > 32:
            for k in list(CIVITAI_DL)[:-16]:
                CIVITAI_DL.pop(k, None)
        CIVITAI_DL[token] = {"state": "running", "bytes": 0, "total": 0,
                             "name": str(name or "")[:120], "cancel": False}


def civitai_dl_state(token: str) -> dict | None:
    with CIVITAI_DL_LOCK:
        e = CIVITAI_DL.get(token)
        return dict(e) if e else None


def civitai_dl_cancel(token: str) -> bool:
    """True when the download will stop. False once it has passed the commit
    point (every byte arrived, the file is being put in place) — a Cancel
    then would answer "cancelled" for a LoRA that still installs."""
    with CIVITAI_DL_LOCK:
        e = CIVITAI_DL.get(token)
        if not e or e.get("state") != "running":
            return False
        e["cancel"] = True
        return True


def _civitai_dl_commit(token: str) -> None:
    """The commit point: honour a Cancel that arrived after the last chunk,
    else close the door on any later one (state -> "installing")."""
    if not token:
        return
    with CIVITAI_DL_LOCK:
        e = CIVITAI_DL.get(token)
        if not e:
            return
        if e.get("cancel"):
            e["state"] = "cancelled"
            raise P.CivitaiDownloadCancelled("Download cancelled — nothing was kept.")
        e["state"] = "installing"


def civitai_dl_end(token: str) -> None:
    with CIVITAI_DL_LOCK:
        CIVITAI_DL.pop(token, None)


def _civitai_dl_tick(token: str, written: int, total: int) -> None:
    """Record progress; raise when the person asked to stop."""
    if not token:
        return
    with CIVITAI_DL_LOCK:
        e = CIVITAI_DL.get(token)
        if not e:
            return
        e["bytes"], e["total"] = int(written), int(total or 0)
        if e.get("cancel"):
            e["state"] = "cancelled"
            raise P.CivitaiDownloadCancelled("Download cancelled — nothing was kept.")


def _civitai_download(download_url: str, meta: dict, token: str = "") -> dict:
    """Download a CivitAI .safetensors into the right LoRA dir for its base
    model and write a sidecar JSON. Returns
    { name, path, sidecar_path, size_bytes, lane }.
    Streams to a .partial file then renames atomically — a Pinokio kill
    mid-download leaves nothing the next scan would mistake for a
    complete LoRA.

    Lane routing (2026-08-09) is decided from the ITEM's own `base_model`,
    not from which browser context the click came from. A MiniMax H3 LoRA
    lands in the H3 weights tree even if the user found it while the browser
    was showing "All", because the file's family is a property of the file,
    and mis-filing it would put an unloadable adapter in the LTX picker."""
    import urllib.parse, urllib.request
    if not download_url:
        raise RuntimeError("download_url required")
    # Restrict to civitai.com to prevent the endpoint being used as a
    # generic HTTP fetcher. Subdomains (e.g. images.civitai.com) are
    # also OK because CivitAI sometimes redirects there.
    parsed = urllib.parse.urlparse(download_url)
    if parsed.scheme != "https" or not P._civitai_host_allowed(parsed.netloc):
        raise RuntimeError(f"refusing to download from {parsed.netloc}; "
                           f"only civitai.com (and *.civitai.com) is allowed")
    # CivitAI's download endpoint started requiring auth for many models
    # in 2025+. The Authorization header sometimes works, but the
    # canonical path is `?token=<key>` on the URL — and many CDN hops
    # only honour the token in the URL. Try the URL form first when a
    # key is available.
    api_key_env = P._active_civitai_key()
    if api_key_env and "token=" not in (parsed.query or ""):
        sep = "&" if parsed.query else "?"
        download_url = download_url + sep + "token=" + urllib.parse.quote(api_key_env)
    # Which engine's library does this belong to? _classify_lora_modes is the
    # single source for that mapping — the picker filter, the sidecar and this
    # router all read the same function, so a new family is one edit there.
    lane = ("h3" if "video:h3" in P._classify_lora_modes(meta.get("base_model"))
            else "ltx")
    loras_dir = P._safe_h3_loras_dir() if lane == "h3" else P._safe_loras_dir()
    # Filename from meta (preferred — preserves CivitAI's name) or fall
    # back to the URL's last path segment.
    fname = (meta.get("filename") or
             os.path.basename(parsed.path) or
             f"civitai_{meta.get('id', 'unknown')}.safetensors")
    if not fname.lower().endswith(".safetensors"):
        fname += ".safetensors"
    # Sanitize aggressively — CivitAI names can contain spaces and
    # weirder characters; the helper later passes this filename through
    # the safetensors loader which is filesystem-only and fine with
    # spaces, but the picker UI is calmer with snake_case.
    safe_fname = re.sub(r"[^A-Za-z0-9._-]+", "_", fname).strip("_")
    target = loras_dir / safe_fname
    if target.exists():
        # Don't silently re-download. Surface the conflict so the user
        # can decide (the UI will offer a choice; for now just skip).
        return {
            "name": meta.get("name") or target.stem,
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "lane": lane,
            "skipped": True,
            "reason": "already exists",
        }
    tmp = target.with_suffix(target.suffix + ".partial")
    P.push(f"[civitai] downloading {meta.get('name') or safe_fname}")
    api_key = P._active_civitai_key()
    headers = {"User-Agent": CIVITAI_USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(download_url, headers=headers)
    bytes_written = 0
    last_log = 0.0
    _opener = P._civitai_opener()  # strips Authorization on cross-host redirect
    try:
        try:
            resp_ctx = _opener.open(req, timeout=60)
        except urllib.request.HTTPError as he:
            # Surface the 401 case with a clear remediation. CivitAI now
            # requires API tokens for most LoRA downloads, even SFW ones,
            # but the API key for the search endpoint is optional — so a
            # user can browse without setting up auth and only hit this
            # wall on first install.
            if he.code == 401:
                raise RuntimeError(
                    "CivitAI returned 401 Unauthorized. CivitAI requires an "
                    "API token for LoRA downloads. Get one at "
                    "https://civitai.com/user/account, then set the "
                    "CIVITAI_API_KEY environment variable before launching "
                    "the panel (or in Pinokio's Phosphene start script). "
                    "Restart Pinokio's Phosphene to pick it up."
                )
            raise
        with resp_ctx as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            P._civitai_dl_tick(token, 0, total)
            with tmp.open("wb") as fh:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
                    bytes_written += len(chunk)
                    P._civitai_dl_tick(token, bytes_written, total)
                    now = time.time()
                    if now - last_log > 1.0:
                        if total:
                            pct = int(100 * bytes_written / total)
                            P.push(f"[civitai] {pct}% · "
                                 f"{bytes_written // (1024*1024)}/"
                                 f"{total // (1024*1024)} MB")
                        else:
                            P.push(f"[civitai] {bytes_written // (1024*1024)} MB")
                        last_log = now
        P._civitai_dl_commit(token)
        os.replace(tmp, target)
    except P.CivitaiDownloadCancelled:
        try: tmp.unlink()
        except OSError: pass
        P.push(f"[civitai] cancelled {meta.get('name') or safe_fname} — partial file removed")
        raise
    except Exception:
        try: tmp.unlink()
        except OSError: pass
        raise
    P.push(f"[civitai] saved {target.name} ({bytes_written // (1024*1024)} MB)")
    # ---- H3 lane: classify the key layout NOW, at install time --------------
    # Doing it here rather than at render time means the ComfyUI rename happens
    # once, in front of the user, with the log open — and a diffusers-namespace
    # LoRA is refused at the moment they clicked Install instead of 30 s into a
    # 20-minute render. The file is REMOVED on refusal: leaving an unloadable
    # adapter in the library would put a permanently-failing entry in the
    # picker, which is worse than the wasted download it cost to find out.
    layout_info = {"layout": None, "converted": False, "prefix": ""}
    if lane == "h3":
        try:
            layout_info = P._h3_lora_prepare(target)
        except Exception as exc:
            try:
                target.unlink()
            except OSError:
                pass
            P.push(f"[civitai] refused {target.name}: {exc}")
            raise RuntimeError(str(exc)) from exc
    strength = (P._h3_lora_browser_strength(target, meta, "civitai") if lane == "h3"
                else float(meta.get("recommended_strength") or 1.0))
    # Write the sidecar — kept tolerant of meta gaps so a partial
    # payload still produces a usable picker entry.
    sidecar = target.with_suffix(".json")
    sidecar_data = {
        "name": meta.get("name") or target.stem,
        "description": meta.get("description") or "",
        "trigger_words": (list(meta.get("trigger_words") or [])
                          or list(layout_info.get("trigger_words") or [])),
        "recommended_strength": strength,
        "preview_url": meta.get("preview_url"),
        "base_model": (meta.get("base_model")
                       or ("MiniMax H3" if lane == "h3" else "LTXV 2.3")),
        "civitai_id": meta.get("id"),
        "civitai_version_id": meta.get("version_id"),
        "civitai_url": meta.get("civitai_url"),
        "downloaded_at": P.iso_now(),
        "downloaded_size_bytes": bytes_written,
        # What we found, and what we did about it. Written for both lanes so
        # the field means the same thing everywhere; None on LTX, where the
        # panel does no layout surgery.
        "lora_layout": layout_info.get("layout"),
        "lora_converted_prefix": (layout_info.get("prefix") or None
                                  if layout_info.get("converted") else None),
    }
    try:
        P.atomic_write_text(sidecar, json.dumps(sidecar_data, indent=2))
    except Exception as exc:
        P.push(f"[civitai] WARN: could not write sidecar ({exc})")
    return {
        "name": sidecar_data["name"],
        "path": str(target),
        "sidecar_path": str(sidecar),
        "size_bytes": bytes_written,
        # "ltx" | "h3" — the client uses this to decide whether it may
        # auto-enable the new LoRA on the currently-selected engine.
        "lane": lane,
        "layout": layout_info.get("layout"),
        "converted": bool(layout_info.get("converted")),
        "skipped": False,
    }
