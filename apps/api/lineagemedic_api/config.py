"""Runtime configuration, read from the environment.

Deliberately dependency-light: a small dataclass built from ``os.environ`` with
explicit defaults. Every default is chosen so that a clean checkout runs in
fixture mode without a ``.env`` file at all.

No secret is ever logged or echoed. :meth:`Settings.redacted` produces the only
representation that leaves this process, and it reports whether a token is set
without disclosing its value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Repository root, resolved from this file's location: config.py ->
#: lineagemedic_api -> api -> apps -> <root>.
REPO_ROOT = Path(__file__).resolve().parents[3]


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in _env(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Effective application configuration."""

    mode: str = field(default_factory=lambda: _env("LINEAGEMEDIC_MODE", "fixture").lower())
    datahub_gms_url: str = field(
        default_factory=lambda: _env("DATAHUB_GMS_URL", "http://localhost:8080")
    )
    datahub_frontend_url: str = field(
        default_factory=lambda: _env("DATAHUB_FRONTEND_URL", "http://localhost:9002")
    )
    datahub_token: str = field(default_factory=lambda: _env("DATAHUB_GMS_TOKEN", ""))
    mcp_server_url: str = field(
        default_factory=lambda: _env("MCP_SERVER_URL", "http://localhost:8000/mcp")
    )
    mcp_timeout_seconds: float = field(
        default_factory=lambda: float(_env("MCP_TIMEOUT_SECONDS", "30"))
    )
    llm_provider: str = field(
        default_factory=lambda: _env("LINEAGEMEDIC_LLM_PROVIDER", "deterministic").lower()
    )
    ollama_base_url: str = field(
        default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "llama3.2:3b"))
    host: str = field(default_factory=lambda: _env("LINEAGEMEDIC_API_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("LINEAGEMEDIC_API_PORT", "8000")))
    log_level: str = field(default_factory=lambda: _env("LINEAGEMEDIC_LOG_LEVEL", "INFO").upper())
    cors_origins: list[str] = field(
        default_factory=lambda: _env_list(
            "LINEAGEMEDIC_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        )
    )
    db_path: Path = field(
        default_factory=lambda: Path(
            _env("LINEAGEMEDIC_DB_PATH", str(REPO_ROOT / "data" / "healthcare.db"))
        )
    )

    @property
    def is_fixture_mode(self) -> bool:
        return self.mode != "live"

    def redacted(self) -> dict[str, object]:
        """Config safe to serialise into an API response or a log line."""
        return {
            "mode": self.mode,
            "datahub_gms_url": self.datahub_gms_url,
            "datahub_frontend_url": self.datahub_frontend_url,
            # Presence only. The value never leaves this process.
            "datahub_token_configured": bool(self.datahub_token),
            "mcp_server_url": self.mcp_server_url,
            "llm_provider": self.llm_provider,
            "ollama_model": self.ollama_model,
            "db_path": str(self.db_path),
        }


_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Clear the cache so tests can re-read a patched environment."""
    global _settings
    _settings = None
