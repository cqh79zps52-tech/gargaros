"""F9 watchdog — separate process that polls /input/lock_status and force-kills
Gargaros if the server hangs while the input lock is active.

Per CDC v2 section 4: if Gargaros freezes with low-level hooks installed, the
keyboard stays jammed until the process dies. This watchdog detects unresponsive
servers and TerminateProcesses them. The hooks fall with the process — no
DLL-injection trickery, no per-thread unhooking from another process.

Run as: `python -m gargaros.watchdog --port 7331 --token X --target-pid Y --lock-id Z`
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time

PROCESS_TERMINATE = 0x0001


def _force_kill(pid: int) -> None:
    if sys.platform != "win32":
        try:
            os.kill(pid, 9)
        except Exception:
            pass
        return
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if h:
        try:
            kernel32.TerminateProcess(h, 1)
        finally:
            kernel32.CloseHandle(h)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7331)
    parser.add_argument("--token", required=True)
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--lock-id", required=True)
    parser.add_argument("--timeout-ms", type=int, default=2000)
    parser.add_argument("--poll-ms", type=int, default=500)
    args = parser.parse_args()

    # Defer the requests import so the module is importable in test envs that
    # don't install httpx/requests just for the watchdog.
    try:
        import urllib.request
        import json as _json
    except ImportError:
        return 2

    base = f"http://127.0.0.1:{args.port}/input/lock_status"
    timeout_s = args.timeout_ms / 1000
    poll_s = args.poll_ms / 1000
    last_ok = time.monotonic()

    while True:
        try:
            req = urllib.request.Request(base, headers={"Authorization": f"Bearer {args.token}"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = _json.loads(resp.read())
            if not payload.get("locked"):
                # Server says unlock — our work is done.
                return 0
            if payload.get("lock_id") != args.lock_id:
                # A new lock was acquired; we're stale, exit cleanly.
                return 0
            last_ok = time.monotonic()
        except Exception:
            pass

        if time.monotonic() - last_ok > timeout_s:
            # Server hung — force kill so hooks fall with the process.
            _force_kill(args.target_pid)
            return 1

        time.sleep(poll_s)


if __name__ == "__main__":
    sys.exit(main())
