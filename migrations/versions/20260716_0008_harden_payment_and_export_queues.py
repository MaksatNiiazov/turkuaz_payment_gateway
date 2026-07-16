"""harden paid invoice uniqueness and export queue indexes

Revision ID: 20260716_0008
Revises: 20260701_0007
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260716_0008"
down_revision: str | None = "20260701_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    paid_rows = connection.execute(
        sa.text(
            """
            SELECT id, external_invoice_id, paid_at, created_at, updated_at
            FROM transactions
            WHERE status = 'paid'
              AND external_invoice_id IS NOT NULL
              AND external_invoice_id <> ''
            ORDER BY external_invoice_id,
                     COALESCE(paid_at, created_at, updated_at, ''),
                     id
            """
        )
    ).mappings().all()
    paid_by_invoice: dict[str, list[dict[str, object]]] = {}
    for row in paid_rows:
        paid_by_invoice.setdefault(str(row["external_invoice_id"]), []).append(dict(row))

    tiger_winners = {
        str(row["invoice_id"]): str(row["paid_transaction_id"])
        for row in connection.execute(
            sa.text("SELECT invoice_id, paid_transaction_id FROM tiger_invoice_exports")
        ).mappings()
    }
    one_c_candidates: dict[str, list[tuple[int, str]]] = {}
    status_priority = {"success": 0, "processing": 1, "pending": 1, "error": 2}
    for row in connection.execute(
        sa.text("SELECT invoice_id, payment_id, status FROM one_c_payment_exports")
    ).mappings():
        invoice_id = str(row["invoice_id"])
        payment_id = str(row["payment_id"])
        priority = status_priority.get(str(row["status"]), 3)
        one_c_candidates.setdefault(invoice_id, []).append((priority, payment_id))

    for invoice_id, invoice_rows in paid_by_invoice.items():
        if len(invoice_rows) < 2:
            continue
        paid_ids = {str(row["id"]) for row in invoice_rows}
        winner_id = tiger_winners.get(invoice_id)
        if winner_id not in paid_ids:
            winner_id = None
        if winner_id is None:
            for _, payment_id in sorted(one_c_candidates.get(invoice_id, [])):
                if payment_id in paid_ids:
                    winner_id = payment_id
                    break
        if winner_id is None:
            winner_id = str(
                min(
                    invoice_rows,
                    key=lambda row: (
                        row["paid_at"] or row["created_at"] or row["updated_at"] or "",
                        row["id"],
                    ),
                )["id"]
            )

        for transaction_id in paid_ids - {winner_id}:
            connection.execute(
                sa.text(
                    "UPDATE transactions SET status = 'duplicate' "
                    "WHERE id = :transaction_id AND status = 'paid'"
                ),
                {"transaction_id": transaction_id},
            )

    paid_invoice_predicate = sa.text(
        "status = 'paid' AND external_invoice_id IS NOT NULL AND external_invoice_id <> ''"
    )
    op.create_index(
        "uq_transactions_paid_invoice",
        "transactions",
        ["external_invoice_id"],
        unique=True,
        if_not_exists=True,
        sqlite_where=paid_invoice_predicate,
        postgresql_where=paid_invoice_predicate,
    )
    op.create_index(
        "idx_tiger_invoice_exports_status_created",
        "tiger_invoice_exports",
        ["status", "created_at", "id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_one_c_payment_exports_status_created",
        "one_c_payment_exports",
        ["status", "created_at", "id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_one_c_payment_exports_status_created",
        table_name="one_c_payment_exports",
        if_exists=True,
    )
    op.drop_index(
        "idx_tiger_invoice_exports_status_created",
        table_name="tiger_invoice_exports",
        if_exists=True,
    )
    op.drop_index(
        "uq_transactions_paid_invoice",
        table_name="transactions",
        if_exists=True,
    )
