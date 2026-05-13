"""Global input release endpoint — last-resort cleanup the agent can call."""

from __future__ import annotations

from litestar import Request, post


@post("/input/release_all")
async def input_release_all(request: Request) -> dict:
    """Release every key and mouse button currently tracked as held."""
    state = request.app.state.input_state
    return await state.release_all()
