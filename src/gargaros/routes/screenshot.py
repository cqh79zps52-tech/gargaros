from __future__ import annotations

import base64
import io
import logging

import anyio
from litestar import Request, Response, get
from PIL import Image

from gargaros.translate import extract_image_bytes

logger = logging.getLogger(__name__)


def _encode_jpeg(png_or_jpeg: bytes, quality: int) -> bytes:
    img = Image.open(io.BytesIO(png_or_jpeg))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=False)
    return buf.getvalue()


async def _capture(request: Request, monitor: int) -> bytes:
    """Return raw image bytes (PNG/JPEG) of the current frame.

    When a hidden desktop is active, capture its windows with PrintWindow and composite
    them (monitor is ignored — the composite spans the whole virtual screen). Otherwise
    fall back to the Windows-MCP Screenshot tool on the visible desktop.
    """
    hd = getattr(request.app.state, "hidden_desktop", None)
    if hd is not None:
        try:
            img = await anyio.to_thread.run_sync(hd.capture_composite)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            logger.exception("composite capture failed; falling back to Windows-MCP")

    backend = request.app.state.backend
    args: dict = {}
    if monitor is not None:
        args["display"] = [int(monitor)]
    result = await backend.call_tool("Screenshot", args)
    img = extract_image_bytes(result)
    if img is None:
        raise RuntimeError("Windows-MCP Screenshot tool returned no image content")
    return img


@get("/screenshot")
async def screenshot(request: Request, monitor: int = 0, fmt: str = "jpeg", quality: int = 80) -> Response:
    raw = await _capture(request, monitor)
    cache = request.app.state.frame_cache
    if fmt.lower() == "jpeg":
        body = _encode_jpeg(raw, quality)
        media_type = "image/jpeg"
    else:
        body = raw
        media_type = "image/png"
    frame = cache.store(monitor, body)
    headers = {"ETag": f'"{frame.digest}"'}

    inm = request.headers.get("if-none-match", "").strip('"')
    if inm and inm == frame.digest:
        return Response(content=b"", status_code=304, headers=headers)
    return Response(content=body, media_type=media_type, headers=headers)


@get("/latest_b64")
async def latest_b64(request: Request, monitor: int = 0) -> Response:
    cache = request.app.state.frame_cache
    cached = cache.get(monitor)

    inm = request.headers.get("if-none-match", "").strip('"')
    if cached and inm and inm == cached.digest:
        return Response(content=b"", status_code=304, headers={"ETag": f'"{cached.digest}"'})

    raw = await _capture(request, monitor)
    body = _encode_jpeg(raw, 80)
    frame = cache.store(monitor, body)
    payload = {"b64": base64.b64encode(frame.data).decode("ascii"), "hash": frame.digest, "monitor": monitor}
    return Response(content=payload, media_type="application/json", headers={"ETag": f'"{frame.digest}"'})
