"""Smoke test the Gargaros browser extension via Playwright.

Launches Chromium with the extension loaded, sets the bearer token in
chrome.storage, opens https://example.com so the extension has a target tab,
then prints the service-worker URL so a separate shell can hit /browser/*.

Keeps the browser open until you press Enter so you can run curl in parallel.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

EXT_DIR = Path(r"D:\Work\Brain1\Gargaros\extension").resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default=os.environ.get("GARGAROS_TOKEN"))
    parser.add_argument("--url", default="http://127.0.0.1:7331")
    parser.add_argument("--target", default="https://example.com")
    parser.add_argument("--user-data-dir", default=str(Path(os.environ["TEMP"]) / "gargaros-playwright-profile"))
    parser.add_argument("--wait-seconds", type=int, default=120, help="Keep browser open this long for manual probing.")
    args = parser.parse_args()

    if not args.token:
        from platformdirs import user_data_dir
        token_file = Path(user_data_dir("Gargaros", appauthor=False)) / "token"
        if token_file.exists():
            args.token = token_file.read_text(encoding="utf-8").strip()
    if not args.token:
        print("No token. Pass --token or set GARGAROS_TOKEN or run Gargaros once.", file=sys.stderr)
        return 1

    print(f"loading extension: {EXT_DIR}")
    print(f"user data dir:    {args.user_data_dir}")
    print(f"target page:      {args.target}")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            args.user_data_dir,
            headless=False,
            args=[
                f"--disable-extensions-except={EXT_DIR}",
                f"--load-extension={EXT_DIR}",
            ],
        )

        # Wait for the extension's service worker to register.
        # MV3 workers sometimes need a nudge — opening any page triggers their wake.
        wake = ctx.new_page()
        wake.goto("about:blank", timeout=5000)

        worker = None
        if ctx.service_workers:
            worker = ctx.service_workers[0]
        else:
            try:
                worker = ctx.wait_for_event("serviceworker", timeout=30000)
            except Exception as e:
                print(f"service worker did not register: {e}", file=sys.stderr)
                # Last-ditch: scan again after the wait
                if ctx.service_workers:
                    worker = ctx.service_workers[0]
        if worker is None:
            print("service worker still not present; bailing", file=sys.stderr)
            ctx.close()
            return 2

        print(f"service worker:   {worker.url}")
        # The extension ID is the host of the SW URL: chrome-extension://<id>/background.js
        ext_id = worker.url.split("/")[2]
        print(f"extension id:     {ext_id}")

        # Inject the token into chrome.storage.local via the SW and force a config reload.
        worker.evaluate(
            """
            async ({url, token}) => {
                await chrome.storage.local.set({gargarosUrl: url, gargarosToken: token});
                if (typeof loadConfig === 'function') { await loadConfig(); }
                return await chrome.storage.local.get(['gargarosUrl', 'gargarosToken']);
            }
            """,
            {"url": args.url, "token": args.token},
        )
        print("token written to chrome.storage.local; extension will repoll within ~25s.")

        # Open the target page in the existing first window.
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(args.target, wait_until="domcontentloaded", timeout=20000)
        print(f"opened target page; title={page.title()!r}")

        print()
        print(f"READY — leaving browser open for {args.wait_seconds}s. Run smoke checks in another shell.")
        print(f"  curl -H \"Authorization: Bearer $TOKEN\" -X POST -H \"Content-Type: application/json\" -d '{{}}' {args.url}/browser/snapshot")
        print()

        sys.stdout.flush()
        time.sleep(args.wait_seconds)
        ctx.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
