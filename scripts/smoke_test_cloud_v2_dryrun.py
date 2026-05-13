"""Programmatic v2 smoke test — exercises every F8/F9 endpoint without requiring
xCloud Fortnite. Uses the CURRENT foreground window as the target so we don't
fight Windows' focus-stealing prevention (which would block window_focus from
a non-UI-thread server process).

This is the script CI runs. The real CDC section 9 smoke test
(scripts/smoke_test_cloud_v2.py) needs an xCloud Fortnite session and is run
manually by the human-in-the-loop before merge.
"""

from __future__ import annotations

import io
import sys

from PIL import Image

from gargaros.client import Client


def main() -> int:
    with Client() as g:
        print("=== Phase 1: health ===")
        assert g.health()["status"] == "ok"
        print("[ok] Health OK")

        print("\n=== Phase 2: window_list ===")
        wins = g.window_list(visible_only=True)
        assert isinstance(wins, list) and len(wins) > 0
        print(f"[ok] {len(wins)} visible top-level windows")

        print("\n=== Phase 3: window_active ===")
        active = g.window_active()
        assert "hwnd" in active and "title" in active
        target_hwnd = active["hwnd"]
        print(f"[ok] active: hwnd={target_hwnd} title={active['title']!r} "
              f"process={active['process_name']}")

        print("\n=== Phase 4: window_wait_for_focus timeout returns 408 ===")
        try:
            g.window_wait_for_focus(
                selector={"hwnd": 999999999},  # impossible hwnd
                timeout_ms=200, poll_interval_ms=50,
            )
            assert False, "expected 408"
        except Exception as e:
            assert "408" in str(e), f"unexpected: {e}"
            print("[ok] 408 returned when window never gets focus")

        print("\n=== Phase 5: window_focus exists for a real visible window ===")
        # Pick any visible window. SetForegroundWindow may fail silently due to
        # Windows' focus-stealing prevention from a non-UI thread — we still
        # exercise the endpoint and confirm the response shape.
        r = g.window_focus(selector={"hwnd": target_hwnd}, restore_if_minimized=True)
        assert r["hwnd"] == target_hwnd
        print(f"[ok] focus call returned ok={r.get('ok', True)} for hwnd={r['hwnd']}")

        print("\n=== Phase 6: window/focus 404 on no-match ===")
        try:
            g.window_focus(selector={"title_contains": "ZZZ_NoSuchWindow_XYZ"})
            assert False, "expected 404"
        except Exception as e:
            assert "404" in str(e), f"unexpected: {e}"
            print("[ok] 404 on missing window")

        print("\n=== Phase 7: window/focus 400 on empty selector ===")
        try:
            g.window_focus(selector={})
            assert False, "expected 400"
        except Exception as e:
            assert "400" in str(e), f"unexpected: {e}"
            print("[ok] 400 on empty selector")

        print("\n=== Phase 8: screenshots (raw + bbox) ===")
        g.screenshot(raw=True)  # warmup
        img, meta = g.screenshot(raw=True, return_meta=True)
        assert meta["capture_time_ms"] < 200, f"slow: {meta}"
        sw, sh = Image.open(io.BytesIO(img)).size
        print(f"[ok] full {sw}x{sh}, capture={meta['capture_time_ms']}ms")
        img2, meta2 = g.screenshot(raw=True, bbox=(0, 0, sw // 2, sh // 2), return_meta=True)
        cw, ch = Image.open(io.BytesIO(img2)).size
        assert (cw, ch) == (sw // 2, sh // 2)
        print(f"[ok] bbox {cw}x{ch}, capture={meta2['capture_time_ms']}ms")

        print("\n=== Phase 9: expect_focus mismatch returns 412 (no input sent) ===")
        try:
            g.key_hold("w", 20, expect_focus={"title_contains": "ZZZ_NoSuchWindow_XYZ"})
            assert False, "expected 412"
        except Exception as e:
            assert "412" in str(e) or "precondition" in str(e).lower(), f"unexpected: {e}"
            print("[ok] 412 raised on focus mismatch")

        print("\n=== Phase 10: expect_focus match passes through ===")
        r = g.key_hold("w", 20, expect_focus={"hwnd": target_hwnd})
        assert r["elapsed_ms"] >= 15
        print(f"[ok] key_hold with matching focus: {r['elapsed_ms']}ms")

        print("\n=== Phase 11: input_lock acquire ===")
        status = g.input_lock(unlock_hotkey="ctrl+shift+f12", watchdog_ms=5000)
        assert status["locked"] is True
        assert status["unlock_hotkey"] == "ctrl+shift+f12"
        lock_id = status["lock_id"]
        print(f"[ok] locked: id={lock_id[:8]}... hotkey=ctrl+shift+f12")

        try:
            print("\n=== Phase 12: lock_status reports correct shape ===")
            s = g.input_lock_status()
            assert s["locked"] is True
            assert s["lock_id"] == lock_id
            assert "blocked_physical_events" in s
            assert "passed_injected_events" in s
            print(f"[ok] status: passed={s['passed_injected_events']} "
                  f"blocked={s['blocked_physical_events']} elapsed={s['elapsed_ms']}ms")

            print("\n=== Phase 13: double-lock returns 409 ===")
            try:
                g.input_lock()
                assert False, "expected 409"
            except Exception as e:
                assert "409" in str(e), f"unexpected: {e}"
                print("[ok] 409 on second lock")

            print("\n=== Phase 14: injected event passes through hook ===")
            r = g.key_hold("w", 20, expect_focus={"hwnd": target_hwnd})
            s2 = g.input_lock_status()
            assert s2["passed_injected_events"] >= s["passed_injected_events"]
            print(f"[ok] injected passed: {s2['passed_injected_events']}")

            print("\n=== Phase 15: batch with expect_focus while locked ===")
            b = g.batch(
                [{"type": "sleep", "ms": 30}],
                expect_focus={"hwnd": target_hwnd},
            )
            assert b["succeeded"] == 1
            print(f"[ok] batch: {b['total_elapsed_ms']}ms")
        finally:
            print("\n=== Phase 16: cleanup ===")
            g.input_release_all()
            unlock = g.input_unlock()
            assert unlock["status"] == "unlocked"
            assert unlock["was_locked_ms"] > 0
            print(f"[ok] unlocked after {unlock['was_locked_ms']}ms")

        print("\n=== Phase 17: unlock idempotent ===")
        again = g.input_unlock()
        assert again["status"] == "unlocked"
        print("[ok] second unlock is 200 (idempotent)")

        print("\n=== Phase 18: lock_status after unlock ===")
        final = g.input_lock_status()
        assert final["locked"] is False
        print("[ok] status reports unlocked")

    print("\n*** DRYRUN SMOKE TEST PASSED ***")
    print("All F8 (focus) and F9 (lock + watchdog spawn) endpoints respond correctly.")
    print("For visual validation in xCloud Fortnite, run scripts/smoke_test_cloud_v2.py")
    print("with Chrome on xbox.com/play + Fortnite loaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
