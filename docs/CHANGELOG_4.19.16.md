# Phosphene 4.19.16

**New, free for everyone: let Claude, Codex or any AI agent make clips with Phosphene.** Plus fixes for the top errors from the field.

- **Agent tools (Free).** Phosphene now comes with a `phosphene` command and an MCP server, so an AI agent such as Claude Code or Codex can make video, images and music on your Mac, wait for them and pick up the files. Add it with one line, for example `claude mcp add phosphene -- ~/pinokio/api/phosphene.git/ltx-2-mlx/env/bin/python ~/pinokio/api/phosphene.git/agents/phosphene_mcp.py` (the exact line for your install is at `http://127.0.0.1:8198/agents/info`). The agent finds Phosphene by itself while it is running, uses the same checks as the page (what your Mac can run, model licences), and can never accept a model licence for you. Guide: docs/AGENT_TOOLS.md.
- **Fixed: a render could start before its model files finished downloading.** Pressing Generate while the video model was still coming down made the clip fail with "model is incomplete". Now the Now card says "Waiting for the model download to finish" with the percentage, and the render starts by itself when the files land. Press Stop to cancel the wait.
- **Fixed: "Expected shape ... initial_conv.weight" right after switching video generation.** A small upscaler settings file could be missing and the model still counted as complete. Phosphene now checks those files too, and Repair fetches whatever is missing.
- **Fixed: on 16 GB Macs, a render stopped by macOS while it read the prompt.** After such a stop, the next render waits up to a minute and a half for memory to free up, and if it does not, tells you what to quit instead of failing again.
- **Fixed: FLUX 3 "returned no complete image"** when the image had in fact finished. Phosphene now recovers it, and a real miss says how many images finished.
- **H3 Flex (preview) greys out what it cannot do.** Keyframes, Extend, Lip-sync, Add sound, Fast HD and the 10 s single pass stay visible but greyed while H3 Flex is picked, with the reason, instead of quietly rendering on H3's own sampler.
- **The dark look is black now**, the same as Phosphene 5.0. The neon accents stay; light mode is unchanged.

Thanks to everyone whose error reports pointed at these.

All of this is free for everyone, and it is also in Phosphene 5.0.

In Pinokio, press Update, then restart Phosphene.
