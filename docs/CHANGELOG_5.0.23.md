# Phosphene 5.0.23

A fix release: the causes of failed renders that the fleet reported, one by one.

Free for everyone:
- Videos with LoRAs (your trained characters or styles) on Macs with 16 to 24 GB of memory: some LoRAs, and some sets of two or more, stopped the render at the start with "Expected shape (19, 4096) but received shape (27, 4096) for parameter attn1.to_gate_logits.lora_a". They render now, with the same result as on bigger Macs.
- When macOS's graphics watchdog stops a video while it reads the prompt (most often on M1 and M2 Macs), Phosphene now waits 45 seconds and tries once more in a fresh engine process, also when it had already switched to smaller graphics steps earlier. Before, that second kind of stop failed the render at once.
- MiniMax H3: when macOS stops an H3 render with a graphics error ("Impacting Interactivity"), Phosphene remembers it, and later H3 renders on that Mac run in smaller graphics steps, the remedy LTX videos already use. Same picture; it may be a little slower. We have not yet measured how often it avoids the stop on H3.
- A picture or clip that a waiting render still needs can no longer be deleted from Recent uploads or moved to the Trash from the gallery. Phosphene says how many renders use it. Before, the render failed later with "The reference image is no longer on disk".
- Pinokio: when the image engine is missing, making a picture now installs it (about a minute, no models) and carries on. Before, the message sent you to "Install/repair image engines" in Pinokio's menu. That action is still there if the install cannot run.
- When the 720p export of a finished clip fails, the message now gives the reason and what to do, instead of only "upscale exited with code 254".
- Cover a song: a recording that cannot be read now says so and asks for another file, instead of a technical error.
- Installs older than 5.0.21 see one message asking them to update: 5.0.20 had a bug that stopped videos with a LoRA on Macs with 24 GB or less.

Mac app: no new app to download; the app picks up Studio 5.0.23 by itself. Pinokio: press Update, then restart.
