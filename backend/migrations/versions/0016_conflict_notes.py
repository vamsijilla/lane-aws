"""notes on a conflict, such as an inference it implies

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conflicts", sa.Column("notes", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("conflicts", "notes")
