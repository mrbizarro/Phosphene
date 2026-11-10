# App update states: the push contract between the Mac app and the Studio page

**Who reads this:** the native Mac app (it writes the push) and the Studio page
(`webapp/js/health.js`, it renders the push). Pinokio installs never see any of it.

**Why it exists.** Four judges used to feed one pill and one toast: Sparkle, the signed
channel, the Pro installer and the Studio's own `/app-latest`. None of them reported a
refusal back to the page, so a refused or failed update showed "Update to vX" and
"Updating…" forever. Now there is **one judge, the app**, and it reports the **outcome**,
not the intent. The Studio page renders exactly what it is told and decides nothing.

Pinned by `test_app_review_update_states.py` (the page's functions are run in node
against every state below). Change this file and that test together.

## 1. The push (app to page)

The app evaluates this in the Studio web view whenever anything below changes, and once
after every page load:

```js
window.phosAppUpdate = { ...payload };
window.dispatchEvent(new Event('phos-app-update'));
```

### Fields

| field | type | required | meaning |
|---|---|---|---|
| `caps` | string[] | yes | `"updateAll"`: the app is the only judge; the page stops asking `/app-latest` for good. `"updateStates"`: the app sends `state` (informational; the page keys on `state` being present). |
| `state` | string | yes for new apps | one of the states in section 2. Absent = an app from 5.0.5 to 5.0.10: the page falls back to `behind`. |
| `parts` | object[] | yes when anything is behind | what the state is about: `[{ "kind": "studio" \| "pro" \| "app", "version": "5.0.14" }]`. `pro` is shown as **Studio** (the Pro pack is the Studio a Pro user runs). At most one Studio-side part and one `app` part. |
| `reason` | string | with `waiting` | one of the reasons in section 3. |
| `message` | string | with `failed`; optional elsewhere | one plain sentence for the person. With `failed` it is the Pro panel's own sentence. When present it replaces the page's default sentence for that state. No raw errors, no paths. |
| `progress` | number 0..1 | optional, with `downloading` | shown as a percentage in the pill. |
| `need_app` | string | optional, with `needs_app` | the lowest app version that can take this update, e.g. `"5.0.11"`. |
| `app_version` | string | optional | the running app's version, e.g. `"5.0.10"`. |
| `current` | string | yes (already sent) | the running Studio version. |
| `behind` | bool | yes (already sent) | kept for older Studio pages: true while a pill should show. |
| `target` | string | yes (already sent) | kept for older Studio pages: the highest version among `parts`. |
| `what` | string | yes (already sent) | kept for older Studio pages: "Pro 5.0.14 and the app 5.0.11". A page that gets no `parts` reads the parts out of this sentence. |

Versions are bare (`5.0.14`); a leading `v` and a `-pro.<hash>` suffix are tolerated and
stripped by the page.

### Example

```json
{ "caps": ["updateAll", "updateStates"], "state": "waiting", "reason": "model_download",
  "parts": [{ "kind": "pro", "version": "5.0.14" }],
  "current": "5.0.13", "app_version": "5.0.11",
  "behind": true, "target": "5.0.14", "what": "Pro 5.0.14" }
```

## 2. States

| `state` | when the app sends it | pill | click sends | toast (once per change) |
|---|---|---|---|---|
| `idle` | nothing is behind | none | – | none |
| `available` | something is behind and nothing has been started | **Update Studio to v5.0.14** / **Update app to v5.0.11** / **Update Studio to v5.0.14 and app to v5.0.11** | `updateAll` | none |
| `downloading` | the app is fetching the Studio, the Pro pack or the app update | *Downloading Studio v5.0.14 40%* (not a button) | – | "Downloading Studio v5.0.14." |
| `waiting` | downloaded and verified; the switch is held back | *Studio v5.0.14 is ready* (not a button) | – | "Studio v5.0.14 is downloaded. It switches over when *reason*." |
| `needs_app` | the channel manifest or the Pro pack was refused for `min_app` | **Update the Phosphene app first** (amber) | `checkUpdate` | "Studio v5.0.14 needs a newer Phosphene app (v5.0.11 or newer). Click to update the app." |
| `failed` | an install that will not be retried by itself: bad hash, tree mismatch, disk full, could not be activated, downgrade, a quarantined manifest, offline at the click | **Update failed. Try again** (amber) | `updateAll` | `message` (stays until dismissed) |
| `done` | a click changed something and the new version is running | none | – | `message`, or "Studio v5.0.14 is installed." |

Rules for the app:

1. **Never leave `available` up for something that was refused or failed.** A refusal is
   `needs_app`; a failure is `failed`. This is the whole point of the contract.
2. **Answer every click.** After `updateAll` the next push must be one of `downloading`,
   `waiting`, `needs_app`, `failed` or `done`, within a few seconds, including when the
   Mac is offline (`failed`, with "Phosphene could not reach the update server. Check
   the connection and try again."). The page shows nothing of its own after a click from
   an app that sends `state`.
3. **`done` is sent once**, after the Studio restarts into the new version (the page is
   new then; the push after its load carries `done`). Follow it with `idle` on the next
   change. The page shows each outcome once per window, so repeating `done` is harmless.
   Suggested `message`: "Studio updated to v5.0.13. The app itself (5.0.10) is current."
   This replaces the updater's stock "You're up to date" dialog after a click that
   changed something.
4. **A re-published pack under the same version is not "behind"** for the label: send
   `available` only when the version the person would read actually changes, or name it
   in `message`.
5. **An app update that is downloaded and waiting for a quit** is `waiting` with reason
   `app_quit`, not `available`.

## 3. `waiting` reasons

| `reason` | the page says "…switches over when" | source |
|---|---|---|
| `render` | the clip that is rendering finishes | Studio `/status.restart_busy_reason` |
| `queue` | the queue is empty | Studio `/status.restart_busy_reason` |
| `model_download` | the model download finishes | Studio `/status.restart_busy_reason`, or the app's own model download |
| `engine_install` | the engine install finishes | Studio `/status.restart_busy_reason`, or the app's bootstrap |
| `app_quit` | you quit Phosphene and open it again | the app |

An unknown reason is shown as "…when Phosphene is idle".

## 4. The Studio's side of "is it safe to restart"

`GET /status` carries:

| field | meaning |
|---|---|
| `restart_busy` | bool. True while a restart would cut work. |
| `restart_busy_reason` | `"render"`, `"queue"`, `"model_download"`, `"engine_install"` or `""`. Pass it through as the `waiting` reason. |

`restart_busy` is true when a clip is rendering, when **clips are waiting in a queue
that is not paused**, or while an engine install, an engine repair or a model download
is running. A paused queue is not busy: nothing in it will render until someone
resumes it.

Two consequences for the app:

- **Read `restart_busy` before pausing the queue.** Pausing changes the answer to
  "not busy", which is the moment the restart is safe.
- **Always resume.** Any path that paused the queue and then did not restart must
  resume it (`POST /queue/resume`).

Also in `/status`, for the app's quiet screen and for support:

| field | meaning |
|---|---|
| `hold` | `null`, or `{scope, text, since_ts, eta_min, waiting}`: what is holding queued clips. `scope: "h3"` means only Hailuo H3 clips wait. |
| `engine_restart` | `{count, last_ts, restarting, text}`: the post-decode rescue; `count` is per Studio run. |

## 5. Messages from the page to the app

Unchanged: `window.webkit.messageHandlers.phosphene.postMessage({ type })`.

| `type` | sent when |
|---|---|
| `updateAll` | the pill is clicked in `available` or `failed` |
| `checkUpdate` | the pill is clicked in `needs_app`, or by an app without `caps: ["updateAll"]` |

## 6. Compatibility

| app | page behaviour |
|---|---|
| older than 5.0.5 (no push) | the page asks `/app-latest` every ten minutes and shows "Update Studio to vX"; a click sends `checkUpdate`. |
| 5.0.5 to 5.0.10 (`caps: ["updateAll"]`, no `state`) | the pill comes only from the push (`behind` + `what`); `/app-latest` is not asked. A click says "Asked Phosphene to update Studio to vX. This button goes away when it is installed." It never says "Updating…". |
| new (sends `state`) | everything above. |
