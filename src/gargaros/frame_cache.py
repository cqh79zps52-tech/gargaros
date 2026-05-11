from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass


@dataclass
class CachedFrame:
    data: bytes
    digest: str
    captured_at: float


class FrameCache:
    """One slot per monitor index. Tiny — just enough to support ETag/304."""

    def __init__(self) -> None:
        self._frames: dict[int, CachedFrame] = {}

    def store(self, monitor: int, data: bytes) -> CachedFrame:
        digest = hashlib.sha256(data).hexdigest()
        frame = CachedFrame(data=data, digest=digest, captured_at=time.time())
        self._frames[monitor] = frame
        return frame

    def get(self, monitor: int) -> CachedFrame | None:
        return self._frames.get(monitor)
