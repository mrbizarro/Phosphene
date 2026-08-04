# Security policy

## Supported versions

Security fixes are made against the current `main` branch. Release tags are
point-in-time snapshots; users should compare the exact commit they run with
the current release before reporting a suspected regression.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that exposes credentials,
permits code execution, or reads/writes files outside Phosphene's data roots.
If GitHub private vulnerability reporting is enabled, use **Security → Report
a vulnerability**; otherwise contact the maintainer privately before opening
an issue. Include the exact commit, macOS version, reproduction steps, and the
narrowest proof of impact possible. Do not include live API tokens or private
generated media.

## Threat model

Phosphene is a local desktop application, not a hardened multi-user or remote
service. The supported deployment is:

- one trusted macOS user;
- the HTTP panel listening only on `127.0.0.1`;
- no reverse proxy, tunnel, port-forward, or LAN exposure;
- model and LoRA files from sources the user trusts;
- no Full Disk Access or other macOS privacy permission granted to Pinokio or
  the Python environment unless a separately reviewed feature requires it.

The loopback API intentionally has no login. Host/Origin validation and
browser anti-framing headers protect against remote web pages, but another
process running as the same user can still call the API and can generally read
or modify that user's files directly. Do not run Phosphene alongside untrusted
local software or in a concurrently used shared account.

Phosphene is not a process sandbox. Its reviewed Python and JavaScript, Python
dependencies, optional engine code, and native libraries run with the current
macOS user's filesystem permissions. Environment filtering reduces accidental
credential inheritance; it does not prevent dependency code from reading files
that the same user can read.

## Credential handling

- GitHub credentials are never discovered automatically. Repo statistics are
  disabled by default and require `PHOSPHENE_ENABLE_REPO_STATS=1` plus the
  dedicated `PHOSPHENE_REPO_STATS_TOKEN` variable. `GH_TOKEN`, `GITHUB_TOKEN`,
  and `gh auth token` are not used by the panel.
- Hugging Face and CivitAI tokens entered in Settings are stored under
  `state/` with mode `0600` and are masked in HTTP responses. Prefer scoped,
  read-only tokens.
- AI and training subprocesses receive a reduced environment. Unrelated shell,
  cloud, and developer credentials are not forwarded. Only credentials needed
  for a specific subprocess are injected.
- BFL is optional cloud generation. When selected, prompts and the BFL API key
  are sent only to `https://api.bfl.ml/v1`; custom credential-bearing endpoints
  are rejected.

## Updates and executable dependencies

The read-only version badge may contact GitHub. It does not modify code by
default. Review incoming changes and use Pinokio's explicit Update action.
`PHOSPHENE_ENABLE_SELF_UPDATE=1` restores the in-panel pull for advanced users.

Fast-forward failure does not trigger a hard reset by default. The historical
recovery path requires `PHOSPHENE_ALLOW_DESTRUCTIVE_UPDATE=1`; Reset → Install
is the preferred visible recovery flow because linked models, outputs, uploads,
and state survive it.

The `ltx-2-mlx` runtime and optional H3 engine are pinned to immutable commits.
Installer Python dependency graphs are constrained to reviewed exact versions,
and the bundled LTX/Gemma/IC-LoRA/H3 downloads use immutable Hugging Face
revisions. These remain third-party supply-chain inputs: a pin makes installs
repeatable but is not a guarantee that upstream code or weights are benign.
Keep Pinokio's protected mode enabled and install optional engines, community
LoRAs, and user-selected models only when their sources are trusted.

## Primary-machine checklist

1. Use the latest signed Pinokio release and keep protected mode enabled.
2. Confirm the panel listens only on loopback:

   ```bash
   lsof -nP -iTCP:8198 -sTCP:LISTEN
   ```

3. Do not grant Full Disk Access, Accessibility, or broad Files & Folders
   access.
4. Keep high-value developer credentials out of the environment that launches
   Phosphene. Use dedicated, read-only Hugging Face/CivitAI tokens when needed.
5. Review changes before updating. Never enable destructive update recovery
   merely to bypass an unexplained Git divergence.
6. Close the panel when it is not in use. Never expose port 8198 to a LAN or
   public tunnel.
