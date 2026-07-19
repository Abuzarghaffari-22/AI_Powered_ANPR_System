"""initial schema baseline

Revision ID: 0001_initial
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This migration baselines the schema that was created by database_schema.sql.
    # It does NOT re-create tables — it just stamps the DB so Alembic knows the
    # current state. Run:  alembic stamp 0001_initial
    #
    # Future schema changes should be added as new revisions:
    #   alembic revision --autogenerate -m "add column X to vehicles"
    pass


def downgrade() -> None:
    # Baseline revision — nothing to downgrade
    pass
