from __future__ import annotations

from gargaros.ocr import Match, OCRCache


def test_put_and_get():
    c = OCRCache()
    matches = [Match(text="hi", bbox=(0, 0, 10, 10), confidence=0.9)]
    c.put("digestA", matches)
    assert c.get("digestA") is matches


def test_lru_eviction():
    c = OCRCache(capacity=2)
    c.put("a", [])
    c.put("b", [])
    c.put("c", [])
    assert c.get("a") is None
    assert c.get("b") == []
    assert c.get("c") == []


def test_get_promotes_to_recent():
    c = OCRCache(capacity=2)
    c.put("a", [Match(text="A", bbox=(0, 0, 1, 1), confidence=0.9)])
    c.put("b", [Match(text="B", bbox=(0, 0, 1, 1), confidence=0.9)])
    _ = c.get("a")  # promotes a
    c.put("c", [])  # should evict b, not a
    assert c.get("a") is not None
    assert c.get("b") is None
    assert c.get("c") is not None
