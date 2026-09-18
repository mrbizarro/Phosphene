# The clip bar, and what is left in the inspector

Status: **built**, 2026-09-12. Sibling to `docs/EDITOR_EFFECTS_MODEL.md` and
`docs/EDITOR_SAVE_MODEL.md`.

## The ruling

> "It's really hard when you are using the editor of Phosphene to use the
> basic tools you actually need to use all the time, for instance, unlink and
> link audio... We have an empty corner that we can use in the upper part of
> the timeline, like a button thingy. Of course, the buttons need to be really
> clear about what they are for. I find myself all the time un-syncing and
> syncing. Maybe some kind of delete button for the anchors of the audio.
> Basic stuff that we are missing exists in a normal video editor, and they
> should be there. We have some, but they are hidden inside that menu, the
> toggle menu on the right. That is not a good use... You can leave it if the
> person wants to dig into the options and then put advanced options there."

And, the same day:

> "You should be able to select, hit Shift, and select multiple clips and move
> them together. It's very important."

## The rule that decides where a control lives

**A verb goes on the bar. A property stays in the inspector.**

A cutter presses split, lift, ripple, unlink and resync hundreds of times in a
session and sets a font size twice. Both were drawn identically, in the same
212px rail, in the corner of the screen — and four of the verbs were rendered
*conditionally*, so they appeared and disappeared as the selection changed.
That is how "Delete sound" came to be invisible on every linked clip, which is
almost every clip: it read as a feature that did not exist.

The test that applies to anything new is not "is this related to a clip" but
**how many times an hour**. Rare, slow, or dialogue-opening stays in the rail
(Retake is the example: it starts a render).

## 2026-09-17: icons, one row, and the panels

The bar is icons only now — "the menu doesn't need to have the words on each
one… just when you hover over it, you see an explanation" — every tooltip
leads with the name, then the consequence, then the key (`sbePaintCbar`), and
the words come back in the More menu and the right-click menu, where a list
reads better than a row. The transport (play, mute, time) moved under the
Program monitor, so the bar and the timeline's own controls (Sound mode, Snap,
zoom, ⓘ, Keys) share ONE row, `.sbe-toolrow`, with the grab below it. The
inspector is a panel you open (☰ / ⌘I / double-click a clip), not a fixture.
The sound lanes no longer size themselves: Sound mode (⇧A) is the switch. See
`webapp/docs/editor.md` §layout and §compact.

## Where it is

A row of its own, between the monitors and the transport, directly above the
tracks — where Resolve and Final Cut both put it, and the corner the owner
pointed at. It costs the column 28px plus its gap, which `sbeFitMonitors`
measures like every other child; at 1600×1000 that is the picture going from
620×349 to 594×334. That is the price, it is paid on purpose, and it is the
one number to argue with if the bar is ever the wrong trade.

## What is on it, in order

Cut, then remove, then the sound, then the pin.

| | verb | acts on | key |
|---|---|---|---|
| 1 | **Split** | the shot under the **playhead** — never the selection, as in every NLE | `S` |
| 2 | **Lift** | the selection; leaves the hole (a black slug) | `⌫` |
| 3 | **Ripple delete** | the selection; closes the gap | `⇧⌫` |
| 4 | **Duplicate** | the selection; each copy lands behind its original | `D` |
| 5 | **Unlink / Re-link / Link sound** | the selection | `⇧L` |
| 6 | **Resync sound** | the selection's out-of-sync strips | `⇧R` |
| 7 | **Mute / Unmute sound** | the selection | |
| 8 | **Delete sound** | the selection; only once the halves are separate | |
| 9 | **Clear points** | the selection's level anchors | |
| 10 | **Lock / Unlock** | the selection | |

Left of them is the one readout that makes the rest legible: the clip's name,
or *"3 clips selected"*. Right of them is **More**, which holds nothing of its
own — see *Overflow* below.

### Three rules every button follows

1. **It disables; it never disappears.** A disabled button's tooltip carries
   the sentence that would make it work ("Unlink the sound first…", "Put the
   playhead over a shot…"). A control that is not on screen teaches nothing.
2. **The label says what you get, the tooltip says the consequence.** "Unlink
   sound" / "Frees this clip's sound from the picture so you can slide it under
   the shot before or after — the J-cut and the L-cut. The picture does not
   move."
3. **One table, three consumers.** `sbeCbarModel()` returns the row set; the
   bar, the overflow panel and the right-click menu all read it. Three sets of
   labels would be three chances for one of them to go on offering a verb the
   model has stopped accepting.

### Overflow, not wrap

`flex-wrap` would answer a narrow pane with a second row, and a second row is
height `sbeFitMonitors` takes off the picture. So `sbeCbarFit` measures the
row and moves its tail into `#sbeCbarMenu`, last button first, until what is
left fits; everything comes home when the pane is widened. There is exactly
one copy of every control, and its position is stamped once (`data-ord`) so a
button returns to where it came from. Measured once per width-and-label
change, not once per painted frame — `sbePaint` runs on every frame of a drag.
Below the 900px stacking breakpoint the page is the scroller, there is no
column height to protect, and the bar is allowed to wrap.

## What stayed in the inspector

Everything that is a **property of the one selected clip**: transition kind and
length, speed, picture fades, brightness, zoom and reframe, sound fades, the
level line's "Add point at playhead", a title's text and styling — and Retake.
Its first section now carries one line, *"Advanced — the everyday verbs are on
the bar above the tracks"*, so a panel that used to be full of buttons does not
read as a panel with buttons missing. With several clips selected it says so,
and whose properties it is showing.

The three section names — **Clip / Sound / Effects** — are unchanged. That is
the model `docs/EDITOR_EFFECTS_MODEL.md` describes and the home the next effect
lands in without a decision.

## "The anchors of the audio"

There are two things in this editor that could be called an anchor, and the
honest answer covers both.

* **The level points** — the dots on the yellow line inside a sound strip. They
  are the only anchors a person *places* on a sound, so **Clear points** is the
  delete button the owner asked for. Removing one is still a right-click or a
  shift-click on the dot itself; the bar removes them all, across the whole
  selection, and says how many went.
* **The link point** — where a strip is pinned to its picture. It is not a
  thing you delete, it is a state you change, and **Unlink sound** /
  **Re-link sound** / **Resync sound** are that state. They sit adjacent on the
  bar because the difference between the last two is the thing that is hard to
  hold in your head: re-link makes the pair permanent at whatever offset it has,
  resync only slides the sound back under its own frame and leaves it free.

No new data model was invented for either.

## Selecting several clips

`SBE.sel` stays a single id and stays the **primary** — the one clip the
inspector describes, the preview loads and the sound buttons read their state
from. `SBE.selSet` is the full selection and always contains it.

The invariant is repaired in **one place**, `sbeSelNormalise()` at the top of
`sbePaint`, and not at every assignment. A lift, a duplicate, a resync and a
dozen other paths write `SBE.sel = …` directly; a set maintained by hand at
each of them is a set that is stale by the next feature. So pointing `SBE.sel`
at a clip outside the set **collapses** the selection to that clip — which is
the correct behaviour at every one of those call sites, and costs them nothing.

### The gestures, and the two modifiers that were already taken

| gesture | what it does |
|---|---|
| click | select this shot |
| click inside a multiple selection | keeps the selection so the drag can move the block; collapses to this shot if the pointer never moves |
| shift-click | the range, from the anchor |
| ⌘-click | add or drop one — resolved on **pointerup**, because the same chord is ripple-drag while the pointer moves |
| ⌘A / Esc / click empty track | all / none / none |
| drag a selected shot | slides the whole block |
| alt+shift-drag | reorder (it was shift alone; shift went to the range) |
| alt+←/→ | nudge the selection one frame, ⇧ ten |

Reorder also became **Move earlier / Move later** in the right-click menu,
which is easier to find than the modifier it gave up.

### The arithmetic

A group move is one question per **boundary**. Slide a set by `d` and leave
everything else put: the only gaps that change are those between a moving clip
and a still one, and each changes by exactly ±d. So the legal range of `d` is
closed-form — the intersection of "no gap goes negative" over every boundary,
which is `sbeGroupLimits` — and it is the same clamp a single-clip move already
applies at its two neighbours, written once for any number of clips. Relative
spacing is preserved by construction, holes inside the selection included.

**The sound comes too**, and this is the one place the rule differs from a
single-clip drag. Dragging one picture deliberately leaves its strip behind —
that is how a J-cut is made. Sliding a block is not that gesture: it means "put
these four shots a second later", and a strip left behind would be a sync error
nobody asked for. Carrying also preserves any J-cut already inside the
selection, because every strip moves by the same `d`.

A group verb is **one undo step** (`sbeMutateEach`), and a locked member does
not abort the rest — refusing the whole gesture over one pinned shot would make
a multiple selection unusable — but the ones left alone are named in a toast.
A mixed selection **converges**: the primary decides, so pressing Lock over
three shots where one is pinned ends with three pinned shots, which is what the
button says.

## The bug this pass found

**⌘S split the shot under the playhead instead of saving.** It has been
documented as the manual save since `docs/EDITOR_SAVE_MODEL.md` §1 was written
— "`Save` (and ⌘S)" — and it never did it: the bare-`S` split took the key
first, with no modifier guard. The one chord every person on a Mac presses to
make their work safe cut their film in half. Gated now in
`TheEverydayGesturesThatWereMissing`.

## 2026-09-17 (round 2): fast tooltips, the Source monitor back, real icons

Owner, after using the redesign: "the tooltips on the buttons are too slow",
"you have two empty dark spaces on the sides [of the Program monitor] … a place
where you can drag the clips and see them … is actually necessary", "the icons
are not very clear, are really weird, and maybe deformed", and the panel
toggles are "a good idea but it's not clear".

* **Icons.** The sprite was bare `<g>`s on a 24-unit grid used from `<svg>`s
  with no viewBox, so a 15px box drew only the top-left 15 units of each glyph
  — that was the deformation. Every glyph is now a `<symbol viewBox="0 0 256
  256">` in Phosphor's geometry (the set the rest of the panel uses): scissors
  for split, a clip lifted out of its gap for lift, a gap closing for ripple
  delete, Phosphor's copy / link / arrows-clockwise / speaker / speaker-slash /
  lock / lock-open / corners-out / keyboard, a trash can with a waveform for
  delete sound, a level line with an × for clear points, a smiley with a
  sparkle for Face Fix, a waveform for Sound mode, sidebars for Inspector and
  Panels, a split screen with a play mark for Source. 18px, stroke 22/256
  (≈1.5px), set once on `.sbe-cbar-i`. The preview's mute is an icon too.
* **View group.** Source · Inspector · Sound · Panels · Full screen are one
  labelled segmented control in the header (`#sbeView`). Pressed = the panel is
  showing (`aria-pressed`, accent fill); `sbeViewTip` writes a tooltip that
  says what a click will show or hide, and the key. Labels fold below a
  1360px window unless the side panels are hidden.
* **Tooltips** (`webapp/js/tips.js`). One shared element: 150 ms after the
  pointer settles, immediately while sliding along a row (400 ms warm window),
  on keyboard focus-visible, flipped above / clamped 8px inside the window, no
  transition under reduced motion. Name · body · keycaps (from `data-shortcut`
  or a trailing "(⌘I)"). The painters keep writing `title`; a button in scope
  has it moved to `data-tip` on hover/focus and by a MutationObserver on every
  rewrite, so the OS tooltip never shows. Scope: the Editor's icon buttons,
  header buttons, chips, and the player's `.po-act` pills.
* **Source monitor** is on screen by default (`phos_sbe_src`, hide with Source
  or ×). Each monitor column is exactly its picture's width, so neither is
  squeezed out of 16:9. It is a drop target: a pool row (`edPoolDragMove` /
  `edPoolDragEnd`) or a shot dragged from the track (`sbeOnTrackMove` /
  `sbeOnTrackUp`, the track restored from the snapshot, no undo step) loads
  there; `sbeSrcDropHover` lights it.

## Deliberately not built

* **Copy / paste clips.** `Duplicate` covers "again, right here"; a clipboard
  that survives across drafts needs insert-at-playhead semantics the gap model
  does not have yet.
* **J / K / L transport.** `Space` and the arrows cover play and step. The
  shuttle needs a playback-rate model the one-`<video>` stage does not have.
* **Snapping to a cut or to the playhead.** Snap is the beat grid only. The
  model already clamps at neighbours, so butt joins are automatic; snapping to
  the playhead is a real want and a bigger change than this pass.
* **Overwrite drop from the media pool.** A drop inserts with ripple
  (`sbeInsertAt`), which is the safe half; overwrite needs a trim-on-drop rule.
* **A verb on the bar for a title or a card.** The bar acts on clips; with an
  overlay selected the readout says so and `⌫` and the inspector handle it.
