"""Make it vertical, the Free layer: a blurred-fill reframe.

A landscape clip is put whole in the middle of a tall canvas. The bands above
and below are a blown-up, blurred, slightly darkened copy of the same clip, so
nothing is cropped and nothing is invented. It is plain ffmpeg: deterministic,
runs in seconds, and is the fallback when the AI fill (outpaint) is not
installed or fails.

This file builds the geometry and the filter graph only; the queue job that
runs it lives in mlx_ltx_panel.py (`run_vertical_export_job_inner`).
"""
from __future__ import annotations

# ratio key -> (width units, height units, label)
RATIOS: dict[str, tuple[int, int, str]] = {
    "9:16": (9, 16, "Vertical 9:16"),
    "4:5": (4, 5, "Portrait 4:5"),
    "1:1": (1, 1, "Square 1:1"),
}
DEFAULT_RATIO = "9:16"
# Output width. 720 keeps the full source width of a 720p clip inside the frame
# (1280x720 is scaled to 720x405 in the middle), and 720x1280 is what social
# apps expect. A wider source is not scaled up past this.
OUT_W = 720


def _even(n: float) -> int:
    return 2 * int(n / 2 + 0.5)


def target_size(ratio: str, src_w: int, src_h: int) -> tuple[int, int]:
    """Output (w, h) for a ratio, both even. Raises ValueError when the clip is
    already as tall as the ratio asks for (nothing to fill)."""
    if ratio not in RATIOS:
        raise ValueError(f"unknown ratio {ratio!r}; choose one of {', '.join(RATIOS)}")
    if src_w <= 0 or src_h <= 0:
        raise ValueError("the clip has no readable size")
    rw, rh, _ = RATIOS[ratio]
    if src_w * rh <= src_h * rw:
        raise ValueError(
            "This clip is already vertical (or as tall as that shape) — "
            "there is nothing to fill above and below.")
    w = OUT_W
    return w, _even(w * rh / rw)


def build_filter(src_w: int, src_h: int, out_w: int, out_h: int) -> str:
    """The -filter_complex graph: blurred cover background + sharp fit on top.

    The foreground is scaled to the full output width (the source is wider than
    the target by construction) and centred. The background is the same clip
    scaled to COVER the canvas, cropped, boxblurred and dimmed so the sharp
    picture stays the subject.
    """
    fg_h = _even(out_w * src_h / src_w)
    # Blur radius scales with the canvas so 720 wide and 1080 wide look alike.
    blur = max(8, out_w // 24)
    return (
        f"[0:v]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h},boxblur={blur}:3,eq=brightness=-0.08:saturation=1.1[bg];"
        f"[fgsrc]scale={out_w}:{fg_h}:flags=lanczos[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]"
    )


def ffmpeg_args(src: str, dest: str, src_w: int, src_h: int, ratio: str, *,
                crf: int | str = 18, preset: str = "medium", has_audio: bool = True,
                ffmpeg: str = "ffmpeg") -> list[str]:
    out_w, out_h = target_size(ratio, src_w, src_h)
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
            "-filter_complex", build_filter(src_w, src_h, out_w, out_h),
            "-map", "[v]"]
    if has_audio:
        args += ["-map", "0:a?", "-c:a", "copy"]
    args += ["-c:v", "libx264", "-crf", str(crf), "-preset", preset,
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", dest]
    return args
