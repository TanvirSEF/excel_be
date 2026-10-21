"""add visitor details to post_views

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-10-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('post_views', sa.Column('ip_address', sa.String(length=45), nullable=True))
    op.add_column('post_views', sa.Column('country', sa.String(length=64), nullable=True))
    op.add_column('post_views', sa.Column('country_code', sa.String(length=2), nullable=True))
    op.add_column('post_views', sa.Column('city', sa.String(length=100), nullable=True))
    op.add_column('post_views', sa.Column('device_type', sa.String(length=20), nullable=True))
    op.add_column('post_views', sa.Column('browser', sa.String(length=50), nullable=True))
    op.add_column('post_views', sa.Column('os', sa.String(length=50), nullable=True))
    op.create_index('ix_post_views_viewed_at', 'post_views', ['viewed_at'])
    op.create_index('ix_post_views_country_code_viewed_at', 'post_views', ['country_code', 'viewed_at'])


def downgrade() -> None:
    op.drop_index('ix_post_views_country_code_viewed_at', table_name='post_views')
    op.drop_index('ix_post_views_viewed_at', table_name='post_views')
    op.drop_column('post_views', 'os')
    op.drop_column('post_views', 'browser')
    op.drop_column('post_views', 'device_type')
    op.drop_column('post_views', 'city')
    op.drop_column('post_views', 'country_code')
    op.drop_column('post_views', 'country')
    op.drop_column('post_views', 'ip_address')
