# Gargaros

Open-source local HTTP control surface for AI agents on Windows.

Gargaros gives AI agents a fast, authenticated HTTP API to take screenshots, click, type, and otherwise control a Windows desktop. It is an MIT-licensed, eyehands-compatible alternative built as a thin gateway in front of [`Windows-MCP`](https://github.com/CursorTouch/Windows-MCP), which does the heavy lifting (DXcam capture, SendInput, UI Automation).

## What's different from upstream eyehands

- **MIT licensed**, free, no phone-home, no trial gating.
- Built on the open-source `Windows-MCP` server — no closed-source binary.
- HTTP API surface intentionally similar to eyehands so existing clients can target it with minimal changes.

## Requirements

- Windows 10 or 11
- Python 3.13 or newer (`Windows-MCP` requires it)

## Quickstart

```powershell
pip install -e .[dev]
python -m gargaros
```

On first launch Gargaros prints the bearer token and the listen URL. The token is also persisted at `%APPDATA%\Gargaros\token`.

```powershell
$token = Get-Content $env:APPDATA\Gargaros\token

curl -H "Authorization: Bearer $token" http://127.0.0.1:7331/screenshot -o screenshot.jpg

curl -X POST `
  -H "Authorization: Bearer $token" `
  -H "Content-Type: application/json" `
  -d '{"x":500,"y":500}' `
  http://127.0.0.1:7331/move
```

Full OpenAPI schema: `http://127.0.0.1:7331/schema`.

## HTTP API

### Core

| Method | Path | Body / params |
|---|---|---|
| GET | `/health` | — |
| GET | `/screenshot` | `?monitor=0&fmt=jpeg&quality=80` |
| GET | `/latest_b64` | `?monitor=0` (supports `If-None-Match`) |
| POST | `/click` | `{x,y,button,double}` |
| POST | `/move` | `{x,y,relative}` |
| POST | `/type` | `{text,press_enter}` |
| POST | `/key` | `{name,modifiers}` |
| POST | `/scroll` | `{dx,dy}` |
| POST | `/drag` | `{from:{x,y},to:{x,y},button}` |
| POST | `/batch` | `{actions:[...],continue_on_error}` (max 100) |

### UI Automation (Phase 2)

| Method | Path | Body |
|---|---|---|
| POST | `/ui/snapshot` | `{use_vision,use_ui_tree,use_annotation,...}` — labeled UI tree |
| POST | `/ui/click_label` | `{label,button,double}` — click element by label id from a recent snapshot |
| POST | `/ui/type_label` | `{label,text,clear,press_enter}` |
| POST | `/ui/scrape` | `{url,query}` — open a URL and extract content |
| POST | `/ui/launch_app` | `{name,mode}` — launch/switch/resize an app on the visible desktop |
| POST | `/agent/launch_app` | `{cmdline}` — launch a process on the hidden desktop (`agent_dsk`) |

`ui_click_label` and `ui_type_label` return **409** if no recent `/ui/snapshot` has been taken — call snapshot first to populate the desktop state.

### OCR (Phase 2)

| Method | Path | Body / params |
|---|---|---|
| GET | `/find` | `?text=foo&monitor=0&fuzzy=true&min_confidence=0.5` |

`/find` runs OCR on the current screen and returns a list of `{text, bbox:[x,y,w,h], center:[cx,cy], confidence}` matches. Results are cached per frame hash, so repeated calls on a static screen return instantly.

OCR support requires the optional `[ocr]` extra:

```powershell
pip install -e .[ocr,dev]
```

The first call after install downloads ~15 MB of models (cached under site-packages).

Every non-`/health` route requires `Authorization: Bearer <token>` AND a `Host` header of `127.0.0.1:7331` or `localhost:7331`.

## Hidden secondary desktop (`agent_dsk`)

By default Gargaros now drives a **hidden Windows desktop object** (`agent_dsk`, created with
`CreateDesktop` and never shown via `SwitchDesktop`). The agent's apps live there, so its clicks,
typing and screen captures **never move the user's cursor or steal keyboard focus** — the human can
keep working on the visible desktop in parallel. This is native Windows isolation: same machine, no VM.

How it changes the endpoints:

- **Capture** (`/screenshot`, `/latest_b64`, `/find`): each `agent_dsk` window is captured with
  `PrintWindow(PW_RENDERFULLCONTENT)` and composited onto a black canvas in absolute virtual-screen
  coordinates — so a composite pixel `(x, y)` is the coordinate you click. ETag caching and OCR are unchanged.
- **Coordinate input** (`/click`, `/move`, `/type`, `/scroll`, `/drag`): routed to `agent_dsk` via
  `PostMessage` (no cursor) when the coordinate resolves to a hidden window, otherwise via a `SendInput`
  fallback executed on a dedicated thread permanently attached to `agent_dsk` (`SetThreadDesktop`).
- **Launch** (`POST /agent/launch_app` with `{cmdline}`): processes are spawned with
  `STARTUPINFO.lpDesktop = "agent_dsk"` via `CreateProcess`. (`/ui/launch_app` still goes through
  Windows-MCP and launches on the **visible** desktop.)

Disable it (revert to driving the visible desktop through Windows-MCP) with
`GARGAROS_HIDDEN_DESKTOP_ENABLED=0`; the desktop name is configurable via `GARGAROS_HIDDEN_DESKTOP`.

**Pass-1 limitations (known, by design):**

- The UIA endpoints `/ui/snapshot`, `/ui/click_label`, `/ui/type_label` and `/batch` and `/key` still
  go through Windows-MCP on the **visible** desktop and do **not** yet see the hidden windows — a
  dedicated UIA layer for `agent_dsk` is the next pass. Use coordinate input + `/find` against the
  composite for now.
- `PrintWindow` can return black for some GPU/WebGL/Chromium/Electron surfaces; fine for classic
  desktop, web and native apps. Validate per target app.
- `/move` with `relative=true` returns **409** on the hidden desktop (no user cursor to anchor to).
- Coordinates are absolute virtual-screen pixels, identical to the composite image space.

## Security notes

- Listens on `127.0.0.1` only. Never bind `0.0.0.0`.
- Bearer token is persisted to `%APPDATA%\Gargaros\token` with user-only permissions.
- Any local process with read access to that file can drive your machine. Treat it like an SSH key.

## Python SDK

```python
from gargaros.client import Client

with Client() as g:                 # auto-discovers token from %APPDATA% or env
    g.health()
    img = g.screenshot()            # bytes (JPEG)
    g.click(500, 500)
    g.type("hello world", press_enter=True)
    matches = g.find("Send")        # OCR
    snap = g.ui_snapshot()          # labeled UI tree
    g.ui_click_label(7)             # click element 7 from snap
```

OpenAI function-calling schemas:

```python
import openai, json
from gargaros.openai_schema import tools

resp = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Take a screenshot"}],
    tools=tools(),
)
```

## MCP server (gargaros-mcp)

Gargaros also ships an MCP-server adapter so Claude Desktop / Claude Code can use it via MCP instead of HTTP. With Gargaros HTTP running, point your MCP-capable client at `gargaros-mcp` (stdio):

```jsonc
// %APPDATA%\Claude\claude_desktop_config.json
{
  "mcpServers": {
    "gargaros": {
      "command": "gargaros-mcp"
    }
  }
}
```

The MCP adapter is a thin translator — auth, host validation, and ETag caching all stay in the HTTP server.

## Architecture

Gargaros is a thin HTTP gateway. All actual screen capture and input is performed by an MIT-licensed
upstream project, [`Windows-MCP`](https://github.com/CursorTouch/Windows-MCP), which Gargaros spawns
as a child process speaking JSON-RPC over stdio. We launch it via `python -m windows_mcp` (using
the same interpreter that runs Gargaros) so PATH configuration is irrelevant.

```
Agent  --HTTP-->  Gargaros (Litestar)  --stdio JSON-RPC-->  Windows-MCP (DXcam + SendInput + UIA)
```

Gargaros adds: bearer-token auth, host-header validation, frame-hash ETag caching for
screenshots, eyehands-shaped JSON, and a batch endpoint with a 100-action ceiling.

## Performance

On the smoke-test machine (Windows 11, Python 3.14): 10 sequential `/screenshot` calls averaged
**130 ms** end-to-end, including Pillow JPEG re-encoding. The eyehands product claims 57 ms; we're
slower because (a) we go through MCP JSON-RPC, and (b) we re-encode PNG→JPEG. Skipping the
re-encode (return PNG directly) gets close to the underlying DXcam latency.

## Credits

- [`CursorTouch/Windows-MCP`](https://github.com/CursorTouch/Windows-MCP) (MIT) — does the actual screen capture and input. Gargaros is a thin gateway in front of it.
- The eyehands HTTP API shape inspired the surface this exposes.

## License

MIT — see `LICENSE`.
