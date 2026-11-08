# Install, update and disk space

[[pinokio]]
Phosphene runs on Apple Silicon Macs (M1 or newer) through Pinokio. This page explains how to install and update it, how much disk space you need, and what to do if a step fails.
[[/pinokio]]
[[mac-app]]
Phosphene runs on Apple Silicon Macs (M1 or newer). This page explains how to install and update the Mac app, how much disk space you need, and what to do if a step fails.
[[/mac-app]]

[[pinokio]]
## Install with Pinokio {#pinokio}

1. Install [Pinokio](https://pinokio.computer).
2. In Pinokio, open **Discover**, choose **Download from URL** and paste `https://github.com/mrbizarro/phosphene`.
3. Click **Install**. The installer checks your free disk space first. If there is not enough, it stops and shows how much you need and how much you have.
4. When the install finishes, click **Start**, then **Open Panel**. The panel opens at `http://127.0.0.1:8198`.

The app opens after its Python runtime and engine packages are ready, usually in a few minutes. No models download before Start or automatically at first open. Choose what you want to make, then download its model.

> **Note** Pinokio cannot install Phosphene on an Intel Mac or on Windows. Linux support on Apple Silicon is a separate community project that Pinokio does not install. See [Linux (community)](https://github.com/mrbizarro/phosphene/blob/main/docs/LINUX.md).
[[/pinokio]]
[[mac-app]]
## Install the Mac app {#mac-app}

1. Download Phosphene from [phosphene.video](https://phosphene.video) and open it.
2. Review the setup plan before anything downloads. It shows every item and its size, the space needed and your free space.
3. If the studio fits but the models don't, you can set up the studio now and download models later from inside it.
4. Follow the progress for each step under **Getting your studio ready**. Use **Pause setup** and **Resume setup** as needed. You can quit safely; setup picks up where it left off next time.
5. Once the studio opens, use **Studio → Show Setup Details** whenever you want to see setup again.

You can start creating as soon as the studio opens, once the models you need are downloaded. Extras download when a feature first needs them, as listed below.

If you installed Phosphene through Pinokio on this Mac, the app reuses its models: **Reuse from Pinokio · no download**.

You need an Apple Silicon Mac (M1 or newer).
[[/mac-app]]

## What gets downloaded, and how much space you need {#disk}

[[pinokio]]
| Part | Download | When |
|---|---|---|
| **App runtime**: Python and engine packages | About 0.5 GB; package versions can change this | Before **Start**. Allow **18 GB free**, including 15 GB kept free |
| **LTX-2.5 Q4 video models**, including the prompt encoder | About 27.5 GB | Click **Download video models** inside the app. Recommended for your first video |
| **Feature extras**: Gemma 3 for Enhance and the Storyboard planner, plus adapters for Upscale & Face Fix, Motion Control, Ingredients and Colorize | 10.7 GB if you choose all of them | Download only the ones you use, from the feature or **Models** |
| **Image models** | Shown beside the selected model | On the first Generate. FLUX.2 [klein] 4B is a compact starting point, about 4.3 GB |
| **LTX-2.5 Q8 weights**, for trained characters and voices | 30.02 GB | optional, from **Settings → Models** or the Pinokio sidebar |
| **High add-on**, for High quality, Keyframes and Extend on LTX | 29.50 GB | Optional, from the Models window (click the health chip, then **Models**) |
| **Hailuo H3**, the second video engine | about 75 GB | optional, from the Pinokio sidebar. See [Hailuo H3](#docs/hailuo-h3/install) |
| **Music engine (YuE2)** | about 11 GB | optional, from the Audio tab or the Pinokio sidebar. See [Music Studio](#docs/music/install) |
| **LTX-2.3**, needed only to *train* a character | about 20 GB, plus about 21 GB for the trainer | offered when you open **Train Character** |
[[/pinokio]]
[[mac-app]]
| Part | Download | When |
|---|---|---|
| **Base install**: LTX-2.5 video engine and its files for reading prompts and showing live previews | about 28 GB | During setup, or later from inside the studio if you chose to download models later. The setup plan shows the space it needs |
| **Background extras**: Gemma 3 (for **Enhance** and the Storyboard planner) and four small add-ons (LoRAs) for Upscale & Face Fix, Motion Control, Ingredients and Colorize | 10.7 GB | When a feature first needs one: it says so and offers **Download now**. Also from the Models window |
| **LTX-2.5 Q8 weights**, for trained characters and voices | 30.02 GB | Optional, from **Settings → Models** |
| **High add-on**, for High quality, Keyframes and Extend on LTX | 29.50 GB | Optional, from the Models window (click the health chip, then **Models**) |
| **Hailuo H3**, the second video engine | about 75 GB | Not available in the Mac app yet. Render on LTX. See [Hailuo H3](#docs/hailuo-h3/install) |
| **Music engine (YuE2)** | about 11 GB | Optional, from **Audio → Compose**. Click **Install music engine**. See [Music Studio](#docs/music/install) |
| **LTX-2.3**, needed only to *train* a character | about 20 GB, plus about 21 GB for the trainer | Offered when you open **Train Character** |
[[/mac-app]]

At a connection speed of 100 Mbps, the video models take about 37 minutes to download. At 300 Mbps, it takes about 12 minutes; at 1 Gbps, about 4 minutes. The app can stay open while models download. The model card shows bytes received, the current file and any remaining packs. **Stop download** stops the whole video download; click **Download video models** again to resume. Downloaded files stay on disk. Downloads from Models keep 15 GB free and wait if space runs low.

If a feature needs an extra that has not finished downloading, it shows a download message or offers **Download now**. It does not fail without a message.

> **Tip** You can add a Hugging Face access token under **Settings** for roughly ten times faster downloads. HDR is the only add-on that requires this token. Everything else works without it.

## Check your Mac's memory {#memory}

Your Mac's memory determines which quality settings and engines you can use. Phosphene checks this automatically. To see what your Mac can run, click the health chip in the header, then **Tier**.

| Feature | Memory it needs |
|---|---|
| LTX video and Images | Any Apple Silicon Mac. Below 48 GB, you get smaller video sizes. LTX's High quality, Start & end frame and Extend are hidden. Macs with 24 GB or less automatically use a low-memory video path. |
| Music Studio | 16 GB or more. 24 GB or more recommended for long songs with other apps open |
| Training a character | 24 GB or more |
| Hailuo H3 | 16 GB or more (Draft and Standard from 16 GB, High from 18 GB). With 60 GB or more, you can also choose the full engine |
| LTX High · 720p | A 64 GB Mac; memory use peaks near 50 GB |

Before a long render, close apps that use a lot of memory, such as browsers with many tabs, Slack or the iOS Simulator. A render is the process of creating your video or audio. If your Mac runs out of memory, it stops the render. See [Troubleshooting](#docs/troubleshooting/memory).

[[pinokio]]
## The Pinokio sidebar {#sidebar}

Select Phosphene in Pinokio to see these actions in the left sidebar. The available actions depend on what is installed and whether the panel is running.

- **Install** / **Resume Install**: install the required files. **Resume Install** appears if the last attempt did not finish. Files that finished downloading are kept.
- **Start**, **Open Panel**, **Terminal**: click **Start** to run Phosphene, then **Open Panel** to use it. **Terminal** shows its activity log.
- **Update**: see [Updating](#update).
- **Outputs**, **Models**, **Uploads**: open those folders in Finder.
- **Install Hailuo H3 (second video engine, ~75 GB)**: after installation, this becomes **Repair Hailuo H3** or **Update Hailuo H3 runner**. Both keep the downloaded model files.
- **Build Hailuo H3 compact engine**: appears on a 16–59 GB Mac when the model files are downloaded but the compact engine is not ready. It takes about 5 minutes and downloads nothing.
- **Install the music engine (YuE2, ~11 GB)**: after installation, this becomes **Repair the music engine**, which keeps the downloaded model files.
- **Reset engine (keeps models & outputs)**: rebuild the LTX engine and the software it needs. This keeps your models and finished work. It does not affect Hailuo H3; to repair H3, use **Repair Hailuo H3**.
[[/pinokio]]
[[mac-app]]
## The Phosphene menus {#menus}

Use the menus at the top of the screen to manage Phosphene.

- **Phosphene → Check for Updates…** updates the studio. Your projects, models and renders stay where they are.
- **Phosphene → About Phosphene** shows app details.
- **Phosphene → Licence…** shows your Phosphene licence (Free or Pro).
- **Studio → Reload Studio** reloads the studio.
- **Studio → Restart Engine** restarts the studio's engine.
- **Studio → Open Outputs Folder** opens your renders folder.
- **Studio → Show Setup Details** shows the setup screen again.
- **Help → Phosphene Help** opens help.
- **Help → Report a Bug…** lets you report a problem.
- **Help → Open Logs Folder** opens the log files folder.
- **Quit Phosphene** (⌘Q) quits the app.

Optional model packs are in **Settings → Models**. Install or repair the music engine in **Audio → Compose**. Hailuo H3 and the image-engine pack are not available in the Mac app yet.
[[/mac-app]]

## Update Phosphene {#update}

[[pinokio]]
- The version pill in the header shows **Up to date**, or **Update to** followed by a newer version. When an update is available, click **Update now** in the banner to install it, or **Later** to wait.
- Click **Update** in the Pinokio sidebar to update everything, including the supporting software. Your queue, settings, models and finished work are kept.
- After an in-app update, the banner reads *"Updated to … — restart to finish"*. Click **Stop**, then **Start**, in Pinokio.
- If you update from a very old version and only the panel seems to change, click **Update** once more. The first update replaces the updater itself.
- If Pinokio's **Update** stops in its first second (versions 4.19.6 and older can do this), click **Update now** in Phosphene's own banner or version pill instead, then restart. See [Update stops right away](#docs/troubleshooting/update-stops).
- If the panel still behaves like the old version, it needs a restart. The version pill then reads **Restart Phosphene**. Click it, or click **Stop** and then **Start** in Pinokio.
- Hailuo H3 and the music engine have separate supporting software. If the panel says either needs an update, click that engine's **Update** or **Repair** entry in Pinokio's sidebar. Your downloaded model files are kept.
[[/pinokio]]
[[mac-app]]
- The version pill shows your version and commit. Click it to copy your build details for a bug report.
- Updates come only from **Phosphene → Check for Updates…**. Your queue, settings, models and finished work are kept.
- To repair the music engine, open **Audio → Compose** and click **Repair music engine**. Your downloaded model files are kept.
- Hailuo H3 is not available in the Mac app yet.
[[/mac-app]]

[[pinokio]]
## If an install or update stops with an error {#fatal}

If a required step fails, Pinokio stops and shows a line beginning with `FATAL error:`. Scroll up in the log Pinokio shows for that install or update to find it. The line explains the problem and what to click next. In almost every case, **nothing was deleted**. After fixing the problem, click **Install** or **Update** again to continue.

Use the table below to match the text after `FATAL error:`:

| The line says | What to do |
|---|---|
| *not enough disk space to install Phosphene* | Free the space it names, then click **Install** again |
| *could not download the video engine version Phosphene needs … (a VPN, proxy or blocked GitHub is the usual cause)* | Check your connection, turn off the VPN or proxy, then click **Install** or **Update** again |
| *the engine environment did not install after 3 attempts* | Check your internet connection and free disk space, then click **Install** or **Update** again. If it repeats, report the line using the instructions below |
| *the video engine is on the wrong version* | Click **Install** or **Update** again. If it repeats, report the line |
| *Hailuo H3 needs a Mac with at least 16 GB of memory* | Your Mac can't run H3. Keep using LTX, which has every mode except Add sound |
| *Hailuo H3 needs about N GB of free disk space* | You need about 97 GB for a new install (the 75 GB download plus the 22 GB compact engine), or about 25 GB if the model files are already downloaded. Free the space, then click **Install Hailuo H3** again |
| *the compact Hailuo H3 engine could not be built* | This usually means the disk is full (the build needs about 22 GB). Free space, then click the H3 entry in Pinokio's sidebar again. It rebuilds from the downloaded model files |
| *a Phosphene render has been running for over 60 minutes, so the compact Hailuo H3 engine was not built* | Wait until Phosphene finishes its current render, then click the H3 entry in Pinokio's sidebar again |
| *the H3 venv has no working Python* | Click **Repair Hailuo H3** in Pinokio's sidebar. Your downloaded model files are kept |
| *YuE2 (the music engine) needs a Mac with at least 16 GB of memory* | This Mac can't run the music engine. The rest of Phosphene is unaffected |
| *YuE2 (the music engine) needs at least 14 GB free* | Free the space, then click **Install the music engine** again |
| *could not download the YuE2 music engine* | Check your internet connection and whether a VPN or proxy is blocking GitHub, then click **Install the music engine** again |
| *untracked/ignored paths obstruct Update. Move them; nothing deleted.* | Files you added inside the app folder are in the way of the update. The lines just above list them. Move them out of the Phosphene folder, then click **Update** again |
| *fetch failed (offline?) - nothing changed, re-run Update* | Reconnect, then click **Update** again |
| *the Update safety check did not pass - not updating* | Nothing was changed. Click **Update** again. If it repeats, report the line |

If your error is not listed, follow the instructions in its `FATAL error:` line. To report an error, copy that line and the few lines above it into a bug report. See [Reporting an issue](#docs/troubleshooting/report).
[[/pinokio]]
[[mac-app]]
## If setup stops with an error {#fatal}

The setup screen shows **Setup paused** and the reason. Click **Resume setup** to continue. Nothing already downloaded is lost.

Open the full log with **Open logs folder** on the setup screen or **Help → Open Logs Folder**.

| Cause | What to do |
|---|---|
| Not enough disk space | Free some space, then click **Resume setup** |
| No connection, or a VPN or proxy blocks GitHub | Restore your connection to GitHub, then click **Resume setup** |
| Music engine installation stops with a `FATAL error:` line on the **Audio → Compose** card | The card names the cause: the music engine needs **16 GB memory**, **14 GB free disk space** and a connection to GitHub |

To report a problem, use **Help → Report a Bug…** and include the last lines of the log. See [Reporting an issue](#docs/troubleshooting/report).
[[/mac-app]]

## Starting over without losing your work {#start-over}

[[pinokio]]
- Click **Reset engine** in Pinokio to rebuild the LTX engine. Your models, finished work and settings are kept.
- To find your finished work, click **Outputs** in Pinokio's sidebar. This opens `mlx_outputs` inside the Phosphene folder.
- To find your downloaded models, click **Models** in the sidebar. This opens `mlx_models`. Don't delete this folder unless you want to download everything again.
[[/pinokio]]
[[mac-app]]
- Quit Phosphene and open it again to re-run the setup check. It checks Python, the engine and the video encoder, and repairs what it can.
- Click **Repair engine** in the bar at the top of the studio to repair the engine's Python packages.
- **Studio → Open Outputs Folder** opens your renders in `~/Library/Application Support/Phosphene/outputs`.
- Your models are in `~/Library/Application Support/Phosphene/models`. Keep this folder to avoid downloading them again.
- Everything is stored in `~/Library/Application Support/Phosphene`, including `models`, `outputs`, `state` and `logs`. Moving the whole folder to the Trash removes the studio and all its data.
[[/mac-app]]

If Phosphene opens but a task fails, models are missing or your queue pauses, see [Troubleshooting](#docs/troubleshooting).
