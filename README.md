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
| POST | `/ui/launch_app` | `{name,mode}` — launch/switch/resize an app |

`ui_click_label` and `ui_type_label` return **409** if no recent `/ui/snapshot` has been taken — call snapshot first to populate the desktop state.

### OCR (Phase 2)

| Method | Path | Body / params |
|---|---|---|
| GET | `/find` | `?text=foo&monitor=0&fuzzy=true&min_confidence=0.5` |

`/find` runs OCR on the current screen and returns a list of `{text, bbox:[x,y,w,h], center:[cx,cy], confidence}` matches. Results are cached per frame hash, so repeated calls on a static screen return instantly.

### Gaming Automation (Phase 3, v0.4)

These endpoints bypass Windows-MCP and drive `SendInput` / `SetCursorPos` directly from Gargaros — they support held keys (W-to-walk, Shift-to-sprint), smooth mouse motion in both absolute (visible-cursor) and relative (pointer-lock / raw input) modes, region capture, and synchronous timing.

| Method | Path | Body / params |
|---|---|---|
| POST | `/key/hold` | `{name, duration_ms, modifiers?}` — press, wait, release |
| POST | `/key/down` | `{name}` — press without releasing |
| POST | `/key/up` | `{name}` — release a previously-pressed key (idempotent) |
| POST | `/key/sequence` | `{keys:[{name,hold_ms}], inter_key_delay_ms?}` |
| POST | `/key/release_all` | release every held key + button |
| POST | `/mouse/move_smooth` | `{dx, dy, duration_ms, steps, mode}` — `mode`: `absolute` (default, SetCursorPos) or `relative` (raw deltas for pointer-locked apps) |
| POST | `/mouse/button` | `{button, action}` — left/right/middle × down/up |
| POST | `/mouse/release_all` | release every held mouse button |
| POST | `/input/release_all` | global cleanup — release everything |
| GET | `/screenshot?bbox=x1,y1,x2,y2` | crop the capture to a region (useful to isolate the cloud-game video stream from browser chrome) |

The `/batch` endpoint accepts these as flat-field actions (per the F1 CDC):

```json
POST /batch
{
  "actions": [
    {"type": "mouse_move_smooth", "dx": 90, "dy": 0, "duration_ms": 150, "steps": 8},
    {"type": "key_down", "name": "w"},
    {"type": "sleep", "ms": 200},
    {"type": "key", "name": "space"},
    {"type": "key_up", "name": "w"}
  ]
}
```

Response shape: `{total_elapsed_ms, succeeded, failed, results:[{index, type, status, elapsed_ms, error?}]}`.

**State tracking & cleanup.** Held keys / buttons are tracked server-side. The state is released:
- on every server shutdown (lifespan + atexit backstop),
- on server startup (recovery from a previous crash),
- by a watchdog task that auto-releases anything held > 30 s,
- via the `/input/release_all` endpoint at any time.

This is non-negotiable for gaming: a Gargaros crash with `W` down would jam the user's keyboard.

**Pointer lock note.** Fullscreen browser games (Xbox Cloud Gaming, GeForce Now, etc.) capture the pointer — at that point the visible cursor is hidden and only raw motion deltas reach the game. Always pass `mode="relative"` to `mouse_move_smooth` once you're in such a context, otherwise the camera won't turn. See `docs/CLOUD_GAMING.md` for the full setup (xCloud Fortnite as worked example).

> ⚠️ **Anti-cheat warning.** Direct `SendInput` is detectable by kernel-mode anti-cheats (Easy Anti-Cheat, Vanguard, BattlEye, Ricochet) on locally-installed games. Cloud streaming routes input through the browser, sidestepping local kernel AC — but the streaming service's ToS still prohibits automation (Microsoft xCloud, etc.). **Use a burner account only.**

OCR support requires the optional `[ocr]` extra:

```powershell
pip install -e .[ocr,dev]
```

The first call after install downloads ~15 MB of models (cached under site-packages).

Every non-`/health` route requires `Authorization: Bearer <token>` AND a `Host` header of `127.0.0.1:7331` or `localhost:7331`.

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

Since v0.4, `/screenshot?raw=true` bypasses Pillow entirely and streams the raw DXcam PNG. The
response includes `X-Capture-Time-Ms` and `X-Encode-Time-Ms` headers so agents can profile.

```python
img, meta = g.screenshot(raw=True, return_meta=True)
# meta = {"capture_time_ms": 67, "encode_time_ms": 0, "content_type": "image/png"}
```

## Credits

- [`CursorTouch/Windows-MCP`](https://github.com/CursorTouch/Windows-MCP) (MIT) — does the actual screen capture and input. Gargaros is a thin gateway in front of it.
- The eyehands HTTP API shape inspired the surface this exposes.

## License

MIT — see `LICENSE`.
