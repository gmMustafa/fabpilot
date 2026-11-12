"""enable timescaledb and pgvector extensions

Revision ID: 0001
Revises:
Create Date: 2026-11-12 22:45:21.688712

Replaces infra/docker/postgres/init/01-extensions.sql: migrations are the single source of truth
for the schema, and they run everywhere (laptop, CI service container, production), unlike init
scripts which only run on an empty Docker volume.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF NOT EXISTS: databases created by the old init script already have them.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS timescaledb")
