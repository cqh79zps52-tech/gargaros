"""Tracks keys / mouse buttons currently held down so we can guarantee release.

Section 8 of the Fortnite CDC mandates this: if Gargaros crashes with a key
"down", the user's keyboard stays jammed. Every state mutation goes through
here; lifespan shutdown, atexit, and the watchdog all call release_all().
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from gargaros import input_driver

logger = logging.getLogger(__name__)

_HELD_TIMEOUT_S = 30.0
_WATCHDOG_INTERVAL_S = 5.0


class InputState:
    """Thread-safe set of currently-held inputs with a watchdog for stuck entries."""

    def __init__(self, driver: Any = input_driver) -> None:
        self._driver = driver
        self._keys: dict[str, float] = {}
        self._buttons: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ---- queries ----

    @property
    def held_keys(self) -> list[str]:
        return list(self._keys.keys())

    @property
    def held_buttons(self) -> list[str]:
        return list(self._buttons.keys())

    def is_key_down(self, name: str) -> bool:
        return name.lower() in self._keys

    def is_button_down(self, button: str) -> bool:
        return button.lower() in self._buttons

    # ---- key state ----

    async def press_key(self, name: str) -> bool:
        """Send key_down and record state. Returns False if already down (no-op driver call)."""
        norm = name.lower()
        async with self._lock:
            if norm in self._keys:
                return False
            self._driver.key_down(name)
            self._keys[norm] = time.monotonic()
            return True

    async def release_key(self, name: str) -> float | None:
        """Send key_up and clear state. Returns held duration in seconds, or None if not down."""
        norm = name.lower()
        async with self._lock:
            if norm not in self._keys:
                return None
            held = time.monotonic() - self._keys.pop(norm)
        # driver call outside the lock (it's a syscall, no contention needed)
        try:
            self._driver.key_up(name)
        except Exception:
            logger.exception("key_up failed for %s", name)
        return held

    # ---- mouse button state ----

    async def press_button(self, button: str) -> bool:
        norm = button.lower()
        async with self._lock:
            if norm in self._buttons:
                return False
            self._driver.mouse_button(button, "down")
            self._buttons[norm] = time.monotonic()
            return True

    async def release_button(self, button: str) -> float | None:
        norm = button.lower()
        async with self._lock:
            if norm not in self._buttons:
                return None
            held = time.monotonic() - self._buttons.pop(norm)
        try:
            self._driver.mouse_button(button, "up")
        except Exception:
            logger.exception("mouse_button up failed for %s", button)
        return held

    # ---- bulk release ----

    async def release_all_keys(self) -> list[str]:
        async with self._lock:
            names = list(self._keys.keys())
            self._keys.clear()
        for n in names:
            try:
                self._driver.key_up(n)
            except Exception:
                logger.exception("release_all_keys: key_up failed for %s", n)
        return names

    async def release_all_buttons(self) -> list[str]:
        async with self._lock:
            names = list(self._buttons.keys())
            self._buttons.clear()
        for n in names:
            try:
                self._driver.mouse_button(n, "up")
            except Exception:
                logger.exception("release_all_buttons: mouse_button up failed for %s", n)
        return names

    async def release_all(self) -> dict[str, list[str]]:
        keys = await self.release_all_keys()
        buttons = await self.release_all_buttons()
        return {"keys": keys, "buttons": buttons}

    # ---- watchdog ----

    async def watchdog_loop(
        self,
        interval: float = _WATCHDOG_INTERVAL_S,
        threshold: float = _HELD_TIMEOUT_S,
    ) -> None:
        """Periodically release any input held longer than `threshold` seconds."""
        try:
            while True:
                await asyncio.sleep(interval)
                await self._sweep_stuck(threshold)
        except asyncio.CancelledError:
            raise

    async def _sweep_stuck(self, threshold: float) -> None:
        now = time.monotonic()
        stuck_keys: list[str] = []
        stuck_buttons: list[str] = []
        async with self._lock:
            for name, pressed_at in list(self._keys.items()):
                if now - pressed_at > threshold:
                    stuck_keys.append(name)
                    del self._keys[name]
            for btn, pressed_at in list(self._buttons.items()):
                if now - pressed_at > threshold:
                    stuck_buttons.append(btn)
                    del self._buttons[btn]
        for name in stuck_keys:
            logger.warning("watchdog: releasing stuck key %r (held > %ss)", name, threshold)
            try:
                self._driver.key_up(name)
            except Exception:
                logger.exception("watchdog: key_up failed for %s", name)
        for btn in stuck_buttons:
            logger.warning("watchdog: releasing stuck mouse button %r (held > %ss)", btn, threshold)
            try:
                self._driver.mouse_button(btn, "up")
            except Exception:
                logger.exception("watchdog: mouse_button up failed for %s", btn)


def emergency_release_sync(state: InputState | None = None) -> None:
    """Synchronous best-effort release for atexit; safe to call from a non-async context."""
    if state is None:
        return
    for name in list(state._keys.keys()):
        try:
            state._driver.key_up(name)
        except Exception:
            logger.exception("emergency_release: key_up failed for %s", name)
    state._keys.clear()
    for btn in list(state._buttons.keys()):
        try:
            state._driver.mouse_button(btn, "up")
        except Exception:
            logger.exception("emergency_release: mouse_button up failed for %s", btn)
    state._buttons.clear()
