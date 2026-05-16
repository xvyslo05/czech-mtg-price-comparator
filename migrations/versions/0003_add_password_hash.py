"""add password_hash to users

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-16

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: OAuth-only users that arrive in PR5/6 won't have a
    # password at all. The signup endpoint enforces non-null for
    # email/password users.
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_hash")
