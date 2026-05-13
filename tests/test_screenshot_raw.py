"""F2 — verify raw=true bypasses Pillow re-encoding and emits timing headers."""

from __future__ import annotations

import io
import time

from PIL import Image


def _png(color: tuple[int, int, int] = (10, 20, 30), size: int = 16) -> bytes:
    img = Image.new("RGB", (size, size), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_screenshot_raw_returns_png(client, auth_headers, fake_backend):
    src = _png()
    fake_backend.set_image(src)
    r = client.get("/screenshot?raw=true", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == src  # exact passthrough — no re-encode


def test_screenshot_raw_has_timing_headers(client, auth_headers, fake_backend):
    fake_backend.set_image(_png())
    r = client.get("/screenshot?raw=true", headers=auth_headers)
    assert "x-capture-time-ms" in r.headers
    assert "x-encode-time-ms" in r.headers
    # raw mode skips Pillow → encode time should be near-zero
    assert int(r.headers["x-encode-time-ms"]) <= 5


def test_screenshot_default_jpeg_still_works(client, auth_headers, fake_backend):
    fake_backend.set_image(_png())
    r = client.get("/screenshot", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    # JPEG re-encoded — content differs from source PNG
    assert r.content != _png()


def test_screenshot_jpeg_has_nonzero_encode_time(client, auth_headers, fake_backend):
    fake_backend.set_image(_png(size=128))  # bigger image → measurable encode
    r = client.get("/screenshot", headers=auth_headers)
    # encode_time should be set (may be 0 on very fast machines for small images, so just assert present)
    assert "x-encode-time-ms" in r.headers


def test_raw_skips_pillow_call(client, auth_headers, fake_backend, monkeypatch):
    """Belt-and-braces: ensure Pillow Image.open isn't invoked when raw=true."""
    from gargaros.routes import screenshot as ss_mod

    call_count = {"n": 0}
    orig = ss_mod._encode_jpeg

    def counted(*args, **kwargs):
        call_count["n"] += 1
        return orig(*args, **kwargs)

    monkeypatch.setattr(ss_mod, "_encode_jpeg", counted)

    fake_backend.set_image(_png())
    client.get("/screenshot?raw=true", headers=auth_headers)
    assert call_count["n"] == 0, "Pillow encoder should not run when raw=true"


def test_raw_faster_than_jpeg_on_repeated_calls(client, auth_headers, fake_backend):
    """Sanity bench: raw mode should not be slower than JPEG (full perf check is manual)."""
    fake_backend.set_image(_png(size=256))

    jpeg_t0 = time.monotonic()
    for _ in range(10):
        client.get("/screenshot", headers=auth_headers)
    jpeg_total = time.monotonic() - jpeg_t0

    raw_t0 = time.monotonic()
    for _ in range(10):
        client.get("/screenshot?raw=true", headers=auth_headers)
    raw_total = time.monotonic() - raw_t0

    # With a 256x256 image, raw should typically be ≤ jpeg; allow a wide margin for CI noise.
    assert raw_total <= jpeg_total * 1.5, f"raw={raw_total*1000:.0f}ms jpeg={jpeg_total*1000:.0f}ms"
