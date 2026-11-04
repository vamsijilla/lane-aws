"""known layouts learned from confirmed documents, with provenance

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-01 16:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "layouts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("source_document_id", sa.String(length=32), nullable=False),
        sa.Column("source_submission_id", sa.String(length=32), nullable=False),
        sa.Column("source_page_no", sa.Integer(), nullable=False),
        sa.Column("confirmed_by", sa.String(length=128), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("audits", sa.Integer(), nullable=False),
        sa.Column("agreements", sa.Integer(), nullable=False),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_layouts")),
    )
    op.create_index(op.f("ix_layouts_doc_type"), "layouts", ["doc_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_layouts_doc_type"), table_name="layouts")
    op.drop_table("layouts")
