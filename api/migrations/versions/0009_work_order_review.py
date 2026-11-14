"""work order review: the decided draft, who rejected, and the reviewer's note (5.3)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "work_orders",
        sa.Column("draft", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("work_orders", sa.Column("rejected_by", sa.String(length=100), nullable=True))
    op.add_column("work_orders", sa.Column("review_note", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_work_orders_rejection_recorded"),
        "work_orders",
        "status <> 'rejected' OR (rejected_by IS NOT NULL AND review_note IS NOT NULL)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("ck_work_orders_rejection_recorded"), "work_orders", type_="check")
    op.drop_column("work_orders", "review_note")
    op.drop_column("work_orders", "rejected_by")
    op.drop_column("work_orders", "draft")
