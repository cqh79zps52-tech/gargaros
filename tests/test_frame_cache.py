from __future__ import annotations

from gargaros.frame_cache import FrameCache


def test_store_and_get():
    c = FrameCache()
    f = c.store(0, b"abc")
    got = c.get(0)
    assert got is not None
    assert got.data == b"abc"
    assert got.digest == f.digest


def test_get_missing_returns_none():
    c = FrameCache()
    assert c.get(99) is None


def test_digest_is_stable():
    c = FrameCache()
    a = c.store(0, b"hello")
    b = c.store(0, b"hello")
    assert a.digest == b.digest


def test_digest_changes_with_data():
    c = FrameCache()
    a = c.store(0, b"hello")
    b = c.store(0, b"world")
    assert a.digest != b.digest
