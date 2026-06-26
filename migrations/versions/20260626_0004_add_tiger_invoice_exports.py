"""add tiger invoice export queue

Revision ID: 20260626_0004
Revises: 20260622_0003
Create Date: 2026-06-26 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260626_0004"
down_revision = "20260622_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tiger_invoice_exports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("invoice_id", sa.String(length=150), nullable=False),
        sa.Column("invoice_number", sa.String(length=150), nullable=True),
        sa.Column("paid_transaction_id", sa.String(length=128), nullable=False),
        sa.Column("paid_provider", sa.String(length=32), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=150), nullable=True),
        sa.Column("target_bank_code", sa.String(length=64), nullable=True),
        sa.Column("target_bank_account_code", sa.String(length=64), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("event_payload", sa.Text(), nullable=False),
        sa.Column("tiger_logical_ref", sa.String(length=128), nullable=True),
        sa.Column("tiger_fiche_no", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.String(length=64), nullable=True),
        sa.Column("exported_at", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", name="uq_tiger_invoice_exports_invoice_id"),
    )
    op.create_index(
        "idx_tiger_invoice_exports_status",
        "tiger_invoice_exports",
        ["status"],
        unique=False,
    )
    op.create_index(
        "idx_tiger_invoice_exports_updated_at",
        "tiger_invoice_exports",
        ["updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_tiger_invoice_exports_updated_at", table_name="tiger_invoice_exports")
    op.drop_index("idx_tiger_invoice_exports_status", table_name="tiger_invoice_exports")
    op.drop_table("tiger_invoice_exports")
