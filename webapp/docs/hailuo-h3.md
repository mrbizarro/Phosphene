# Hailuo H3 video engine

Hailuo H3 is Phosphene's second video engine. It creates video, dialogue and sound together. Choose H3 for scenes of people talking, with lip movements that match their voices. Choose LTX for more modes, trained characters and combinations of add-ons called LoRAs. To choose an engine, use the switch at the top right of the panel.

## Install H3 {#install}

You install H3 separately because it needs a large download.

[[pinokio]]
- **What it needs:** an Apple Silicon Mac with **36 GB of memory or more**, and about **97 GB free** on the drive where you store models. This covers the 75 GB download and the 22 GB compact engine built during installation.
- **How:** first, in the panel, click the **Hailuo H3** side of the engine switch, choose **Read the licence** and tick **I accept**. Then select Phosphene in Pinokio and click **Install Hailuo H3 (second video engine, ~75 GB)** in the sidebar; until you accept, the installer stops with a `FATAL` line.
- **On a 36–59 GB Mac**, installation ends by preparing H3's compact engine, which uses less memory. This takes about 5 more minutes and downloads nothing. If you already have the downloaded model files but not the compact engine, click **Build Hailuo H3 compact engine** in the sidebar.
[[/pinokio]]
[[mac-app]]
- Hailuo H3 is not available in the Mac app yet. Everything else works without it. Render on LTX.
[[/mac-app]]
- **Licence:** H3 uses the MiniMax H3 Community License, which does not cover the European Union, the United Kingdom, South Korea or the United States. The licence screen asks you to accept once per Mac; your acceptance is saved, and you are asked again only if the licence changes. If H3 is already installed, it keeps working and asks once before your next H3 render. Phosphene shows the notice for you to confirm; it does not check your location. While H3 is selected, the panel shows **MiniMax H3** with **Licence** and **Report misuse** links; reports go through a GitHub issue.

[[pinokio]]
After installation, the sidebar entry becomes **Repair Hailuo H3** or **Update Hailuo H3 runner**. These repair or update H3's supporting software and keep your downloaded model files. You do not need to download those files again. If installation stops, look for the `FATAL error:` line in Pinokio's terminal. See [When an install stops](#docs/install/fatal).
[[/pinokio]]

### Full or compact engine {#engine-size}

Open **Settings → Hailuo H3 model** to choose which version of H3 to use:

- **Automatic** (the default) uses the compact engine whenever it is ready.
- **Compact (Q8)** uses around 21 GB of memory at its peak.
- **Full (bf16)** uses around 42 GB of memory at its peak. You can choose it only on Macs with 60 GB or more.

In a test with the same description and random starting value (seed), Compact used about half the memory of Full and took about the same time.

## What H3 can make {#modes}

Select H3 with the engine switch first. Then choose a mode at the top of the Video tab:

| Mode | You give it | You get |
|---|---|---|
| **Text** | A written description (prompt) | A clip with video, voices and sound |
| **Image** | A picture and a prompt | A clip that starts with your picture |
| **Start & end frame** | A picture for the end, and optionally one for the start | A clip that moves between the pictures. On H3, you can leave out the start picture |
| **Keyframes** | 3–8 pictures | A clip that reaches each picture at its set time |
| **Extend** | any clip | the same clip, continued |
| **Add sound** | Any clip, with or without sound | The same video with new sound to match it. H3 only |
| **Lip-sync** | A voice recording or song, plus a picture | A performance with lip movements that match your audio |

**Character** and **Remix** work only with LTX. If you choose either, Phosphene switches back to LTX and tells you why.

### Extend on H3 {#extend}

Choose **Extend**, then select your clip under **Source video**. Set how many seconds to add, up to 15, and describe what happens next in the prompt. H3 continues the motion and sound from the last 17 frames of your clip. You can use any clip up to 60 seconds long, including one made with LTX.

### Add sound {#add-sound}

Choose **Add sound** to give a clip new background sounds, footsteps, impacts or voices. **The picture is kept exactly as it was**, frame for frame. Describe the sound you want. You can also use a short prompt and let H3 add sounds based on what it sees. In the gallery, click **Add sound** on any silent clip to begin.

### Lip-sync to your voice or a song {#lip-sync}

Click **Lip-sync** on the Video tab to open **Audio → Lip-sync**. Under **Engine**, choose **Hailuo H3**, then choose the picture size. H3 creates movement in the face, body and surroundings to match your track.

- H3 keeps your audio file **exactly as it is** and creates video to match it.
- Use a picture of a person facing the camera with their mouth closed. A mouth that starts open tends to stay open.
- Describe how the person should perform. Avoid the words "still" and "static": they stop the mouth moving too.
- For a **song**, tick **Listen to the voice only**. This separates the vocals from the instruments for H3 to follow (about 20 seconds, once per song). Your finished clip still includes the vocals and instruments. Without this option, the mouth often stops moving after a couple of seconds.

The rest of the Lip-sync form is described on [Audio](#docs/audio).

## Choose the video quality {#quality}

| Quality | Size | What it is for |
|---|---|---|
| **Draft** | 640×384 | Check framing, movement and dialogue timing. Use Standard or above for clear faces and fine detail |
| **Fast HD** | 640×384, then sharpened to 1280×768 | You get a Draft clip and a sharper version from **Upscale & Face Fix**, which preserves the face using the clip itself. Both jobs appear in the queue, and both results appear next to each other. Requires the Fast add-on and the Upscale add-on (0.3 GB, from the Models window) |
| **Standard** | 768×448 | For general use. The best-tested size for 10 s and 15 s clips |
| **High** | 1024×576 | A 16:9 picture with sharper faces than Standard. Recommended for clips you plan to share |
| **Native** | 1344×768 | The most detail H3 can produce. Always uses Best, so expect a long wait |

Choose portrait under **Orientation** to make any size vertical. The render time stays the same.

## Choose the clip length {#length}

Choose the length before you create the clip:

- **3s** and **5s** each create a clip in one pass, with no joined sections. For these options, 5 s is the longest single pass.
- **10s** and **15s** join 5-second sections, called windows. Each section continues the previous section's movement and sound.
- **10s single pass** creates one continuous shot with no join. It takes 2–4× as long as the joined 10 s option and always uses Best.

### Give each section its own prompt {#windows}

A 10 s or 15 s clip uses your main prompt for every 5-second section by default. This can make an action repeat in each section. To control what happens next, open **Per-window prompts** and turn on **One line per window**. You get two boxes for 10 s and three for 15 s. Describe each section's action in its box. An empty box uses the main prompt.

## Speed: Fast or Best {#speed}

Use the **Speed** switch above the quality options.

- **⚡ Fast**: 3 steps, with slightly softer fine detail. Use it for drafts and most shots. It becomes the default once installed. To download it, click **Install (180 MB)** on the Fast side of the switch. This is a separate **180 MB** download.
- **✦ Best**: the sharpest faces for your chosen size, with a much longer wait. Only Best shows **Steps**. Choose **Auto** for the recommended count, or choose **12** (about 1.4× the time), **16** (about 1.9×) or **20** (about 2.4×).

Fast runs on Draft, Fast HD, Standard and High. Native and 10s single pass always render on Best.

### Best on High: lightx2v v1.3 {#v13}

On **High**, **Best** can use **lightx2v v1.3**, an adapter by LightX2V. In our test on an M4 Max with 64 GB of memory, a 5-second High clip took about 15 minutes, down from 33: 55% less time. In our side-by-side tests, it looks richer and more detailed.

- **Get it:** click **Get it · 1.4 GB** under Speed. Phosphene downloads LightX2V's original file directly from Hugging Face, verifies it and prepares it on your Mac. You'll need about 5 GB free during setup, 2 GB when done. New H3 installs get it automatically.
- **When it runs:** Best on High, in Text and Image modes, at any length. Draft, Standard, Native and Fast are unchanged.
- **Use the old Best:** choose a **Steps** count under Best to use the full sampler.
- **Credit and licence:** lightx2v v1.3 is by [LightX2V](https://huggingface.co/lightx2v/Minimax-h3-Turbo). Code: Apache-2.0. Weights: MiniMax H3 Community License, the same as H3.

## H3 Flex (preview) {#flex}

**H3 Flex** is Pika H3 Flex by Pika Labs: a six-step version of H3, about 2.2× faster than Best at the same size. It is a **preview**: new on Mac, so tell us how it goes.

- **Pick it:** choose **H3 Flex** in the engine picker at the top, right after Hailuo H3.
- **Get it:** if its adapter is not on your Mac yet, the same click downloads it (2.8 GB, from Pika's own Hugging Face page at a fixed version) and prepares it on your Mac. The picker shows the progress. You need Hailuo H3 installed; if it isn't, the picker offers H3 first.
- **What it makes:** Text and Image (first frame), with dialogue and sound, on every 5-second length (10 s and 15 s are 5 s pieces joined, and later pieces can drift).
- **Times on an M4 Max, 64 GB, per 5 s:** Draft about 5 minutes, High about 16 minutes (Best: 36.5 minutes on the same shot). Full size (1344×768) runs; its timing is still to come.
- **Tip:** name every person who should stay in the scene. Flex drops people the prompt doesn't mention.
- **Back to H3's own Fast / Best:** pick **Hailuo H3** in the picker.

Powered by MiniMax H3. Pika H3 Flex by Pika Labs (Mellis, Inc.): the weights are a MiniMax H3 Model Derivative under the MiniMax H3 Community License; Pika's code is Apache-2.0. Phosphene converts the file on your Mac and marks it as modified. Mac weights are also on Hugging Face: [Mrbizarro/Pika-H3-Flex-MLX](https://huggingface.co/Mrbizarro/Pika-H3-Flex-MLX).

## How long it takes {#times}

Each quality and length option shows an estimated time for your Mac, based on completed videos from similar Macs. The table shows median times from Phosphene's anonymous usage counts: half finished sooner and half took longer. A dash means there are too few completed videos to give a time.

| Quality | Length | M4 Max · Fast | M4 Max · Best | M4 Pro · Fast | M4 Pro · Best |
|---|---|---|---|---|---|
| Draft | 3 s | 3:15 | 4:35 | 3:55 | — |
| Draft | 5 s | 5:10 | 6:20 | 6:40 | 12:20 |
| Draft | 10 s | 8:20 | 12:20 | 12:30 | 26:00 |
| Standard | 3 s | 4:10 | 6:00 | — | — |
| Standard | 5 s | 5:10 | 13:00 | 11:00 | 22:30 |
| Standard | 10 s | 12:40 | 24:00 | 19:00 | 49:00 |
| Standard | 15 s | — | — | 38:00 | 67:00 |
| High | 3 s | 6:10 | — | — | — |
| High | 5 s | 12:00 | — | — | — |
| High | 10 s | 24:30 | — | — | — |
| Native | 5 s | Best only | 54:00 | Best only | — |

Times are minutes:seconds, measured from 2026-08-10 to 2026-10-20. For all Mac chips, clip lengths and memory sizes, see the [speed benchmarks](https://phosphene.video/benchmarks/) page.

- Your first video after starting the panel takes longer because the model also needs to load.
- On a 36–59 GB Mac, 10 s and 15 s clips take about a third longer than on a 64 GB Mac. Quit other apps first. If your Mac needs to use disk space as extra memory, creating the video can take far longer.
- For Fast HD, allow time for both the Draft video on Fast and Face Fix. Together, they take about two thirds of the time for High on Fast.

## Writing prompts for H3 {#prompting}

H3 uses your prompt as written. **Enhance** is available only on LTX and is hidden on H3. Rewriting the prompt would remove the special dialogue markers H3 needs.

- Click **Structure** to insert the three-part layout H3 was trained to use: the shot, the surrounding sounds and the music.
- Click **+ Line** to insert one dialogue line in the language selected next to it. The button adds H3's required markers, identifies the speaker and tells the mouth to close after the line.
- **Spoken words go inside `<d>[English] …</d>` and nowhere else.** A line in plain quotes is not spoken. The panel warns you when it sees quoted speech outside the tag.
- Allow about **eight words per five seconds**. A longer line runs past the end of that section.
- After each spoken line, describe the mouth closing ("…and his mouth settles closed"). Otherwise, it keeps moving after the speech ends.
- Keep **one face in frame while a line plays**: every mouth in frame moves to whatever voice plays.
- For a voice-over, add "while his lips remain completely closed" to the prompt. Otherwise, the person on screen moves their mouth along with the narration.
- Describe music by its instruments ("a slow piano and brushed drums"), not by a mood word.
- H3 has no **Avoid** box. If it adds something you did not ask for, such as camera movement or on-screen text, ask it to leave that out in a plain sentence.

Phosphene adds short instructions when it sends your prompt to H3. These keep dialogue spoken rather than sung and discourage rings on bare hands. It also replaces "..." inside dialogue with a comma or full stop. Your original prompt stays as you typed it.

## Use add-ons (LoRAs) with H3 {#loras}

H3 has its own library of LoRA add-ons. You cannot use LTX LoRAs or trained characters with H3. See [Stacking LoRAs on Hailuo H3](#docs/loras/h3-stacking).

## Choose the export size {#after}

Open **After the render → Upscale** to choose the export size: **Native**, **720p fit** (the default) or **1080p fit**. To add sharpening and face correction after the draft, tick **Also run Upscale & Face Fix after the draft**. See [Upscale and export](#docs/video/upscale).

## When something goes wrong {#troubleshooting}

| What you see | What to do |
|---|---|
| No engine switch in the header | Your Mac has less than 36 GB of memory. H3 cannot run on it. Use LTX |
[[pinokio]]
| *"Hailuo H3 runs on this Mac … it is not built here yet"* | Click **Build Hailuo H3 compact engine** in the Pinokio sidebar (about 5 minutes, no download) |
| A mode or length is missing on H3, or a mode switches back to LTX | H3's supporting software needs an update. Click **Update Hailuo H3 runner** in Pinokio's sidebar. Your downloaded model files are kept |
| *"the installed Hailuo H3 runner is behind this panel"* | Click **Install Hailuo H3** again in Pinokio's sidebar. Your downloaded model files are kept |
| A render is refused with a **Repair** button | H3's supporting software has incompatible versions. Click **Repair**, or **Repair Hailuo H3** in Pinokio's sidebar |
[[/pinokio]]
[[mac-app]]
| *"Hailuo H3 runs on this Mac … it is not built here yet"* | Hailuo H3 is not available in the Mac app yet. Render on LTX |
| A mode or length is missing on H3, or a mode switches back to LTX | Hailuo H3 is not available in the Mac app yet. Render on LTX |
| *"the installed Hailuo H3 runner is behind this panel"* | Hailuo H3 is not available in the Mac app yet. Render on LTX |
| A render is refused with a **Repair** button | Click **Repair** |
[[/mac-app]]
| The render is killed for lack of memory | Quit other apps, pick a shorter length or a smaller size, or set **Settings → Hailuo H3 model** to **Compact** |

More: [Troubleshooting](#docs/troubleshooting/h3-compact).
