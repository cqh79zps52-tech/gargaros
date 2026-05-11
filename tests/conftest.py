from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from litestar.testing import TestClient
from PIL import Image

from gargaros.app import build_app
from gargaros.backend import MCPBackend
from gargaros.config import Settings


class FakeBackend(MCPBackend):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._image: bytes | None = None
        self._fail_with: Exception | None = None
        self._error_text: str | None = None
        self._alive = False

    def set_image(self, png_bytes: bytes) -> None:
        self._image = png_bytes

    def fail_next(self, exc: Exception) -> None:
        self._fail_with = exc

    def set_error_result(self, text: str) -> None:
        """Make the next call_tool return an isError=True CallToolResult."""
        self._error_text = text

    async def start(self) -> None:
        self._alive = True

    async def stop(self) -> None:
        self._alive = False

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if self._fail_with is not None:
            exc, self._fail_with = self._fail_with, None
            raise exc
        if self._error_text is not None:
            err, self._error_text = self._error_text, None
            return SimpleNamespace(content=[SimpleNamespace(text=err)], isError=True)
        if name == "Screenshot":
            img = self._image or _dummy_png()
            return SimpleNamespace(content=[SimpleNamespace(data=base64.b64encode(img).decode(), mimeType="image/png")])
        return SimpleNamespace(content=[SimpleNamespace(text=f"ok: {name}")])

    @property
    def is_alive(self) -> bool:
        return self._alive


def _dummy_png() -> bytes:
    img = Image.new("RGB", (16, 16), (10, 20, 30))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(token_path=tmp_path / "token")


@pytest.fixture
def token() -> str:
    return "test-token-1234567890"


@pytest.fixture
def app(settings: Settings, fake_backend: FakeBackend, token: str):
    return build_app(settings, backend=fake_backend, token=token)


@pytest.fixture
def client(app):
    with TestClient(app=app, base_url="http://127.0.0.1:7331") as c:
        yield c


@pytest.fixture
def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Host": "127.0.0.1:7331"}
