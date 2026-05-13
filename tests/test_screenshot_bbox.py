"""F6 cloud — region capture via ?bbox=x1,y1,x2,y2."""

from __future__ import annotations

import io

from PIL import Image


def _png(size: tuple[int, int] = (1920, 1080), color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_bbox_returns_cropped_region(client, auth_headers, fake_backend):
    fake_backend.set_image(_png(size=(1920, 1080)))
    r = client.get("/screenshot?bbox=100,100,500,400&raw=true", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (400, 300)


def test_bbox_chrome_video_area(client, auth_headers, fake_backend):
    """The CDC example: bbox=0,80,1920,1160 — skip the Chrome tab bar."""
    fake_backend.set_image(_png(size=(1920, 1200)))
    r = client.get("/screenshot?bbox=0,80,1920,1160&raw=true", headers=auth_headers)
    assert r.status_code == 200
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (1920, 1080)


def test_bbox_out_of_screen_returns_400(client, auth_headers, fake_backend):
    fake_backend.set_image(_png(size=(800, 600)))
    r = client.get("/screenshot?bbox=0,0,2000,2000&raw=true", headers=auth_headers)
    assert r.status_code == 400
    assert "outside screen" in r.text.lower()


def test_bbox_inverted_returns_400(client, auth_headers, fake_backend):
    fake_backend.set_image(_png())
    r = client.get("/screenshot?bbox=500,500,100,100&raw=true", headers=auth_headers)
    assert r.status_code == 400


def test_bbox_negative_returns_400(client, auth_headers, fake_backend):
    fake_backend.set_image(_png())
    r = client.get("/screenshot?bbox=-1,0,100,100&raw=true", headers=auth_headers)
    assert r.status_code == 400


def test_bbox_malformed_returns_400(client, auth_headers, fake_backend):
    fake_backend.set_image(_png())
    r = client.get("/screenshot?bbox=foo,bar&raw=true", headers=auth_headers)
    assert r.status_code == 400


def test_bbox_with_jpeg_format(client, auth_headers, fake_backend):
    fake_backend.set_image(_png(size=(1920, 1080)))
    r = client.get("/screenshot?bbox=100,100,500,400&fmt=jpeg", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (400, 300)


def test_bbox_omitted_returns_full_screen(client, auth_headers, fake_backend):
    """Backward compat: no bbox = behavior unchanged."""
    src = _png(size=(640, 480))
    fake_backend.set_image(src)
    r = client.get("/screenshot?raw=true", headers=auth_headers)
    assert r.status_code == 200
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (640, 480)


def test_bbox_smaller_than_full_screen(client, auth_headers, fake_backend):
    """Sanity: cropped output is smaller (in bytes) than the full-screen capture."""
    fake_backend.set_image(_png(size=(1920, 1080)))
    r_full = client.get("/screenshot?raw=true", headers=auth_headers)
    r_crop = client.get("/screenshot?bbox=0,0,800,600&raw=true", headers=auth_headers)
    assert len(r_crop.content) < len(r_full.content)
