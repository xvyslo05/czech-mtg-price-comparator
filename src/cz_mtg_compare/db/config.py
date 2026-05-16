"""Database configuration loaded from environment.

The single source of truth for the connection URL. Production deployments
set ``CZ_MTG_DATABASE_URL`` to a postgres DSN; tests default to in-memory
SQLite via ``aiosqlite`` so the suite stays hermetic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DATABASE_URL_ENV = "CZ_MTG_DATABASE_URL"
DATABASE_ECHO_ENV = "CZ_MTG_DATABASE_ECHO"

# Default to in-memory SQLite. Acceptable for tests and for booting the
# server when no DB is configured yet; not a production default — the
# server should error out at first DB-dependent request instead of
# silently writing to a transient store.
_DEFAULT_URL = "sqlite+aiosqlite:///:memory:"


@dataclass(frozen=True)
class DatabaseSettings:
    url: str
    echo: bool = False

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        return cls(
            url=os.environ.get(DATABASE_URL_ENV, _DEFAULT_URL),
            echo=os.environ.get(DATABASE_ECHO_ENV, "").lower() in {"1", "true", "yes"},
        )
