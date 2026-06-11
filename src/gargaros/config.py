from __future__ import annotations

import os
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
    mcp_args: tuple[str, ...] = ("-m", "windows_mcp", "serve", "--transport", "stdio")
    batch_max: int = 100
    # Hidden secondary desktop ("agent_dsk"): the agent acts on its own Windows desktop
    # object so the user's cursor/focus on the visible desktop are never disturbed.
    hidden_desktop_name: str = "agent_dsk"
    hidden_desktop_enabled: bool = True

    def __post_init__(self) -> None:
        if self.token_path is None:
            object.__setattr__(self, "token_path", _default_token_path())

    @property
    def allowed_hosts(self) -> set[str]:
        return {f"{self.host}:{self.port}", f"localhost:{self.port}"}


def load() -> Settings:
    name = os.environ.get("GARGAROS_HIDDEN_DESKTOP", "agent_dsk")
    raw = os.environ.get("GARGAROS_HIDDEN_DESKTOP_ENABLED", "1").strip().lower()
    return Settings(
        hidden_desktop_name=name,
        hidden_desktop_enabled=raw not in {"0", "false", "no", ""},
    )
