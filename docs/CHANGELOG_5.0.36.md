# Phosphene 5.0.36

Fix, free for everyone:
- Upscale x2 on Macs with less than 96 GB of memory (64 GB Macs included) no longer runs for a long time and then gets stopped by macOS for memory. The x2 result is now held to 2048x1152, the size that fits; a clip that would come out bigger is refused right away, with the source size to use (1024x576 or smaller). Macs with 96 GB or more keep the 2560x1440 limit.

Mac app: no new app to download; the app picks up Studio 5.0.36 on its next update check (or choose Check for Updates). Pinokio: press Update, then restart.
