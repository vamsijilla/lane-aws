"""pages kept by file, triage version and page number, across submissions

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-02 02:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "page_cache",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("triage_version", sa.String(length=16), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("page", sa.JSON(), nullable=False),
        sa.Column("hidden", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_page_cache")),
    )
    with op.batch_alter_table("page_cache", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_page_cache_file_sha256"), ["file_sha256"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("page_cache", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_page_cache_file_sha256"))
    op.drop_table("page_cache")
