// YuE2 is a peer engine. Python 3.12 and mlx==0.32.2 require its own venv;
// LTX stays on Python 3.11 / mlx==0.31.1. Repairs keep verified weights.
const fs = require("fs")
const path = require("path")
const MUSIC_PIN = fs.readFileSync(path.join(__dirname, "scripts/music/engine_pin.txt"), "utf8").trim()
if (!/^[0-9a-f]{40}$/.test(MUSIC_PIN)) throw new Error("Invalid YuE2 engine pin")
module.exports = {
  requires: { bundle: "ai" },
  run: [
    { method: "notify", params: {
      html: "<b>Installing YuE2 (the music engine, ~11 GB).</b><br>Writes a full song — vocals, arrangement and all — from lyrics and a style description, and sits beside LTX and Hailuo H3 as a peer. Needs a 16 GB+ Apple Silicon Mac. Resumable if interrupted."
    } },
    // Pinokio 8.2 ignores a step's exit code and stops only on /error:/i or
    // /errno /i in its output: every script here (and music_fetch.py, in its
    // fetch mode) prints a "FATAL error:" line when it fails, which is what
    // ends this install. scripts/check_pinokio_scripts.js holds that.
    // 4.19.8: YuE2's licence (CC BY-NC 4.0 + the individual-creator
    // permission) is accepted in the panel first; a NEW install stops here
    // until it is. An install that already has the weights continues.
    { method: "shell.run", params: { message: "bash scripts/pinokio/music_licence_gate.sh" } },
    { method: "shell.run", params: { message: "bash scripts/pinokio/music_preflight.sh" } },
    { method: "shell.run", params: { message: "bash scripts/pinokio/music_clone.sh" } },
    // Both lanes read and validate engine_pin.txt.
    { method: "shell.run", params: {
      message: "bash scripts/pinokio/music_checkout.sh \"${LTX_MUSIC_ROOT:-$PWD/yue2-mlx}\""
    } },
    { method: "shell.run", params: { message: "bash scripts/pinokio/music_venv.sh \"${LTX_MUSIC_ROOT:-$PWD/yue2-mlx}\"" } },
    { method: "shell.run", params: { message: "bash scripts/pinokio/music_sync.sh \"${LTX_MUSIC_ROOT:-$PWD/yue2-mlx}\"" } },
    // Preserve Pinokio's HF_HOME rather than substituting a different cache.
    { method: "shell.run", params: { message: "\"${LTX_MUSIC_ROOT:-$PWD/yue2-mlx}/.venv/bin/python\" scripts/pinokio/music_fetch.py --min-free-gb 14" } },
    { method: "notify", params: {
      html: "<b>YuE2 ready.</b><br>Open Audio → Compose to write a song. Generated with YuE2 by Multimodal Art Projection · MLX port by vanch007."
    } }
  ]
}
