"""add external invoice id to transactions

Revision ID: 20260614_0002
Revises: 20260525_0001
Create Date: 2026-06-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260614_0002"
down_revision = "20260525_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("external_invoice_id", sa.String(length=150)))
    op.create_index(
        "idx_transactions_external_invoice_id",
        "transactions",
        ["external_invoice_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_transactions_external_invoice_id", table_name="transactions")
    op.drop_column("transactions", "external_invoice_id")
