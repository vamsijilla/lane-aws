"""document labels set by people

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-31 19:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_labels",
        sa.Column("document_id", sa.String(length=32), nullable=False),
        sa.Column("submission_id", sa.String(length=32), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("was", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_document_labels_submission_id_submissions"),
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("pk_document_labels")),
    )
    with op.batch_alter_table("document_labels", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_document_labels_submission_id"), ["submission_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("document_labels", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_document_labels_submission_id"))
    op.drop_table("document_labels")
