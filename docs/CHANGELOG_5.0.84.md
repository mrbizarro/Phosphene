# Phosphene 5.0.84

Fixes:
- Smooth video playback (Free): hidden previews and closed What's new clips stop playing in the background, so finished videos play smoothly.
- References before download (Pro): pressing Generate with References before its model is downloaded stops before adding to the queue and offers a one-click Get References button.
- Video ready after downloads (Free): video renders reload cleanly after a finished download or failed load instead of getting stuck saying the model is not loaded.
- Missing video model prompt (Free): every video Generate button stops before adding to the queue when the video model is missing and offers a one-click Download the video model button.
- Stopping renders and quitting (Free): stopping a render or quitting Phosphene no longer stops the next picture or counts a quit as a failure, and external stops say what stopped the render.
- Video renders on 16 GB Macs (Free): reading the prompt now runs in smaller steps so macOS stops cutting off clips, and memory stops during prompt reading wait for memory and retry automatically.

Mac app: it picks up this update by itself the next time it starts. Pinokio: press Update, then restart.
