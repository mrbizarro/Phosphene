# Speech: turn a script into a voice

**Lip-sync it** adds scene ambience and sound effects by default on LTX-2.5. Describe the setting in the scene prompt, or use **Scene sound description** for specific sounds. Turn **Add scene sound** off for voice only. This adds one sound-generation pass with the installed LTX model. The clean voice still drives the lips; the bed is mixed afterward and lowers during speech. Effects follow the scene description; exact action timing is not guaranteed. Add optional music on the Editor timeline.

Open **Audio → Speech** to turn a script into a spoken WAV file on your Mac. **Speech** sits alongside **Compose** and **Music video**. Choose a preset voice, a saved character's voice or a clip you add. The finished file appears in **Outputs** and can go straight to **Lip-sync**.

## Install Speech {#install}

Speech needs a one-time download of about **6 GB** for the speech engine and two voice models.

- **What it needs:** an Apple Silicon Mac with **16 GB of unified memory or more**, and about **8 GB of free disk** for the install.
- **How:** open **Audio → Speech**. The form stays visible but greyed out until installation finishes. Use the in-app install card or click **Install Speech (~6 GB)**, which appears in place of **Generate**.
- **Pause and resume:** click **Stop** to pause the download. Start the install again to resume it.
- **Licence:** the models use Apache-2.0. There is no licence screen.

After installation, Speech works offline.

## Write your script {#script}

Enter up to 2,000 characters. Each line is spoken in turn, with a short pause between lines. Punctuation shapes the delivery: try !, ? or … where you want emphasis, a question or a pause.

Below the box, “About N s of speech” estimates the length. The app also shows whether your script fits one lip-sync clip: about 10 seconds, or roughly 25 words. Split a longer script into shorter scripts for individual clips.

## Choose a voice {#voice}

- **Preset voice:** choose from nine model voices. Ryan and Aiden are native English speakers. Serena, Vivian, Uncle Fu, Dylan and Eric are native Chinese speakers; Ono Anna is Japanese and Sohee is Korean. All can speak English, with an accent for the non-native English voices. These are the model's own voices, not real people.
- **A character:** use the voice sample a saved character was trained with in the **Train** tab. Only characters with a voice sample appear in the list. Type the words spoken in that sample for a closer copy of the voice; Phosphene remembers them for that character.
- **A clip you add:** add 5–20 seconds of one person speaking clearly, without music. Use WAV, MP3, M4A or FLAC. You can also type the words spoken in the clip for a closer copy of the voice.

## Permission to use a voice {#consent}

Each time you use **A character** or **A clip you add**, tick **This is my voice, or I have the speaker's permission to use it.** The confirmation is saved with the file.

Only clone a voice with the speaker's permission. Never use a cloned voice to deceive. Phosphene's [acceptable-use policy](https://phosphene.video/acceptable-use/) forbids impersonation.

## Adjust the delivery {#controls}

For preset voices, choose a **Delivery**: **Natural**, **Warm**, **Excited**, **Deadpan**, **Dramatic**, **Whisper**, **Sad** or **Angry**. Or type your own direction below the styles, such as “tired, like it's 3 a.m. and the render just failed”. Typed direction takes priority over the selected style.

Character and clip voices have no delivery styles. Their delivery follows the clip's mood and your script's punctuation.

| Control | What it does |
|---|---|
| **Speed** | Set the pace from 0.70× to 1.40×. The pitch stays the same |
| **Advanced → Language** | Choose English, Chinese, Japanese, Korean, German, French, Russian, Portuguese, Spanish or Italian |
| **Advanced → Seed** | The same seed, text and voice give the same take. Leave `-1` for a random seed |

## Generate and use your speech {#generate}

Click **Generate**. Speech joins the same queue as videos and songs, so it waits if a video is rendering. Once it starts, a line takes a few seconds. The finished speech plays right below **Generate**.

- Click **Lip-sync it** to open **Lip-sync** with the audio loaded. Add a picture of the face, then click **Generate**.
- Click **Use this audio for…** to choose **Lip-sync**, **Lip-sync with Hailuo H3** when installed, **Music video**, **Soundtrack for a video** or **Cover it**.

Find the file in **Outputs** using the **Audio** filter. It is a 24 kHz mono WAV. A small `.json` file beside it records the text, voice and seed.

For agents and scripts, use `POST /speech/generate`; see the API doc.

## When something goes wrong {#troubleshooting}

| What you see | What to do |
|---|---|
| Installation failed | Click **Install Speech (~6 GB)** again. The download resumes and keeps finished files |
| “Stopped before the Mac ran out of memory” | Close other apps and browser tabs, then try again |
| A cloned voice sounds off | Use a cleaner 10–15 second clip with no music or background noise, and type its words |
| A preset sounds too flat | Choose a **Delivery** or type your own direction |

## Credits {#credits}

Speech uses Qwen3-TTS by the Qwen team (Apache-2.0), running on Apple's MLX through mlx-audio (MIT).
