# Phosphene 5.0.35

Fixes, free for everyone:
- Fewer LTX renders stopped by the Mac's graphics watchdog while reading the prompt on M2 Pro and M2 Max Macs. Other Macs render exactly as before.
- Hailuo H3 on 16 to 24 GB Macs no longer falls back to the slow one-piece video finish when the faster finish fits: the memory check stopped over-counting that last step. Same frames, done sooner.
- Engine checks: a failing Hailuo H3 engine test can no longer be reported as a pass.

Mac app: no new app to download; the app picks up Studio 5.0.35 on its next update check (or choose Check for Updates), and updates its Hailuo H3 engine by itself (no new model download). Pinokio: press Update, then restart.
