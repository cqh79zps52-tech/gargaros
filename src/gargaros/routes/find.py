from __future__ import annotations

from litestar import Request, Response, get

from gargaros.ocr import OCRCache
from gargaros.ocr import run as ocr_run
from gargaros.routes.screenshot import _capture, _encode_jpeg


def _filter(matches, query: str, fuzzy: bool, min_confidence: float) -> list[dict]:
    q = query.lower()
    out: list[dict] = []
    for m in matches:
        if m.confidence < min_confidence:
            continue
        text = m.text.lower()
        hit = q in text
        if not hit and fuzzy:
            try:
                from thefuzz import fuzz  # type: ignore[import-not-found]
                hit = fuzz.partial_ratio(q, text) >= 80
            except ImportError:
                hit = False
        if hit:
            x, y, w, h = m.bbox
            out.append({
                "text": m.text,
                "bbox": [x, y, w, h],
                "center": [x + w // 2, y + h // 2],
                "confidence": round(m.confidence, 4),
            })
    return out


@get("/find")
async def find(
    request: Request,
    text: str,
    monitor: int = 0,
    fuzzy: bool = True,
    min_confidence: float = 0.5,
) -> Response:
    if not text:
        return Response(content={"error": "text query is required"}, status_code=400)

    state = request.app.state
    cache: OCRCache = state.ocr_cache
    runner = getattr(state, "ocr_runner", ocr_run)

    raw = await _capture(request, monitor)
    body = _encode_jpeg(raw, 80)
    frame = state.frame_cache.store(monitor, body)

    matches = cache.get(frame.digest)
    if matches is None:
        matches = runner(body)
        cache.put(frame.digest, matches)

    return Response(
        content={
            "matches": _filter(matches, text, fuzzy, min_confidence),
            "frame_hash": frame.digest,
            "monitor": monitor,
        },
        media_type="application/json",
    )
