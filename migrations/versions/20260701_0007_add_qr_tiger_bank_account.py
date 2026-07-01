"""add Tiger bank account mapping to printable QR codes

Revision ID: 20260701_0007
Revises: 20260630_0006
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0007"
down_revision: str | None = "20260630_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "print_qr_codes",
        sa.Column("tiger_bank_account_code", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("print_qr_codes", "tiger_bank_account_code")
