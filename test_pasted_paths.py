"""Pasted paths, tolerant decoding and useful failure lines (v4.14.3).

Three fleet failures share one shape — the panel refused work over punctuation
or died on a byte, and told the user something that named no cause:

  * `control video not found: "'Macintosh HD<path> Tinder 1.mp4'"` — a Finder
    "Copy as pathname" (quotes + startup-disk name) and a Terminal drag
    (backslash-escaped spaces), pasted into the Control video field.
  * `'utf-8' codec can't decode byte 0xb0 in position 37` — a subprocess line
    that was not UTF-8, decoded strictly, killing the render that spawned it.
  * `helper failed to start: {'event': 'exit', 'reason': 'python_normal_exit'}`
    — the top failure in the fleet, whose text is bookkeeping: the helper
    printed a traceback and exited, and the message mentioned neither.

CPU only, no renders.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile

import pytest

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="paste-test-state-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
import mlx_ltx_panel as P


# ---- what people paste -------------------------------------------------------

@pytest.mark.parametrize("pasted,expect", [
    ("'/Users/me/clips/Tinder 1.mp4'", "/Users/me/clips/Tinder 1.mp4"),
    ('"/Users/me/clips/Tinder 1.mp4"', "/Users/me/clips/Tinder 1.mp4"),
    ("Macintosh HD/Users/me/clips/Tinder 1.mp4", "/Users/me/clips/Tinder 1.mp4"),
    ("'Macintosh HD/Users/me/Els Rechts/Tinder 1.mp4'", "/Users/me/Els Rechts/Tinder 1.mp4"),
    ("/Volumes/Macintosh HD/Users/me/a.mp4", "/Users/me/a.mp4"),
    ("Macintosh HD:Users:me:a.mp4", "/Users/me/a.mp4"),
    ("/Users/me/Els\\ Rechts/Tinder\\ 1.mp4", "/Users/me/Els Rechts/Tinder 1.mp4"),
    ("file:///Users/me/clips/Tinder%201.mp4", "/Users/me/clips/Tinder 1.mp4"),
    ("file://localhost/Users/me/a.mp4", "/Users/me/a.mp4"),
    ("  /Users/me/a.mp4\n", "/Users/me/a.mp4"),
    ("/Users/me/a.mp4'", "/Users/me/a.mp4"),
    ("'Macintosh HD/Users/me/Els\\ Rechts/Tinder\\ 1.mp4'", "/Users/me/Els Rechts/Tinder 1.mp4"),
])
def test_normalize_pasted_path(pasted, expect):
    assert P.normalize_pasted_path(pasted) == expect


def test_tilde_becomes_the_home_directory():
    assert P.normalize_pasted_path("~/a.mp4") == str(Path.home() / "a.mp4")


def test_empty_and_plain_values_are_untouched():
    assert P.normalize_pasted_path("") == ""
    assert P.normalize_pasted_path("   ") == "   "
    assert P.normalize_pasted_path("/Users/me/a.mp4") == "/Users/me/a.mp4"
    assert P.normalize_pasted_path("panel_uploads/ref.png") == "panel_uploads/ref.png"
    assert P.normalize_pasted_path(None) is None


def test_a_real_file_always_wins_over_the_rewrite(tmp_path):
    """A file whose NAME contains a backslash is not an escaped space."""
    odd = tmp_path / "take\\ 2.mp4"
    odd.write_bytes(b"x")
    assert P.normalize_pasted_path(str(odd)) == str(odd)


def test_the_boot_volume_is_discovered_not_only_guessed():
    names = P._boot_volume_names()
    assert "Macintosh HD" in names
    for name in names:
        assert os.path.realpath(f"/Volumes/{name}") == "/" or name == "Macintosh HD"


# ---- and where it reaches the job -------------------------------------------

def test_make_job_cleans_every_path_field():
    job = P.make_job({
        "mode": "control",
        "prompt": "a hand turning a page",
        "control_video_path": "'Macintosh HD/Users/me/Els Rechts/Tinder 1.mp4'",
        "image": '"Macintosh HD/Users/me/ref.png"',
        "audio": "file:///Users/me/song%20one.wav",
        "video_path": "/Users/me/Els\\ Rechts/source.mp4",
        "upscale_source_path": "Macintosh HD/Users/me/clip.mp4",
        "restore_video_path": "~/bw.mp4",
        "start_image": "'/Users/me/a.png'",
        "end_image": "'/Users/me/b.png'",
    })
    p = job["params"]
    assert p["control_video_path"] == "/Users/me/Els Rechts/Tinder 1.mp4"
    assert p["image"] == "/Users/me/ref.png"
    assert p["audio"] == "/Users/me/song one.wav"
    assert p["video_path"] == "/Users/me/Els Rechts/source.mp4"
    assert p["upscale_source_path"] == "/Users/me/clip.mp4"
    assert p["restore_video_path"] == str(Path.home() / "bw.mp4")
    assert p["start_image"] == "/Users/me/a.png" and p["end_image"] == "/Users/me/b.png"


def test_make_job_cleans_image_refs():
    job = P.make_job({"mode": "image", "prompt": "a ring road at dusk",
                      "refs": '["Macintosh HD/Users/me/ref.png", "\'/Users/me/two.png\'"]'})
    assert job["params"]["refs"] == ["/Users/me/ref.png", "/Users/me/two.png"]


def test_every_path_field_make_job_reads_is_declared():
    """A path field added to make_job without joining PASTED_PATH_FIELDS is
    the same silent no-op as the allowlist trap it lives next to."""
    src = Path(P.__file__).read_text()
    body = src[src.index("def make_job("):src.index("def _queue_snapshot")
               if "def _queue_snapshot" in src else src.index("def make_job(") + 60000]
    named = set(re.findall(r'"([a-z0-9_]*(?:image|audio|video_path|source_path))": f\(', body))
    missing = {n for n in named if n not in P.PASTED_PATH_FIELDS} - {
        "ingredient_images_json", "stage2_image_conditioning"}
    assert not missing, f"path-ish make_job fields outside PASTED_PATH_FIELDS: {missing}"


# ---- the line that names the cause ------------------------------------------

def test_failure_line_prefers_the_exception_over_pybind_argument_dump():
    tail = [
        "  File \"/x/minimax_h3_mlx/denoise.py\", line 91, in step",
        "    out = mx.matmul(a, b)",
        "TypeError: matmul(): incompatible function arguments. The following argument types are supported:",
        "    1. (a: array, b: array, *, stream: Stream | Device | None = None) -> array",
        "Invoked with types: mlx.core.array, mlx.core.array",
    ]
    assert P.failure_line(tail).startswith("TypeError: matmul()")


def test_failure_line_falls_back_to_the_last_real_line():
    assert P.failure_line(["step 3/8", "[h3] window 1", "killed by the sandbox"]) == "killed by the sandbox"
    assert P.failure_line(["step 3/8", "Window 2/2"]) == "Window 2/2"
    assert P.failure_line([]) == "" and P.failure_line(["", "   "]) == ""


def test_h3_failure_uses_it_and_keeps_a_deep_tail():
    src = Path(P.__file__).read_text()
    assert "_last = failure_line(_h3_tail)" in src
    assert "collections.deque(maxlen=14)" in src[src.index("_h3_tail: collections.deque"):
                                                  src.index("_h3_tail: collections.deque") + 120]


def test_helper_start_failure_says_what_happened_and_what_to_do():
    msg = P._helper_start_failure(
        {"event": "exit", "reason": "python_normal_exit"},
        ["Traceback (most recent call last):",
         '  File "/x/mlx_warm_helper.py", line 1, in <module>',
         "ModuleNotFoundError: No module named 'ltx_core_mlx'"])
    assert msg.startswith("helper failed to start:")          # the fleet series
    assert "quit while loading" in msg
    assert "ModuleNotFoundError: No module named 'ltx_core_mlx'" in msg
    assert "Update" in msg and "Logs" in msg
    assert "python_normal_exit" not in msg
    timeout = P._helper_start_failure(None, [])
    assert timeout.startswith("helper failed to start:") and "never reported ready" in timeout


def test_helper_start_failure_is_classified_as_a_start_failure():
    msg = P._helper_start_failure({"event": "exit"}, ["ImportError: bad venv"])
    assert P._analytics_error_class(msg) == "helper_start_timeout"


# ---- no strict decode of somebody else's bytes -------------------------------

SHIPPED = [
    "mlx_ltx_panel.py", "mlx_warm_helper.py", "image_engine.py", "storyboard_edit.py",
    "storyboard_planner.py", "panel/routes_meta.py", "panel/routes_train.py",
    "scripts/check_output_codec.py", "scripts/join_smooth.py", "scripts/publish_pack_release.py",
]


def test_no_subprocess_pipe_decodes_strict_utf8():
    """`text=True` without `errors=` is a strict UTF-8 decode of another
    program's output. One non-UTF-8 byte from ffmpeg, a trainer or a runner
    then ends the render — the fleet's `'utf-8' codec can't decode byte 0xb0
    in position 37` (42 events, three installs), which no user could act on.
    """
    offenders = []
    for name in SHIPPED:
        lines = (ROOT / name).read_text().split("\n")
        for i, line in enumerate(lines):
            if "text=True" not in line:
                continue
            window = " ".join(lines[max(0, i - 2):i + 3])
            if "errors=" not in window:
                offenders.append(f"{name}:{i + 1}")
    assert not offenders, f"strict-utf8 subprocess pipes: {offenders}"
