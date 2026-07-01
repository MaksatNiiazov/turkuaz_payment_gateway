"""add stable payment code to 1C export queue

Revision ID: 20260630_0006
Revises: 20260630_0005
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260630_0006"
down_revision = "20260630_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "one_c_payment_exports",
        sa.Column("payment_code", sa.String(length=64), nullable=True),
    )
    op.execute(
        "UPDATE one_c_payment_exports "
        "SET payment_code = CASE paid_provider "
        "WHEN 'mkassa' THEN 'mbank' "
        "WHEN 'odengi' THEN 'obank' "
        "ELSE paid_provider END "
        "WHERE payment_code IS NULL"
    )


def downgrade() -> None:
    op.drop_column("one_c_payment_exports", "payment_code")
