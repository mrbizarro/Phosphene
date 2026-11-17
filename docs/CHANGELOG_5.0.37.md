# Phosphene 5.0.37

Fixes, free for everyone:
- A reference picture from anywhere on your Mac (your Desktop or Downloads, for example) is now copied in and used, instead of the render failing. Files that are not pictures or videos are still refused.
- Upscale x2 works on phone videos again: clips that carry a rotation tag no longer stop with "could not read the size".
- Qwen-Edit picture edits no longer stop with a GPU crash on M1, M2 and M4 Max Macs: the work goes to the GPU in smaller pieces, so macOS stops killing it. The picture comes out the same.
- Picking a FLUX.1 LoRA for Auto or Klein pictures now says right away that it was made for FLUX.1, instead of failing halfway through the render.
- Image-to-video no longer fails when the quick ffmpeg pass on the starting picture stalls; the render carries on with the picture as it is.

Mac app: no new app to download; the app picks up Studio 5.0.37 on its next update check (or choose Check for Updates). Pinokio: press Update, then restart.
