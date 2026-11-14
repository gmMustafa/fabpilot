"""work order thread_id: the agent run that produced it, as an idempotency key (5.4)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("work_orders", sa.Column("thread_id", sa.String(length=64), nullable=True))
    op.create_unique_constraint(op.f("uq_work_orders_thread_id"), "work_orders", ["thread_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("uq_work_orders_thread_id"), "work_orders", type_="unique")
    op.drop_column("work_orders", "thread_id")
