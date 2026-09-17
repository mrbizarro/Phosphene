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
      html: "<b>Installing YuE2 (the music engine, ~11 GB).</b><br>Writes a full song — vocals, arrangement and all — from lyrics and a style description, and sits beside LTX and Hailuo H3 as a peer. Needs a 24 GB+ Apple Silicon Mac. Resumable if interrupted."
    } },
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
