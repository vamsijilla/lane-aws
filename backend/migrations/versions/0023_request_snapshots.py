"""request snapshots (marked as sent) and when the analysis was written

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-01 22:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "request_snapshots",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("submission_id", sa.String(length=32), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("sent_by", sa.String(length=128), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_request_snapshots_submission_id_submissions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_snapshots")),
    )
    with op.batch_alter_table("request_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_request_snapshots_submission_id"), ["submission_id"], unique=False
        )
    with op.batch_alter_table("analyses", schema=None) as batch_op:
        batch_op.add_column(sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("analyses", schema=None) as batch_op:
        batch_op.drop_column("analyzed_at")
    with op.batch_alter_table("request_snapshots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_request_snapshots_submission_id"))
    op.drop_table("request_snapshots")
