"""Manual smoke test for the Fortnite-Cloud-ready Gargaros build (CDC annex).

Run with Gargaros already running (`python -m gargaros` in another terminal) AND Chrome open
on xbox.com/play, Fortnite loaded, fullscreen with pointer lock active (click once in the game
area to enable). The character should walk, sprint, jump, and turn the camera; on exit, no keys
should remain held.
"""

from __future__ import annotations

import io
import sys

from PIL import Image

from gargaros.client import Client


def main() -> int:
    with Client() as g:
        # 1. Health
        h = g.health()
        assert h["status"] == "ok", f"unexpected health: {h}"
        print(f"health OK: {h}")

        # 2. Full-screen raw PNG capture with timing.
        # Warmup first — DXcam's first capture loads the duplication API and is ~200ms;
        # we measure the second (steady-state) call against the CDC's <100ms target.
        g.screenshot(raw=True)
        img, meta = g.screenshot(raw=True, return_meta=True)
        assert meta["capture_time_ms"] < 100, f"slow capture (after warmup): {meta}"
        screen_w, screen_h = Image.open(io.BytesIO(img)).size
        print(f"screenshot full OK: {len(img)} bytes, capture={meta['capture_time_ms']}ms, {screen_w}x{screen_h}")

        # 3. Region capture: just the cloud-stream video area (strip the top 80px tab bar).
        # The CDC literal example assumes 1920x1160 — we derive bbox from the actual screen.
        bbox = (0, 80, screen_w, screen_h)
        img, meta = g.screenshot(raw=True, bbox=bbox, return_meta=True)
        cropped_w, cropped_h = Image.open(io.BytesIO(img)).size
        print(f"screenshot bbox OK: {bbox} -> {cropped_w}x{cropped_h}, capture={meta['capture_time_ms']}ms")

        # 4. Held key — walk forward 1 s
        r = g.key_hold("w", 1000)
        print(f"key_hold w 1s OK ({r['elapsed_ms']}ms)")

        # 5. Sprint — Shift + W for 1.5 s
        g.key_down("shift")
        g.key_hold("w", 1500)
        up = g.key_up("shift")
        print(f"sprint OK (shift held {up.get('was_held_ms')}ms)")

        # 6. Sequence — advance + jump + advance
        seq = g.key_sequence([
            {"name": "w", "hold_ms": 500},
            {"name": "space", "hold_ms": 50},
            {"name": "w", "hold_ms": 500},
        ])
        print(f"key_sequence OK ({seq['total_elapsed_ms']}ms)")

        # 7. Smooth mouse — turn camera 90° right in RELATIVE mode (pointer-lock-aware)
        m = g.mouse_move_smooth(dx=540, dy=0, duration_ms=500, steps=20, mode="relative")
        assert m["mode"] == "relative", m
        print(f"mouse_move_smooth relative OK ({m['steps_done']} steps, {m['elapsed_ms']}ms)")

        # 8. Batch — turn back, advance, jump
        b = g.batch([
            {"type": "mouse_move_smooth", "dx": -540, "dy": 0,
             "duration_ms": 500, "steps": 20, "mode": "relative"},
            {"type": "key_hold", "name": "w", "duration_ms": 800},
            {"type": "key", "name": "space"},
            {"type": "sleep", "ms": 300},
        ])
        assert b["failed"] == 0, f"batch had failures: {b}"
        print(f"batch OK ({b['total_elapsed_ms']}ms, {b['succeeded']} ok, {b['failed']} failed)")

        # 9. Cleanup
        cleanup = g.input_release_all()
        print(f"input_release_all OK: {cleanup}")

    print("SMOKE TEST PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
