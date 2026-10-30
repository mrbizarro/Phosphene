# Phosphene 4.19.15

- New in the engine picker: **H3 Flex (preview)**, right next to Hailuo H3. Try Pika Labs' faster version of H3, running natively on Apple Silicon with Phosphene's own code. As far as we know, this is the first Mac port.
- **About 2.2x faster than Best at the same size.** On an M4 Max with 64 GB, a 5-second clip takes about 5 minutes at Draft (640x384) or 16 minutes at High (1024x576). Best took 36.5 minutes on the same High shot and seed.
- Start with a sentence using **Text-to-video**, or a first-frame image using **Image-to-video**. Dialogue and sound are included, just like normal H3.
- Pick **H3 Flex** in the engine picker at the top. If its 2.8 GB adapter is not on your Mac yet, the same click downloads it from Pika's own Hugging Face page and prepares it on your Mac; the picker shows the progress. You need Hailuo H3 installed (if it isn't, the picker offers it first).
- **We're sharing this preview early so you can try it.** Tell us what breaks through GitHub issues or comments on the Pinokio post. We'll fix problems fast.
- Fixed: a build newer than the public release no longer offers the older release as an "update".
- A small update for **Best at High**: its existing speed-up now uses the setting it was trained for, following an update to H3.

## Known limits

- A 15-second clip joins three 5-second pieces. Details can change in later pieces: a dog's breed or a sofa's colour, for example.
- Full size (1344x768) runs; full timing to come. One test pass suggested about 37 minutes per 5-second clip.
- Pika's second mode, which transfers a reference or motion into a video, is not included yet.

**Tip:** Name every person who should stay in the scene in your prompt. The model drops people the prompt doesn't mention. This happens in Pika's own sitcom showcase too.

## Credits

Phosphene is a free, open-source Mac app by Mr Bizarro.

**Powered by MiniMax H3.** Pika H3 Flex is by Pika Labs (Mellis, Inc.). The weights are a MiniMax H3 Model Derivative under the MiniMax H3 Community License. Pika's code is Apache-2.0.

Mac weights on Hugging Face: [Mrbizarro/Pika-H3-Flex-MLX](https://huggingface.co/Mrbizarro/Pika-H3-Flex-MLX) (the converted MLX adapter, with credits and licence). The app itself downloads from Pika's Hugging Face page at a fixed version, never re-hosted by us. Phosphene converts the file on your Mac and marks it as modified with a modification notice. MiniMax H3's licence excludes the EU, UK, South Korea and US, as it already does for H3 in Phosphene.

In Pinokio, press Update, then restart Phosphene.
