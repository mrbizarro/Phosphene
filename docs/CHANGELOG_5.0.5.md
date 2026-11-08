# Phosphene 5.0.5

**Pictures work again in the Mac app, songs wait for memory on smaller Macs, and a missing language model file is named before a video starts.** Nothing that was free was taken away.

## Mac app

- **Pictures work on a fresh Mac app install.** FLUX.2, Qwen-Edit and Ideogram pictures failed on Mac app installs that had not copied an engine over from Pinokio, with a message that told people to quit and reopen the app (which did nothing). Now, when you make a picture and the image engine is missing, Phosphene adds it by itself, once (about a minute, no models are downloaded), and then makes the picture. If it cannot, it says why in plain words: no internet, or the disk is full.

## Video

- **A missing text-model file is named before the video starts.** If the model that reads your prompt is incomplete on disk, Phosphene now stops straight away, names the missing files and points to the Repair banner in Settings, instead of failing a moment later with an unclear message.

## Music

- **Songs wait for memory on Macs with 24 GB or less.** A song needs about 11 GB. Phosphene now waits up to 90 seconds for enough free memory before the first note and shows "Waiting for memory"; if there still is not enough, it stops with a plain message instead of failing partway through. When memory runs out anyway, the message now says which kind of shortage it was.

## Everywhere

- Press **Update**, then restart (Pinokio). On the Mac app the update arrives by itself. For Pinokio the picture change makes no difference, because Pinokio already installs the image engine.

## Free

- Nothing was taken away from Free. All of this is Free.
