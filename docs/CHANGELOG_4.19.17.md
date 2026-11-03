# Phosphene 4.19.17

**A bug-fix release: fewer crashes when a video starts, and clear messages instead of errors you could not act on.** Free for everyone.

- **Fixed: "the graphics chip took too long" crashes at the start of a video.** On some Macs, mostly 16 GB ones and Macs with the models on a slower or busy disk, an LTX render could crash while it was reading your prompt, and trying again often crashed the same way. Phosphene now loads that part of the model before the graphics chip needs it, so the chip is never left waiting on the disk. Your clips look exactly the same and take no longer.
- **Fixed: training stopped with "exited with code 1" and no reason.** Most of these were runs started before the training model files had finished downloading. Now Start and Retry tell you what is missing and bring back the Download all button, instead of queueing a run that cannot work. If a training run does fail, its card shows the trainer's own reason.
- **Fixed: image renders with a LoRA that is no longer on your Mac.** If a LoRA you had picked was deleted or renamed, every render failed with "LoRA file not found". Phosphene now drops it from the picker and, if one still slips through, names the file and asks you to un-pick it.
- **Fixed: FLUX.2 LoRAs made for a different FLUX.2 size.** A LoRA made for FLUX.2 [klein] 9B or [dev] cannot load on the [klein] 4B that Auto and Klein-Edit use. It used to fail halfway with a "matmul" error; now Phosphene tells you before the render starts which model the LoRA was made for.
- **Clearer: too many reference images.** The image engines take at most 3 reference images (FLUX 3 takes 10). Generate now says so before you start, instead of the render failing.
- **Clearer: not enough disk space for an image model.** The space check now uses the real download size of the model you picked (for example 4.6 GB for FLUX.2 [klein] 4B, 57.7 GB for Reference Edit) and tells you how much more to free, in plain words.
- **New: a one-time "help keep the lights on" card.** After your fifth finished render, a small card in the corner asks once whether you would star Phosphene on GitHub. Whatever you click, it never comes back. It never covers your clip, and if you already starred Phosphene from inside the app it never shows.

Thanks to everyone whose error reports pointed at these.

All of this is free for everyone, and it is also in Phosphene 5.0.

In Pinokio, press Update, then restart Phosphene.
