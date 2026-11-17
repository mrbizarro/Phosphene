# Phosphene 5.0.32

Fix, free for everyone:
- The update pop-up on Pinokio now simply says a new version is out and how to update: press Update now, then Restart now (or use Update in Pinokio, then Start). Your queue, settings and models stay.
- More Pinokio installs hear about new versions: when GitHub's update check is blocked or rate-limited, Phosphene now compares the public version number instead of staying silent.
- Choosing Later on the update pop-up is remembered for that release, so it no longer comes back on every reload. The Update button stays in the top bar.
- Installs and updates now come from Phosphene's own server first (phosphene.video), with GitHub as the second source: the Studio itself, the video engine, the Hailuo H3 engine, the model downloads and the new-version check.
- Mac app users now receive developer notices from Phosphene, and see a quiet, short "an update is available" hint when the app has one ready.

Mac app: no new app to download; the app picks up Studio 5.0.32 on its next update check (or choose Check for Updates). Pinokio: press Update, then restart. Pinokio copies installed before 5.0.32 still update from GitHub, so they update again once GitHub is reachable; a new install from phosphene.video/download works today.
