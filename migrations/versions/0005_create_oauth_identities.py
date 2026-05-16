"""create oauth_identities + sessions.oauth_state

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-16

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider", "provider_user_id", name="uq_oauth_identities_provider_subject"
        ),
        sa.UniqueConstraint(
            "provider", "user_id", name="uq_oauth_identities_provider_user"
        ),
    )
    op.create_index(
        "ix_oauth_identities_user_id", "oauth_identities", ["user_id"]
    )

    # Carries the per-flow random ``state`` we hand to Google; the
    # callback verifies the returned state matches the row (single-use,
    # cleared on consume).
    op.add_column(
        "sessions",
        sa.Column("oauth_state", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "oauth_state")
    op.drop_index("ix_oauth_identities_user_id", table_name="oauth_identities")
    op.drop_table("oauth_identities")
