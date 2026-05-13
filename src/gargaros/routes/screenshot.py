from __future__ import annotations

import base64
import io
import logging
import time

from litestar import Request, Response, get
from litestar.exceptions import ClientException
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


def _parse_bbox(s: str) -> tuple[int, int, int, int]:
    parts = s.split(",")
    if len(parts) != 4:
        raise ClientException(detail="bbox must be 'x1,y1,x2,y2'")
    try:
        x1, y1, x2, y2 = (int(p.strip()) for p in parts)
    except ValueError as e:
        raise ClientException(detail=f"bbox values must be integers: {e}") from e
    if x1 < 0 or y1 < 0:
        raise ClientException(detail=f"bbox coords must be non-negative; got ({x1},{y1})")
    if x2 <= x1 or y2 <= y1:
        raise ClientException(detail=f"bbox is empty or inverted: ({x1},{y1})-({x2},{y2})")
    return x1, y1, x2, y2


def _crop_png(png_bytes: bytes, bbox: tuple[int, int, int, int]) -> bytes:
    img = Image.open(io.BytesIO(png_bytes))
    x1, y1, x2, y2 = bbox
    w, h = img.size
    if x2 > w or y2 > h:
        raise ClientException(
            detail=f"bbox ({x1},{y1})-({x2},{y2}) outside screen ({w}x{h})",
        )
    cropped = img.crop((x1, y1, x2, y2))
    buf = io.BytesIO()
    cropped.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


async def _capture(request: Request, monitor: int) -> tuple[bytes, int]:
    """Returns (png_bytes_from_dxcam, capture_time_ms)."""
    backend = request.app.state.backend
    args: dict = {}
    if monitor is not None:
        args["display"] = [int(monitor)]
    t0 = time.monotonic()
    result = await backend.call_tool("Screenshot", args)
    capture_ms = int((time.monotonic() - t0) * 1000)
    img = extract_image_bytes(result)
    if img is None:
        raise RuntimeError("Windows-MCP Screenshot tool returned no image content")
    return img, capture_ms


@get("/screenshot")
async def screenshot(
    request: Request,
    monitor: int = 0,
    fmt: str = "jpeg",
    quality: int = 80,
    raw: bool = False,
    bbox: str | None = None,
) -> Response:
    raw_png, capture_ms = await _capture(request, monitor)
    cache = request.app.state.frame_cache
    encode_t0 = time.monotonic()

    # F6: optional region crop (bbox=x1,y1,x2,y2). Always crops the source PNG before
    # any JPEG re-encode so the encoded payload is also smaller.
    if bbox is not None:
        rect = _parse_bbox(bbox)
        raw_png = _crop_png(raw_png, rect)

    # raw=true bypasses Pillow re-encoding (the bbox crop above already used Pillow,
    # but only on the smaller image — the JPEG full-image re-encode is what we skip).
    if raw or fmt.lower() == "png":
        body = raw_png
        media_type = "image/png"
    else:
        body = _encode_jpeg(raw_png, quality)
        media_type = "image/jpeg"
    encode_ms = int((time.monotonic() - encode_t0) * 1000)

    frame = cache.store(monitor, body)
    headers = {
        "ETag": f'"{frame.digest}"',
        "X-Capture-Time-Ms": str(capture_ms),
        "X-Encode-Time-Ms": str(encode_ms),
    }

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

    raw_png, _ = await _capture(request, monitor)
    body = _encode_jpeg(raw_png, 80)
    frame = cache.store(monitor, body)
    payload = {"b64": base64.b64encode(frame.data).decode("ascii"), "hash": frame.digest, "monitor": monitor}
    return Response(content=payload, media_type="application/json", headers={"ETag": f'"{frame.digest}"'})
