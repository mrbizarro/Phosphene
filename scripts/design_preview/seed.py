#!/usr/bin/env python3
"""Seed a throwaway studio for design previews: clips, stills, songs, a board.

The redesign is judged on screenshots of the REAL panel, and an empty panel
shows nothing but empty states. This writes a work directory the panel can be
pointed at (LTX_STATE_DIR / LTX_OUTPUT_DIR / LTX_UPLOADS_DIR / LTX_MODELS_DIR)
holding:

  outputs/   eight short clips with sound + sidecars, two songs (wav)
  uploads/   six library stills with image sidecars
  models/    every required_files.json row as a SPARSE placeholder file, so
             the panel reads "installed" without a byte of real weights
  state/     one storyboard + edit.json whose clips are the seeded mp4s

The pictures are drawn here (numpy + Pillow): layered ridges, skies, water and
light, so thumbnails read like film frames instead of test cards. Nothing is
downloaded and no GPU is touched. Re-running replaces the directory.

    python3 scripts/design_preview/seed.py /tmp/phos-preview
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
W, H = 1280, 720

SCENES = [
    # id, title, prompt, palette (sky top, sky bottom, ridge near, ridge far, light)
    ("fjord", "Lantern fjord", "A lone fisherman in a yellow coat drifts across a still fjord at dusk, hundreds of paper lanterns rising around his boat.",
     ((12, 22, 54), (236, 150, 120), (10, 22, 38), (44, 70, 104), (255, 200, 120))),
    ("diner", "Order up", "Live-action, a 1950s all-night diner at three in the morning, neon reflections on rain-streaked glass, steam rising from the griddle.",
     ((6, 4, 24), (64, 18, 74), (14, 8, 30), (34, 20, 64), (255, 60, 170))),
    ("desert", "Heat on the highway", "A vintage car crosses an empty desert highway at golden hour, heat haze shimmering off the asphalt, slow tracking shot.",
     ((70, 120, 190), (250, 196, 120), (120, 62, 32), (190, 112, 70), (255, 236, 170))),
    ("aurora", "North light", "An aurora unfolds over a frozen lake, green and violet curtains reflected in the ice, a tiny cabin glowing on the shore.",
     ((2, 8, 26), (20, 60, 80), (6, 12, 22), (18, 32, 52), (120, 255, 190))),
    ("storm", "The keeper", "A lighthouse on a black cliff during a storm, its beam sweeping through heavy rain, waves exploding against the rocks.",
     ((24, 30, 44), (70, 84, 104), (8, 10, 16), (30, 38, 52), (240, 240, 210))),
    ("forest", "First light", "Morning mist drifts through a pine forest clearing, sunbeams cutting between the trunks, a deer lifts its head.",
     ((120, 150, 150), (230, 220, 180), (16, 34, 26), (48, 78, 62), (255, 244, 200))),
    ("city", "Blue hour", "Rooftops of an old city at blue hour, lights coming on in the windows one by one, a slow aerial push across the skyline.",
     ((12, 26, 70), (120, 110, 170), (8, 12, 26), (26, 34, 70), (255, 214, 140))),
    ("canyon", "Red canyon", "A lone rider on horseback at the edge of a vast red canyon, dust in the air, wide establishing shot at sunset.",
     ((90, 60, 90), (250, 140, 80), (70, 22, 14), (150, 60, 36), (255, 220, 150))),
]


def _ridge(rng: np.random.Generator, n: int, base: float, amp: float, octaves: int = 5) -> np.ndarray:
    x = np.linspace(0, 1, n)
    y = np.full(n, base)
    for o in range(octaves):
        f = 2 ** o * rng.uniform(1.5, 3.0)
        y += amp / (2.3 ** o) * np.sin(2 * math.pi * (f * x + rng.uniform(0, 1)))
    return y


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_scene(sid: str, pal, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    top, bottom, near, far, light = pal
    # Sky: vertical gradient with a soft glow where the light sits.
    t = np.linspace(0, 1, H)[:, None, None]
    sky = (np.array(top)[None, None, :] * (1 - t) + np.array(bottom)[None, None, :] * t)
    sky = np.repeat(sky, W, axis=1).astype(np.float32)
    lx, ly = rng.uniform(0.3, 0.7) * W, rng.uniform(0.38, 0.52) * H
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt((xx - lx) ** 2 + ((yy - ly) * 1.4) ** 2)
    glow = np.exp(-(d / (W * 0.22)) ** 2)[..., None]
    sky = sky * (1 - 0.65 * glow) + np.array(light)[None, None, :] * 0.65 * glow
    img = Image.fromarray(np.clip(sky, 0, 255).astype(np.uint8))
    dr = ImageDraw.Draw(img, "RGBA")
    if sid in ("desert", "canyon", "fjord", "forest", "city"):
        r = 34 if sid != "forest" else 60
        dr.ellipse([lx - r, ly - r, lx + r, ly + r], fill=light + (230,))
    if sid == "aurora":
        for k in range(5):
            cx = rng.uniform(0.1, 0.9) * W
            col = (90 + 40 * k % 160, 255 - 30 * k, 190 + 10 * k)
            for j in range(60):
                x0 = cx + 380 * math.sin(j / 9 + k)
                dr.line([(x0, 60 + 3 * j), (x0 + 40, 300 + 3 * j)], fill=col + (10,), width=26)
        for _ in range(240):
            sx, sy = rng.uniform(0, W), rng.uniform(0, H * 0.5)
            dr.point((sx, sy), fill=(255, 255, 255, int(rng.uniform(80, 220))))
    # Far and near ridges.
    horizon = 0.62 if sid not in ("city",) else 0.7
    for layer, (col, base, amp) in enumerate([(far, horizon - 0.08, 0.06), (near, horizon + 0.02, 0.09)]):
        ys = _ridge(rng, W, base * H, amp * H, 6 if sid != "city" else 1)
        if sid == "city":
            ys = np.zeros(W)
            x = 0
            while x < W:
                bw = int(rng.uniform(30, 90))
                ys[x:x + bw] = (base - rng.uniform(0.02, 0.22 if layer else 0.15)) * H
                x += bw
        poly = [(0, H)] + [(i, float(ys[i])) for i in range(0, W, 4)] + [(W, H)]
        dr.polygon(poly, fill=col + (255,))
        if sid == "city" and layer == 1:
            for _ in range(420):
                wx = rng.uniform(0, W)
                top_y = ys[int(wx)]
                wy = rng.uniform(top_y + 6, H - 10)
                if rng.uniform() < 0.55:
                    dr.rectangle([wx, wy, wx + 3, wy + 4], fill=light + (int(rng.uniform(120, 255)),))
    # Water for the lake / fjord scenes: mirror of the sky band with ripples.
    if sid in ("fjord", "aurora", "storm"):
        wy = int(H * 0.74)
        band = np.asarray(img).copy()
        refl = band[wy - (H - wy):wy][::-1].astype(np.float32) * 0.55
        ripple = (np.sin(np.arange(H - wy) * 0.9)[:, None, None] * 6)
        band[wy:] = np.clip(refl + ripple, 0, 255).astype(np.uint8)
        img = Image.fromarray(band)
        dr = ImageDraw.Draw(img, "RGBA")
    if sid == "fjord":
        for _ in range(140):
            x, y = rng.uniform(0, W), rng.uniform(H * 0.15, H * 0.95)
            r = rng.uniform(1.5, 4.5) * (y / H + 0.3)
            dr.ellipse([x - r, y - r, x + r, y + r], fill=(255, 196, 110, int(rng.uniform(140, 255))))
        bx, by = W * 0.5, H * 0.8
        dr.polygon([(bx - 70, by), (bx + 70, by), (bx + 52, by + 18), (bx - 52, by + 18)], fill=(20, 16, 14, 255))
        dr.rectangle([bx - 6, by - 38, bx + 8, by], fill=(232, 178, 40, 255))
        dr.ellipse([bx - 6, by - 50, bx + 8, by - 36], fill=(40, 30, 24, 255))
    if sid == "diner":
        for k in range(7):
            x0 = rng.uniform(0, W - 200)
            y0 = rng.uniform(H * 0.15, H * 0.55)
            col = [(255, 46, 159), (94, 234, 255), (177, 74, 255)][k % 3]
            dr.rounded_rectangle([x0, y0, x0 + rng.uniform(120, 300), y0 + 12], 6, fill=col + (200,))
        for _ in range(500):
            x, y = rng.uniform(0, W), rng.uniform(0, H)
            dr.line([(x, y), (x - 3, y + 18)], fill=(200, 220, 255, 40), width=1)
    if sid == "storm":
        dr.rectangle([W * 0.7, H * 0.36, W * 0.7 + 18, H * 0.6], fill=(220, 220, 210, 255))
        dr.polygon([(W * 0.7 + 9, H * 0.37), (0, H * 0.18), (0, H * 0.34)], fill=(250, 250, 220, 50))
        for _ in range(900):
            x, y = rng.uniform(0, W), rng.uniform(0, H)
            dr.line([(x, y), (x - 6, y + 22)], fill=(200, 210, 230, 50), width=1)
    if sid == "forest":
        for _ in range(26):
            x = rng.uniform(0, W)
            wdt = rng.uniform(8, 26)
            dr.rectangle([x, H * 0.2, x + wdt, H], fill=(14, 26, 20, 235))
        for k in range(6):
            x = W * 0.45 + k * 30
            dr.polygon([(x, 0), (x + 60, 0), (x - 160, H), (x - 260, H)], fill=(255, 250, 220, 18))
    if sid in ("desert",):
        dr.polygon([(W * 0.5 - 6, H * 0.6), (W * 0.5 + 6, H * 0.6), (W * 0.9, H), (W * 0.1, H)], fill=(44, 40, 44, 255))
        for k in range(10):
            y = H * 0.62 + (k ** 1.8) * 4
            dr.rectangle([W * 0.5 - 1 - k * 0.6, y, W * 0.5 + 1 + k * 0.6, y + 2 + k], fill=(240, 220, 160, 255))
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    # Grain + vignette: the two things that make a still read as a frame.
    arr = np.asarray(img).astype(np.float32)
    arr += rng.normal(0, 5, arr.shape)
    vig = 1 - 0.45 * (((xx - W / 2) / (W / 1.6)) ** 2 + ((yy - H / 2) / (H / 1.4)) ** 2)
    arr *= np.clip(vig, 0.35, 1)[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def make_clip(still: Path, out: Path, seconds: float, w: int, h: int, freq: int) -> None:
    frames = int(seconds * 24)
    vf = (f"scale=2560:-2,zoompan=z='min(1.0+on*0.0011,1.25)':d={frames}:"
          f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps=24,format=yuv420p")
    af = (f"anoisesrc=color=pink:amplitude=0.06:duration={seconds},lowpass=f=900,"
          f"volume=0.6[a0];sine=f={freq}:duration={seconds},volume=0.05[a1];[a0][a1]amix=inputs=2")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(still),
                    "-filter_complex", af, "-vf", vf, "-t", str(seconds), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "22", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-shortest", str(out)],
                   check=True)


def make_song(out: Path, seconds: float, base: int) -> None:
    expr = (f"0.18*sin(2*PI*{base}*t)*exp(-mod(t,0.5)*5)+0.12*sin(2*PI*{base*1.5}*t)*(0.5+0.5*sin(2*PI*0.25*t))"
            f"+0.08*sin(2*PI*{base*2}*t)*exp(-mod(t+0.25,0.5)*8)")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"aevalsrc='{expr}':s=44100:d={seconds}", "-ac", "2", str(out)], check=True)


def sparse(path: Path, size: int = 2 << 20) -> None:
    """A structurally valid .safetensors whose one tensor is a sparse hole.

    The panel checks the header at boot (length prefix, JSON, data_offsets
    against the file size), so an empty file reads as a corrupt download and
    the page fills with Repair banners. This passes that check and costs no
    disk: the data region is a truncate() hole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = json.dumps({"__metadata__": {"format": "design_preview"},
                         "w": {"dtype": "U8", "shape": [size], "data_offsets": [0, size]}}).encode()
    header += b" " * (-len(header) % 8)
    with open(path, "wb") as fh:
        fh.write(len(header).to_bytes(8, "little"))
        fh.write(header)
        fh.truncate(8 + len(header) + size)


def seed_models(models: Path) -> None:
    req = json.loads((ROOT / "required_files.json").read_text())
    for repo in req["repos"]:
        local = Path(repo["local_dir"])
        rel = local.relative_to("mlx_models") if local.parts[0] == "mlx_models" else local
        for f in repo.get("files", []):
            name = f if isinstance(f, str) else f.get("path") or f.get("name")
            if not name:
                continue
            p = models / rel / name
            if name.endswith(".json"):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps({"_design_preview": True, "pad": "x" * 2048}))
            else:
                sparse(p)


def seed(work: Path) -> None:
    if work.exists():
        shutil.rmtree(work)
    out, up, state, models = (work / n for n in ("outputs", "uploads", "state", "models"))
    for d in (out, up / "library" / "manual", state, models):
        d.mkdir(parents=True, exist_ok=True)
    stills = work / "stills"
    stills.mkdir()
    now = time.time()
    clips = []
    for i, (sid, title, prompt, pal) in enumerate(SCENES):
        still = stills / f"{sid}.png"
        draw_scene(sid, pal, 1000 + i).save(still)
        mp4 = out / f"{sid}_{title.lower().replace(' ', '_')}.mp4"
        dims = (1280, 720) if i % 3 else (1024, 576)
        seconds = 5.0 if i % 2 == 0 else 4.0
        make_clip(still, mp4, seconds, *dims, freq=180 + 40 * i)
        engine = "h3" if sid in ("diner", "storm") else "ltx"
        side = {
            "params": {"prompt": prompt, "mode": "i2v" if i % 3 == 1 else "t2v", "width": dims[0], "height": dims[1],
                       "frames": int(seconds * 24) + 1, "quality": "balanced", "seed": str(41000 + i * 7),
                       "engine": engine, "preset_label": title},
            "engine": engine, "elapsed_sec": 140 + 23 * i, "model": "ltx-2.5-mlx-q4" if engine == "ltx" else "hailuo-h3",
            "generated_at": now - 600 * (i + 1),
        }
        (out / (mp4.name + ".json")).write_text(json.dumps(side, indent=2))
        ts = now - 600 * (i + 1)
        os.utime(mp4, (ts, ts))
        clips.append(mp4)
    lib = up / "library" / "manual"
    for i, (sid, title, prompt, pal) in enumerate(SCENES[:6]):
        img = draw_scene(sid, pal, 3000 + i).resize((1024, 576))
        png = lib / f"still_{sid}.png"
        img.save(png)
        (lib / (png.name + ".json")).write_text(json.dumps({
            "schema": "phosphene/library/image@1", "png_path": str(png), "prompt": prompt, "refs": [],
            "engine": "mflux", "family": "flux2", "model": "flux2-klein-4b", "seed": 9000 + i,
            "width": 1024, "height": 576, "aspect": "16:9", "elapsed_sec": 18 + i,
            "generated_at": now - 300 * (i + 1), "source": "panel.queue.image"}, indent=2))
        os.utime(png, (now - 300 * (i + 1) - 30,) * 2)
    for i, (name, base) in enumerate((("forty_frames_to_go", 110), ("neon_mile", 146))):
        wav = out / f"song_{name}.wav"
        make_song(wav, 20.0, base)
        (out / (wav.name + ".json")).write_text(json.dumps({
            "params": {"mode": "music", "prompt": "synthwave, warm analog bass, female vocal, 96 bpm",
                       "preset_label": name.replace("_", " ").title()},
            "engine": "music", "elapsed_sec": 200 + 40 * i, "generated_at": now - 200 * (i + 1)}, indent=2))
        os.utime(wav, (now - 200 * (i + 1) - 5,) * 2)
    seed_models(models)
    # Two installed LoRAs, so the picker shows rows rather than its empty
    # state; a sidecar each, in the CivitAI shape the panel reads.
    loras = models / "loras"
    for i, (slug, name, trig) in enumerate((("anamorphic_night", "Anamorphic Night", "anamorphic_style"),
                                             ("super8_memories", "Super 8 Memories", "super8_style"))):
        sparse(loras / f"{slug}.safetensors")
        (loras / f"{slug}.json").write_text(json.dumps({
            "name": name, "trigger_words": [trig], "recommended_strength": 0.8 + 0.1 * i,
            "base_model": "LTXV 2.3", "kind": "style", "civitai_id": 9100 + i,
            "description": "A look LoRA for cinematic night exteriors."}, indent=2))
    # One storyboard with an edit whose clips are real seeded files.
    bid = "sb_design_preview"
    d = state / "storyboards" / bid
    d.mkdir(parents=True)
    shots = []
    for n, mp4 in enumerate(clips[:5]):
        shots.append({"n": n + 1, "title": SCENES[n][1], "mode": "text", "engine": "ltx",
                      "prompt": SCENES[n][2], "duration_s": 5.0, "seed": 101 + n, "refs": [], "status": "planned"})
    (d / "storyboard.json").write_text(json.dumps({
        "schema": 1, "id": bid, "title": "Three places", "created_at": int(now - 3600), "cast": [],
        "engine_mode": "ltx", "policy": {}, "brief": "A quiet film about three places at the edge of the day.",
        "shots": shots}, indent=2))
    film = 0.0
    eclips = []
    for n, mp4 in enumerate(clips[:5]):
        dur = 4.0
        eclips.append({"id": f"kprev{n}", "path": str(mp4), "proxy": None, "kind": "video", "start": 0.0, "end": dur,
                       "film_start": film, "film_end": film + dur, "source": "auto", "locked": False})
        film += dur
    (d / "edit.json").write_text(json.dumps({
        "version": 2, "board": bid, "revision": 1,
        "audio": {"path": str(out / "song_forty_frames_to_go.wav"), "duration": 20.0, "offset": 0.0, "mode": "under"},
        "overlays": [], "beats": None, "clips": eclips}, indent=2))
    print(f"seeded {work}: {len(clips)} clips, 6 stills, 2 songs, models + 1 board")


if __name__ == "__main__":
    seed(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/phos-preview").resolve())
