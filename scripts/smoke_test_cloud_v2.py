"""Manual smoke test for Fortnite Cloud Gaming v2 (CDC section 9, hardened).

Prerequisites (READ THESE):
  1. Gargaros running (`python -m gargaros`).
  2. Chrome open on xbox.com/play with **Fortnite already loaded INTO A MATCH**
     (not the lobby — you need a character that can move).
  3. Chrome is **FULLSCREEN** (F11) — pointer lock only engages in fullscreen.
  4. After Phase 3's countdown, click ONCE in the game viewport, then DON'T
     TOUCH ANYTHING until the script finishes (~12 seconds total).

Emergency abort: press ctrl+shift+f12 to unlock the keyboard at any time.

The selector uses process_name='chrome.exe' + title_contains='Fortnite' so it
won't accidentally match a terminal window whose title happens to contain
'fortnite'.
"""

from __future__ import annotations

import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from gargaros.client import Client

FOCUS_SELECTOR = {"process_name": "chrome.exe", "title_contains": "Fortnite"}


def main() -> int:
    with Client() as g:
        print("=== Phase 1 : sanity checks ===")
        assert g.health()["status"] == "ok"
        print("[ok] Health OK")

        print("\n=== Phase 2 : find Chrome/Fortnite window ===")
        windows = g.window_list(visible_only=True)
        chrome_fortnite = [
            w for w in windows
            if w["process_name"].lower() == "chrome.exe"
            and "fortnite" in w["title"].lower()
        ]
        if not chrome_fortnite:
            print("ERROR: no chrome.exe window with 'Fortnite' in title.")
            print("Open xbox.com/play and load Fortnite first.")
            return 1
        target = chrome_fortnite[0]
        print(f"[ok] Found: {target['title']!r} (hwnd={target['hwnd']})")
        # Best-effort: try to bring it forward. SetForegroundWindow can fail
        # silently, so the user may still need to click Chrome in the taskbar.
        try:
            g.window_focus(selector={"hwnd": target["hwnd"]}, restore_if_minimized=True)
            print("[ok] window_focus called (Windows may still leave it in the background)")
        except Exception as e:
            print(f"window_focus warning: {e}")

        print("\n=== Phase 3 : focus Fortnite Chrome (waiting up to 30s) ===")
        print(">>> 1. Alt-Tab or click on the Fortnite Chrome window NOW")
        print(">>> 2. You must be IN A MATCH (not lobby) for character movement")
        print(">>> 3. Press F11 to go fullscreen if not already")
        print(">>> 4. Click ONCE in the game viewport to engage pointer lock")
        print(">>> 5. STOP TOUCHING THE KEYBOARD/MOUSE after that")
        print("")
        # Manual poll loop with visible progress so we can see what the active window is.
        deadline = time.monotonic() + 30
        last_title = None
        while time.monotonic() < deadline:
            active = g.window_active()
            title = f"{active['process_name']} {active['title']!r}"
            if title != last_title:
                print(f"   active: {title}")
                last_title = title
            if (active["process_name"].lower() == "chrome.exe"
                    and "fortnite" in active["title"].lower()):
                break
            time.sleep(0.5)
        else:
            print("ERROR: Fortnite Chrome never became foreground (30s timeout).")
            return 2
        print(f"[ok] Fortnite Chrome is foreground: {active['title']!r}")

        print("\n=== Phase 4 : 3s pause for pointer-lock click ===")
        print(">>> Click in the game viewport to engage pointer lock if not done.")
        for i in range(3, 0, -1):
            print(f"   {i}...")
            time.sleep(1)
        # Re-verify focus after the pause
        active = g.window_active()
        if active["process_name"].lower() != "chrome.exe" or "fortnite" not in active["title"].lower():
            print(f"ERROR: focus drifted to {active['process_name']} {active['title']!r}")
            return 2
        print(f"[ok] still on {active['title']!r}")

        print("\n=== Phase 5 : input lock ===")
        g.input_lock(unlock_hotkey="ctrl+shift+f12")
        print("[ok] Locked. Your physical keyboard is now blocked except ctrl+shift+f12.")
        time.sleep(1)

        try:
            print("\n=== Phase 6 : full screenshot (PNG raw) ===")
            g.screenshot(raw=True)  # warmup (cold-start can be 200ms+)
            img, meta = g.screenshot(raw=True, return_meta=True)
            print(f"[ok] full ({meta['capture_time_ms']}ms — target <100ms warm)")

            print("\n=== Phase 7 : bbox screenshot ===")
            from io import BytesIO
            from PIL import Image
            w, h = Image.open(BytesIO(img)).size
            img, meta = g.screenshot(raw=True, bbox=(0, 80, w, h), return_meta=True)
            print(f"[ok] bbox ({meta['capture_time_ms']}ms)")

            print("\n=== Phase 8 : walk forward 1.5s ===")
            g.key_hold("w", 1500, expect_focus=FOCUS_SELECTOR)
            print("[ok] key_hold w 1500ms — character should have walked forward")
            time.sleep(0.5)

            print("\n=== Phase 9 : turn camera 90 right (relative mouse) ===")
            g.mouse_move_smooth(
                dx=720, dy=0, duration_ms=600, steps=24,
                mode="relative",
                expect_focus=FOCUS_SELECTOR,
            )
            print("[ok] camera should have turned right")
            time.sleep(0.5)

            print("\n=== Phase 10 : batch (turn left + jump) ===")
            g.batch(
                [
                    {"type": "mouse_move_smooth", "dx": -720, "dy": 0,
                     "duration_ms": 600, "steps": 24, "mode": "relative"},
                    {"type": "key", "name": "space"},
                    {"type": "sleep", "ms": 300},
                ],
                expect_focus=FOCUS_SELECTOR,
            )
            print("[ok] batch — camera left + jump")
            time.sleep(0.5)

            print("\n=== Phase 11 : lock_status counters ===")
            status = g.input_lock_status()
            print(f"  passed_kb_injected    = {status.get('passed_kb_injected', '?')}")
            print(f"  passed_mouse_injected = {status.get('passed_mouse_injected', '?')}")
            print(f"  blocked_kb_events     = {status.get('blocked_kb_events', '?')}")
            print(f"  blocked_mouse_events  = {status.get('blocked_mouse_events', '?')}")
            sample = status.get("sample_blocked_kb", [])
            if sample:
                print(f"  sample blocked kb events (vk, flags, wparam):")
                for s in sample:
                    print(f"    {s}")
            else:
                print("  (no kb events seen by the hook — try typing more during the next run)")
        finally:
            print("\n=== Phase 12 : cleanup ===")
            g.input_release_all()
            unlock = g.input_unlock()
            print(f"[ok] unlocked after {unlock['was_locked_ms']}ms — keyboard restored")

    print("\n*** SMOKE TEST PASSED ***")
    print("Visually: did the character walk forward, turn right, turn left, jump?")
    print("If yes, Gargaros is operational for Fortnite Cloud Gaming.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
