from __future__ import annotations

import asyncio

import pytest

from gargaros.browser_bridge import BrowserBridge


@pytest.mark.asyncio
async def test_dispatch_and_deliver():
    bridge = BrowserBridge()
    task = asyncio.create_task(bridge.dispatch("snapshot", {"x": 1}, timeout=5))
    job = await bridge.pull(wait=2)
    assert job is not None
    assert job.op == "snapshot"
    assert job.args == {"x": 1}

    delivered = bridge.deliver(job.job_id, result={"ok": True})
    assert delivered is True
    assert await task == {"ok": True}


@pytest.mark.asyncio
async def test_deliver_error_propagates():
    bridge = BrowserBridge()
    task = asyncio.create_task(bridge.dispatch("click", {"selector": "#x"}, timeout=5))
    job = await bridge.pull(wait=2)
    bridge.deliver(job.job_id, error="not found")
    with pytest.raises(RuntimeError, match="not found"):
        await task


@pytest.mark.asyncio
async def test_pull_times_out():
    bridge = BrowserBridge()
    job = await bridge.pull(wait=0.1)
    assert job is None


@pytest.mark.asyncio
async def test_dispatch_times_out_when_no_extension():
    bridge = BrowserBridge()
    with pytest.raises(asyncio.TimeoutError):
        await bridge.dispatch("snapshot", {}, timeout=0.2)


@pytest.mark.asyncio
async def test_deliver_unknown_job_returns_false():
    bridge = BrowserBridge()
    assert bridge.deliver("nonexistent", result={"x": 1}) is False
