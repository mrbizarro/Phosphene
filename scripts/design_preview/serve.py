#!/usr/bin/env python3
"""Boot the real panel against a seeded preview directory, as a 64 GB Mac.

    python3 scripts/design_preview/seed.py  /tmp/phos-preview
    python3 scripts/design_preview/serve.py /tmp/phos-preview --port 8240

The panel itself runs unmodified. Two things are faked so a Linux box (or a
small Mac) draws the screens an M4 Max sees:

  * hostinfo reports 64 GiB / "Apple M4 Max", so tier-gated controls (Q8,
    Character, Keyframes, Extend, H3) render as available instead of locked;
  * LTX_HELPER_PYTHON points at this interpreter, which is not a venv, so the
    engine-environment check stands down ("not ours to judge") instead of
    covering the page with its repair banner.

Nothing renders: there is no engine. Use shoot.mjs to stub /status for the
generating states. Never bind :8199 (the owner's panel) or :8198.
"""
from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--port", type=int, default=8240)
    ap.add_argument("--ram-gb", type=float, default=64.0)
    args = ap.parse_args()
    if args.port in (8198, 8199):
        sys.exit("refusing to bind a real panel port")
    work = Path(args.work).resolve()
    os.environ.update({
        "LTX_PORT": str(args.port),
        "LTX_STATE_DIR": str(work / "state"),
        "LTX_OUTPUT_DIR": str(work / "outputs"),
        "LTX_UPLOADS_DIR": str(work / "uploads"),
        "LTX_MODELS_DIR": str(work / "models"),
        "LTX_HELPER_PYTHON": sys.executable,
        "LTX_NO_BROWSER": "1",
        "LTX_DEFERRED_DOWNLOADS": "off",
        "PHOSPHENE_SEPARATOR_AUTOINSTALL": "0",
        "PHOSPHENE_NO_TELEMETRY": "1",
        "PHOSPHENE_ANALYTICS_DISABLED": "1",
        "PHOSPHENE_DISABLE_VERSION_CHECK": "1",
    })
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    import hostinfo
    ram = int(args.ram_gb * 1024 ** 3)
    hostinfo.total_ram_bytes = lambda: ram
    hostinfo.chip_brand = lambda: "Apple M4 Max"
    _mu = hostinfo.memory_usage

    def memory_usage():
        info = dict(_mu() or {})
        info.update(total_gb=args.ram_gb, used_gb=round(args.ram_gb * 0.31, 1), pressure_pct=22, swap_gb=0.0)
        return info
    hostinfo.memory_usage = memory_usage
    hostinfo.available_bytes = lambda: int(ram * 0.62)
    sys.argv = [str(ROOT / "mlx_ltx_panel.py")]
    runpy.run_path(str(ROOT / "mlx_ltx_panel.py"), run_name="__main__")


if __name__ == "__main__":
    main()
