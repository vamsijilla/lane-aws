"""page preparation: the original image and what was done

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-01 09:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A plain ADD COLUMN with the reference inline: rebuilding `pages` (batch mode) would break
    # the tables that point at it, and Alembic will not add a constraint in SQLite otherwise.
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE pages ADD COLUMN original_sha256 VARCHAR(64) "
            "CONSTRAINT fk_pages_original_sha256_blobs REFERENCES blobs (sha256)"
        )
    else:
        op.add_column("pages", sa.Column("original_sha256", sa.String(length=64), nullable=True))
        op.create_foreign_key(
            "fk_pages_original_sha256_blobs", "pages", "blobs", ["original_sha256"], ["sha256"]
        )
    op.add_column("pages", sa.Column("prep", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("pages", schema=None) as batch_op:
        batch_op.drop_column("prep")
        batch_op.drop_column("original_sha256")
