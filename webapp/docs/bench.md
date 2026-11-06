# Phosphene Bench

## What it does

Bench renders a fixed suite locally in the free app. PB1 means Phosphene Bench 1. Prompts and seeds are fixed, with no prompt enhancement or upscaling. Changes to prompts, seeds, sizes or steps get a new suite name. Different suites are never mixed.

## The PB1 steps

| Step | Settings |
| --- | --- |
| Engine warm-up | 1-second LTX-2.5 Quick clip. Time recorded, excluded from timed results. |
| LTX-2.5 Quick | 5-second text-to-video clip with sound, 640×448, 121 frames at 24 fps. |
| LTX-2.5 Balanced | 5-second text-to-video clip with sound, 1024×576 or 768×448 below 48 GB memory, 121 frames at 24 fps. |
| FLUX.2 [klein] 4B | Four 1024×1024 stills in one job, 4 steps, 4-bit. Includes image-engine startup. |
| Hailuo H3 Draft | Optional 5-second clip. Requires H3 installed, sufficient memory and accepted licence. |

Warm-up loads the video engine before timing. Quick, Balanced and images are the core steps; all must finish for a complete run. H3 is separate from the headline and never downloaded by Bench.

## What is measured

The app’s render timer measures each step from rendering start to saved file, as on /benchmarks. Memory is sampled twice a second: peak use, increase over the pre-step level, swap and engine peak where reported. Bench records chip, CPU and GPU cores, memory, model identifier, macOS, app version and edition, power source, Low Power Mode, and starting and worst thermal state.

## Running it

Open Settings → Phosphene Bench → Run Bench. Finish or clear queued or running renders first. Use the charger with Low Power Mode off; Bench warns and records otherwise. Allow several minutes, much longer on smaller Macs. Missing models get an Install button; nothing downloads until clicked.

## The result card and JSON

Save a PNG card with Balanced’s time as the headline, save the full JSON record, or copy a plain-text summary. Results stay local unless submitted.

## The leaderboard

Submission is opt-in and requires confirmation. With anonymous usage counts off, nothing is sent. Submission sends every stored complete PB1 run: suite, times, memory, canvases, hardware class, macOS, conditions, app version, edition and random install ID. Never prompts, file names, paths, images or video. Incomplete runs are ineligible.

## Fair comparisons

Compare matching suites, editions and canvases. Conditions affect results. No best-of: each Mac contributes its median; chip-and-memory groups show the median of those medians and Mac count. Free and Pro stay separate. The board refreshes on site rebuilds. Nearby /benchmarks fleet medians provide everyday-render context, not the same test.
