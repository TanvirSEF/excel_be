"""add is_trending_pinned to posts

Revision ID: a7b8c9d1e2f3
Revises: e1b2c3d4e5f6
Create Date: 2026-09-16 11:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b8c9d1e2f3'
down_revision: Union[str, Sequence[str], None] = 'e1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('posts', sa.Column('is_trending_pinned', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('posts', 'is_trending_pinned')
