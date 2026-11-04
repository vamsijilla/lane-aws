"""the underwriter's decision, beside Lane's recommendation

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-02 01:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "underwriter_decisions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("submission_id", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(length=128), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_underwriter_decisions_submission_id_submissions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_underwriter_decisions")),
    )
    with op.batch_alter_table("underwriter_decisions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_underwriter_decisions_submission_id"), ["submission_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("underwriter_decisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_underwriter_decisions_submission_id"))
    op.drop_table("underwriter_decisions")
