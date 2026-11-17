# Phosphene 5.0.34

Fixes, free for everyone:
- Training no longer stops at the caption check when your captions don't name the trigger word: the trigger word is added to every caption and the run goes on.
- Covers of busy songs no longer stop with "the transcription came out incomplete": the cover goes on from the score it read.
- When FLUX 3 stops, the message names the real reason (disk, download or verify) instead of showing a progress line.
- When a Mac cannot reach huggingface.co, image makes say so in plain words and what to do, instead of a bare "exit 1".
- When a model file cannot be opened (missing, or its drive unplugged), the card says to reconnect the drive or press Repair in Models, instead of a Retry that fails again.
- The Pro window's Founding card shows "Best value · first 100 buyers" in all ten Studio languages. Prices and plans are unchanged.
- Release checks: a stale style-sheet comment is gone, and a check that reads a proof file on an unreachable drive now stops after 10 seconds instead of hanging.

Mac app: no new app to download; the app picks up Studio 5.0.34 on its next update check (or choose Check for Updates). Pinokio: press Update, then restart.
