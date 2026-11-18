#!/usr/bin/env python3
"""Run the installed H3 runner with References' picture sizing.

Usage (the Studio's References lane): <h3 venv python> scripts/h3_refs_run.py <h3 checkout>
<canvas width> <canvas height> <runner arguments...>

Each `--reference` picture keeps its own shape and gets the canvas's pixel area, never upscaled,
on the 32-pixel grid: the sizing the reference adapter was distilled with. The runner's own
sizing (one short edge for every picture) is replaced for this process only; the engine checkout
is not changed, so the installed engine stays exactly the pinned one.
"""
from __future__ import annotations

import os
import runpy
import sys


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit("usage: h3_refs_run.py <h3 checkout> <canvas w> <canvas h> <runner args...>")
    engine, cw, ch = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, here)
    from h3_refs import match_size
    sys.path.remove(here)
    sys.path.insert(0, engine)
    os.chdir(engine)
    import minimax_h3_mlx.reference as reference

    def prepare(path, short_edge=2048):  # noqa: ARG001 - the runner passes its own size; match wins
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        w, h = match_size(im.size[0], im.size[1], cw, ch)
        print(f"[h3-refs] picture {os.path.basename(str(path))} {im.size[0]}x{im.size[1]} -> {w}x{h}", flush=True)
        return im if im.size == (w, h) else im.resize((w, h), Image.Resampling.LANCZOS)

    reference.prepare_reference_image = prepare
    runner = os.path.join(engine, "scripts", "generate_staged.py")
    sys.argv = [runner] + sys.argv[4:]
    runpy.run_path(runner, run_name="__main__")


if __name__ == "__main__":
    main()
