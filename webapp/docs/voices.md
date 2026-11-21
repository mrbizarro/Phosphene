# Voices and characters

Save a voice once and use it again and again: give it to a character, make two characters talk to each other in one Hailuo H3 scene, or (with Pro) re-voice a clip you already made.

## Add a voice {#add}

1. Open **Voices** in the left column.
2. Type a **Name**, then drop a clip or click to choose one: 10 to 60 seconds of one person talking, with no music. An audio file or a video works. You can also **Record with the microphone**.
3. Press **Save voice**.

Phosphene cleans the clip (it takes the voice away from background sound) and then learns the voice by itself. Each voice shows where it is: Cleaning the clip, Training with its step and the time left, Waiting its turn in the queue, Ready, or Did not finish with the reason and a **Try again** button. Learning one voice took 6 to 14 minutes on an M4 Max with 64 GB in our tests; you can keep working or close the window meanwhile.

- **First time only:** learning a voice needs extra model files, about 29 GB. Phosphene downloads them the first time you train a voice; the Voices window says so.
- **Memory:** learning a voice needs a Mac with 24 GB of memory or more.
- Use only voices you have permission to use.

## Characters with pictures and a voice {#characters}

In **Video**, choose **Character**, then **New character**.

- **Name** the character.
- **Pictures:** up to 3, optional. Pictures work right away; no training needed.
- **Voice:** pick a saved voice, or add a voice clip here (it is cleaned and learned automatically).
- **Train face** is optional. Tick it to go on to Train Character with these pictures.

On LTX, a character with pictures and no trained face starts the clip from its first picture. On Hailuo H3, a character's pictures go into References, which is part of Pro.

## Two people talking in one Hailuo H3 scene {#two-people}

Choose **Hailuo H3**, write the scene, and start each spoken line with the character's name:

```
Two friends sit at a small kitchen table in the morning light.
Bizarro: Did you hear that noise last night?
Aria: I thought it was you.
```

H3 makes the picture, the voices and the sound together. Then each line spoken by a character who has a saved voice is swapped for that voice, so each of them speaks in their own voice. Someone without a saved voice keeps the voice H3 gave them. The picture is not touched.

Under **Voice** in the form you choose **How**: **Swap in the trained voice after the render** (the closest match, the default) or **Guide the render with the voice sample**, which uses References (Pro).

## Re-voice a clip you already made (Pro) {#revoice}

**Re-voice** gives any line in a finished video or audio file a saved voice. The picture and the rest of the sound stay exactly as they are, and a new copy is saved in Outputs, so the original is never changed.

- **Where:** choose **Re-voice** next to Lip-sync and Remix, or **More** on a finished clip, or **Re-voice a clip you already made** in Voices.
- Phosphene finds the lines and who says them. Play each line to hear it, pick a voice per speaker (or **Same voice for every line**), and press **Re-voice N lines**.
- Each new word is placed where the original word was, so the mouth and the voice stay together.
- A line that cannot be matched well keeps its original voice, and the job tells you which one.

Re-voice is part of **Phosphene Pro**. Without Pro, choosing it opens the Pro window.
