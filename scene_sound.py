"""Post-render scene sound for LTX Speech → Lip-sync.

Reuse the installed distilled LTX joint AV model for an ambience/SFX take.
Its disposable picture is not a video-to-audio constraint: events are matched
to the prompt, not guaranteed to land on a particular frame. The original
voice alone drives A2V; neither this bed nor the final mix goes to its encoder.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


def is_speech_audio(audio: str) -> bool:
    try:
        meta = json.loads(Path(str(audio) + ".json").read_text())
        return isinstance(meta, dict) and meta.get("engine") == "speech"
    except (OSError, ValueError):
        return False


def enabled(params: dict, audio: str, version: str) -> bool:
    if version != "ltx25" or params.get("engine", "ltx") != "ltx":
        return False
    choice = str(params.get("a2v_scene_sound", "auto")).strip().lower()
    if choice in ("0", "off", "false", "no"):
        return False
    return choice in ("1", "on", "true", "yes") or is_speech_audio(audio)


def sound_prompt(scene: str, override: str = "") -> str:
    # Quoted dialogue must not become a second performance in the bed.
    context = re.sub(r'"[^"\n]*"|“[^”\n]*”', "", override or scene)
    context = re.sub(r"\b(?:speaks?|says?|sings?|delivers?)\b[^.!?]*[.!?]?", "", context,
                     flags=re.I)
    context = " ".join(s for s in re.split(r"(?<=[.!?])\s+", context)
                       if not re.search(r"lip.sync|consonants|mouth|lips|supplied voice", s, re.I))
    context = " ".join(context.split())[:1800]
    return (
        "An establishing view of the environment, with the camera looking away from people. "
        "Environmental field recording: layered natural ambience and physical sound effects "
        "from the surroundings. " + context + " "
        "Soundtrack: only the environment, wind, surfaces and objects appropriate to this place. "
        "No human voices, no dialogue, no singing, no narration, no music."
    )


def generate_bed(*, helper, run, ffmpeg, model_dir, output: Path, prompt: str,
                 frames: int, fps: float, seed, job_id: str) -> dict:
    """Serial helper call under the parent's GPU lock; no additional weights."""
    scratch = output.with_name("." + output.stem + ".scene.mp4")
    bed = output.with_name(output.stem + ".scene.wav")
    try:
        result = helper.run({"action": "generate", "id": job_id, "params": {
            "mode": "t2v", "model_dir": str(model_dir), "prompt": prompt,
            "output_path": str(scratch), "width": 512, "height": 320,
            "frames": frames, "frame_rate": fps, "steps": 8, "seed": seed,
            "loras": [], "accel": "off", "upscale": "off",
        }})
        run([str(ffmpeg), "-y", "-i", str(scratch), "-vn", "-ac", "2",
             "-ar", "48000", "-c:a", "pcm_s16le", str(bed)], "Scene sound stem")
        return {"source": "ltx25_joint_audio", "path": str(bed), "prompt": prompt,
                "width": 512, "height": 320, "steps": 8, "seed": result.get("seed_used", seed),
                "elapsed_sec": result.get("elapsed_sec"), "frame_locked": False}
    finally:
        scratch.unlink(missing_ok=True)


def mix(*, video: Path, voice: str, bed: str, start: float, duration: float,
        ffmpeg, run) -> dict:
    """Copy picture and retain voice timing; sidechain only affects the bed."""
    tmp = video.with_name("." + video.stem + ".sound-mix.mp4")
    graph = (
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{duration},"
        "asetpts=PTS-STARTPTS,asplit=2[voice][key];"
        f"[2:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{duration},"
        "asetpts=PTS-STARTPTS,loudnorm=I=-25:TP=-3:LRA=11,aresample=48000,"
        f"afade=t=in:d=0.12,afade=t=out:st={max(0, duration - .25)}:d=0.25[bed];"
        "[bed][key]sidechaincompress=threshold=0.02:ratio=5:attack=12:release=350:"
        "knee=4:makeup=1[ducked];"
        "[voice][ducked]amix=inputs=2:normalize=0:duration=first,"
        "alimiter=limit=0.891:level=false:latency=true[mix]"
    )
    try:
        run([str(ffmpeg), "-y", "-i", str(video), "-ss", str(max(0.0, start)),
             "-i", str(voice), "-i", str(bed), "-filter_complex", graph,
             "-map", "0:v:0", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac",
             "-b:a", "256k", "-ar", "48000", "-t", str(duration),
             "-movflags", "+faststart", str(tmp)], "Scene sound mix")
        os.replace(tmp, video)
    finally:
        tmp.unlink(missing_ok=True)
    return {"voice": voice, "voice_start_sec": start, "duration_sec": duration,
            "bed_lufs": -25, "duck_ratio": 5, "duck_threshold": .02,
            "attack_ms": 12, "release_ms": 350, "video_stream_copied": True}
