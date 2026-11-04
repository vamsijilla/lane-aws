"""why a file could not be read

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-01 11:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("attachments", sa.Column("read_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("attachments", schema=None) as batch_op:
        batch_op.drop_column("read_error")
