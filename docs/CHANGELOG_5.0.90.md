# Phosphene 5.0.90

Fixes:
- Fewer stops while reading prompts: video renders on smaller Macs no longer stop early while reading your prompt.
- Fewer Hailuo H3 memory errors on larger Macs: Hailuo H3 on 64 GB and larger Macs uses memory more carefully and retries in low-memory mode if graphics memory runs tight.
- Clearer Hailuo H3 time estimates: Fast and Best now show separate time estimates suited to your Mac instead of showing the same time.
- Faster multi-picture MiniMax-H3-Image batches: making several pictures in one job loads the model once for the batch instead of reloading it for each picture.

Mac app: it picks up this update by itself the next time it starts. Pinokio: press Update, then restart.
