# Driving Phosphene from an agent: the CLI and the MCP server

Phosphene is one local server (the studio) that the browser page, the Mac app and agent tools all
talk to over the same HTTP API ([`API.md`](API.md)). Two small tools sit on top of it, in `agents/`:

| | what | run it with |
|---|---|---|
| `agents/phosphene_cli.py` | the `phosphene` command: make video / images / speech / music, list jobs, wait, fetch files, status | any `python3` (3.9+, standard library only) |
| `agents/phosphene_mcp.py` | an MCP server (stdio) with the same actions, for Claude Code, Codex or any MCP client | same |

Both are thin layers over `agents/phosphene_client.py`, so they find the studio the same way and say
the same thing when it is not running.

## Connect an agent

The running studio prints the exact line for this install (its own Python and path).
Ask the CLI, which finds the studio whatever port it is on (the Mac app picks a free
one in 8930–8999 at each start; Pinokio uses 8198):

```bash
python3 agents/phosphene_cli.py mcp-config                          # finds the studio itself
cat ~/.phosphene/servers/*.json                                     # or: every running studio, with its port
curl -s http://127.0.0.1:<port>/agents/info | python3 -m json.tool  # the same answer over HTTP, see "agents"
```

Then, for example:

```bash
claude mcp add phosphene -- python3 /path/to/phosphene/agents/phosphene_mcp.py
codex  mcp add phosphene -- python3 /path/to/phosphene/agents/phosphene_mcp.py
```

In the Mac app the studio code lives under `~/Library/Application Support/Phosphene/app/<version>/`;
`/agents/info` gives the quoted path (it has a space in it).

MCP tools: `phosphene_status`, `phosphene_make_video`, `phosphene_make_image`, `phosphene_make_speech`,
`phosphene_make_music`, `phosphene_list_jobs`, `phosphene_wait`, `phosphene_fetch`, `phosphene_stop`.
Renders take minutes: a `make_*` tool queues and returns a job id; `phosphene_wait` blocks up to
`timeout_sec` (default 1500 s) and can be called again.

## The CLI

```bash
phosphene status
phosphene video "A night ferry crosses a dark harbour in light rain ..." --seconds 5 --out ./clips
phosphene video "..." --engine h3 --quality draft --seconds 10 --wait
phosphene video "..." --image still.png             # image to video (the picture is uploaded first)
phosphene image "a travel poster of a red tram" --n 2 --aspect 3:4 --out ./stills
phosphene jobs ; phosphene wait JOB --out DIR ; phosphene fetch JOB --out DIR ; phosphene stop
phosphene servers                                    # every studio answering on this Mac
```

`--json` on any command gives machine-readable output. Exit codes: `0` ok, `1` the studio refused
or the job failed (its sentence is printed), `2` bad arguments, `3` Phosphene is not running.
(`phosphene` here is `python3 agents/phosphene_cli.py`; alias it as you like.)

## How a tool finds the studio

The studio's port depends on where it runs: **8198** in Pinokio, **8199** for the dev profile, a free
port in **8930–8999** in the Mac app (`PanelProcess.freePort`), anything with `LTX_PORT`. So:

1. `PHOSPHENE_URL`, when set, is used and nothing else.
2. Every studio writes `~/.phosphene/servers/<port>.json` once its listener is bound and removes it
   when it stops (`agent_discovery.py`). A record is trusted only if its pid is alive and the URL
   answers `GET /agents/info` with `"app": "phosphene"`. Newest first.
   `PHOSPHENE_DISCOVERY_DIR` moves the folder; `PHOSPHENE_DISCOVERY=0` turns announcing off.
3. With no record (an older studio): 8198, 8199, then 8930–8999.

Nothing answering: *"Phosphene is not running on this Mac. Start it first: open the Phosphene app,
or press Start on Phosphene in Pinokio."* (CLI exit 3; an MCP tool error, never a crash).

## Free, Pro and licences

The tools add no route and decide nothing. Every request is a request the page itself makes
(`/queue/add`, `/speech/generate`, `/upload`, `/status`, `/file`), so the studio applies the same checks:
what this Mac can run, model licences (HTTP 451 until the person accepts), and what Free and Pro
include. A form cannot carry a Pro speed profile into a job (`make_job` builds `params` from an
allow-list; `test_agent_tools.py` pins it). There is deliberately **no tool that accepts a model
licence**: accepting on someone's behalf is accepting for them, so the person does it once in
Phosphene.

## GET /agents/info

```jsonc
{"app": "phosphene", "api": 1, "url": "http://127.0.0.1:8198", "port": 8198, "pid": 123,
 "version": "5.0.0", "distribution": "pinokio" | "mac-app", "edition": "free" | "pro", "profile": "production",
 "pro": {"active": false, ...},                       // GET /pro/status
 "engines": {"ltx": {"available": true, "qualities": [...]},
             "h3": {"available": false, "qualities": [...], "lengths": ["3s", "5s", "10s", "15s", ...]},
             "speech": {...}, "music": {...}, "image": {...}},
 "outputs_dir": "...",
 "agents": {"cli": "...", "mcp": "...", "python": "...", "mcp_config": {...}, "claude_code": "...", "codex": "..."}}
```

Cheap (no disk walk), safe to poll. Tests: `test_agent_tools.py`.
