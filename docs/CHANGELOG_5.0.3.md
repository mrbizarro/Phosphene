# Phosphene 5.0.3

**Faster pictures, a Mac app that sets Hailuo H3 up by itself, and a fix for One Shot renders that failed on the Mac app.** Nothing that was free was taken away.

## Images

- **P-H3-Image is about five times faster by default.** Fast is now the first choice: a picture takes about 1 minute instead of about 5 (56 s for the first and about 47 s for each next one on an M4 Max with 64 GB). On 64 GB Macs the image models stay loaded between pictures, so the next one starts right away. When memory gets tight Phosphene lets them go and renders the normal way. Fast makes a 1.5 MP picture. The 2, 4 and 8 MP choices are still in the picker for when you want more detail.
- **Pro: Faster Fast images.** With Pro, Fast is quicker still, at a slightly smaller size. The picker shows the size you will get.
- The Hailuo H3 engine moves to a new version so it can keep image models loaded. Press **Update**, then restart. An older H3 engine still works at the new size, just without the speed-up from keeping models loaded.
- If the image engine is missing, Phosphene now says so and says how to get it back.

## One Shot

- **One Shot renders that start from a picture no longer fail on the Mac app.** The step that takes the last frame of a clip for the next one used an option the Mac app's video tool no longer has. It now uses the current option and falls back to the old one, so it works with both.

## Mac app

- **Hailuo H3 sets itself up.** If the H3 weights are on your Mac, the app prepares H3 in the background. There is no repair box and nothing to click. On Macs with 36 to 59 GB it also builds the compact engine H3 needs there, once, when there is room.
- **One calm loading logo** in the app and in the Studio, the same animation everywhere.
- **Clearer anonymous counts.** The Mac app now counts when it is opened, whether its setup worked, whether Studio started, whether an update worked and whether it closed unexpectedly, so problems can be found and fixed. It is anonymous, never includes your prompts, files or paths, and follows the same switch as the other statistics (Settings, Anonymous usage analytics).

## Everywhere

- The header **Pro** button for Free users wears the same purple tag as the rest of the app.
- The prompt box hint uses plain short sentences.
- **Pro video:** Faster H3 picture-to-video is kept on the Pro speed path with the new engine.

## Free

- Nothing was taken away from Free. The faster default, keeping models loaded and the new engine are for everyone.
