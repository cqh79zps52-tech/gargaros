from __future__ import annotations

import atexit
import logging
import sys

import uvicorn

from gargaros.app import build_app
from gargaros.config import load
from gargaros.input_state import emergency_release_sync
from gargaros.token_store import load_or_create


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    settings = load()
    token = load_or_create(settings.token_path)
    print(
        f"Gargaros listening on http://{settings.host}:{settings.port}\n"
        f"  bearer token (also at {settings.token_path}):\n"
        f"  {token}",
        flush=True,
    )
    app = build_app(settings, token=token)
    # Section 8 backstop: if uvicorn dies hard and lifespan shutdown doesn't run,
    # atexit still fires for sys.exit / unhandled-exception paths.
    atexit.register(emergency_release_sync, app.state.input_state)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
