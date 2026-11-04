"""rule outcomes record the missing fact they wait on

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-02 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("rule_results") as batch:
        batch.add_column(sa.Column("waiting_on", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("rule_results") as batch:
        batch.drop_column("waiting_on")
