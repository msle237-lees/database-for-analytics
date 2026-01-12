"""
@file config.py
@brief Environment-based configuration loader.

Loads settings from .env at the project root (if present) and environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def _find_project_root(start: Path) -> Path:
    """
    @brief Walk upward until a project root marker is found.
    @param start Starting directory.
    @return Project root directory.

    Root markers: pyproject.toml, docker-compose.yml, or .env
    """
    current = start
    while True:
        if (current / "pyproject.toml").exists():
            return current
        if (current / "docker-compose.yml").exists():
            return current
        if (current / ".env").exists():
            return current

        if current.parent == current:
            return start
        current = current.parent


_THIS_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _find_project_root(_THIS_FILE.parent)
_ENV_PATH = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


def _get_env(name: str, default: Optional[str] = None) -> str:
    """
    @brief Read a required environment variable.
    @param name Environment variable name.
    @param default Optional default.
    @return Value.
    @throws RuntimeError If missing and default not provided.
    """
    val = os.getenv(name, default)
    if val is None or val == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def _get_env_int(name: str, default: int) -> int:
    """
    @brief Read an integer environment variable.
    @param name Environment variable name.
    @param default Default integer if missing.
    @return Parsed integer.
    @throws RuntimeError If present but not parseable.
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Invalid int for env var {name}: {raw}") from e


@dataclass(frozen=True)
class Settings:
    """
    @brief Immutable application settings loaded from env vars.
    """

    app_name: str
    app_env: str

    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_driver: str
    db_encrypt: str
    db_trust_server_cert: str
    db_timeout: int


def get_settings() -> Settings:
    """
    @brief Build Settings from environment variables.
    @return Settings object.
    """
    return Settings(
        app_name=os.getenv("APP_NAME", "generic-fastapi-sqlserver"),
        app_env=os.getenv("APP_ENV", "dev"),
        db_host=_get_env("DB_HOST"),
        db_port=_get_env_int("DB_PORT", 1433),
        db_name=_get_env("DB_NAME"),
        db_user=_get_env("DB_USER"),
        db_password=_get_env("DB_PASSWORD"),
        db_driver=os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
        db_encrypt=os.getenv("DB_ENCRYPT", "no"),
        db_trust_server_cert=os.getenv("DB_TRUST_SERVER_CERT", "yes"),
        db_timeout=_get_env_int("DB_TIMEOUT", 5),
    )


settings = get_settings()

