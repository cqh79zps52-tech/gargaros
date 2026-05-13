from __future__ import annotations

from litestar import Request, get

from gargaros import __version__


@get("/health", sync_to_thread=False)
def health(request: Request) -> dict:
    backend = request.app.state.backend
    return {
        "ok": True,
        "status": "ok",
        "version": __version__,
        "backend_alive": backend.is_alive,
    }
