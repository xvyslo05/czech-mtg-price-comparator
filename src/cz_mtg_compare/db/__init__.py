"""Database layer.

Imports are lazy because the DB stack (sqlalchemy, asyncpg, alembic) is only
installed via the ``[web]`` extra. The MCP-only install path never touches
this module.
"""

from __future__ import annotations

__all__ = [
    "Base",
    "DatabaseSettings",
    "User",
    "create_engine_from_settings",
    "get_session",
    "session_factory_from_engine",
]


def __getattr__(name: str):
    if name in {"Base", "User"}:
        from .models import Base, User

        return {"Base": Base, "User": User}[name]
    if name == "DatabaseSettings":
        from .config import DatabaseSettings

        return DatabaseSettings
    if name in {"create_engine_from_settings", "session_factory_from_engine", "get_session"}:
        from .engine import (
            create_engine_from_settings,
            get_session,
            session_factory_from_engine,
        )

        return {
            "create_engine_from_settings": create_engine_from_settings,
            "session_factory_from_engine": session_factory_from_engine,
            "get_session": get_session,
        }[name]
    raise AttributeError(name)
