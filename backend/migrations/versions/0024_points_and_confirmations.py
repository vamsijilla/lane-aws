"""request lines keyed by question; rule changes from agent documents await confirmation

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-02 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule_confirmations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("submission_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("rule_id", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("before", sa.String(length=24), nullable=False),
        sa.Column("after", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_rule_confirmations_run_id_runs")
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_rule_confirmations_submission_id_submissions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_confirmations")),
    )
    with op.batch_alter_table("rule_confirmations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_rule_confirmations_submission_id"), ["submission_id"], unique=False
        )
    with op.batch_alter_table("agent_request_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("detail_keys", sa.JSON(), server_default="[]", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_request_items", schema=None) as batch_op:
        batch_op.drop_column("detail_keys")
    with op.batch_alter_table("rule_confirmations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_rule_confirmations_submission_id"))
    op.drop_table("rule_confirmations")
