# Using Gargaros with cloud gaming (Xbox Cloud Gaming / xbox.com/play)

This guide explains how to drive a cloud-streamed game from Gargaros — concretely Fortnite via
Xbox Cloud Gaming running in Chrome fullscreen, but the same approach works for any pointer-locked
fullscreen browser game.

## Why cloud changes things

A locally-installed game and a cloud-streamed game look similar to a user, but the input plumbing
is very different:

| Local game | Cloud game (xCloud / GeForce Now / etc.) |
|---|---|
| Anti-cheat reads kernel input → `SendInput` is detectable | Chrome is the only local process — no kernel anti-cheat to dodge |
| Borderless Windowed mode needs configuring in-game | Browser handles fullscreen; no in-game settings |
| Crash with key down jams the physical keyboard | Crash with key down jams only the Chrome tab — Alt-Tab fixes it |
| Cursor lives at absolute screen coords | Browser pointer-locks the cursor; only raw motion deltas reach the stream |
| Capture shows native game render | Capture shows H.264-decoded video — minor compression artifacts |
| ~10-30 ms input → reaction | ~150-300 ms input → reaction (network round trip) |

The two implications that matter for Gargaros:

1. **Mouse motion must be `mode="relative"` once you're in the game.** Pointer lock means
   `SetCursorPos` does nothing useful (the cursor is hidden). You need raw motion deltas via
   `SendInput MOUSEEVENTF_MOVE` so Chrome forwards them as `WM_INPUT` events to the WebRTC stream.

2. **Capture the video region, not the whole Chrome window.** When fullscreen is active the video
   fills the screen, but during setup (queueing, controls overlay) the page has a navigation bar.
   Use `bbox=` to crop to just the stream area.

## Setup checklist

1. Start Gargaros: `python -m gargaros`. Note the token.
2. Launch Chrome → `xbox.com/play`.
3. Sign in with the **burner Microsoft account**. Cloud anti-cheat is lighter than kernel-mode AC,
   but Microsoft can still suspend accounts for automation. Don't risk your main account.
4. Queue + launch Fortnite from the xCloud catalog.
5. Once the game is loaded, click into the video area to focus + pointer-lock, then press F11 to
   make Chrome fullscreen.

## Smoke test

With the setup above, run:

```powershell
python scripts/smoke_test_cloud.py
```

The script (CDC annex) does: health check → full screenshot → bbox screenshot → `key_hold w` →
sprint sequence → key_sequence → `mouse_move_smooth` in **relative** mode → batch with mixed
actions → `input_release_all`.

If the character walks, sprints, jumps, and turns the camera, Gargaros is operational for cloud
gaming and you can hand off to the agent skill.

## SDK usage patterns

```python
from gargaros.client import Client

with Client() as g:
    # Capture just the video area (1920x1080 below an 80px Chrome tab bar)
    img, meta = g.screenshot(raw=True, bbox=(0, 80, 1920, 1160), return_meta=True)

    # Walk forward 2 seconds (held key works the same as native)
    g.key_hold("w", 2000)

    # Sprint: held modifier + held key
    g.key_down("shift")
    g.key_hold("w", 1500)
    g.key_up("shift")

    # Turn camera 90° right — relative mode is REQUIRED in pointer-lock
    g.mouse_move_smooth(dx=540, dy=0, duration_ms=500, steps=20, mode="relative")

    # One round-trip for a combined turn + advance + jump
    g.batch([
        {"type": "mouse_move_smooth", "dx": 360, "dy": 0,
         "duration_ms": 300, "steps": 12, "mode": "relative"},
        {"type": "key_hold", "name": "w", "duration_ms": 800},
        {"type": "key", "name": "space"},
    ])

    # Belt-and-braces cleanup after the turn ends
    g.input_release_all()
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `key_hold("w", 2000)` types `wwww` in the browser address bar | Chrome focus is on the URL bar, not the video area | Click once in the video, press F11 for fullscreen |
| Camera doesn't turn even though the script runs without error | Used `mode="absolute"` — pointer lock ignores SetCursorPos | Pass `mode="relative"` for in-game mouse moves |
| Camera "jumps" instead of turning smoothly | Too few `steps` (cloud latency masks small steps) | Use `steps >= 20` and `duration_ms >= 300` |
| Captured image shows Chrome chrome (tab bar) | Page isn't fullscreen, or bbox doesn't match | Press F11; tune `bbox=(0, y_offset, screen_w, y_offset+1080)` |
| Character keeps walking forever after the agent dies | A `key_down` wasn't paired with `key_up` | Watchdog releases after 30 s; or call `g.input_release_all()` manually |

## Calibration

`mouse_move_smooth` in relative mode uses "mickeys" — raw motion deltas. On a Windows desktop with
default mouse sensitivity, 1 mickey ≈ 1 screen pixel of motion. Cloud games typically apply their
own sensitivity multiplier on top, so the effective in-game rotation per mickey varies.

Quick calibration: send `mouse_move_smooth(dx=1000, dy=0, mode="relative")` and measure how far
the in-game camera rotates. Adjust your script's dx by the ratio.

## Why not just use Chrome DevTools / WebRTC instead?

You could drive Chrome via CDP (DevTools Protocol) and synthesize keyboard/mouse events at the
browser level. Reasons we don't:

- CDP synthetic events don't go through the WebRTC stream the way native OS events do — xCloud
  treats them differently.
- The pointer-lock raw input pipeline is a `WM_INPUT` capture that only fires for real OS events.
- Gargaros' value is being a thin wrapper over `SendInput` — using it sidesteps a whole class of
  browser-injection detection.

## What's out of scope of this guide

- The agent skill that decides what to do — see the `fortnite-player` skill spec separately.
- OCR / templating for in-game UI — Gargaros exposes `/find` but interpretation is the skill's
  responsibility.
- Anti-cheat evasion: cloud gaming doesn't have a kernel-mode AC, but Microsoft's ToS still
  prohibits automation. **Burner accounts only.**
