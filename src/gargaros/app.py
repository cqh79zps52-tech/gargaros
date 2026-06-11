from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import anyio
from litestar import Litestar
from litestar.config.cors import CORSConfig
from litestar.middleware.base import DefineMiddleware
from litestar.openapi import OpenAPIConfig

from gargaros import __version__
from gargaros.auth import BearerAndHostMiddleware
from gargaros.backend import MCPBackend
from gargaros.browser_bridge import BrowserBridge
from gargaros.config import Settings, load
from gargaros.frame_cache import FrameCache
from gargaros.hidden_desktop import HiddenDesktop
from gargaros.ocr import OCRCache
from gargaros.routes.batch import batch
from gargaros.routes.browser import (
    browser_click,
    browser_navigate,
    browser_pull,
    browser_result,
    browser_snapshot,
    browser_tabs,
    browser_type,
)
from gargaros.routes.find import find
from gargaros.routes.input import click, drag, key, move, scroll, type_text
from gargaros.routes.meta import health
from gargaros.routes.screenshot import latest_b64, screenshot
from gargaros.routes.ui import (
    agent_launch_app,
    ui_click_label,
    ui_launch_app,
    ui_scrape,
    ui_snapshot,
    ui_type_label,
)
from gargaros.token_store import load_or_create

logger = logging.getLogger(__name__)


def build_app(settings: Settings | None = None, *, backend: MCPBackend | None = None, token: str | None = None) -> Litestar:
    settings = settings or load()
    token = token or load_or_create(settings.token_path)
    backend = backend or MCPBackend(command=settings.mcp_command, args=settings.mcp_args)

    @asynccontextmanager
    async def lifespan(app: Litestar):
        await backend.start()
        hd: HiddenDesktop | None = None
        if settings.hidden_desktop_enabled:
            try:
                hd = await anyio.to_thread.run_sync(
                    lambda: HiddenDesktop(settings.hidden_desktop_name)
                )
            except Exception:
                logger.exception("hidden desktop init failed; falling back to Windows-MCP only")
        app.state.hidden_desktop = hd
        try:
            yield
        finally:
            if hd is not None:
                await anyio.to_thread.run_sync(hd.shutdown)
            await backend.stop()

    auth_mw = DefineMiddleware(
        BearerAndHostMiddleware,
        token=token,
        allowed_hosts=settings.allowed_hosts,
    )

    app = Litestar(
        route_handlers=[
            health,
            screenshot,
            latest_b64,
            click,
            move,
            type_text,
            key,
            scroll,
            drag,
            batch,
            ui_snapshot,
            ui_click_label,
            ui_type_label,
            ui_scrape,
            ui_launch_app,
            agent_launch_app,
            find,
            browser_snapshot,
            browser_click,
            browser_type,
            browser_navigate,
            browser_tabs,
            browser_pull,
            browser_result,
        ],
        middleware=[auth_mw],
        lifespan=[lifespan],
        cors_config=CORSConfig(allow_origins=["*"]),
        openapi_config=OpenAPIConfig(title="Gargaros", version=__version__, path="/schema"),
        debug=False,
    )
    app.state.settings = settings
    app.state.backend = backend
    app.state.hidden_desktop = None  # set during lifespan startup
    app.state.frame_cache = FrameCache()
    app.state.ocr_cache = OCRCache()
    app.state.browser_bridge = BrowserBridge()
    app.state.token = token
    return app
