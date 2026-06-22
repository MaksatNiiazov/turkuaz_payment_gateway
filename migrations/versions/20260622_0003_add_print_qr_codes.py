"""add print qr code settings

Revision ID: 20260622_0003
Revises: 20260614_0002
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260622_0003"
down_revision = "20260614_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "print_qr_codes",
        sa.Column("code", sa.String(length=64), primary_key=True),
        sa.Column("label", sa.String(length=150), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("slot", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "idx_print_qr_codes_enabled_sort",
        "print_qr_codes",
        ["enabled", "sort_order"],
    )
    table = sa.table(
        "print_qr_codes",
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("provider", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("slot", sa.Integer),
        sa.column("sort_order", sa.Integer),
        sa.column("created_at", sa.String),
        sa.column("updated_at", sa.String),
    )
    now = "2026-06-22T00:00:00+00:00"
    op.bulk_insert(
        table,
        [
            {
                "code": "mbank",
                "label": "MBank",
                "provider": "mkassa",
                "enabled": True,
                "slot": 1,
                "sort_order": 10,
                "created_at": now,
                "updated_at": now,
            },
            {
                "code": "obank",
                "label": "О!Банк",
                "provider": "odengi",
                "enabled": True,
                "slot": 2,
                "sort_order": 20,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_print_qr_codes_enabled_sort", table_name="print_qr_codes")
    op.drop_table("print_qr_codes")
