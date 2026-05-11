"""Synchronous HTTP client for talking to a running Gargaros server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from platformdirs import user_data_dir


def _discover_token() -> str | None:
    env = os.environ.get("GARGAROS_TOKEN")
    if env:
        return env.strip()
    p = Path(user_data_dir("Gargaros", appauthor=False)) / "token"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return None


class Client:
    """Thin sync wrapper around the Gargaros HTTP API.

    Token is auto-discovered: explicit arg → GARGAROS_TOKEN env → %APPDATA%\\Gargaros\\token.
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str = "http://127.0.0.1:7331",
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        resolved = token or _discover_token()
        if not resolved:
            raise RuntimeError(
                "no Gargaros token found; pass token=, set GARGAROS_TOKEN, "
                "or run `python -m gargaros` once to generate one"
            )
        self._token = resolved
        headers = {"Authorization": f"Bearer {resolved}"}
        self._http = httpx.Client(base_url=base_url, headers=headers, timeout=timeout, transport=transport)

    # ---- lifecycle ----

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ---- meta ----

    def health(self) -> dict:
        return self._http.get("/health").raise_for_status().json()

    # ---- screen ----

    def screenshot(self, monitor: int = 0, fmt: str = "jpeg", quality: int = 80) -> bytes:
        r = self._http.get("/screenshot", params={"monitor": monitor, "fmt": fmt, "quality": quality})
        r.raise_for_status()
        return r.content

    def latest_b64(self, monitor: int = 0) -> tuple[bytes, str]:
        r = self._http.get("/latest_b64", params={"monitor": monitor})
        r.raise_for_status()
        body = r.json()
        import base64
        return base64.b64decode(body["b64"]), body["hash"]

    # ---- input ----

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        self._post("/click", {"x": x, "y": y, "button": button, "double": double})

    def move(self, x: int, y: int, relative: bool = False) -> None:
        self._post("/move", {"x": x, "y": y, "relative": relative})

    def type(self, text: str, press_enter: bool = False) -> None:
        self._post("/type", {"text": text, "press_enter": press_enter})

    def key(self, name: str, modifiers: list[str] | None = None) -> None:
        self._post("/key", {"name": name, "modifiers": modifiers or []})

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        self._post("/scroll", {"dx": dx, "dy": dy})

    def drag(self, src: tuple[int, int] | dict, to: tuple[int, int] | dict, button: str = "left") -> None:
        if isinstance(src, tuple):
            src = {"x": src[0], "y": src[1]}
        if isinstance(to, tuple):
            to = {"x": to[0], "y": to[1]}
        self._post("/drag", {"from": src, "to": to, "button": button})

    def batch(self, actions: list[dict], continue_on_error: bool = False) -> list[dict]:
        return self._post("/batch", {"actions": actions, "continue_on_error": continue_on_error})["results"]

    # ---- ui ----

    def ui_snapshot(self, **kw: Any) -> dict:
        return self._post("/ui/snapshot", kw)

    def ui_click_label(self, label: int, button: str = "left", double: bool = False) -> None:
        self._post("/ui/click_label", {"label": label, "button": button, "double": double})

    def ui_type_label(self, label: int, text: str, clear: bool = False, press_enter: bool = False) -> None:
        self._post("/ui/type_label", {"label": label, "text": text, "clear": clear, "press_enter": press_enter})

    def ui_scrape(self, url: str, query: str | None = None) -> dict:
        body: dict[str, Any] = {"url": url}
        if query is not None:
            body["query"] = query
        return self._post("/ui/scrape", body)

    def ui_launch_app(self, name: str, mode: str = "launch", **kw: Any) -> dict:
        return self._post("/ui/launch_app", {"name": name, "mode": mode, **kw})

    # ---- browser ----

    def browser_snapshot(self, tab_id: int | None = None, include_screenshot: bool = True) -> dict:
        body: dict[str, Any] = {"include_screenshot": include_screenshot}
        if tab_id is not None:
            body["tab_id"] = tab_id
        return self._post("/browser/snapshot", body)

    def browser_click(self, selector: str, tab_id: int | None = None) -> dict:
        body: dict[str, Any] = {"selector": selector}
        if tab_id is not None:
            body["tab_id"] = tab_id
        return self._post("/browser/click", body)

    def browser_type(
        self,
        selector: str,
        text: str,
        clear: bool = False,
        press_enter: bool = False,
        tab_id: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "selector": selector,
            "text": text,
            "clear": clear,
            "press_enter": press_enter,
        }
        if tab_id is not None:
            body["tab_id"] = tab_id
        return self._post("/browser/type", body)

    def browser_navigate(self, url: str, tab_id: int | None = None, wait_for_load: bool = True) -> dict:
        body: dict[str, Any] = {"url": url, "wait_for_load": wait_for_load}
        if tab_id is not None:
            body["tab_id"] = tab_id
        return self._post("/browser/navigate", body)

    def browser_tabs(self) -> list[dict]:
        r = self._http.get("/browser/tabs")
        r.raise_for_status()
        return r.json()["tabs"]

    # ---- find / OCR ----

    def find(self, text: str, monitor: int = 0, fuzzy: bool = True, min_confidence: float = 0.5) -> list[dict]:
        r = self._http.get("/find", params={"text": text, "monitor": monitor, "fuzzy": fuzzy, "min_confidence": min_confidence})
        r.raise_for_status()
        return r.json()["matches"]

    # ---- internal ----

    def _post(self, path: str, body: dict) -> Any:
        r = self._http.post(path, json=body)
        r.raise_for_status()
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return None
