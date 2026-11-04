# Phosphene 4.19.18

**Lip-sync that keeps time, a friendlier thank-you card, and a stopped image download that carries on.** Free for everyone.

- **Lip-sync: the mouth now moves with the voice, not a few frames ahead.** Both video engines' lip-sync clips moved the mouth up to a quarter of a second before the voice. Phosphene now checks each lip-sync clip after it is made and holds the picture back by exactly those frames; the sound, the length and where the clip sits on your song stay the same. A clip it cannot read clearly (no clear face, singing that blurs the timing) is left as it was made.
- **A friendlier one-time thank-you card, and one-click starring if your Mac is signed in to GitHub.** After your fifth video, once you have stepped away for half a minute, a small card asks for a GitHub star. One button, a close button, and it never comes back. If your Mac is already signed in to GitHub (the `gh` command), the button stars Phosphene right there and says thank you; otherwise it opens GitHub. If you already starred, you never see it.
- **Fixed: "The download for ... is stuck" on every Generate.** If the first download of an image model (Ideogram 4, FLUX.2, Reference Edit, Z-Image Turbo) was stopped partway, every later Generate with that model could fail at once with that message until you restarted Phosphene. Now every Generate carries the download on from where it is.
- **Clearer messages:** an image model download that breaks off says which model, how far it got and what went wrong; a LoRA that does not fit the image model is named; the progress line says when a file has to start again from the beginning.

All of this is free for everyone, and it is also in Phosphene 5.0.

In Pinokio, press Update, then restart Phosphene.
