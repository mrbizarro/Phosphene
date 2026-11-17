# Phosphene 5.0.41

Fix, free for everyone:
- Mac app setup: when a new install cannot reach our update service and sets up its built-in Studio instead, it now still downloads the video models, taking the big files from Hugging Face. Before, that spare path looked for them on our own server, which now keeps only small files, so the model step could fail.

Mac app: a new app build 5.0.41; the app offers it on its next update check (or choose Check for Updates), and the download page has it. Pinokio: nothing changes for you in this release (press Update to get the new version number).
