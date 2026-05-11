from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_engine = None


@dataclass(frozen=True)
class Match:
    text: str
    bbox: tuple[int, int, int, int]  # x, y, w, h
    confidence: float


def _load_engine():
    global _engine
    if _engine is not None:
        return _engine
    try:
        from rapidocr import RapidOCR  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "rapidocr is not installed. Install with: pip install -e .[ocr]"
        ) from e
    logger.info("loading RapidOCR engine (first call may download ~15 MB of models)")
    _engine = RapidOCR()
    return _engine


def run(image_bytes: bytes) -> list[Match]:
    """Run OCR on raw image bytes (PNG or JPEG). Returns a list of Match.

    Handles two RapidOCR output shapes:
      * 3.x: a `RapidOCROutput` object with parallel `boxes`, `txts`, `scores`.
      * Older: a list/tuple of (polygon, text, confidence) triples.
    """
    import io

    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    arr = np.array(img)
    engine = _load_engine()
    raw = engine(arr)
    if raw is None:
        return []

    out: list[Match] = []

    boxes = getattr(raw, "boxes", None)
    txts = getattr(raw, "txts", None)
    scores = getattr(raw, "scores", None)
    if boxes is not None and txts is not None and scores is not None:
        for poly, text, conf in zip(boxes, txts, scores, strict=False):
            xs = [int(p[0]) for p in poly]
            ys = [int(p[1]) for p in poly]
            x, y = min(xs), min(ys)
            w, h = max(xs) - x, max(ys) - y
            out.append(Match(text=str(text), bbox=(x, y, w, h), confidence=float(conf)))
        return out

    iter_obj = raw[0] if isinstance(raw, tuple) and raw and raw[0] is not None else (raw or [])
    for item in iter_obj:
        try:
            bbox, text, conf = item
        except (TypeError, ValueError):
            continue
        xs = [int(p[0]) for p in bbox]
        ys = [int(p[1]) for p in bbox]
        x, y = min(xs), min(ys)
        w, h = max(xs) - x, max(ys) - y
        out.append(Match(text=str(text), bbox=(x, y, w, h), confidence=float(conf)))
    return out


class OCRCache:
    """Bounded LRU keyed by frame digest. Avoids re-running OCR on identical frames."""

    def __init__(self, capacity: int = 8) -> None:
        self._capacity = capacity
        self._store: OrderedDict[str, list[Match]] = OrderedDict()

    def get(self, digest: str) -> list[Match] | None:
        if digest not in self._store:
            return None
        self._store.move_to_end(digest)
        return self._store[digest]

    def put(self, digest: str, matches: list[Match]) -> None:
        if digest in self._store:
            self._store.move_to_end(digest)
        self._store[digest] = matches
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)
