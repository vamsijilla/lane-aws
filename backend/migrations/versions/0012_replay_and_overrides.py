"""replay fingerprints on runs and field overrides

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-31 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("versions", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("input_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("output_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("replay_of", sa.String(length=32), nullable=True))
    with op.batch_alter_table("field_resolutions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="choice")
        )
        batch_op.add_column(sa.Column("before", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("field_resolutions", schema=None) as batch_op:
        batch_op.drop_column("before")
        batch_op.drop_column("kind")
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_column("replay_of")
        batch_op.drop_column("output_hash")
        batch_op.drop_column("input_hash")
        batch_op.drop_column("versions")
