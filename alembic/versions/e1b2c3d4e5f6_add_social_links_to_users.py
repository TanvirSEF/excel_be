"""add social links to users

Revision ID: e1b2c3d4e5f6
Revises: d4a9c1e7b2f6
Create Date: 2026-09-15 21:54:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd4a9c1e7b2f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('website_url', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('linkedin_url', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('twitter_url', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('github_url', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'github_url')
    op.drop_column('users', 'twitter_url')
    op.drop_column('users', 'linkedin_url')
    op.drop_column('users', 'website_url')
