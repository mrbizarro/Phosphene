#!/usr/bin/env python3
"""Child process for H3 "Write it for me" (see h3_rewriter.py).

Reads ONE job as JSON on stdin ({"prompt", "duration", "one_shot", "task"}),
loads the converted rewriter with mlx-lm, writes one reply line prefixed with
the sentinel, and exits — process exit is the only complete way to give MLX's
memory back.

The request format (task prefix + MiniMax's writing guide as the system
message; task type, duration and shot instructions in the user message) is the
one the checkpoint was trained on; the code is ours. Sampling as measured in
R&D L48: temperature 0.7, top-p 0.8, top-k 20, thinking off (thinking cost
32-68 s for no visible gain).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SENTINEL = "@@REWRITER@@ "
GUIDE_FILE = "VIDEO_PROMPT_WRITING_GUIDE_base_en.md"

PREFIX = (
    "You are a professional prompt enhancement engineer for video and audio generation, "
    "with expertise in visual storytelling, shot planning, camera movement, and sound design. "
    "Before enhancing the prompt, carefully interpret the user's intended scene, subjects, actions, "
    "and audiovisual requirements. "
    "Preserve the core meaning, event sequence, and explicit constraints. "
    "Add relevant visual and sound details that enrich the intended scene without changing its "
    "premise or introducing unrelated events. "
    "Rewrite the user's prompt according to the following guide. "
    "Write the entire enhanced prompt in English, regardless of the original prompt's language. "
    "Apply only the specified task type and return only the final prompt.\n\n"
)

MULTI_SHOT = (
    "Choose shots that best express the original intent, using separate shots for distinct subjects, "
    "details, reactions, or meaningful changes in scene, viewpoint, or time.\n"
    "Allocate shot durations according to content and pacing.\n"
)
# L48: left to itself the model cut 19 of 20 five-second clips into two shots.
# "One shot" asks for a single continuous take in the same words the guide
# uses for explicit shot requests, which the model is trained to respect.
ONE_SHOT = (
    "Use exactly one continuous shot for the whole video: [Shot 1] only, with no cuts.\n"
)


def user_message(prompt: str, duration: int, one_shot: bool, task: str = "T2VA") -> str:
    return (
        f"Task type: {task}.\nVideo duration: {int(duration)} seconds.\n"
        + (ONE_SHOT if one_shot else MULTI_SHOT)
        + "Respect explicit shot requests and follow the guide's task-specific, formatting, "
          "and transition rules.\n"
        + f"Original prompt: {prompt}\n"
    )


def system_message(model_dir: Path) -> str:
    return PREFIX + (model_dir / GUIDE_FILE).read_text(encoding="utf-8")


def reply(obj: dict) -> None:
    sys.stdout.write(SENTINEL + json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--max-tokens", type=int, default=1500)
    a = ap.parse_args()
    try:
        job = json.loads(sys.stdin.read() or "{}")
        prompt = str(job.get("prompt") or "").strip()
        if not prompt:
            reply({"error": "empty prompt"}); return 2
        model_dir = Path(a.model)
        import mlx.core as mx
        from mlx_lm import generate, load
        from mlx_lm.sample_utils import make_sampler
        mx.reset_peak_memory()
        t0 = time.time()
        model, tok = load(str(model_dir))
        load_s = time.time() - t0
        text = tok.apply_chat_template(
            [{"role": "system", "content": system_message(model_dir)},
             {"role": "user", "content": user_message(
                 prompt, int(job.get("duration") or 5), bool(job.get("one_shot")),
                 str(job.get("task") or "T2VA"))}],
            tokenize=False, add_generation_prompt=True, enable_thinking=False)
        t1 = time.time()
        out = generate(model, tok, prompt=text, max_tokens=a.max_tokens,
                       sampler=make_sampler(temp=0.7, top_p=0.8, top_k=20))
        final = out.split("</think>")[-1].strip()
        reply({"rewrite": final, "load_seconds": round(load_s, 1),
               "gen_seconds": round(time.time() - t1, 1),
               "peak_gb": round(mx.get_peak_memory() / 1e9, 2)})
        return 0
    except Exception as exc:                                    # noqa: BLE001
        reply({"error": f"{type(exc).__name__}: {exc}"[:300]})
        return 1


if __name__ == "__main__":
    sys.exit(main())
