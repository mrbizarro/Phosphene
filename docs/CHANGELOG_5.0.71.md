# Phosphene 5.0.71

Fixes:
- Downloads: if a download stopped partway (a dropped connection, for example), it could start again from zero. Phosphene now keeps what it already saved and tries again by itself, from another place if needed. If it still stops, choose Download again in the Studio, or Resume in the Mac app, and it carries on from where it stopped.

Mac app: it picks up this update by itself the next time it starts. Pinokio: press Update, then restart.
