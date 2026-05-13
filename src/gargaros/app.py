from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from litestar import Litestar
from litestar.config.cors import CORSConfig
from litestar.middleware.base import DefineMiddleware
from litestar.openapi import OpenAPIConfig

from gargaros import __version__, input_driver
from gargaros.auth import BearerAndHostMiddleware
from gargaros.backend import MCPBackend
from gargaros.browser_bridge import BrowserBridge
from gargaros.config import Settings, load
from gargaros.frame_cache import FrameCache
from gargaros.input_state import InputState
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
from gargaros.routes.input_meta import input_release_all
from gargaros.routes.key_state import (
    key_down,
    key_hold,
    key_release_all,
    key_sequence,
    key_up,
)
from gargaros.routes.meta import health
from gargaros.routes.mouse import mouse_button, mouse_move_smooth, mouse_release_all
from gargaros.routes.screenshot import latest_b64, screenshot
from gargaros.routes.ui import (
    ui_click_label,
    ui_launch_app,
    ui_scrape,
    ui_snapshot,
    ui_type_label,
)
from gargaros.token_store import load_or_create

logger = logging.getLogger(__name__)


def build_app(
    settings: Settings | None = None,
    *,
    backend: MCPBackend | None = None,
    token: str | None = None,
    input_state: InputState | None = None,
) -> Litestar:
    settings = settings or load()
    token = token or load_or_create(settings.token_path)
    backend = backend or MCPBackend(command=settings.mcp_command, args=settings.mcp_args)
    state = input_state or InputState(driver=input_driver)

    @asynccontextmanager
    async def lifespan(app: Litestar):
        await backend.start()
        # Section 8: release anything left over from a previous crashed session.
        await state.release_all()
        import asyncio
        watchdog = asyncio.create_task(state.watchdog_loop())
        try:
            yield
        finally:
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("watchdog raised on shutdown")
            await state.release_all()
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
            find,
            browser_snapshot,
            browser_click,
            browser_type,
            browser_navigate,
            browser_tabs,
            browser_pull,
            browser_result,
            key_hold,
            key_down,
            key_up,
            key_sequence,
            key_release_all,
            mouse_move_smooth,
            mouse_button,
            mouse_release_all,
            input_release_all,
        ],
        middleware=[auth_mw],
        lifespan=[lifespan],
        cors_config=CORSConfig(allow_origins=["*"]),
        openapi_config=OpenAPIConfig(title="Gargaros", version=__version__, path="/schema"),
        debug=False,
    )
    app.state.settings = settings
    app.state.backend = backend
    app.state.frame_cache = FrameCache()
    app.state.ocr_cache = OCRCache()
    app.state.browser_bridge = BrowserBridge()
    app.state.token = token
    app.state.input_state = state
    app.state.input_driver = input_driver
    return app
