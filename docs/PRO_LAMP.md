# Pro header and lamp

The studio's Pro button opens an optional upgrade dialog. It does not gate any
free feature. The Pro dialog and the Mac app's Pro tab share the title, copy,
SVG paths, Archivo font, colour and 2.8-second entrance.

The source is the `LIGHTS_ON_HEADER` block in `webapp/index.html` and the
`LIGHTS_ON_CSS` block in `webapp/style/panel.css`. The native app bundles exact
copies, rendered locally by `LightsOnHeader.swift`. In the app repository:

```sh
python3 tools/sync_pro_header.py /path/to/free/studio
python3 tools/sync_pro_header.py /path/to/free/studio --check
```

The SVG is decorative. The heading and subline remain accessible text. The
entrance runs once each time the dialog becomes visible or the native Pro tab
is created. Polls and repeated open calls do not replay it. The pull chain,
small head shiver and two brief bulb pulses settle into a steady warm light.
There are no looping effects or audio. Reduced motion starts with the lamp
already on, including the native accessibility preference.

Escape, Tab trapping and scroll locking use the studio's modal scaffold.
Closing returns focus to the opener. Pricing and waitlist links remain visible
without running any animation. At narrow widths the price cards stack.

Validation against a sandbox studio (with the shared GPU locks held):

```sh
python3 scripts/measure_pro_lamp.py http://127.0.0.1:PORT /path/to/proof
```

This exercises Chromium and WebKit, focus, revisit timing, reduced motion,
pricing, and narrow layouts. It saves screenshots and a 5.2-second recording.
No jobs or store actions are submitted. The paid engine is not part of this UI.

## Surface inventory

The free studio had no paid upgrade page on the starting branch. Its new
header Pro button is the upgrade entry. Existing “Q8 Pro” labels name a video
quality preset, not the paid edition, and are unchanged. Model licence dialogs
also stay separate. The Mac app has one pricing screen, `ProView`, reached by
the Pro tab or its menu command. Its licence and installed-component cards
share that header. The Support tab only points people to Pro; it has no prices.
