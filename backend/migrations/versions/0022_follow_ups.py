"""follow-up emails: how a submission relates to an earlier one, the email behind each run, and
OCR cached by file, page and engine

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-01 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ocr_cache",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column("words", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_ocr_cache")),
    )
    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("related_to_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("relation", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("relation_how", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("relation_reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("relation_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("relation_by", sa.String(length=64), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_submissions_related_to_id"), ["related_to_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_submissions_related_to_id_submissions"),
            "submissions",
            ["related_to_id"],
            ["id"],
        )
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_runs_email_id_email_messages"), "email_messages", ["email_id"], ["id"]
        )


def downgrade() -> None:
    op.drop_table("ocr_cache")
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_runs_email_id_email_messages"), type_="foreignkey")
        batch_op.drop_column("email_id")
    with op.batch_alter_table("submissions", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_submissions_related_to_id_submissions"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_submissions_related_to_id"))
        for col in (
            "relation_by",
            "relation_at",
            "relation_reason",
            "relation_how",
            "relation",
            "related_to_id",
        ):
            batch_op.drop_column(col)
