# Phosphene Bench: reviewer guide

PB1 is a fixed local workload, not a score or a quality ranking. Use a supplied Bench-enabled build. Record its version and edition with every result. The public release and leaderboard may not yet include Bench.

**Run it.** Plug in the Mac, turn Low Power Mode off, let it cool, and close GPU-heavy apps. Open Settings → Phosphene Bench. Use the model Install buttons if offered, then wait for downloads to finish. Keep at least 15 GB free after installation. Leave Output format at Standard. Finish or clear the queue, then click Run Bench. Leave the Mac idle until the result appears. Save the PNG and JSON; Copy results gives a text summary. Stop keeps an incomplete record. Do not submit results unless you intend to share them.

**What runs.** An untimed 1-second LTX-2.5 warm-up, Quick 5 seconds with sound (640×448), Balanced 5 seconds with sound (1024×576, or 768×448 below 48 GB), then four 1024×1024 FLUX.2 [klein] 4B images. H3 Draft is optional and runs only if already installed, supported and licensed. Prompts and seed are fixed. Enhancement, upscaling and acceleration are off.

**Read the numbers.** Lower seconds means faster. The card's large number is Balanced alone, not total suite time. Each time covers the app's render job through saving the output. Warm-up is separate; the image step includes image-engine startup. Peak memory is whole-Mac use sampled twice a second, not Phosphene's allocation. “Added” is the rise above the pre-step level; engine peak, when available, is a separate measure. Swap, heat, power settings and other apps affect results. Compare matching PB1 fingerprint, edition, canvas and output preset. Keep all repeats and report the median, not the fastest take. All three core steps must finish for a complete result.

**Time to allow.** On an M4 Max, 40-core GPU, 64 GB, PB1 measured 4m 47s from start to finish on October 23: warm-up 28s, Quick 68s, Balanced 136s, images 53s, H3 skipped. A prior run on the same Mac took about 5 minutes. Allow 5–10 minutes there, excluding installation and optional H3.

For other chips, PB1 totals have not been measured here. These local October 22 fleet medians are planning context for the video steps only, from everyday renders with different settings. They are not PB1 predictions or an apples-to-apples chip ranking. RAM and sample mix differ.

| Chip | Quick / Balanced, 5-second video |
| --- | --- |
| M1 Pro / Max / Ultra | 5m50s / unknown; 3m20s / 5m30s; 1m35s / 3m |
| M2 Pro / Max / Ultra | unknown; 1m55s / 4m; unknown / 2m30s |
| M3 Pro / Max / Ultra | 7m50s / unknown; 2m / 4m25s; 55s / 1m50s |
| M4 / Pro / Max | 7m / unknown; 2m55s / 5m50s; 2m / 2m55s |
| M5 / Pro / Max | 3m45s / unknown; 1m20s / 2m30s; 50s / 1m20s |

Allow additional time for warm-up, four images and any H3 step. Base M1–M3 and configurations absent above have no verified time here. Measure them before quoting a PB1 total. Source: the held site's `src/data/bench.json`, fleet section; PB1 JSON remains the result of record.
