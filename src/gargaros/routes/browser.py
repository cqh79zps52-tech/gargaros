from __future__ import annotations

from typing import Any

from litestar import Request, Response, get, post
from litestar.exceptions import ClientException
from msgspec import Struct


class SnapshotBody(Struct):
    tab_id: int | None = None
    include_screenshot: bool = True


class ClickBody(Struct):
    selector: str
    tab_id: int | None = None


class TypeBody(Struct):
    selector: str
    text: str
    clear: bool = False
    press_enter: bool = False
    tab_id: int | None = None


class NavigateBody(Struct):
    url: str
    tab_id: int | None = None
    wait_for_load: bool = True


class ResultBody(Struct):
    job_id: str
    result: Any = None
    error: str | None = None


def _bridge(request: Request):
    return request.app.state.browser_bridge


@post("/browser/snapshot")
async def browser_snapshot(request: Request, data: SnapshotBody) -> dict:
    return await _bridge(request).dispatch(
        "snapshot",
        {"tab_id": data.tab_id, "include_screenshot": data.include_screenshot},
    )


@post("/browser/click")
async def browser_click(request: Request, data: ClickBody) -> dict:
    return await _bridge(request).dispatch(
        "click", {"selector": data.selector, "tab_id": data.tab_id}
    )


@post("/browser/type")
async def browser_type(request: Request, data: TypeBody) -> dict:
    return await _bridge(request).dispatch(
        "type",
        {
            "selector": data.selector,
            "text": data.text,
            "clear": data.clear,
            "press_enter": data.press_enter,
            "tab_id": data.tab_id,
        },
    )


@post("/browser/navigate")
async def browser_navigate(request: Request, data: NavigateBody) -> dict:
    return await _bridge(request).dispatch(
        "navigate",
        {"url": data.url, "tab_id": data.tab_id, "wait_for_load": data.wait_for_load},
    )


@get("/browser/tabs")
async def browser_tabs(request: Request) -> dict:
    return await _bridge(request).dispatch("tabs", {})


@get("/browser/_pull")
async def browser_pull(request: Request, wait: float = 25.0) -> Response:
    if wait > 60:
        raise ClientException(detail="wait must be <= 60 seconds")
    job = await _bridge(request).pull(wait=wait)
    if job is None:
        return Response(content=b"", status_code=204)
    return Response(
        content={"job_id": job.job_id, "op": job.op, "args": job.args},
        media_type="application/json",
    )


@post("/browser/_result")
async def browser_result(request: Request, data: ResultBody) -> dict:
    delivered = _bridge(request).deliver(data.job_id, result=data.result, error=data.error)
    return {"ok": delivered}
