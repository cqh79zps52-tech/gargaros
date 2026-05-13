# Changelog

## v0.4.0 — Fortnite Cloud Gaming ready

Per `gargaros_fortnite_cloud_cdc.pdf`. Brings the input primitives needed to drive a cloud-streamed
game (Fortnite via Xbox Cloud Gaming in Chrome fullscreen) — held keys, sequences, smooth mouse
motion (both absolute and pointer-lock-aware relative), batched actions, and region capture.

### Added

- **F1** — `/batch` extended with new action types: `key_hold`, `key_down`, `key_up`, `key_sequence`,
  `mouse_move_smooth`, `mouse_button`, `sleep`. Accepts both legacy `{op, args}` and flat-field
  `{type, ...}` syntax. Response now includes `total_elapsed_ms`, `succeeded`, `failed`, and per-action
  `{index, type, status, elapsed_ms, error?}`.
- **F2** — `/screenshot?raw=true` returns PNG without Pillow re-encoding, and emits
  `X-Capture-Time-Ms` / `X-Encode-Time-Ms` headers for profiling.
- **F3** — `POST /key/hold`, `/key/down`, `/key/up`, `/key/release_all`. State is tracked in
  `app.state.input_state` so we can release everything on shutdown.
- **F4** — `POST /key/sequence` for tight combos with cumulative timings.
- **F5** — `POST /mouse/move_smooth` with `mode` parameter:
  - `mode="absolute"` (default): incremental `SetCursorPos` updates — backward-compat, visible cursor.
  - `mode="relative"` (new): raw `MOUSEEVENTF_MOVE` deltas via `SendInput` — generates `WM_INPUT`
    raw input events that pointer-locked apps (Chrome fullscreen cloud gaming) consume.
  - `POST /mouse/button` for held clicks, `/mouse/release_all`.
- **F6** — `/screenshot?bbox=x1,y1,x2,y2` crops the capture to a region. Useful in cloud gaming
  to isolate the WebRTC video stream from browser chrome. Returns 400 if bbox is out of bounds,
  inverted, or malformed.
- **F7 / Cleanup (CDC §8)** — non-negotiable safety nets: release_all on lifespan startup
  (recovery from crashed sessions), release_all on lifespan shutdown, atexit backstop, 30-second
  watchdog that auto-releases anything stuck. `POST /input/release_all` is exposed for the agent
  to call manually.
- MCP integration: all new endpoints exposed as MCP tools with descriptions explaining when to use
  each primitive (e.g., `mouse_move_smooth` description spells out the absolute vs relative mode
  decision based on pointer lock).
- SDK: `Client.key_hold/down/up/sequence/release_all`, `mouse_move_smooth(..., mode=)`,
  `mouse_button/release_all`, `input_release_all`,
  `screenshot(raw=True, bbox=(...), return_meta=True)`.
- New module `src/gargaros/input_driver.py` — ctypes-based `SendInput` + `SetCursorPos` /
  `GetCursorPos` wrappers (no `pywin32`).
- New module `src/gargaros/input_state.py` — `InputState` tracker + watchdog loop.
- Docs: `docs/CLOUD_GAMING.md`, new "Gaming Automation" section in README, this CHANGELOG.
- Script: `scripts/smoke_test_cloud.py` (per CDC annex).
- Tests: `test_input_driver`, `test_key_hold`, `test_key_state`, `test_key_sequence`,
  `test_mouse_modes` (both modes), `test_cleanup`, `test_batch_gaming`, `test_screenshot_raw`,
  `test_screenshot_bbox`.

### Changed

- `/health` response now includes `"status": "ok"` alongside the existing `ok` / `version` /
  `backend_alive` fields (smoke test relies on it).
- `Client.batch()` now returns the full response dict (previously returned only `results`).
- `/batch` response shape: per-action `{ok: true/false}` is replaced by `{status: "ok"|"error"}`.
  Bump from 0.3.0 → 0.4.0 reflects this breaking change.

### Warnings

- Microsoft's xCloud ToS prohibits automation. Cloud streaming sidesteps kernel-mode anti-cheat
  (EAC etc.) but the account can still be suspended. **Burner accounts only** — see
  `docs/CLOUD_GAMING.md` for the full setup.
