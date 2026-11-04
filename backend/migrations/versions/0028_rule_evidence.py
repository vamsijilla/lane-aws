"""rule outcomes record the facts behind details they read, fields they found absent, and
what the other date basis would ask for

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-02 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("rule_results") as batch:
        for name in ("evidence", "absent", "anchor_items"):
            batch.add_column(sa.Column(name, sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("rule_results") as batch:
        for name in ("evidence", "absent", "anchor_items"):
            batch.drop_column(name)
