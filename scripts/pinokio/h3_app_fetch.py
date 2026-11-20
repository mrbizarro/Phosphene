#!/usr/bin/env python3
"""Mac app only: download the Hailuo H3 weights, resumably, and verify every byte.

WHY THIS EXISTS (5.0.9). A fresh Mac-app user had no way to get H3: the app builds the
H3 runner only when the weights are already on the Mac, and the weights only came from
Pinokio's install_h3.js (which runs the engine's own scripts/download_selected.py). That
script uses hf_hub_download, whose Xet transfer restarts an unfinished file from zero
(measured 3 Nov 2026), so quitting the app 30 GB into the 41 GB master would cost the
whole file again. This fetcher downloads exactly the same files, from the same repos,
into the same folders, but:

  * pinned to exact repo commits, with every file's size and checksum written down
    below (sha256 for the large files, the git blob id for the small ones), so a file
    is only ever named as finished after it was checked;
  * resumable at the byte: each file grows as `<name>.part` with HTTP range requests
    (curl -C -), so a quit, a crash, a restart or a dropped connection continues from
    the last byte on disk;
  * disk-checked before it starts and before every file: the rest of the download plus
    room for the compact engine build that follows it.

Nothing here needs git, Hugging Face tools or a login (the three repos are public).

CHINA (5.0.76). huggingface.co is blocked in mainland China: a customer's install died here
with "curl: (28) Failed to connect to huggingface.co port 443 after 30003 ms". One quick
probe (scripts/hf_source.py) now decides the source for the session: when Hugging Face
cannot be reached, the files come from the China mirror (hf-mirror.com) and the log says
"Hugging Face unreachable, using the China mirror". A connection failure in the middle of a
download switches too (never a 401/403/404/429). The mirror is not trusted: the sizes and
checksums below were taken from Hugging Face, every file is checked before it gets its
name, no login is ever sent, and its redirects must stay on HTTPS.

  usage : h3_app_fetch.py --root <H3 models folder> [--reserve-gb N] [--plan]
  output: plain lines, "@@progress <done> <total>" now and then, and on failure one
          "FATAL error: <reason + what to do>" line (the Studio shows the reason).
  exit  : 0 = every file is in place and verified; 1 = failed (nothing finished is lost);
          3 = not enough free disk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HF = os.environ.get("PHOSPHENE_H3_FETCH_ENDPOINT", "https://huggingface.co").rstrip("/")


def _hf_source():
    """scripts/hf_source.py, imported by name (a compiled Pro copy sits beside it as a .so).
    None when it is missing: then Hugging Face only, exactly as before."""
    try:
        from scripts import hf_source as m          # run as an import from ROOT (the Studio)
        return m
    except ImportError:
        pass
    here = str(Path(__file__).resolve().parents[1])
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import hf_source as m                       # run as a script
        return m
    except ImportError:
        return None


#: The base this run downloads from: HF, or the mirror once the session switched.
_SOURCE = {"base": HF, "mirror": False}


def _use_mirror(log=True) -> bool:
    """Switch this run to the China mirror (announced once). False when there is none."""
    m = _hf_source()
    if m is None or not m.mirror_base() or m.mode() == "hf":
        return False
    if not _SOURCE["mirror"]:
        _SOURCE.update(base=m.mirror_base(), mirror=True)
        if log:
            m.announce()
    return True


def choose_source() -> str:
    """Hugging Face, unless the session's probe says it cannot be reached."""
    m = _hf_source()
    if m is not None and m.use_mirror():
        _use_mirror()
    return _SOURCE["base"]

# (repo, commit, path in repo, folder under the layout, bytes, kind, digest)
#   kind "sha256": the LFS sha256 of the file; kind "git": the git blob id (sha1 of
#   "blob <size>\0" + bytes). Measured 9 Nov 2026 from the Hugging Face API
#   (paths-info at these commits). The same files download_selected.py fetches.
_DIT_REPO, _DIT_REV = "DeepBeepMeep/MiniMax-H3", "3d3c6fb64d610dcd2d0ea5dd69a9360e69d5f24f"
_Q8_REPO, _Q8_REV = "ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit", "64314cde0ac6d90f132bc94ae58e0c82f77396c6"
_META_REPO, _META_REV = "MiniMaxAI/MiniMax-H3", "42ed227ee7df40d41602854ae760620d6eb651fe"
FILES = (
    (_DIT_REPO, _DIT_REV, "MiniMax-H3-FL2VA-pruned_bf16.safetensors", "deepbeep-pruned-bf16",
     41399442656, "sha256", "85f57a3a7a26def920999f4be786ebb91d0e677ddf327d1848b7724ca829d187"),
    (_Q8_REPO, _Q8_REV, "text_encoder.safetensors", "ddalcu-q8",
     28222741760, "sha256", "79514b061aa0acbeb802bbe37e4853f1b0d0e6fe7fad4f61624057201773bf56"),
    (_Q8_REPO, _Q8_REV, "video_vae.safetensors", "ddalcu-q8",
     5207808496, "sha256", "7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522"),
    (_Q8_REPO, _Q8_REV, "audio_vae.safetensors", "ddalcu-q8",
     605254808, "sha256", "8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48"),
    (_Q8_REPO, _Q8_REV, "config.json", "ddalcu-q8", 724, "git", "3cfcb114392cd7846566fbe2cfdbab30b95b3a82"),
    (_Q8_REPO, _Q8_REV, "tokenizer.json", "ddalcu-q8", 7032403, "git", "c6cc1014128b19d1fc46b1d30a23e3b1d35db421"),
    (_Q8_REPO, _Q8_REV, "tokenizer_config.json", "ddalcu-q8", 11003, "git", "204d76f78dac6dedc820418c30bf01145de78a21"),
    (_Q8_REPO, _Q8_REV, "vocab.json", "ddalcu-q8", 2776833, "git", "4783fe10ac3adce15ac8f358ef5462739852c569"),
    (_Q8_REPO, _Q8_REV, "merges.txt", "ddalcu-q8", 1671839, "git", "20024bfe7c83998e9aeaf98a0cd6a2ce6306c2f0"),
    (_Q8_REPO, _Q8_REV, "LICENSE", "ddalcu-q8", 17604, "git", "c389b45855337ccec8ddeb389f4fe902abcd0b19"),
    (_Q8_REPO, _Q8_REV, "NOTICE", "ddalcu-q8", 552, "git", "41d4acd322a0088b3055f6cdb07f289d0cfcac3b"),
    (_Q8_REPO, _Q8_REV, "MODIFICATIONS.md", "ddalcu-q8", 1630, "git", "5f97513622e2dada5e310f03fed7ead9eff7e880"),
    (_META_REPO, _META_REV, "FL2VA/text_encoder/config.json", "upstream-meta",
     1474, "git", "29e925092dee9f14278c53e7e7d876cea8a997bc"),
    (_META_REPO, _META_REV, "FL2VA/model_index.json", "upstream-meta",
     719, "git", "ed4dfb751d8417e5ab8cf58a6915214bda6ba676"),
)
TOTAL_BYTES = sum(f[4] for f in FILES)            # 75,446,762,501 bytes (~75.4 GB)
#: Room the compact engine build needs after the download (h3_build_q8.sh: ~22 GB on
#: disk) plus a margin so macOS is never left with nothing.
RESERVE_BYTES = 25 * 10 ** 9
_DIT_REL = Path("deepbeep-pruned-bf16") / "MiniMax-H3-FL2VA-pruned_bf16.safetensors"


def layout(root: Path) -> Path:
    """The folder the three weight folders go in: the same rule as h3_roots.sh (flat
    when a flat master is already there, else `<root>/models`, which is what a fresh
    Pinokio install writes), so a Pinokio library and an app download look the same."""
    if (root / _DIT_REL).is_file() and not (root / "models").is_dir():
        return root
    return root / "models"


def target(root: Path, spec) -> Path:
    return layout(root) / spec[3] / spec[2]


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def finished(root: Path, spec) -> bool:
    """A file under its final name with the right size was checked before it got that
    name (the only writer is this script). A file someone else put there with the right
    size is trusted the same way the Studio's own probe trusts it."""
    return _size(target(root, spec)) == spec[4]


def bytes_on_disk(root: Path) -> int:
    """Finished files plus the part files: the progress, without parsing anything."""
    done = 0
    for spec in FILES:
        t = target(root, spec)
        done += spec[4] if _size(t) == spec[4] else min(_size(t.with_name(t.name + ".part")), spec[4])
    return done


def remaining_bytes(root: Path) -> int:
    return max(0, TOTAL_BYTES - bytes_on_disk(root))


def free_bytes(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 0


def plan(root: Path, reserve: int = RESERVE_BYTES) -> dict:
    rem = remaining_bytes(root)
    free = free_bytes(root)
    need = rem + reserve if rem else 0
    return {"total_bytes": TOTAL_BYTES, "done_bytes": TOTAL_BYTES - rem, "remaining_bytes": rem,
            "free_bytes": free, "need_bytes": need, "enough_space": free >= need,
            "complete": rem == 0 and all(finished(root, s) for s in FILES)}


def _digest(path: Path, kind: str, size: int) -> str:
    h = hashlib.sha256() if kind == "sha256" else hashlib.sha1(b"blob %d\0" % size)
    with open(path, "rb") as f:
        while True:
            b = f.read(8 * 1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def _url(spec, base: str | None = None) -> str:
    return f"{base or _SOURCE['base']}/{spec[0]}/resolve/{spec[1]}/{spec[2]}"


def _progress(root: Path) -> None:
    print(f"@@progress {bytes_on_disk(root)} {TOTAL_BYTES}", flush=True)


def _curl(url: str, part: Path) -> int:
    """One resumable transfer into `part` (continues from its size). curl's own retries
    cover short drops; a stall under 10 kB/s for 2 minutes ends the try."""
    # On Hugging Face: a short connect wait and few retries, so a blocked connection hands
    # over to the mirror in about a minute instead of curl's 6 x 30 s per try. Redirects
    # (the mirror's CDN) must stay on HTTPS; no credential is ever passed to curl.
    on_hf = not _SOURCE["mirror"]
    retries, connect = ("2", "15") if on_hf else ("5", "30")
    # -q must be first: curl then ignores ~/.curlrc (it could add a header or relax the
    # redirect rule for every request, the mirror's included).
    cmd = ["/usr/bin/curl", "-q", "-sS", "-L", "--fail", "-C", "-", "--retry", retries, "--retry-delay", "5",
           "--retry-all-errors", "--connect-timeout", connect, "--speed-limit", "10240", "--speed-time", "120",
           "--max-redirs", "5", "-A", "Phosphene-H3-app-fetch", "-o", str(part), url]
    if url.startswith("https://"):
        cmd[2:2] = ["--proto-redir", "=https"]
    if not Path(cmd[0]).exists():
        cmd[0] = "curl"
    # The lock's fd goes to curl too: a curl left running by a killed parent keeps the
    # folder locked, so a second download can never write the same part file.
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, errors="replace", pass_fds=_LOCK_FD)
    err = proc.stderr.read()
    rc = proc.wait()
    if rc != 0 and err.strip():
        print("  " + err.strip().splitlines()[-1][:200], flush=True)
    return rc


_LOCK_FD: tuple = ()


def _take_lock(folder: Path, wait_s: float = 90.0) -> bool:
    """One download per weights folder (flock; the kernel drops it when every holder,
    curl included, is gone). Waits a little for a download that is still ending."""
    import fcntl
    global _LOCK_FD
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(folder / ".h3_app_fetch.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.time() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _LOCK_FD = (fd,)
            return True
        except OSError:
            if time.time() >= deadline:
                os.close(fd)
                return False
            time.sleep(2)


def fetch_one(root: Path, spec, tries: int = 6) -> str | None:
    """Bring one file in. Returns None when it is in place and verified, else the reason."""
    t = target(root, spec)
    size, kind, want = spec[4], spec[5], spec[6]
    if _size(t) == size:
        return None
    t.parent.mkdir(parents=True, exist_ok=True)
    part = t.with_name(t.name + ".part")
    if t.exists():                      # a wrong-size file under the final name: start it over
        os.replace(t, part) if not part.exists() else t.unlink()
        if _size(part) > size:
            part.unlink()
    big = size > 100 * 10 ** 6
    for attempt in range(1, tries + 1):
        have = _size(part)
        if have > size:                 # more bytes than the file has: not ours to keep
            part.unlink()
            have = 0
        if have < size:
            if big:
                print(f"Downloading {spec[2]} ({_gb(size)})" + (f", resuming at {_gb(have)}" if have else ""), flush=True)
            rc = _curl(_url(spec), part)
            if rc != 0 and _size(part) < size:
                m = _hf_source()
                never_reached = m is not None and (rc in m.CURL_CONNECT_FAILURES
                                                   or (rc == m.CURL_TIMEOUT and _size(part) == have))
                if never_reached and not _SOURCE["mirror"] and _use_mirror(log=False):
                    m.mark_unreachable()
                    m.announce()
                    continue                     # straight to the mirror, resuming the same part
                if attempt < tries:
                    time.sleep(min(60, 5 * attempt))
                    continue
                where = " from the China mirror" if _SOURCE["mirror"] else ""
                return (f"the download of {spec[2]}{where} kept failing (curl exit {rc}). "
                        "Check the internet connection, then press Resume: what is downloaded is kept")
        if _size(part) != size:
            part.unlink(missing_ok=True)
            continue
        if big:
            print(f"Checking {spec[2]}", flush=True)
        got = _digest(part, kind, size)
        if got != want:
            part.unlink(missing_ok=True)
            if _SOURCE["mirror"] and big:
                # A damaged copy from the mirror is not fetched again and again (41 GB each).
                return (f"the China mirror sent a damaged copy of {spec[2]} (it did not match "
                        "Hugging Face's checksum, so it was not used). Press Resume to try again later")
            print(f"  {spec[2]} did not match its checksum; downloading it again", flush=True)
            continue
        os.replace(part, t)
        return None
    return f"{spec[2]} could not be downloaded intact. Press Resume to try again"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, type=Path, help="the H3 models folder (LTX_H3_MODELS)")
    ap.add_argument("--reserve-gb", type=float, default=RESERVE_BYTES / 1e9)
    ap.add_argument("--plan", action="store_true", help="print what is left and the disk need as JSON")
    a = ap.parse_args(argv)
    root = a.root.expanduser()
    reserve = int(a.reserve_gb * 1e9)
    if a.plan:
        print(json.dumps(plan(root, reserve)))
        return 0
    p = plan(root, reserve)
    if p["complete"]:
        print("Hailuo H3 weights: all in place.")
        _progress(root)
        return 0
    if not p["enough_space"]:
        print(f"FATAL error: Hailuo H3 needs {_gb(p['need_bytes'])} free for the rest of the download and "
              f"the compact engine; this disk has {_gb(p['free_bytes'])}. Free some space, then press Resume.",
              flush=True)
        return 3
    if not _take_lock(layout(root)):
        print("FATAL error: another Hailuo H3 download is still running on this Mac. "
              "Wait a minute, then press Resume.", flush=True)
        return 1
    choose_source()
    print(f"Hailuo H3 weights: {_gb(p['remaining_bytes'])} to download of {_gb(TOTAL_BYTES)}"
          f" (into {layout(root)})", flush=True)
    _progress(root)
    for spec in sorted(FILES, key=lambda s: s[4]):          # small files first: quick to finish
        if finished(root, spec):
            continue
        rem = remaining_bytes(root)
        free = free_bytes(root)
        if free < rem + reserve:
            print(f"FATAL error: the disk filled up: {_gb(rem)} still to download and {_gb(free)} free. "
                  "Free some space, then press Resume.", flush=True)
            return 3
        err = fetch_one(root, spec)
        _progress(root)
        if err:
            print(f"FATAL error: {err}.", flush=True)
            return 1
    print("Hailuo H3 weights: all in place and checked.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
