# abcjs 6.7.0 — vendored

Renderer for ABC music notation. Used by Phosphene's Music Studio to draw the
sheet music YuE2 writes for every song (the `score_abc` in a song's sidecar)
and to let it be edited and re-rendered.

- Source: https://github.com/paulrosen/abcjs (MIT — see LICENSE.md)
- File: `abcjs-basic-min.min.js`, byte-identical to the 6.7.0 npm/cdnjs build
- Vendored rather than loaded from a CDN because the panel is local-first:
  a song's score must render with the network off.
