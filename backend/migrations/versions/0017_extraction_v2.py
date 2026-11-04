"""extraction v2: per-fact verification, per-document coverage, cost and latency

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("facts", sa.Column("tier", sa.String(16)))
    op.add_column("facts", sa.Column("verified", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("facts", sa.Column("verification", sa.Text()))
    op.add_column("facts", sa.Column("second_read", sa.Text()))
    op.add_column("facts", sa.Column("reads", sa.JSON()))
    op.add_column(
        "document_extractions",
        sa.Column("auto_escalated", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column("document_extractions", sa.Column("coverage", sa.JSON()))
    op.add_column("document_extractions", sa.Column("cost_usd", sa.Double()))
    op.add_column("document_extractions", sa.Column("latency_ms", sa.Integer()))


def downgrade() -> None:
    for col in ("latency_ms", "cost_usd", "coverage", "auto_escalated"):
        op.drop_column("document_extractions", col)
    for col in ("reads", "second_read", "verification", "verified", "tier"):
        op.drop_column("facts", col)
