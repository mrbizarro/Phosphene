# Music Studio: songs with vocals

Open **Audio → Compose** to use Music Studio. It creates complete songs on your Mac, including vocals and instruments, using the YuE2 music engine. You can also make music without vocals, create a new version of an existing song, or turn a recording into sheet music.

## Install the music engine {#install}

The music engine is a separate download of about **11 GB**.

- **What it needs:** an Apple Silicon Mac with **16 GB of memory or more**, and **14 GB free** on the drive where you store models. On a 16 GB Mac, close other apps and browser tabs before you compose.
- **How:** open **Audio → Compose**. The form stays greyed out until installation finishes. Click **Install music engine (YuE2, ~11 GB)** on the card, or click **Compose**, to start the download. The card shows your progress.
- **Licence:** YuE2 shows a licence screen before download or first use; accept once per Mac unless the licence changes. The weights use CC BY-NC 4.0 (non-commercial use only), with permission for individual creators to sell songs they make; companies need a licence for the weights.
[[pinokio]]
- To pause installation, click **Stop**. Start the install again to resume it. You can also install through **Install the music engine** in Pinokio's sidebar or **Settings → Models**.
[[/pinokio]]
[[mac-app]]
- To pause installation, click **Stop**. Start the install again to resume it. You can also install from **Settings → Models**.
[[/mac-app]]
- You can use the form as soon as installation finishes. You do not need to restart anything.

Two optional extras download when you first need them:

- **The music LoRA pack** (about 300 MB) improves music without vocals. It also adds voice and style add-ons to the **Voice & style LoRA** list. The form offers the pack when it would help.
- **The cover models** (2.8 GB) are needed for **Cover a song** and **Get the score**.

[[pinokio]]
If the engine stops working after an update, click **Repair the music engine** in Pinokio's sidebar. Your downloaded model files are kept.
[[/pinokio]]
[[mac-app]]
If the engine stops working after an update, open **Audio → Compose** and click **Repair music engine**. Your downloaded model files are kept.
[[/mac-app]]

## Choose Simple or Custom {#simple-custom}

Use the switch at the top of Music Studio to choose how much you write yourself.

- **Simple**: briefly describe the song you want. For example: "a slow piano ballad for a friend moving away, a woman with a low warm voice, brushed drums". Gemma, an AI model running on your Mac, writes a style description and lyrics. You see both before the song is created. Tick **Instrumental** for a track with no vocals.
- **Custom**: write your own lyrics and style description, with access to all the controls below.

## Write a song {#write}

In **Custom**, choose **Write a song**, then:

1. Enter a **Title** if you want one.
2. Under **What is it about?**, describe your idea, story or feeling in a sentence or two. Click **Write lyrics** to have Gemma draft lyrics from it. If **Style** below is filled in, the lyrics follow that style too. You can edit the draft and its song sections.
3. Under **Lyrics**, choose **Sections** to edit cards. Add cards with **Intro**, **Verse**, **Pre-Chorus**, **Chorus**, **Bridge**, **Instrumental** or **Outro**. Or choose **Text** to type everything in one box. Put each section name in square brackets on its own line, followed by its lyrics. Leave a blank line between sections:

   ```
   [Verse]
   The station lights are fading
   Your footsteps find the street

   [Chorus]
   Carry the morning home
   ```

   Leave a section's lyrics empty for a passage with no vocals.
4. In **Style**, describe the genre, instruments, singer, language and tempo in plain language. For example: *English, warm piano pop, expressive female voice, acoustic piano, light drums, 88 BPM*. You do not need separate labelled fields.
5. Click **Compose**. Your song joins the same queue as your videos.

### Instrumental {#instrumental}

Tick **Instrumental** for a whole track without vocals. Your lyrics stay in the box but are not used. The music LoRA pack provides a dedicated add-on that improves the results. Without it, the engine relies only on a written request for no vocals. To include a passage without vocals inside a song, leave that section's lyrics empty instead.

### Adjust the song settings {#controls}

| Control | What it does |
|---|---|
| **Score**: **Melody + chords** (default), **Melody only**, **No score** | Choose whether YuE2 plans the song as sheet music before creating it. Melody + chords gives the most structured songs |
| **Quality**: **Draft** (8 steps) or **Final** (32 steps, default) | Use Draft to try ideas. It is several times faster than Final |
| **Max length**: 30 s to 6 min (default 4:00) | Set the longest allowed song. Your lyrics determine its length. This setting cuts off a longer song but does not stretch a shorter one |
| **Seed** | The number used to vary the result. Leave `-1` for a random choice. Reuse a seed to get the same song again |
| **Takes**: 1 to 8 | Queue this many versions from your form settings, each with its own seed |
| **Voice & style LoRA** | Choose add-ons that change the writing and sound, and set how strongly each applies. To add your own `.safetensors` file, put it in the LoRA folder's `user/` subfolder. It then appears in this list. These add-ons are paused while Instrumental is selected |
| **Advanced → Text guidance** | Set how closely the song follows your style and lyrics. The default is 1.0. Higher values follow them more literally but can sound stiff; lower values allow more variation |
| **Advanced → Precision**: **8-bit** (default) or **bf16** | If you choose bf16, allow about 8 GB more memory |

## Cover a song, or get its score {#cover}

- Choose **Cover a song**, then drag an audio file into **Source song**. Choose what to keep: **Melody + chords** takes the tune and harmony from the full recording; **The sung line** takes only the vocal tune, with new backing music; **Everything** also keeps the backing music and takes the longest to read. The result uses your style and lyrics.
- Choose **Get the score** to turn a recording into sheet music without creating a song.

Both need the cover models (2.8 GB, downloaded once). Only cover music you have the rights to use.

## What you can do with a finished song {#song-card}

Find your finished song in Outputs, saved as a 48 kHz stereo WAV audio file. Use the **Audio** filter to show only audio. Below the player, the song card shows the title, style, lyrics and sheet music. Use these buttons to create another version:

| Button | Keeps | Changes |
|---|---|---|
| **New take** | The sheet music, lyrics and style | The performance, using a new seed. The song is not planned again |
| **Re-roll the sound** | The sheet music, lyrics, style and performance | The recording sound. Takes roughly a third of the time needed to create a full song |
| **New style on this score** | The sheet music | The lyrics and style. Fill in the form with your changes before clicking |
| **Cover it** | The tune you hear | Everything else |

Click **Edit the score** to open the sheet music as text. After editing, click **Preview** to see the updated sheet music. Click **Render this score** to add a song using your edits to the queue.

Songs made before version 4.16 lack the saved plan these buttons need. Their buttons explain why these actions are unavailable.

Click **Use…** on the song card to choose what to make next: **Lip-sync** makes a face in your picture sing the song; **Lip-sync with Hailuo H3** does this with H3 when installed. You can also choose **Music video** or **Soundtrack for a video**.

## Music video {#music-video}

Open **Audio → Music video** to make a film from your song and pictures. Choose the song, add your pictures and mark each as **Singer** (shown singing), **Instrument** or **Room**. In **Look**, describe the appearance you want for every shot.

Click **Plan the video**. Planning is instant and free. It reads the beat, creates a shot list and opens it in [Storyboard](#docs/storyboard). In Storyboard, create the shots, then combine them with the song to make your film.

## What to expect {#expect}

- **Time.** The estimate under **Compose** assumes a song as long as **Max length**, so most songs finish sooner. On an M4 Max, Final creates a 3-minute song in about 3 minutes; Draft takes less than half that time. Your first song after installation takes longer because the models also need to load.

  The median time is about 1.5 minutes on an M4 Max, 3 minutes on an M5 Pro, 5.5 minutes on an M1 Max and 6 minutes on an M4 Pro. Half of songs finish sooner than the median and half take longer. These times come from Phosphene's anonymous usage counts; see the [speed benchmarks](https://phosphene.video/benchmarks/).

- **Memory.** With 8-bit selected, memory use peaks around 11 GB while your song is written. On a 16 GB Mac, close other apps and browser tabs before you compose. We still recommend 24 GB or more for long songs while other apps are open.
- **Takes.** Try two or three versions on **Draft**, then create the one you like on **Final**. If you already like a song's tune, use **New take** for another performance.
- **Length.** Your lyrics determine the song's length. Short lyrics make a short song even with Max length set to 6 minutes.
- **Your own voice.** You cannot train a voice in the panel. You can use voice add-ons made elsewhere through **Voice & style LoRA**.

## When something goes wrong {#troubleshooting}

| What you see | What to do |
|---|---|
| Compose stays greyed out | Installation is missing or unfinished. Click **Install music engine** on the card to start or resume it |
| The install stops with a `FATAL error:` line | It names the cause: not enough memory (16 GB), not enough disk (14 GB free) or no connection to GitHub. See [When an install stops](#docs/install/fatal) |
[[pinokio]]
| The engine says it's unavailable, but the model files are downloaded | Click **Repair the music engine** in Pinokio's sidebar. You do not need to download the model files again |
[[/pinokio]]
[[mac-app]]
| The engine says it's unavailable, but the model files are downloaded | Open **Audio → Compose** and click **Repair music engine**. You do not need to download the model files again |
[[/mac-app]]
| *Music Studio stopped your song because your Mac was running low on memory* | Close other apps and browser tabs, then compose again. Nothing was saved for this song |
| A song waits with *Waiting for the GPU* | Another task is using your Mac's graphics processor. Your song starts when that task finishes. Click **Stop** to cancel the wait |
| A song was stopped | No partial song is saved. Click **Compose** to queue it again |
