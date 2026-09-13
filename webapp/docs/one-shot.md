# One Shot

One continuous shot of **30 seconds to 2 minutes** that never cuts. You write the whole shot once; Phosphene renders it in parts, each continuing from the last frame of the one before, and joins them into one file.

## Making one {#make}

1. Choose the **Engine**: **LTX 2.5** renders 10 s parts and is the one for faces, voices and dialogue; **Hailuo H3** renders 15 s parts and is the one for motion, landscapes and crowds.
2. Write **The shot** — who, where, the time of day and the weather, the sound — once, for the whole shot. This is the only thing required.
3. Pick a **Length**: 30 s, 45 s, 1 min, 1½ min or 2 min. Each chip shows how many beats and parts it makes.
4. Pick a **Quality**. The estimate line says about how long it takes on this Mac.
5. Press **Generate** (or [[sc:prompt.generate]] in the prompt box). The whole shot is one request in the queue.

## The optional parts {#options}

- **Start frame** — *Choose an image* and the shot opens on it.
- **Who is in it** (LTX) — pick a trained character: Quality switches to that character's qualities and **Hand off where the line ends** turns on.
- **Beats** — one row per 5 seconds, each with its time stamp. Write what happens in each, or let Phosphene fill them: **Split my prompt into beats** puts one sentence per beat, **Write the beats for me** has the planner write them. [[sc:oneshot.beats]] Pasting a list fills the rows.
- **Camera** — one move for the whole shot, its direction and speed. Every part continues it.

## The three switches {#switches}

| Switch | On | Off |
|---|---|---|
| **Lock the light** | the same light in every part (default) | each beat as written |
| **Redo a part that drifts** | a part that drifts is rendered once more, at most (default) | keep the first pass |
| **Hand off where the line ends** | for a talking character: the next part starts where the spoken line stops | each part starts from the last frame |

Under **More**: **Seed**, a **Label** for the queue, and **No music** (voice, sound and ambience only).

## Watching it render {#status}

The status card updates every few seconds: *Rendering*, *part 2 of 6*, and when it is done, *in the gallery on the right*. For each finished part with speech it reports whether the voice is on the mouth — **voice on the mouth**, **borderline** or **voice-over** — and **light drifted** when a part's light moved.
