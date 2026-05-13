"""Manual smoke test for Fortnite Cloud Gaming v2 (CDC section 9).

Run with Gargaros already running (`python -m gargaros`) and Chrome open on
xbox.com/play with Fortnite loaded. The script focuses Chrome, locks physical
input, then drives the character. Press ctrl+shift+f12 at any time to abort
and unlock the keyboard manually.
"""

from __future__ import annotations

import sys
import time

from gargaros.client import Client


def main() -> int:
    with Client() as g:
        print("=== Phase 1 : sanity checks ===")
        assert g.health()["status"] == "ok"
        print("[ok] Health OK")

        print("\n=== Phase 2 : focus de fenêtre ===")
        windows = g.window_list(visible_only=True)
        xbox_win = next(
            (w for w in windows if "Xbox" in w["title"] or "Fortnite" in w["title"]),
            None,
        )
        assert xbox_win, (
            "Aucune fenêtre Chrome avec xCloud trouvée. Ouvre xbox.com/play d'abord."
        )
        print(f"[ok] Trouvé : {xbox_win['title']} (hwnd={xbox_win['hwnd']})")

        g.window_focus(selector={"hwnd": xbox_win["hwnd"]}, restore_if_minimized=True)
        g.window_wait_for_focus(selector={"hwnd": xbox_win["hwnd"]}, timeout_ms=2000)
        print("[ok] Chrome au premier plan")

        print("\n=== Phase 3 : interaction manuelle ===")
        print(">>> Clique MAINTENANT dans la zone de jeu Fortnite")
        print(">>> pour activer le pointer lock (curseur disparaît).")
        print(">>> Tu as 7 secondes. NE TAPE PAS sur le clavier.")
        for i in range(7, 0, -1):
            print(f"   {i}...")
            time.sleep(1)

        print("\n=== Phase 4 : activation du lock ===")
        g.input_lock(unlock_hotkey="ctrl+shift+f12")
        print("[ok] Input lock activé.")
        print("  (clavier/souris physiques bloqués, sauf ctrl+shift+f12)")
        time.sleep(1)

        try:
            print("\n=== Phase 5 : screenshot rapide PNG brut ===")
            g.screenshot(raw=True)  # warmup
            img, meta = g.screenshot(raw=True, return_meta=True)
            assert meta["capture_time_ms"] < 100, f"slow: {meta}"
            print(f"[ok] Screenshot full ({meta['capture_time_ms']}ms)")

            print("\n=== Phase 6 : screenshot bbox ===")
            # Derive bbox from the actual screen height
            from io import BytesIO
            from PIL import Image
            w, h = Image.open(BytesIO(img)).size
            img, meta = g.screenshot(
                raw=True, bbox=(0, 80, w, h), return_meta=True
            )
            print(f"[ok] Screenshot bbox ({meta['capture_time_ms']}ms)")

            print("\n=== Phase 7 : avancer 1.5s ===")
            g.key_hold("w", 1500, expect_focus={"hwnd": xbox_win["hwnd"]})
            print("[ok] key_hold OK (le personnage devrait avoir avancé)")
            time.sleep(0.5)

            print("\n=== Phase 8 : tourner la caméra à droite ===")
            g.mouse_move_smooth(
                dx=720, dy=0, duration_ms=600, steps=24,
                mode="relative",
                expect_focus={"hwnd": xbox_win["hwnd"]},
            )
            print("[ok] Caméra tournée à droite")
            time.sleep(0.5)

            print("\n=== Phase 9 : batch (tourner gauche + sauter) ===")
            g.batch(
                [
                    {"type": "mouse_move_smooth", "dx": -720, "dy": 0,
                     "duration_ms": 600, "steps": 24, "mode": "relative"},
                    {"type": "key", "name": "space"},
                    {"type": "sleep", "ms": 300},
                ],
                expect_focus={"hwnd": xbox_win["hwnd"]},
            )
            print("[ok] Batch OK")
            time.sleep(0.5)
        finally:
            print("\n=== Phase 10 : cleanup ===")
            g.input_release_all()
            g.input_unlock()
            print("[ok] Inputs relâchés et lock désactivé")

    print("\n*** SMOKE TEST PASSED ***")
    print("Si tu as vu Fortnite réagir à chaque phase, c'est gagné.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
