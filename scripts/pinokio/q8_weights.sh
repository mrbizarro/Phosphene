#!/usr/bin/env bash
# Download the LTX-2.5 Q8 weights — the optional pack trained characters and
# voices need. Dispatched by download_q8.js. See scripts/pinokio/README.md.
#
#   cwd : the app root
#
# WHY THIS FILE EXISTS AT ALL, AND WHY THE COPY ALONE WAS NOT THE FIX.
# Through v3.8.3 this step ran `hf download dgrauet/ltx-2.3-mlx-q8` and its
# notify said "~37 GB". Both halves were 2.3 facts. v4.0 makes 2.5 the
# generation the panel serves, so correcting only the copy would have produced
# a button that says "LTX-2.5 Q8 weights (~30 GB)" and then downloads 2.3's
# 37 GB pack — a worse lie than the stale one, and exactly the class of drift
# this release keeps closing. The LANE moves with the words.
#
# WHY NOT `hf download`: the 2.5 packs are OUR quantisation of a gated upstream
# and our HF token is read-only, so they are mirrored as GitHub release assets,
# sharded at 1.9 GB because the asset cap is 2 GiB. fetch_pack_release.py
# downloads each shard, verifies its sha256, reassembles, and only renames a
# file into place once the whole-file hash matches the published manifest. It
# is resumable and idempotent: re-running it costs a read pass over what is
# already there, which is what makes "click it again" the correct advice.
#
# THE HIGH ADD-ON IS NOT FETCHED HERE, deliberately. hq_25 is a separate 29.5 GB
# download that lands INSIDE this pack's directory, and folding it in would hold
# a complete 30 GB pack hostage to a 60 GB wait. Q8 alone is what characters and
# voices need; High is one more click in Settings → Models.
#
# A USER PINNED BACK TO 2.3 FOLLOWS THEIR PIN. This used to fetch q8_25
# unconditionally while the sidebar entry that dispatches it was gated on the
# same hardcoded key — so a 2.3-pinned install with its own Q8 pack complete was
# still offered "Download Q8 weights (~30 GB)", and clicking it fetched 30 GB of
# a generation that install does not load. The version is resolved from the same
# ENVIRONMENT file the launcher sources and pinokio.js reads, so the button, its
# gate and this fetch all name one pack.
VERSION=$(sed -n 's/^[[:space:]]*LTX_MODEL_VERSION[[:space:]]*=[[:space:]]*\([^[:space:]#]*\).*/\1/p' \
            ENVIRONMENT 2>/dev/null | tail -1)
VERSION="${LTX_MODEL_VERSION:-$VERSION}"
case "$VERSION" in
  ltx23) REPO_KEY=q8;    LABEL='LTX-2.3 Q8 weights'; SIZE_GB=37 ;;
  *)     REPO_KEY=q8_25; LABEL='LTX-2.5 Q8 weights'; SIZE_GB=30.02 ;;
esac
echo "Fetching $LABEL (pack key: $REPO_KEY)…"

PY=./ltx-2-mlx/env/bin/python3.11

# THE TRANSPORT COMES FROM THE REGISTRY, NOT FROM THE KEY. Only the 2.5 packs
# are mirrored as GitHub release assets; LTX-2.3's q8 lives on Hugging Face
# only, and handing its key to the release fetcher failed before a single
# byte moved ("repo 'q8' is not mirrored through a GitHub release") — under a
# banner blaming the network (Codex INST-06, 2026-09-24). An unmirrored pack
# goes through `hf download` with the registry's own --include allowlist, the
# same lane the in-panel Models page uses for it.
fetch_pack() {
  if $PY -c 'import json,sys
r = next(x for x in json.load(open("required_files.json"))["repos"] if x["key"] == sys.argv[1])
sys.exit(0 if (r.get("mirror") or {}).get("kind") == "github-release" else 1)' "$REPO_KEY"
  then
    $PY scripts/fetch_pack_release.py --repo-key "$REPO_KEY"
    return
  fi
  ARGS=()
  while IFS= read -r -d '' a; do ARGS+=("$a"); done < <($PY -c 'import json,sys
r = next(x for x in json.load(open("required_files.json"))["repos"] if x["key"] == sys.argv[1])
out = [r["repo_id"], "--local-dir", r["local_dir"]]
for pat in r.get("download_include") or []:
    out += ["--include", pat]
sys.stdout.write("\0".join(out) + "\0")' "$REPO_KEY")
  [ "${#ARGS[@]}" -gt 0 ] || return 1
  HF_HUB_ENABLE_HF_TRANSFER=1 ./ltx-2-mlx/env/bin/hf download "${ARGS[@]}"
}

if fetch_pack; then
  echo "$LABEL ready (verified against the published manifest)."
else
  echo '=================================================================='
  echo "PHOSPHENE COULD NOT DOWNLOAD THE $LABEL"
  echo 'Nothing is broken - this pack is optional. The panel keeps rendering'
  echo 'on the base weights; trained characters and voices are what need Q8.'
  echo 'This is almost always a network problem (no connection, a VPN or'
  echo "proxy, GitHub or Hugging Face blocked) or a full disk: the pack needs about $SIZE_GB GB."
  echo 'Fix that and click again - nothing already downloaded is fetched'
  echo 'twice, and a part-finished pack resumes where it stopped.'
  echo '=================================================================='
  exit 1
fi
