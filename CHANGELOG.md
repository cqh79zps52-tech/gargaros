# Changelog

## v0.5.0 — Fortnite Cloud Gaming v2: window focus + physical input lock

Per `gargaros_fortnite_cloud_cdc_v2.pdf`. Adds the two reliability primitives the v1
smoke test was missing: window focus management (so inputs go to Chrome, not the
terminal) and the physical input lock with watchdog (so the user can't fight the
agent and the keyboard can't stay stuck after a crash).

### Added

- **F8 — Window focus management.**
  - `GET /window/list`, `GET /window/active`, `POST /window/focus`, `POST /window/wait_for_focus`.
  - Selectors: `title_contains`, `title_regex`, `process_name`, `hwnd` (combinable, AND).
  - `SetForegroundWindow` uses the `AttachThreadInput` workaround for reliability.
  - **`expect_focus` parameter** added to every input endpoint (`click`, `key`, `key_hold`,
    `key_down`, `key_up`, `key_sequence`, `mouse_move_smooth`, `mouse_button`, `batch`).
    Returns HTTP 412 (Precondition Failed) if the foreground window doesn't match, *without*
    sending the input.

- **F9 — Physical input lock.**
  - `POST /input/lock`, `POST /input/unlock`, `GET /input/lock_status`.
  - Installs `WH_KEYBOARD_LL` + `WH_MOUSE_LL` low-level hooks on a dedicated thread with a
    message loop. Filters by `LLKHF_INJECTED` / `LLMHF_INJECTED` — Gargaros' own SendInput
    events pass, physical events are dropped (return 1 to Windows).
  - Configurable unlock hotkey (default `ctrl+shift+f12`).
  - Safe-keys allowlist: `Win+L`, `Alt+F4` (when `allow_safe_keys=true`, default).
    `Ctrl+Alt+Del` is intercepted by Windows above any user-mode hook anyway.
  - **Watchdog child process** (`python -m gargaros.watchdog`) spawned at lock time. Pings
    `/input/lock_status` every 500 ms. If the server stops responding for > `watchdog_ms`
    (default 2000), the watchdog `TerminateProcess`es Gargaros — hooks fall with the process.
  - Lifespan shutdown drops the lock if still held (last-resort safety).
  - SDK: `Client.input_lock(...)`, `input_unlock()`, `input_lock_status()`,
    `window_list/active/focus/wait_for_focus`. `expect_focus=` on every input method.

- MCP integration: new tools `window_list`, `window_active`, `window_focus`,
  `window_wait_for_focus`, `input_lock`, `input_unlock`, `input_lock_status`. Descriptions
  explain when to use each, e.g. lock should wrap an agent's game loop, focus should run
  before any input batch.
- New modules: `src/gargaros/window_driver.py`, `src/gargaros/input_lock.py`,
  `src/gargaros/watchdog.py`, `src/gargaros/routes/window.py`,
  `src/gargaros/routes/input_lock.py`.
- New tests (51 total): `test_window_list`, `test_window_focus`, `test_expect_focus`,
  `test_input_lock_basic`, `test_input_lock_injected_pass`, `test_input_lock_hotkey`,
  `test_input_lock_safe_keys`, `test_input_lock_watchdog`.
- New smoke test: `scripts/smoke_test_cloud_v2.py` per CDC section 9.

### Changed

- Bump version 0.4.0 → 0.5.0.

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
