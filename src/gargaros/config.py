from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir


def _default_token_path() -> Path:
    return Path(user_data_dir("Gargaros", appauthor=False)) / "token"


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 7331
    token_path: Path = None  # type: ignore[assignment]
    # Spawn Windows-MCP via the current interpreter so it works regardless of whether
    # the Scripts dir is on PATH. Equivalent to `python -m windows_mcp`.
    mcp_command: str = sys.executable
    mcp_args: tuple[str, ...] = ("-m", "windows_mcp")
    batch_max: int = 100

    def __post_init__(self) -> None:
        if self.token_path is None:
            object.__setattr__(self, "token_path", _default_token_path())

    @property
    def allowed_hosts(self) -> set[str]:
        return {f"{self.host}:{self.port}", f"localhost:{self.port}"}


def load() -> Settings:
    return Settings()
