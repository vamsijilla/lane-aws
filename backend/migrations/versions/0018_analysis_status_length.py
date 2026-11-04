"""analysis status holds awaiting_approval (17 characters)

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-01 11:30:00

SQLite never enforced the 16-character limit; Postgres does.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analyses", schema=None) as batch_op:
        batch_op.alter_column(
            "status", existing_type=sa.String(length=16), type_=sa.String(length=32)
        )


def downgrade() -> None:
    with op.batch_alter_table("analyses", schema=None) as batch_op:
        batch_op.alter_column(
            "status", existing_type=sa.String(length=32), type_=sa.String(length=16)
        )
