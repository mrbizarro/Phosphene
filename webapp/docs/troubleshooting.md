# Troubleshooting

When Phosphene refuses a render, it says why and what to do. These are the messages people actually see.

## Memory and this Mac's tier {#memory}

The health chip in the header shows memory at a glance; click it for the **Tier**, **Memory**, **Helper**, **Models**, **Queue** and **Render** rows. Click **Tier** to see what this Mac can run.

- *"Helper killed by the OS — out of memory"* — macOS stopped the render. Close memory-heavy apps (browsers, Slack, the iOS Simulator) and try again, or switch Quality to **Quick**, which uses about half the memory.
- *"Image pre-flight: … needs a N GB Mac"* — that image engine does not fit this Mac. Pick **Auto** or a lighter preset. *"… needs ~N GB free"* means it fits, but other apps are holding the memory right now.
- *"Training needs at least 24 GB of memory"* — character training cannot run on this Mac.

### Hailuo H3 on a 36–60 GB Mac {#h3-compact}

H3's full engine needs 60 GB. From 36 GB it runs on its compact Q8 engine, which has to be built once:

> *"Hailuo H3 runs on this Mac — on its reduced-RAM lane … Run 'Install Hailuo H3' from the Phosphene sidebar in Pinokio — it builds that engine locally (~5 minutes, ~22 GB on disk, no extra download)."*

Do that, then choose Settings → **Hailuo H3 model** → Automatic or Compact. Below 36 GB, H3 is not available: render on LTX, which serves every mode.

## A model or add-on is not downloaded {#missing}

- *"Extend needs the LTX-2.5 High add-on (the Q8 model), which isn't downloaded on this Mac yet"* — the same for Keyframes and High quality. Install it from the Models window (click the health chip, then **Models**).
- *"Upscale ×2 needs the LTX-2.5 Pixel Spatial Upscaler adapter"* — download it from the Models window, then render again.
- *"Stopped before rendering — the model weights are incomplete"* — a download was interrupted. Open the Models window and resume it. Settings → **Verify model files** checks every file and offers a re-download of any that are damaged.

## "Click Update" — an engine older than the panel {#stale-engine}

*"This install's vendored engine predates LTX-2.5's text encoder … Click Update — and if the first click only moves the panel, click it once more."*

The panel was updated but the engine underneath was not. Click **Update** in Pinokio's Phosphene sidebar. If nothing seems to change, click it a second time: an update started by an old version updates itself first. For H3, *"the installed Hailuo H3 runner is behind this panel"* means: re-run **Install Hailuo H3** — every weight already on disk is kept.

If you updated and the panel still behaves like the old version, it is still running the old code. The version pill in the header then reads **Restart Phosphene** — click it, or click **Stop**, then **Start**, in Pinokio.

## The GPU watchdog {#gpu-watchdog}

*"the macOS GPU watchdog killed a Metal command buffer"* — macOS itself stopped a GPU task that ran too long. It is a driver-level kill, not a Phosphene bug report. Phosphene retries the prompt encoding at a shorter length for the rest of the session. If it keeps happening, the message links to the GitHub issue where chip, macOS version and the crash log help most.

## The queue after a restart {#queue-restart}

Restarting the panel resumes the queue: a queue that was paused when the panel stopped starts again on its own, and the log says so.

## Reading the log {#logs}

The **Logs** tab in the bottom panel shows the render log. When a render dies, the last line that starts with `step:` names the stage it reached — that line is the most useful thing to include in a report. Pinokio's **Terminal** shows the same output.

## Reporting an issue {#report}

The bug button in the header opens **Report a bug**: it fills in the version, your Mac's details and the last 50 log lines, and opens a GitHub issue in a new tab for you to finish — nothing is sent until you submit it there. Issues live at [github.com/mrbizarro/phosphene/issues](https://github.com/mrbizarro/phosphene/issues).
