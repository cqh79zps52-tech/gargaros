from __future__ import annotations

import logging
import sys

import uvicorn

from gargaros.app import build_app
from gargaros.config import load
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
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
