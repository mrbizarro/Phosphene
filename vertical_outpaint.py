"""Make it vertical, the Pro layer: AI fill (outpaint) for the bands.

The Free layer (vertical_reframe.py) fills the bands above and below a
landscape clip with a blurred copy of itself. This layer asks LTX to paint
real picture there instead, with Lightricks' In/Outpainting IC-LoRA
(`ltx-2.3-22b-ic-lora-in-outpainting`, which Lightricks reuses unchanged on
LTX-2.5: their own example workflow
`example_workflows/2.5/LTX-2.5_ICLoRA_Outpaint_Two_Stage_Distilled.json`).

We follow that workflow's recipe, written for MLX:

1. Reference. The source is scaled to the canvas width and centred on a tall
   canvas; the new bands are flat green #66FF00. That green IS the mask the
   LoRA was trained on (`LTXVInpaintPreprocess` composites exactly this colour
   under the mask). No attention mask: the official graph leaves the guide
   node's attention_mask unconnected. Written losslessly so the green edge
   does not bleed through chroma subsampling.
2. Stage 1. Half resolution, the 8-step distilled table, the IC-LoRA at 1.0
   with the reference on the IC channel.
3. Blend. Laplacian-pyramid blend of the stage-1 frames with the source, so
   the middle is the real picture again before refining.
4. Stage 2. The blended clip is upsampled ×2 and refined for 2 steps from
   sigma 0.725 (the workflow's own `0.725, 0.4219, 0.0`), the reference
   re-applied at full size (ICLoraPipeline's source_video mode).
5. Final. The refined frames are scaled to the output canvas and blended
   with the ORIGINAL source a last time; well inside the source rectangle the
   original pixels are pasted back untouched, so the subject is never
   re-rendered.

This file holds the geometry, the ffmpeg arguments and the frame blending
(numpy, cv2 when present). The model calls live in mlx_warm_helper.py
(`generate_outpaint`), the queue job in mlx_ltx_panel.py.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

import vertical_reframe as vr

# Lightricks' official In/Outpainting IC-LoRA (gated repo
# Lightricks/LTX-2.3-22b-IC-LoRA-In-Outpainting; byte-identical un-gated copy
# in DeepBeepMeep/LTX-2, sha256 73dd0841...6dae6b5, 1,308,778,338 bytes).
# NOT the older `ltx-2.3-22b-ic-lora-outpaint.safetensors` from the same
# mirror (sha256 32c5d3e0..., a different, unofficial outpaint adapter).
OUTPAINT_LORA_FILE = "ltx-2.3-22b-ic-lora-in-outpainting-0.9.safetensors"
OUTPAINT_LORA_SHA256 = "73dd0841c0d4f0eb26fb1f017781b841b2752021944ac5ecefe57917f6dae6b5"
OUTPAINT_LORA_BYTES = 1308778338
OUTPAINT_LORA_STRENGTH = 1.0
# Where the Studio fetches it on first use: the un-gated public copy (one file,
# never the whole repository). Licence: LTX-2 Community License (LICENSES/).
OUTPAINT_LORA_REPO = "DeepBeepMeep/LTX-2"
GREEN_HEX = "0x66FF00"
GREEN_RGB = (0x66, 0xFF, 0x00)
STAGE1_STEPS = 8
REFINE_STEPS = 2
# Longest clip one pass renders (frames, 1 + 8k). Past this the job splits
# the clip into windows (see windows()).
MAX_WINDOW_FRAMES = 129
# Where the seam sits: this many output pixels INSIDE the source edge (the
# model redraws the source too, so a seam just inside it is invisible), and
# from this many pixels further in the source is pasted back untouched.
SEAM_INSET = 6
KEEP_INSET = 40


def _m64(n: float) -> int:
    return max(64, 64 * int(n / 64 + 0.5))


def canvas_size(out_w: int, out_h: int) -> tuple[int, int]:
    """The canvas the model renders on: the output shape snapped to 64 (the
    two-stage grid). 720x1280 -> 704x1280, 720x900 -> 704x896."""
    return _m64(out_w), _m64(out_h)


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def inset(self, d: int) -> "Rect":
        d = max(0, min(d, self.w // 2 - 1, self.h // 2 - 1))
        return Rect(self.x + d, self.y + d, self.w - 2 * d, self.h - 2 * d)


def source_rect(src_w: int, src_h: int, canvas_w: int, canvas_h: int) -> Rect:
    """Where the whole source sits on a canvas: full width, centred, even."""
    h = vr._even(canvas_w * src_h / src_w)
    if h > canvas_h - 64:
        # Under 32 px of band each side there is nothing worth painting.
        raise ValueError("the clip is already (almost) as tall as the canvas")
    return Rect(0, (canvas_h - h) // 2, canvas_w, h)


def render_frames(n: int) -> int:
    """Frames the model renders for an n-frame clip: the next 1 + 8k."""
    n = max(1, int(n))
    return 1 + 8 * max(1, -(-(n - 1) // 8))


def windows(n: int, max_frames: int = MAX_WINDOW_FRAMES) -> list[tuple[int, int]]:
    """Split n frames into consecutive (start, count) windows of at most
    max_frames. A long take is rendered in pieces; each piece's bands are
    painted on their own, so a cut between windows is the price (the panel
    splits films on their shot cuts first, where a change is natural)."""
    if n <= max_frames:
        return [(0, n)]
    k = -(-n // max_frames)
    size = -(-n // k)
    out, s = [], 0
    while s < n:
        c = min(size, n - s)
        out.append((s, c))
        s += c
    return out


def reference_args(src: str, dest: str, src_w: int, src_h: int, canvas_w: int, canvas_h: int,
                   frames: int, *, start_frame: int = 0, fps: float = 24.0,
                   ffmpeg: str = "ffmpeg") -> list[str]:
    """ffmpeg: the source fitted to the canvas width, centred on flat green,
    `frames` long (the last frame held if the clip is shorter), lossless."""
    r = source_rect(src_w, src_h, canvas_w, canvas_h)
    vf = []
    if start_frame:
        vf.append(f"trim=start_frame={int(start_frame)},setpts=PTS-STARTPTS")
    vf += [f"fps={fps:g}", f"scale={r.w}:{r.h}:flags=lanczos",
           f"pad={canvas_w}:{canvas_h}:{r.x}:{r.y}:color={GREEN_HEX}",
           f"tpad=stop_mode=clone:stop={int(frames)}", "setsar=1"]
    return [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
            "-vf", ",".join(vf), "-frames:v", str(int(frames)), "-an",
            "-c:v", "libx264", "-qp", "0", "-preset", "veryfast", "-pix_fmt", "yuv444p",
            dest]


# ---------------------------------------------------------------- blending

def _np():
    import numpy as np
    return np


def _cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except Exception:                                             # noqa: BLE001
        return None


def _down(img):
    cv2 = _cv2()
    if cv2 is not None:
        return cv2.pyrDown(img)
    np = _np()
    k = np.array([1, 4, 6, 4, 1], np.float32) / 16
    p = np.pad(img, ((2, 2), (2, 2)) + ((0, 0),) * (img.ndim - 2), mode="reflect")
    t = sum(k[i] * p[i:i + img.shape[0]] for i in range(5))
    t = sum(k[i] * t[:, i:i + img.shape[1]] for i in range(5))
    return t[::2, ::2]


def _up(img, shape):
    cv2 = _cv2()
    if cv2 is not None:
        return cv2.pyrUp(img, dstsize=(shape[1], shape[0]))
    np = _np()
    out = np.repeat(np.repeat(img, 2, axis=0), 2, axis=1)[:shape[0], :shape[1]]
    if out.shape[0] < shape[0] or out.shape[1] < shape[1]:
        out = np.pad(out, ((0, shape[0] - out.shape[0]), (0, shape[1] - out.shape[1]))
                     + ((0, 0),) * (out.ndim - 2), mode="edge")
    return out


def laplacian_blend(a, b, mask, levels: int = 5):
    """Blend float32 images a and b (H, W, 3) with mask (H, W): 1 = a, 0 = b.
    Each frequency band is mixed with a matching blur of the mask, so the seam
    has no hard edge and no ghost (LTXVLaplacianPyramidBlend, in numpy)."""
    np = _np()
    m = mask.astype(np.float32)
    if m.ndim == 2:
        m = m[..., None]
    ga, gb, gm = [a], [b], [m]
    for _ in range(levels):
        ga.append(_down(ga[-1]))
        gb.append(_down(gb[-1]))
        d = _down(gm[-1])
        gm.append(d[..., None] if d.ndim == 2 else d)
    out = ga[-1] * gm[-1] + gb[-1] * (1 - gm[-1])
    for i in range(levels - 1, -1, -1):
        la = ga[i] - _up(ga[i + 1], ga[i].shape)
        lb = gb[i] - _up(gb[i + 1], gb[i].shape)
        out = _up(out, ga[i].shape) + la * gm[i] + lb * (1 - gm[i])
    return out


def band_mask(w: int, h: int, rect: Rect, seam_inset: int = SEAM_INSET):
    """1 where the AI fill shows, 0 over the source (pulled `seam_inset` px
    inside its edge)."""
    np = _np()
    m = np.ones((h, w), np.float32)
    r = rect.inset(seam_inset)
    m[r.y:r.y + r.h, r.x:r.x + r.w] = 0.0
    return m


def compose_frame(gen, src_canvas, mask, keep: Rect, levels: int = 5):
    """One output frame: generated bands blended into the source, and the
    source pasted back exactly inside `keep`. uint8 in, uint8 out."""
    np = _np()
    a = gen.astype(np.float32)
    b = src_canvas.astype(np.float32)
    out = laplacian_blend(a, b, mask, levels)
    out = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    out[keep.y:keep.y + keep.h, keep.x:keep.x + keep.w] = \
        src_canvas[keep.y:keep.y + keep.h, keep.x:keep.x + keep.w]
    return out


def composite_video(gen_path: str, source_path: str, dest: str, out_w: int, out_h: int,
                    src_w: int, src_h: int, frames: int, *, start_frame: int = 0,
                    fps: float = 24.0, lossless: bool = False, crf: int | str = 18,
                    audio: bool = False, audio_offset_sec: float = 0.0,
                    seam_inset: int = SEAM_INSET, keep_inset: int = KEEP_INSET,
                    source_is_canvas: bool = False, ffmpeg: str = "ffmpeg") -> dict:
    """Stream the generated clip and the source through compose_frame.

    The generated clip is scaled to COVER out_w x out_h and centre-cropped (a
    704x1280 render becomes 720x1280 by losing 14 rows of band top and
    bottom). The source is scaled to its rectangle and held on its last frame
    when shorter. With source_is_canvas the source is already a padded canvas
    (the green reference) and only its source rectangle is read.
    """
    np = _np()
    rect = source_rect(src_w, src_h, out_w, out_h)
    keep = rect.inset(keep_inset)
    mask = band_mask(out_w, out_h, rect, seam_inset)
    fsz = out_w * out_h * 3
    gen_cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", gen_path, "-vf",
               f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase:flags=lanczos,"
               f"crop={out_w}:{out_h},setsar=1",
               "-frames:v", str(frames), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    trim = f"trim=start_frame={int(start_frame)},setpts=PTS-STARTPTS," if start_frame else ""
    if source_is_canvas:
        fit = (f"scale={out_w}:{out_h}:flags=lanczos,"
               f"crop={rect.w}:{rect.h}:{rect.x}:{rect.y}")
    else:
        fit = f"scale={rect.w}:{rect.h}:flags=lanczos"
    src_cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", source_path, "-vf",
               f"{trim}fps={fps:g},{fit},"
               f"tpad=stop_mode=clone:stop={frames},setsar=1",
               "-frames:v", str(frames), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    enc = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}", "-r", f"{fps:g}", "-i", "-"]
    if audio:
        enc += ["-ss", f"{audio_offset_sec:.4f}", "-i", source_path, "-map", "0:v", "-map", "1:a?",
                "-c:a", "aac", "-b:a", "192k", "-shortest"]
    if lossless:
        enc += ["-c:v", "libx264", "-qp", "0", "-preset", "veryfast", "-pix_fmt", "yuv444p"]
    else:
        enc += ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    enc.append(dest)
    rsz = rect.w * rect.h * 3
    g = subprocess.Popen(gen_cmd, stdout=subprocess.PIPE)
    s = subprocess.Popen(src_cmd, stdout=subprocess.PIPE)
    e = subprocess.Popen(enc, stdin=subprocess.PIPE)
    written, last_gen = 0, None
    try:
        for _ in range(frames):
            sb = s.stdout.read(rsz)
            if len(sb) < rsz:
                break
            gb = g.stdout.read(fsz)
            if len(gb) == fsz:
                last_gen = np.frombuffer(gb, np.uint8).reshape(out_h, out_w, 3)
            if last_gen is None:
                raise RuntimeError("the generated clip has no frames")
            src = np.frombuffer(sb, np.uint8).reshape(rect.h, rect.w, 3)
            canvas = last_gen.copy()
            canvas[rect.y:rect.y + rect.h, rect.x:rect.x + rect.w] = src
            e.stdin.write(compose_frame(last_gen, canvas, mask, keep).tobytes())
            written += 1
    finally:
        try:
            e.stdin.close()
        except Exception:                                         # noqa: BLE001
            pass
        e.wait()
        for p in (g, s):
            try:
                p.stdout.close()
            except Exception:                                     # noqa: BLE001
                pass
            p.kill()
            p.wait()
    if e.returncode != 0:
        raise RuntimeError(f"writing {dest} failed (ffmpeg exit {e.returncode})")
    if written == 0:
        raise RuntimeError("no frames were composited")
    return {"frames": written}


# Painted fine texture (a speckled floor, grass) shimmers from frame to frame
# about seven times more than the real picture next to it: measured mean frame
# difference 3.4 in the painted band against 0.44 in the source band of the
# same clip. An adaptive temporal average over 9 frames, on the painted bands
# only, brings that to ~1.0 and keeps real motion (it averages only pixels that
# barely change). The source rectangle is never filtered.
STEADY = "atadenoise=0a=0.06:0b=0.12:1a=0.06:1b=0.12:2a=0.06:2b=0.12:s=9"


def steady_filter(out_w: int, out_h: int, src_w: int, src_h: int) -> str:
    """The filter graph that steadies the painted bands of an out_w x out_h
    frame whose middle is the source. Empty when there are no bands."""
    h = vr._even(out_w * src_h / src_w)
    top = (out_h - h) // 2
    top -= top % 2
    bot = top + h
    bot += bot % 2
    if top < 16 or out_h - bot < 16:
        return ""
    return (f"[0:v]split=3[o][t][b];[t]crop={out_w}:{top}:0:0,{STEADY}[t2];"
            f"[b]crop={out_w}:{out_h - bot}:0:{bot},{STEADY}[b2];"
            f"[o][t2]overlay=0:0[x];[x][b2]overlay=0:{bot}[v]")


def join_parts(parts: list[str], source_path: str, dest: str, *, fps: float = 24.0,
               crf: int | str = 18, audio: bool = True, ffmpeg: str = "ffmpeg",
               steady: tuple[int, int, int, int] | None = None) -> None:
    """Concatenate the finished windows (lossless intermediates) into the
    delivered H.264 file, with the source's own soundtrack. `steady` =
    (out_w, out_h, src_w, src_h) steadies the painted bands (steady_filter)."""
    import os
    import tempfile
    fd, lst = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as f:
        for p in parts:
            f.write("file '" + p.replace("'", "'\\''") + "'\n")
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
            "-i", lst]
    def build(graph: str) -> list[str]:
        a = list(args)
        if audio:
            a += ["-i", source_path]
        if graph:
            a += ["-filter_complex", graph]
        if audio:
            a += ["-map", "[v]" if graph else "0:v", "-map", "1:a?", "-c:a", "aac",
                  "-b:a", "192k", "-shortest"]
        elif graph:
            a += ["-map", "[v]"]
        return a + ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-r", f"{fps:g}",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", dest]

    graph = steady_filter(*steady) if steady else ""
    try:
        r = subprocess.run(build(graph), capture_output=True, text=True, errors="replace")
        if r.returncode != 0 and graph:
            # An ffmpeg build without the filter must not cost the whole render.
            r = subprocess.run(build(""), capture_output=True, text=True, errors="replace")
    finally:
        os.unlink(lst)
    if r.returncode != 0:
        raise RuntimeError(f"joining the outpainted windows failed: {r.stderr[-300:]}")
