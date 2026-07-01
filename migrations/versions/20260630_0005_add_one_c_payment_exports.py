"""add 1C payment export queue

Revision ID: 20260630_0005
Revises: 20260626_0004
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260630_0005"
down_revision = "20260626_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "one_c_payment_exports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("payment_id", sa.String(length=128), nullable=False),
        sa.Column("invoice_id", sa.String(length=150), nullable=False),
        sa.Column("invoice_number", sa.String(length=150), nullable=True),
        sa.Column("paid_provider", sa.String(length=32), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=150), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("event_payload", sa.Text(), nullable=False),
        sa.Column("one_c_document_id", sa.String(length=150), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.String(length=64), nullable=True),
        sa.Column("exported_at", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", name="uq_one_c_payment_exports_payment_id"),
    )
    op.create_index(
        "idx_one_c_payment_exports_status",
        "one_c_payment_exports",
        ["status"],
        unique=False,
    )
    op.create_index(
        "idx_one_c_payment_exports_updated_at",
        "one_c_payment_exports",
        ["updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_one_c_payment_exports_updated_at", table_name="one_c_payment_exports")
    op.drop_index("idx_one_c_payment_exports_status", table_name="one_c_payment_exports")
    op.drop_table("one_c_payment_exports")
