# Phosphene 5.0.76

Fixes:
- Downloads work in mainland China. When your Mac cannot reach Hugging Face (it is blocked in mainland China), Phosphene now downloads Hailuo H3, the FLUX pictures and YuE2 music from the China mirror (hf-mirror.com) instead. It decides in a few seconds, says so in the download log ("Hugging Face unreachable, using the China mirror"), and checks every file against Hugging Face's own record before using it. Downloads that stop pick up where they left off. Models that need a Hugging Face login still come only from Hugging Face.
- Mac app: after a Studio update, the video and picture tools you already have are kept instead of being set up again.

Mac app: it picks up this update by itself the next time it starts. Pinokio: press Update, then restart.
