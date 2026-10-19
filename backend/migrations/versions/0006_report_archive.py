"""Reports can be archived (hidden from lists, restorable), never deleted.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reports", sa.Column("archived_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "archived_by")
    op.drop_column("reports", "archived_at")
