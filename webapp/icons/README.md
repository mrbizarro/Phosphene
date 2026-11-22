# Studio icons (one file per icon)

`src/<name>.svg` is the icon as drawn (24x24, white part in currentColor, one spectrum accent).
`<name>.svg` (dark theme) and `light/<name>.svg` (light theme) are made from it with the ink baked in, because
the Studio shows icons as images so the accent keeps its colours. To swap the set in one step, put the new
files in a folder (same names, or the sheet's long names like text-to-video / lip-sync) and run
`swap_icons.py <folder> <studio>` (layout-build/scripts). Names:

- Video modes: text, character, image, startend, keyframes, extend, sound, lipsync, revoice, remix
- Remix tools: ingredients, motion, colorize, upscale
- Picture slots: person, thing, place, frame
- Voice marks on a character: voice, novoice
- Sections and dock: lora, upscale, batch, more
- In the set, not placed yet: prompt, seed, help, queue, generate
