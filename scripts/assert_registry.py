#!/usr/bin/env python3
"""Hard gate over the model-version registry, run against the REAL panel module.

WHY THIS FILE IS IN THE REPO
============================
It was written in a session scratchpad when the registry gained its first seam
(`ltx25_panel_seam.md` §7 nominated it as "the file to port into the repo first
if the registry grows a second entry"). The registry grew a second entry on
2026-08-12 and the file stayed in the scratchpad, where it rotted: by the time
anyone looked, **28 of its 71 assertions were failing** — every one of them
because `ltx23` had stopped being the default, not because anything was broken.
A gate nobody can read is worse than no gate: a real regression hides in the
noise, and this one did (see DEFECT-1 below, which had been live for a day).

So: expectations updated to current truth, and the file lives here now, where
`git log` shows when an expectation moved and why.

    ./ltx-2-mlx/env/bin/python3.11 scripts/assert_registry.py

Exit 0 = PASS. It touches no GPU, opens no socket, starts no server, and writes
only into an isolated LTX_STATE_DIR.

WHAT IT IS FOR
==============
The registry decides, for every generation, which weights load, which text
encoder conditions them, which surface the UI offers, and which files must be
present before a job is allowed to start. Every one of those has already been
wrong at least once, and NONE of them raised at the time:

  * `base_model_dir()` handed LTX-2.5 the 2.3 pack. Rendered in 44 s and looked
    fine.
  * The registry had no seam for the TEXT ENCODER, so the 2.5 DiT was
    conditioned on Gemma 3. Both towers project to 188160, so every shape
    agreed, nothing raised, and the output was merely wrong.
  * A `download_include` allowlist dropped one 1 GB upscaler, the loader built
    it from random initialisation, and the result was the rainbow "mosaic" —
    two weeks and three wrong theories before anyone read the file list.

None of those is catchable by a test that mocks the registry. They are only
catchable by asking the real module about the real install.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(tempfile.mkdtemp(prefix="phos-assert-registry-"))

# Isolate every piece of mutable state so importing the panel cannot touch a
# real install, and never let this gate phone home.
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8299")
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("panel", ROOT / "mlx_ltx_panel.py")
p = importlib.util.module_from_spec(spec)
sys.modules["panel"] = p
spec.loader.exec_module(p)

OK = FAIL = DEFECT = 0
_failures: list[str] = []


def eq(label, got, want):
    global OK, FAIL
    if got == want:
        OK += 1
    else:
        FAIL += 1
        _failures.append(f"{label}\n    got : {got!r}\n    want: {want!r}")


def raises(label, fn, needle):
    global OK, FAIL
    try:
        fn()
    except RuntimeError as exc:
        if needle in str(exc):
            OK += 1
            return
        FAIL += 1
        _failures.append(f"{label}: raised but the message lacks {needle!r}: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        _failures.append(f"{label}: wrong exception {type(exc).__name__}: {exc}")
        return
    FAIL += 1
    _failures.append(f"{label}: did not raise")


def no_raise(label, fn):
    global OK, FAIL
    try:
        fn()
        OK += 1
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        _failures.append(f"{label}: raised {type(exc).__name__}: {exc}")


def known_defect(tag, label, got, current, should_be):
    """Pin a WRONG behaviour so the gate stays green and the bug stays visible.

    The alternative is to assert the correct value and leave the gate red, at
    which point everybody learns to ignore it — which is exactly how this file
    accumulated 28 failures and stopped being read.

    So the current, wrong value is asserted, a banner is printed every run, and
    **fixing the bug turns this red**, forcing whoever fixes it to come back
    here and delete the marker. A known defect that can be fixed without anyone
    noticing is not being tracked, it is being forgotten.
    """
    global OK, FAIL, DEFECT
    if got == current:
        OK += 1
        DEFECT += 1
        print(f"  DEFECT {tag}: {label}")
        print(f"         is  : {got!r}")
        print(f"         should be: {should_be}")
    else:
        FAIL += 1
        _failures.append(
            f"{tag} moved: {label}\n    got : {got!r}\n    pinned-as-broken: {current!r}\n"
            f"    If you FIXED it — thank you: delete this known_defect() call and assert the\n"
            f"    correct value. If you changed it by accident, this is the warning."
        )


# =============================================================================
# 1. Registry shape — two generations, 2.5 is default AND active
# =============================================================================
eq("registered versions", [v["id"] for v in p.MODEL_VERSIONS], ["ltx23", "ltx25"])
eq("active version", p.ACTIVE_MODEL_VERSION, "ltx25")
eq("default version", p.default_model_version_id(), "ltx25")
eq("2.3 label", p.model_version("ltx23")["label"], "LTX-2.3")
eq("2.5 label", p.model_version("ltx25")["label"], "LTX-2.5")
eq("an unknown version falls back to the default", p.model_version("nope")["id"], "ltx25")
eq("both generations offer q4 + q8", p.version_quants("ltx23"), ("q4", "q8"))
eq("both generations offer q4 + q8 (2.5)", p.version_quants("ltx25"), ("q4", "q8"))
eq("cap tiers 2.3", p.version_cap_tiers("ltx23"), ("q4", "q8"))
eq("cap tiers 2.5", p.version_cap_tiers("ltx25"), ("q4", "q8"))

# The reverse lookup is what lets a repo key (which is what the download lane
# and /status speak) be turned back into a quant.
eq("repo key -> quant, 2.3 q4", p._quant_for_repo_key("q4", "ltx23"), "q4")
eq("repo key -> quant, 2.3 q8", p._quant_for_repo_key("q8", "ltx23"), "q8")
eq("repo key -> quant, 2.5 q4", p._quant_for_repo_key("q4_25", "ltx25"), "q4")
eq("repo key -> quant, 2.5 q8", p._quant_for_repo_key("q8_25", "ltx25"), "q8")
eq("an encoder repo is not a pack", p._quant_for_repo_key("gemma"), None)
eq("None is not a pack", p._quant_for_repo_key(None), None)

# =============================================================================
# 2. Each generation resolves its OWN pack — the §4.1 bug
# =============================================================================
# base_model_dir() handed 2.5 the 2.3 directory because it keyed off
# "am I the ACTIVE version" while ltx23 was both default and active. It now
# keys off PACK IDENTITY: a version gets MODEL_ID only when its own q4 pack
# path IS Q4_LOCAL_PATH. That is why the answer stopped moving when the
# default did, and it is the assertion that would have caught it.
eq("2.3 base dir is the 2.3 pack", Path(p.base_model_dir("ltx23")).name, "ltx-2.3-mlx-q4")
eq("2.5 base dir is the 2.5 pack", Path(p.base_model_dir("ltx25")).name, "ltx-2.5-mlx-q4")
eq("the default lane resolves 2.5", p.base_model_dir(), p.base_model_dir("ltx25"))
eq("2.3 keeps the env-aware MODEL_ID spelling", p.base_model_dir("ltx23"), str(p.MODEL_ID))
eq("2.3 q4 path", p.pack_path("q4", "ltx23"), p.Q4_LOCAL_PATH)
eq("2.3 q8 path", p.pack_path("q8", "ltx23"), p.Q8_LOCAL_PATH)
eq("2.5 q4 path", p.pack_path("q4", "ltx25").name, "ltx-2.5-mlx-q4")
eq("2.5 q8 path", p.pack_path("q8", "ltx25").name, "ltx-2.5-mlx-q8")
eq("an unregistered quant falls back to the models root", p.pack_path("q9"), p.MODELS_DIR)

# =============================================================================
# 3. Each generation names its own TEXT ENCODER — the §4.2 bug, the worse one
# =============================================================================
# Gemma 3 12B and Gemma 4 12B both project to 3840 x (48+1) = 188160, so a 2.5
# DiT conditioned on the 2.3 encoder raises nothing, agrees on every shape, and
# is merely wrong. The port's resolve_text_tower() refusal exists to prevent
# exactly this and could not, because the panel never handed it the 2.5
# encoder. A refusal only fires on a path something reaches.
eq("2.3 encoder is Gemma 3", Path(p.text_encoder_dir("ltx23")).name, "gemma-3-12b-it-4bit")
eq("2.5 encoder is Gemma 4", Path(p.text_encoder_dir("ltx25")).name, "gemma4-12b-ltx25-q4")
eq("the default lane uses Gemma 4", p.text_encoder_dir(), p.text_encoder_dir("ltx25"))
eq("2.3 names the GEMMA constant so LTX_GEMMA_PATH still works",
   p.model_version("ltx23")["text_encoder"]["repo_key"], "gemma")
eq("2.5 names its own encoder repo",
   p.model_version("ltx25")["text_encoder"]["repo_key"], "gemma4_25")
eq("the two encoders are different directories",
   p.text_encoder_dir("ltx23") != p.text_encoder_dir("ltx25"), True)

# =============================================================================
# 4. required_files.json is the single source of truth (never a copy)
# =============================================================================
_by_key = {r["key"]: r for r in p._REQUIRED["repos"]}
eq("every registered repo key exists in required_files.json",
   sorted(k for k in ("q4", "q8", "q4_25", "q8_25", "hq_25", "gemma", "gemma4_25")
          if k not in _by_key), [])
for vid, q, key in (("ltx23", "q4", "q4"), ("ltx23", "q8", "q8"),
                    ("ltx25", "q4", "q4_25"), ("ltx25", "q8", "q8_25")):
    eq(f"{vid}/{q} pack repo IS the json entry", p.pack_repo(q, vid), _by_key[key])
eq("2.3 q4 file count", len(_by_key["q4"]["files"]), 7)
eq("2.3 q8 file count", len(_by_key["q8"]["files"]), 8)
eq("2.5 q4 file count", len(_by_key["q4_25"]["files"]), 10)
eq("2.5 q8 file count", len(_by_key["q8_25"]["files"]), 10)
eq("the June mosaic casualty is still in the 2.3 allowlist",
   "spatial_upscaler_x2_v1_1.safetensors" in _by_key["q4"]["download_include"], True)
eq("2.5 packs are BASE, not optional — the default lane needs them",
   [_by_key[k].get("kind") for k in ("q4_25", "gemma4_25")], ["base", "base"])

# The HQ add-on is a DOWNLOAD UNIT, not a directory: it shares q8_25's
# local_dir on purpose, because those two files are loaded out of it by name.
eq("2.5 declares an HQ add-on", p.model_version("ltx25").get("hq_addon_repo_key"), "hq_25")
eq("2.3 declares none, so the two-stage gates are inert for it",
   p.model_version("ltx23").get("hq_addon_repo_key"), None)
eq("the add-on is a guest in the q8 directory",
   _by_key["hq_25"].get("publish_scope"), "files")

# User-facing character warnings are rendered twice in the panel (Manual and
# Storyboard), but both fragments come from this one registry-backed helper.
# Exercise BOTH generations: checking only the active default is how literal
# "Install Q8 (30 GB)" copy passed while the LTX23 pin needed 37 GB.
_q23_copy = p.q8_character_install_copy("ltx23")
_q25_copy = p.q8_character_install_copy("ltx25")
eq("2.3 character install copy names its own pack",
   "LTX 2.3" in _q23_copy and "37 GB" in _q23_copy, True)
eq("2.3 character install copy never advertises the 2.5 pack",
   "2.5" not in _q23_copy and "30.02 GB" not in _q23_copy, True)
eq("2.5 character install copy names its own pack",
   "LTX-2.5" in _q25_copy and "30.02 GB" in _q25_copy, True)
eq("the served page contains no unresolved Q8-copy placeholder",
   "__Q8_CHARACTER_INSTALL_COPY__" in p.page(), False)

# =============================================================================
# 5. Completeness — this install, and a synthetic incomplete one
# =============================================================================
eq("q8_missing_files delegates to the registry", p.q8_missing_files(), p.pack_missing_files("q8"))
eq("q8_available_anywhere delegates", p.q8_available_anywhere(), p.pack_available_anywhere("q8"))
eq("an unregistered quant invents no missing files", p.pack_missing_files("q9"), [])
for vid in ("ltx23", "ltx25"):
    for q in ("q4", "q8"):
        eq(f"this install is complete ({vid}/{q})", p.pack_missing_files(q, vid), [])
    eq(f"this install has {vid}'s text encoder", p.text_encoder_missing_files(vid), [])
eq("the HQ surface is complete on this box", p.hq_surface_missing("ltx25"), [])
eq("2.3 has no HQ surface of its own to be missing", p.hq_surface_missing("ltx23"), [])

# =============================================================================
# 6. cap_tier — the machine's RAM vs the version's ceiling
# =============================================================================
_ram_tier = "q8" if p.SYSTEM_CAPS.get("allows_q8") else "q4"
eq("the quant tier is a property of the MACHINE", p._resolve_quant_tier(), _ram_tier)
eq("neither shipped generation clamps below it",
   (p._resolve_cap_tier("ltx23"), p._resolve_cap_tier("ltx25")), (_ram_tier, _ram_tier))

_orig = p.MODEL_VERSIONS
_q4only = dict(p.model_version("ltx25"))
_q4only.update(id="ltx_q4only_test", cap_tiers=("q4",), packs=(p.version_pack("q4", "ltx25"),))
_q8only = dict(p.model_version("ltx25"))
_q8only.update(id="ltx_q8only_test", cap_tiers=("q8",))
p.MODEL_VERSIONS = _orig + (_q4only, _q8only)
try:
    eq("a q4-only version folds the surface DOWN on a q8 Mac",
       p._resolve_cap_tier("ltx_q4only_test"), "q4")
    eq("...without lying about the machine's own tier", p._resolve_quant_tier(), _ram_tier)
    eq("a q4-only version has no q8 pack", p.version_pack("q8", "ltx_q4only_test"), None)
    eq("a q8-only version is never folded UP past the machine",
       p._resolve_cap_tier("ltx_q8only_test"), _ram_tier)
finally:
    p.MODEL_VERSIONS = _orig
eq("the registry is restored", [v["id"] for v in p.MODEL_VERSIONS], ["ltx23", "ltx25"])

# =============================================================================
# 7. Job-time preflight — the anti-mosaic refusal
# =============================================================================
# DEFECT-1, FIXED (v4.0 step 7). `_canonical_layout()` compared MODEL_ID against
# `pack_path("q4")` with NO version_id — the DEFAULT generation. That was an
# identity while ltx23 was the only entry; from f1d2139 (2026-08-12) it compared
# the 2.3 install path against the 2.5 pack path, which are never equal, so it
# was False on every stock install. It is the SOLE gate on
# `ltx_pack_preflight()`, so the June-2026 mosaic guard was switched off in the
# field: an incomplete pack rendered a rainbow mosaic instead of naming the file.
#
# It now compares within a version, against ANY registered generation's own q4
# pack, which is what the expression meant before a second generation existed.
eq("the canonical layout is recognised on a stock install",
   p._canonical_layout(), True)
# And the property that made it rot: the answer must not depend on which
# generation is active. It is a question about THIS install's layout.
_saved_active_cl = p.ACTIVE_MODEL_VERSION
try:
    _seen_cl = {}
    for _vid in ("ltx25", "ltx23"):
        p.ACTIVE_MODEL_VERSION = _vid
        _seen_cl[_vid] = p._canonical_layout()
    eq("the layout check does not move with the active generation",
       _seen_cl["ltx25"], _seen_cl["ltx23"])
    eq("...and it is True from either", _seen_cl["ltx23"], True)
finally:
    p.ACTIVE_MODEL_VERSION = _saved_active_cl

# The refusal itself still works when it is reached — proven by driving it with
# the layout check forced, so the day DEFECT-1 is fixed the machinery is known
# good rather than merely re-enabled.
_saved_canon = p._canonical_layout
p._canonical_layout = lambda: True
try:
    for vid in ("ltx23", "ltx25"):
        no_raise(f"a complete pack passes ({vid}/q4)",
                 lambda vid=vid: p.ltx_pack_preflight("q4", "X", vid))
        no_raise(f"a complete pack passes ({vid}/q8)",
                 lambda vid=vid: p.ltx_pack_preflight("q8", "X", vid))
    no_raise("an unregistered quant is a no-op", lambda: p.ltx_pack_preflight("q9", "X"))

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "incomplete-q8"
        fake.mkdir()
        repo = p.pack_repo("q8", "ltx23")
        for f in repo["files"]:
            if f == "spatial_upscaler_x2_v1_1.safetensors":
                continue          # the actual June 2026 casualty
            (fake / f).write_bytes(b"x" * 4096)
        pack = p.version_pack("q8", "ltx23")
        saved = pack["path"]
        pack["path"] = fake
        # `pack_available_anywhere` is a second, legitimate early return: a pack
        # that is incomplete in mlx_models but complete in the HF cache is not
        # broken. On a developer box the 2.3 q8 files ARE in the cache, so the
        # refusal would never be reached and this section would test nothing.
        # Forced off for the duration — the property under test is "missing
        # everywhere", not "missing here".
        _saved_anywhere = p.pack_available_anywhere
        p.pack_available_anywhere = lambda q, v=None: False
        try:
            eq("the missing file is NAMED", p.pack_missing_files("q8", "ltx23"),
               ["spatial_upscaler_x2_v1_1.safetensors"])
            for needle, what in (
                ("spatial_upscaler_x2_v1_1.safetensors", "names the file"),
                ("mosaic", "explains the mosaic"),
                ("Extend", "names the feature"),
                (repo["repo_id"], "names the repo to re-download"),
            ):
                raises(f"the refusal {what}",
                       lambda: p.ltx_pack_preflight("q8", "Extend", "ltx23"), needle)
            # ...and the OTHER generation is untouched by it.
            no_raise("2.5 is unaffected by a broken 2.3 pack",
                     lambda: p.ltx_pack_preflight("q8", "Extend", "ltx25"))
        finally:
            pack["path"] = saved
            p.pack_available_anywhere = _saved_anywhere
    eq("the 2.3 q8 path is restored", p.pack_path("q8", "ltx23"), p.Q8_LOCAL_PATH)
    no_raise("preflight is quiet again", lambda: p.ltx_pack_preflight("q8", "X", "ltx23"))
finally:
    p._canonical_layout = _saved_canon

# A non-canonical layout must never be second-guessed: we have no manifest for
# a bare HF repo id or a directory the user assembled, so a completeness claim
# about either would be a lie.
_saved_model_id = p.MODEL_ID
try:
    for spelling in ("dgrauet/ltx-2.3-mlx-q4", "/somewhere/custom/weights"):
        p.MODEL_ID = spelling
        eq(f"{spelling!r} is not the canonical layout", p._canonical_layout(), False)
        no_raise(f"{spelling!r} is never second-guessed",
                 lambda: p.ltx_pack_preflight("q8", "Extend", "ltx23"))
finally:
    p.MODEL_ID = _saved_model_id

# =============================================================================
# 8. Deep-verify SHA sources — hf-api vs the shipped manifest
# =============================================================================
# The 2.5 packs are OUR quantisation, mirrored as GitHub release assets, so
# HuggingFace has no hashes for them and the pack ships its own manifest. The
# 2.3 packs are dgrauet's and are verified against the HF API. Getting this
# backwards means either a network call that returns nothing (and a silent
# fallback) or a pack that is never really verified.
eq("2.3 q4 verifies against hf-api", p._pack_verify_source("q4"), "hf-api")
eq("2.3 q8 verifies against hf-api", p._pack_verify_source("q8"), "hf-api")
eq("2.5 q4 verifies against its manifest", p._pack_verify_source("q4_25"), "manifest")
eq("2.5 q8 verifies against its manifest", p._pack_verify_source("q8_25"), "manifest")

# DEFECT-2, FIXED (v4.0 step 1). Those four answers used to be right with two of
# them right by accident: `_pack_verify_source(repo_key)` resolved
# `version_pack(quant)` with NO version_id — i.e. through whichever generation
# was DEFAULT — so the OTHER generation's repo key was not recognised as a pack
# at all and fell through to the "hf-api" default. Flipping the default to 2.3
# sent the 2.5 packs to a HuggingFace repo that does not exist and never will,
# surviving only on `_expected_meta`'s empty-answer fallback to the manifest:
# one silent step from "verified" meaning "not checked".
#
# The fix is `pack_for_repo_key()`, which searches every registered generation,
# because where a pack's checksums live is a property of the PACK. The assertion
# that matters is therefore the one that used to be impossible: the answers must
# not depend on which generation is active. Both directions, both generations.
eq("2.5 q4's verify source is a property of the pack, not of the session",
   p.pack_for_repo_key("q4_25")["quant"], "q4")
eq("2.3 q4 resolves to a pack too, from a 2.5-default process",
   p.pack_for_repo_key("q4")["quant"], "q4")
eq("2.3 q8 resolves to a pack", p.pack_for_repo_key("q8")["quant"], "q8")
eq("2.5 q8 resolves to a pack", p.pack_for_repo_key("q8_25")["quant"], "q8")
eq("a non-pack repo key is still not a pack", p.pack_for_repo_key("gemma"), None)
eq("the HQ add-on is a FEATURE surface, not a pack", p.pack_for_repo_key("hq_25"), None)
# The property under test, stated as itself: swing ACTIVE_MODEL_VERSION across
# both generations and every verify source must be unchanged. This is the
# assertion the known_defect marker stood in for, and it is red on the old code.
_saved_active = p.ACTIVE_MODEL_VERSION
try:
    _seen = {}
    for _vid in ("ltx25", "ltx23"):
        p.ACTIVE_MODEL_VERSION = _vid
        _seen[_vid] = {k: p._pack_verify_source(k)
                       for k in ("q4", "q8", "q4_25", "q8_25", "gemma", "ic_colorize")}
    eq("verify sources do not move when the active generation does",
       _seen["ltx25"], _seen["ltx23"])
    eq("...and they are the right answers, from either generation",
       _seen["ltx23"],
       {"q4": "hf-api", "q8": "hf-api", "q4_25": "manifest", "q8_25": "manifest",
        "gemma": "hf-api", "ic_colorize": "hf-api"})
finally:
    p.ACTIVE_MODEL_VERSION = _saved_active
eq("an unknown repo defaults to hf-api", p._pack_verify_source("ic_colorize"), "hf-api")
eq("None defaults to hf-api", p._pack_verify_source(None), "hf-api")
eq("the 2.5 q4 pack really does carry a manifest",
   (p.pack_path("q4", "ltx25") / p.PACK_MANIFEST_NAME).exists(), True)
eq("the 2.3 q4 pack really does not",
   (p.pack_path("q4", "ltx23") / p.PACK_MANIFEST_NAME).exists(), False)

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    eq("no manifest -> {}", p._manifest_meta(base), {})
    (base / p.PACK_MANIFEST_NAME).write_text("{ not json")
    eq("malformed manifest -> {} (never 'corrupt')", p._manifest_meta(base), {})
    (base / p.PACK_MANIFEST_NAME).write_text('{"files": "wrong type"}')
    eq("wrong-shaped manifest -> {}", p._manifest_meta(base), {})
    (base / p.PACK_MANIFEST_NAME).write_text(json.dumps({
        "tool": "quantize_ltx.py", "tool_version": "1",
        "files": {
            "transformer-distilled.safetensors": {"sha256": "a" * 64, "bytes": 11322002285},
            "connector.safetensors": {"sha256": "b" * 64, "size": 42},
            "junk": "not a dict",
            "no_hash.safetensors": {"bytes": 0},
        },
    }))
    meta = p._manifest_meta(base)
    eq("manifest sha parsed", meta["transformer-distilled.safetensors"]["sha256"], "a" * 64)
    eq("manifest bytes parsed", meta["transformer-distilled.safetensors"]["size"], 11322002285)
    eq("'size' is accepted as well as 'bytes'", meta["connector.safetensors"]["size"], 42)
    eq("a non-dict entry is skipped", "junk" in meta, False)
    eq("a hash-less zero-byte entry is skipped", "no_hash.safetensors" in meta, False)

    # Drive the real _expected_meta on the DEFAULT generation's q4 pack, which
    # is the one `_pack_verify_source` actually resolves (see DEFECT-2). Its
    # repo key is what a caller passes; the network is spied, never called.
    _calls: list[str] = []
    _real = p._upstream_meta
    p._upstream_meta = lambda rid: (_calls.append(rid) or {})
    _key = p.pack_repo("q4")["key"]              # "q4_25" while 2.5 is default
    _rid = p.pack_repo("q4")["repo_id"]
    _pk = p.version_pack("q4")
    _saved_src = _pk["verify_source"]
    try:
        _pk["verify_source"] = "manifest"
        eq("a manifest pack reads the manifest",
           p._expected_meta(_key, _rid, base)
           ["transformer-distilled.safetensors"]["sha256"], "a" * 64)
        eq("a manifest pack NEVER asks HuggingFace", _calls, [])
        _pk["verify_source"] = "hf-api"
        got = p._expected_meta(_key, _rid, base)
        eq("an hf-api pack asks HuggingFace first", _calls, [_rid])
        eq("an empty network answer falls back to the manifest",
           got["connector.safetensors"]["sha256"], "b" * 64)
        p._upstream_meta = lambda rid: {"connector.safetensors": {"sha256": "c" * 64, "size": 1}}
        eq("a real network answer still wins over the manifest",
           p._expected_meta(_key, _rid, base)["connector.safetensors"]["sha256"], "c" * 64)
    finally:
        p._upstream_meta = _real
        _pk["verify_source"] = _saved_src
eq("the verify source is restored", p._pack_verify_source("q4_25"), "manifest")

# =============================================================================
# THE QUALITY REGISTRY — canvases, pipelines, and the two literals that must
# track it.
#
# This block exists because of a real, shipped regression. A session moved the
# `high` cell from 1024×576 to 1280×704 on the reasoning that the chip "priced
# a recipe no lane runs" — which silently DOUBLED the cost of a tier that was
# already public under that name. Every sidecar, every Load Params round-trip
# and every issue thread quoting "High" changed meaning under the user, and
# nothing raised, because no gate had an opinion about what a shipped key
# renders. Now one does: THE CANVAS OF A SHIPPED KEY IS PART OF ITS CONTRACT.
#
# A new canvas is a NEW KEY (see `high_720p`), never a redefinition.
_SHIPPED_CANVASES = {
    "quick":     (640, 448),
    "balanced":  (1024, 576),
    "standard":  (1280, 704),
    "high":      (1024, 576),
    "high_720p": (1280, 704),
}
for _k, _wh in _SHIPPED_CANVASES.items():
    eq(f"{_k} keeps its shipped canvas",
       (p.LTX_QUALITIES[_k]["width"], p.LTX_QUALITIES[_k]["height"]), _wh)
eq("no quality key has been added or dropped without updating this gate",
   sorted(p.LTX_QUALITIES), sorted(_SHIPPED_CANVASES))

# `ltx_quality_uses_hq` is the ONE answer to "does this run the two-stage HQ
# lane". Five call sites used to compare against the literal "high", which is
# what made them blind to a second HQ tier. The predicate must agree with the
# cell it reads, for every key — including keys nobody has invented yet.
for _k, _cell in p.LTX_QUALITIES.items():
    eq(f"uses_hq({_k}) agrees with the cell's own pipeline",
       p.ltx_quality_uses_hq(_k), _cell["pipeline"] == "hq")
eq("an unknown quality is not an HQ quality", p.ltx_quality_uses_hq("nonesuch"), False)
eq("None is not an HQ quality", p.ltx_quality_uses_hq(None), False)

# Every HQ tier needs the q8 pack. If a future HQ cell ships on q4 it will be
# routed to weights that do not contain the dev transformer it loads.
for _k, _cell in p.LTX_QUALITIES.items():
    if _cell["pipeline"] == "hq":
        eq(f"the HQ tier {_k} requires the q8 pack", _cell["pack"], "q8")

# STORYBOARD_FINAL_QUALITIES is a module-level LITERAL, read at import time
# before the registry builder exists — so it cannot be derived. This assertion
# is the thing that makes the literal safe: a tier added to one and forgotten
# in the other fails here instead of vanishing from the delivery-pass chips.
for _k in p.STORYBOARD_FINAL_QUALITIES:
    eq(f"storyboard final quality {_k} is a real registry key",
       _k in p.LTX_QUALITIES, True)
eq("the storyboard's final pass offers every tier except the draft-only floor",
   sorted(p.STORYBOARD_FINAL_QUALITIES),
   sorted(k for k in p.LTX_QUALITIES if k != "quick"))

# A measured ETA row may only name a quality the registry actually has, or it
# silently never fires and the chip quietly prints a modelled number forever.
for _key in p.LTX_MEASURED_ETA:
    eq(f"measured ETA row {_key} names a real quality",
       _key[1] in p.LTX_QUALITIES, True)
    eq(f"measured ETA row {_key} names a real length",
       _key[2] in p.LTX_LENGTHS, True)

# The 720p tier's measurement belongs to the geometry it was taken at, and to
# NOTHING else: 1280×704 × 121 frames. Any other length must print modelled.
eq("high_720p at 5s is the measured row",
   p.LTX_TIERS["high_720p_5s"]["eta_measured"], True)
eq("...and it is measured at the canvas it was timed on",
   (p.LTX_TIERS["high_720p_5s"]["width"],
    p.LTX_TIERS["high_720p_5s"]["height"],
    p.LTX_TIERS["high_720p_5s"]["frames"]), (1280, 704, 121))
for _ln in p.LTX_LENGTHS:
    if _ln != "5s":
        eq(f"high_720p at {_ln} is modelled, not measured",
           p.LTX_TIERS[f"high_720p_{_ln}"]["eta_measured"], False)
# `high` lost its measured row when the 491 s number went to the tier whose
# canvas actually produced it. It must NOT quietly inherit one again.
eq("high claims no measurement it did not earn",
   any(p.LTX_TIERS[f"high_{_ln}"]["eta_measured"] for _ln in p.LTX_LENGTHS), False)

# =============================================================================
print()
for f in _failures:
    print("FAIL  " + f)
print()
print(f"{OK} passed, {FAIL} failed, {DEFECT} known defect(s) pinned")
if DEFECT:
    print("A pinned defect is NOT a pass. See the DEFECT lines above.")
raise SystemExit(1 if FAIL else 0)
