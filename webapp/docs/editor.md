# Editor

The Editor is a timeline for the clips you make — from any engine, from a storyboard or from nothing. Trim and split, move sound for J-cuts and L-cuts, add music, titles and transitions, then render one file or hand the cut to Premiere, Resolve or After Effects.

## Opening a timeline {#open}

- On the **Editor** tab, *Pick a sequence to cut* lists every sequence that has clips — click one.
- From **Storyboard**: step **3 Edit**, **Open in Editor** on the board list, or **Re-cut in the Editor** on the film screen.
- The last timeline you had open comes back when you return to the tab. Switching to another tab does not close it; **⋯ → Close** does (nothing is deleted).
- A new, empty or copied version of a sequence is a draft — see [Saving, drafts and versions](#docs/editor/saving).

## The layout {#layout}

**The header** — the sequence's name, the draft chip, whether it is saved (*unsaved changes*, *saved · revision N*), **Undo**, **Redo**, **Save**, **Render** with its **▾**, and **⋯** (Drafts and versions, Media pool, Auto-edit, Storyboard, Close).

**The media pool** (left) — **This Sequence**, **Other sequences**, **Generations**, **Images** and **Sound**. Click a row to watch it in the Source monitor; **+** puts it at the end of the sequence; drag it onto the track to insert it where you drop it. On **Sound**, **+** puts the sound on an audio track at the playhead; on a video row, **♪** puts only its sound there — see [Audio tracks](#docs/editor/tracks). **Add black** adds that many seconds of black at the end, **Add title** puts a title at the playhead, **Filter by name** searches ([[sc:search.focus]]).

**The monitors** — **Source** plays what you clicked in the pool (**Add to timeline** places it). **Program** plays the timeline. Beside them, the inspector (below) and *Rendered but not on the timeline*, with **Place** for each clip that finished but was never put on the track.

**The tracks**, top to bottom:

| Track | What is on it |
|---|---|
| **V2 Overlay** | titles and cards laid over the picture |
| **V1 Picture** | the shots, in order |
| **A1 Clip sound A** / **Clip sound B** | each shot's own sound, on two lanes — shots alternate A, B, A… so neighbours can overlap and crossfade |
| **A2 Music** | the soundtrack |
| **A3, A4, …** | audio tracks — laugh tracks, stings, beds, any number of sounds; every track plays together |

**The transport** — **Play**, 🔊 (mutes the preview only), the time, **Snap to beat**, zoom **−** / slider / **+**, the **i** (the preview is approximate at cuts; the render is exact) and **Keys**. Drag the timeline's top edge up for taller tracks; double-click it to reset. With the sound lanes small (below) the edge goes much further down, and the picture gets what it leaves.

## Selecting {#selecting}

- **Click** a clip to select it. Click an empty part of the track to select nothing (and move the playhead there).
- **⇧-click** selects the range from the selected clip to this one. **⌘-click** adds one clip or drops it.
- [[sc:editor.selectAll]] selects every clip. [[sc:editor.deselect]] clears the selection — and with nothing selected, closes the timeline.
- With several clips selected, the clip bar acts on **all** of them, as one undo step; the inspector shows the properties of the one you clicked last. A locked clip in the selection is left alone and named in a message.

## Moving, trimming and splitting {#moving}

- **Drag** a clip to move it. Drag a selected clip and the whole selection moves together, sound included.
- **Drag the edge** of a clip to trim it. **⌘ while dragging** ripples: everything after it slides too.
- Clips snap to the beat grid while **Snap to beat** is ticked ([[sc:editor.snap]] toggles it). **⌥ while dragging** ignores the grid for that drag. Sound strips and overlays snap to cuts.
- **⌥⇧-drag** reorders instead of moving; right-click → **Move earlier** / **Move later** swaps a shot with its neighbour.
- [[sc:editor.nudge]] nudges the selection one frame (with ⇧, ten).
- **Split** ([[sc:editor.split]]) cuts the shot under the **playhead** — not the selection — into two.

## The clip bar {#clip-bar}

The row of buttons directly above the tracks holds the actions you use all day. Left of them, the readout names what is selected (*3 clips selected*).

Every button is always there. When it cannot act, it is greyed out and its tooltip says what would make it work.

| Button | What it does | Greyed out when |
|---|---|---|
| **Split** [[sc:editor.split]] | cuts the shot under the playhead in two at the playhead; nothing moves, nothing is lost | the playhead is not over a shot, is exactly on a cut, or the shot is locked |
| **Lift** [[sc:editor.lift]] | takes the selection out and **leaves the hole** as black, so nothing after it moves | nothing is selected |
| **Ripple delete** [[sc:editor.ripple]] | takes the selection out and **closes the gap** — everything after slides earlier and the film gets shorter | nothing is selected |
| **Duplicate** [[sc:editor.duplicate]] | the same shot again right after itself — trim, speed, fades and grade included; everything after slides. On a **sound on an audio track**: the same sound right after itself on its track (or the next free spot on it). On the **music (A2)** or a **clip's sound (A1)**: a copy of the sound on an audio track, right after it — the first track with room, or a new one | nothing is selected; the music's length is not known yet (press **Prepare**); the clip has no sound, or plays at a speed other than 1× |
| **Unlink sound** / **Link sound** / **Re-link sound** [[sc:editor.link]] | frees the sound from its picture so you can slide it (the J-cut and the L-cut). **Link sound** keeps the offset you made and makes the pair travel together; **Re-link sound** appears when the sound is back exactly under its picture | the selection is a still or black, which has no sound |
| **Resync sound** [[sc:editor.resync]] | slides the sound back to where its own picture plays it. Your trim is kept and it stays unlinked | the sound is still linked (so it cannot be out of sync), the clip has no sound, or it is already in sync |
| **Mute sound** / **Unmute sound** | switches this clip's own sound off — in the preview, the render and the export. The strip stays; the music is not affected | the clip has no sound |
| **Delete sound** | removes the clip's sound; the picture keeps playing, silent. Undo brings it back | the sound is still linked — unlink it first |
| **Clear points** | deletes the level points (the dots on the yellow line) from the selected strips. Fades are left alone | the sound has no level points |
| **Lock** / **Unlock** | pins a shot to its place — everything else flows around it, and it cannot be dragged or trimmed | nothing is selected |
| **Face Fix ×2** (Upscale & Face Fix) | queues a 2× re-render of the clip that keeps the face and the sound. When it lands, a line above the timeline offers **Swap it in** (same cut, same in and out points) or **Keep the old one**; the original file is not changed | nothing, several clips, a still or black is selected |

When the pane is too narrow, the last buttons move into **More ▾**. **Right-click** a clip for the same actions at the pointer; right-click a hole for **Close this hole** and **Generate a shot here…**.

## The inspector (Advanced) {#inspector}

The panel beside the monitors holds the **properties** of the selected clip — the settings you change occasionally. It has three sections:

- **Clip** — **Speed**, 0.25× to 4× (**0.5x**, **1x**, **2x** buttons); the clip's slot on the film changes and everything after it moves. **Retake** renders a new take of a storyboard shot — see [Replace a shot with a retake](#docs/editor/job-retake).
- **Sound** — **Fade in** / **Fade out** in seconds, and **Add point at playhead** for the level line.
- **Effects** — **Brightness** (−0.5 to +0.5, **Reset**), picture **Fade in** / **Fade out** (**Clear**), and **Zoom** (1× to 3×, **Reset**) with **Across** and **Down** to reframe the zoomed picture.

Select the mark between two clips and the inspector shows the **Transition**; select a title and it shows the **Text**.

## Sound {#sound}

### Linked and unlinked {#linked}

A clip's sound starts **linked**: it moves with its picture, shows dimmer, and cannot be dragged on its own. **Unlink sound** frees it. An unlinked strip can be dragged, trimmed at either end, faded at its corners and shaped with level points — and it can drift out of sync, which is what Resync is for.

### J-cuts and L-cuts {#j-cut}

A **J-cut** starts the next shot's sound before its picture; an **L-cut** lets a shot's sound run on under the next picture. Both are made the same way: unlink the sound, drag or trim the strip under the neighbouring shot, then **Link sound** to keep that offset.

### Two sound lanes: crossfades {#ab-lanes}

**A1** has two lanes, **Clip sound A** and **Clip sound B**. A new timeline lays its shots' sound on them in turn — the 1st shot on A, the 2nd on B, the 3rd on A — and a shot you add later goes on the lane its neighbour is not on. Sounds on **one** lane play one at a time (if two overlap there, the later one cuts the earlier one off); sounds on **different** lanes overlap and play together. That is what makes a sound dissolve: pull the end of one shot's sound under the start of the next and fade both.

- **Alternate A/B** on the **Clip sound B** head — or right-click any shot or sound → **Alternate sound lanes** — lays an existing timeline's sound A, B, A… in film order. Nothing moves in time; one **Undo** puts the lanes back.
- **Drag a sound up or down** to move it to the other lane — linked sounds too, since changing lane does not move them. Right-click a sound → **Move sound to lane B** / **Move sound to lane A** does the same.
- Everything else works the same on both lanes: unlink, trim, J-cut and L-cut, fades, level points, Mute, Resync, Duplicate, snapping (a dragged edge also snaps to the other sounds' edges).
- The preview plays both lanes, the render mixes them under the same safety limiter as the music (and **Duck under dialogue** follows sound on either lane), and the export puts **Clip sound B** on its own audio track, right after the first.

### Sound lanes small, so you can see the picture {#compact}

**A1**, **A2** and every audio track make themselves **small** — thin strips showing where each sound sits — while you are not working on sound, and come back to full height the moment you are. The picture and the monitors get the height they give up.

- **They open by themselves** when you click, hover or drag any sound or its lane head, when a sound is selected, when you open **Sound** in the media pool, when you add a track or a sound file, and while the film is playing. They never shrink in the middle of a drag, during playback, or under an open menu.
- **They make themselves small again** a couple of seconds after you leave the sound alone.
- **A small lane is a picture, not a control**: the waveform shows where the sound is; the grips, the corner fades and the level line are not there. Moving the pointer onto the lanes is enough to open them, so by the time you click, the sound is at full height and behaves exactly as it always has.
- **▾ on the A1 Clip sound A head** ([[sc:editor.soundLanes]]) pins it: press it once and the lanes stay small whatever you do; press it again (**▸**) and they stay at full height. Clicking a small lane hands the decision back to the automatic one. The choice is remembered in this browser, not in the film.
- With them small, the timeline's top edge can be pushed much lower than before — the box is only as tall as what is in it.

### When sound drifts: Resync {#resync}

An unlinked strip that is not under its own picture shows a label with the offset, such as **+0.25s**. Click the label, or select the clip and press **Resync sound** ([[sc:editor.resync]]), to slide it back. It stays unlinked, so you can move it again.

### Muting {#mute}

- **Mute sound** on the clip bar is a decision about the film: that clip is silent in the preview, the render and the export.
- 🔊 in the transport ([[sc:editor.mute]]) only silences what you hear while editing. The film is not changed.

### Level points and fades {#levels}

On an **unlinked** strip:

- Drag a strip's **corner handle** inward for a fade.
- **Click the yellow line** to add a level point, and drag it up or down to set the level. Double-clicking the strip adds one too, and **Add point at playhead** in the inspector puts one exactly there.
- **⇧-click** or **right-click** a point to remove it; **Clear points** removes them all.

### The music track {#music}

- Open **▾** on the **A2 Music** head, give the soundtrack's path or **Change…**, then **Prepare**: it builds the waveform and finds the beat, which gives the timeline its beat grid.
- The mode menu: **under the clips** mixes the music under the clips' own sound; **replaces clip sound** uses the music alone — any dialogue in the clips is lost.
- **Level** sets the music's volume, 0–100%.
- **Duck under dialogue** (off by default) steps the music back wherever a clip's own sound is playing. If you have drawn your own fades or level points on the music, they drive it instead, and the head says *"off — your own level line is driving the bed"*.
- The music strip drags, trims, fades and takes level points exactly like a clip's sound.

### Audio tracks {#tracks}

Under **A2 Music** are the audio tracks — **A3**, **A4** and on. A track holds any number of sounds; sounds on **different** tracks play together (a laugh on A3 over a sting on A4 over the music), and sounds on the **same** track play one at a time and never overlap.

- **+ Add audio track** (under the track names) makes an empty one. Each track's head has its **name** (click to rename), **M** to mute the whole track, a **level** slider, and **×** to remove the track with its sounds (Undo brings it back).
- **Put a sound on a track:** open **Sound** in the media pool — this sequence's own audio folder and every sound file in the outputs (.wav .m4a .mp3 .aac) — and press **+** (it lands at the playhead) or drag the row onto a track. **♪** on a video row, or dragging a video onto a track, takes **its sound only**. **♪** beside **+ Add audio track** is **Add sound file…**: paste the path of any sound on this Mac; a file outside the outputs is copied into the sequence's audio folder so the preview can play it.
- Drop a sound on the dashed row under the last track and it gets **a track of its own**.
- A sound on a track works like an unlinked clip sound: **drag** it (up or down onto another track too — onto the dashed row makes a new track), **drag either end** to trim it, **drag a corner** to fade it, **click the yellow line** for a level point. It snaps to cuts, to the other sounds and to the beat; ⌥ while dragging ignores them.
- The clip bar acts on it: **Split** cuts the selected sound at the playhead, **Lift** / **Delete sound** take it off, **Ripple delete** also closes the gap on that track, **Duplicate**, **Mute sound**, **Clear points**, **Lock**. **⇧-click** / **⌘-click** selects several. Right-click it for the same verbs. **Unlink** and **Resync** stay grey — a sound on a track has no picture to link to.
- The inspector shows the selected sound's **Level**, **Fade in** / **Fade out** and **Add point at playhead**. The level you hear is the sound's level times its track's level.
- Everything plays in the preview, mixes into the render under the same safety limiter as the music, and exports: each audio track becomes its own audio track in Premiere / Resolve and its own layers in After Effects, with trims, levels, fades and mutes editable there.

## Titles, cards and black {#titles}

- **Add title** (media pool) puts a 3-second title on **V2 Overlay** at the playhead. In the inspector: the text, **Size**, **Colour**, **Align**, **Across** / **Down** position and **Box behind**, plus fades. Drag it to move it, drag its edges to change its length; [[sc:editor.removeOverlay]] removes it.
- **A card** — press **▣** on a still in the media pool to lay it over the picture at the playhead. A black background is removed automatically; **Keep original** puts it back.
- **Black** — **Add black** appends a black clip; **Lift** leaves one where a shot was.

### Holes {#holes}

A **hole** is empty space on the picture track — made by dragging a clip away from its neighbour. It shows its length and **fill it**. Click it to open *Fill this hole*: write the shot, choose **Length** and **Pass** (Draft or Delivery), and **Queue the shot**. When it lands, it waits under *Rendered but not on the timeline* — press **Place**.

> **Holes close when you render.** The render joins shots end to end, so a hole closes up and everything after it moves earlier. Render asks before it does that. Use black if you want the gap kept.

## Transitions {#transitions}

1. Click the mark between two clips.
2. In the inspector, choose **Kind**: **None — a hard cut**, **Dissolve** or **Fade through black**, and a **Length** (up to 2 seconds, and no more than half the shorter clip).
3. **Remove** takes it off again ([[sc:editor.removeTransition]] with the cut selected).

A transition borrows extra picture from beyond each clip's trim, so the cut does not move and neither does the sound. If a clip has no picture to spare, the transition is refused with a message saying which side is short — trim that clip in, or shorten the transition. The preview only approximates a dissolve; the render is exact.

## Saving, drafts and versions {#saving}

- **Save** ([[sc:editor.save]]) is the only thing that writes your draft. The button lights up when there are unsaved changes. Between saves your edits are kept as a backup, and when one is newer than your last save the Editor offers it as an *unsaved snapshot* — **Restore it**, **Discard** or **Later**.
- If saving keeps failing, a red bar says *SAVING IS FAILING* with **Try again**.
- **Undo** / **Redo** ([[sc:editor.undo]] / [[sc:editor.redo]]) go back 80 steps. Switching drafts, Auto-edit and swapping in finished shots clear the undo history.

**Drafts** — open them from the draft chip or **⋯ → Drafts and versions…**:

- A sequence can have several drafts. **Copy** starts a new draft from this one, **Empty** starts a blank one; each draft in the list has **Open**, **Copy**, **Rename** and **Delete**.
- *Your saves of this draft* lists every save with **Restore**. Type a name and press **Keep** to mark a save you want to keep for good — old unnamed saves are pruned, kept ones never are.
- If the same draft was saved from another tab, the Editor asks: **Load theirs** or **Keep mine**.

## Render and deliver {#render}

**Render** ([[sc:editor.render]]) saves first, then assembles the timeline into one file and opens it on the Storyboard film screen.

**▾ → Deliver as** chooses what the file is; the choices are remembered, and the Render button names them (*Render · 1080p*):

| Row | Choices | Notes |
|---|---|---|
| **Format** | **H.264** · **HEVC** · **ProRes** | H.264 plays everywhere, HEVC is half the size, ProRes (.mov) is for grading |
| **Size** | **As cut** · **1080p** · **4K** | only ever up, never a crop; 4K adds pixels, not detail |
| **Finish** | **Clean** · **Grain** · **Heavy grain** | on the delivered file only — the preview and the export stay clean |

**Export for Premiere / Resolve / AE** (in the same menu) writes a folder with an FCP7 XML (for Premiere and Resolve), an After Effects script and the media, and shows it in Finder. Sound comes out as separate stems, not the mixed track — the clips' sound (lane A, then lane B as its own track when it is used), the music, and one audio track per **A3, A4, …**. Cuts, trims, speed, fades, mutes and reframing travel into the project as editable settings; titles do not, and transitions arrive as plain cuts.

**Auto-edit…** (⋯) re-cuts the sequence from scratch and throws away this arrangement — it asks first.

## Common jobs {#jobs}

### Cut a scene out {#job-cut}

1. Put the playhead where the unwanted part starts and press **Split** ([[sc:editor.split]]).
2. Move to where it ends ([[sc:editor.cut]] jumps between cuts, [[sc:editor.frame]] steps frames) and **Split** again.
3. Click the piece in the middle.
4. **Ripple delete** ([[sc:editor.ripple]]) to close the gap — or **Lift** ([[sc:editor.lift]]) to leave black in its place.
5. **Save**.

### Fix sound that drifted {#job-drift}

1. Find the strip with an offset label (**+0.25s**) on **A1 Clip sound**.
2. Click the label — or select the clip and press **Resync sound** ([[sc:editor.resync]]).
3. To stop it drifting again, press **Link sound**.
4. **Save**.

### Make a J-cut {#job-j-cut}

1. Select the **incoming** shot and press **Unlink sound** ([[sc:editor.link]]).
2. On **A1 Clip sound**, drag that strip's left end earlier, under the end of the previous shot (hold ⌥ to ignore the grid). The picture does not move.
3. Optional: drag a corner handle for a short fade in.
4. Press **Link sound** to keep the offset — the message confirms *Linked at …*.
5. **Save**.

For an L-cut, do the same with the **outgoing** shot and drag its strip's right end later.

### Crossfade two shots' sound {#job-crossfade}

1. Check the two shots' sound sits on different lanes (**Clip sound A** and **Clip sound B**). If not, press **Alternate A/B** on the **Clip sound B** head, or drag one sound to the other lane.
2. Select the **outgoing** shot, press **Unlink sound** ([[sc:editor.link]]) and drag its sound's right end later, under the next shot — it snaps to the next sound's start and to the cut.
3. Drag the right corner handle of that strip for a fade out, and the left corner handle of the incoming sound for a fade in over the same seconds. (The incoming sound can reach back the same way: unlink it and drag its left end earlier.)
4. Play across the cut — both sounds play together while they overlap.
5. **Save**.

### Replace a shot with a retake {#job-retake}

1. Select the shot. Retake works on shots that came from a storyboard.
2. In the inspector's **Clip** section press **Retake**.
3. In *Retake this shot*, edit the prompt if you want, choose **Draft** or **Delivery**, and **Queue the retake**.
4. When the new take finishes, the Editor says *New take of … is ready*: **Use it** swaps it in with the same cut and timings; **Keep the old one** leaves the timeline as it is (the new take stays in the media pool).

When shots from the board have been finished at the delivery pass since you cut, the Editor offers **Use the finished versions** the same way.

### Deliver 1080p {#job-1080p}

1. Press **▾** beside **Render**.
2. Under **Deliver as**, choose **H.264**, **1080p** and **Clean** (or a grain).
3. The button now reads **Render · 1080p**. Press it, or [[sc:editor.render]].
4. If the timeline has holes, decide whether to let them close.
5. The film opens on the Storyboard film screen when it is done.

## Keys {#keys}

The same list is behind the **Keys** button above the tracks.

[[shortcuts:editor,editor-mouse]]
