# Audio

The Audio tab makes a **video driven by a sound file you bring** — a voice line, a song. The sound steers the picture while it is generated (it is not laid on afterwards), so a mouth moves to the words and motion follows the music. It does not make sound from text; for that, describe the sound in any Video prompt.

## Making one {#make}

1. Drop the **Audio** — WAV, MP3, M4A or FLAC.
2. Optional: a **Reference image** to open the clip on that frame — a portrait for a talking head. Leave it empty for pure audio-to-video.
3. Write the prompt. **Enhance** rewrites it for the model.
4. Set **Width** and **Height** (default 1024×576), **Audio start** (seconds into the file, e.g. 30 to drive the clip from 0:30) and **Duration** (1–30 s, default 7).
5. **Audio conditioning strength** (0.5–5.0, default 1.0): higher holds the picture to the audio more tightly and leaves it less free.
6. **Generate**.

> **Tip** Some seeds do not take — if the mouth does not move, retry with a new seed. A large canvas with a long duration is a very long render; the tab warns you and suggests a smaller size.

Audio always renders on LTX: on the High (Q8) pipeline when it is installed, otherwise on the fast pipeline.
