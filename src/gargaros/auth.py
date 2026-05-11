from __future__ import annotations

from collections.abc import Iterable

from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult

PUBLIC_PATHS = frozenset({"/health", "/schema", "/schema/openapi.json", "/schema/redoc", "/schema/swagger"})


class BearerAndHostMiddleware(AbstractAuthenticationMiddleware):
    """Require a bearer token AND a Host header in the allowlist.

    Public paths (health/schema) skip both checks so agents can introspect.
    """

    def __init__(self, app, *, token: str, allowed_hosts: Iterable[str], exclude=None, scopes=None):
        super().__init__(app, exclude=exclude, scopes=scopes)
        self._token = token
        self._allowed_hosts = {h.lower() for h in allowed_hosts}

    async def authenticate_request(self, connection: ASGIConnection) -> AuthenticationResult:
        path = connection.scope.get("path", "")
        if any(path == p or path.startswith(p + "/") for p in PUBLIC_PATHS):
            return AuthenticationResult(user="anon", auth=None)

        host = connection.headers.get("host", "").lower()
        if host not in self._allowed_hosts:
            raise PermissionDeniedException(detail="Host header not allowed")

        header = connection.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise NotAuthorizedException(detail="Missing bearer token")
        presented = header[7:].strip()
        if not _constant_time_eq(presented, self._token):
            raise NotAuthorizedException(detail="Invalid bearer token")
        return AuthenticationResult(user="agent", auth=presented)


def _constant_time_eq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode(), b.encode(), strict=True):
        result |= x ^ y
    return result == 0
