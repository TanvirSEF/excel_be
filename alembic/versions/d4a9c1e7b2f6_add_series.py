"""add series

Revision ID: d4a9c1e7b2f6
Revises: c536d6c5858a
Create Date: 2026-09-14 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a9c1e7b2f6'
down_revision: Union[str, Sequence[str], None] = 'c536d6c5858a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('series',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('category_id', sa.UUID(), nullable=True),
    sa.Column('seo_title', sa.String(length=255), nullable=True),
    sa.Column('seo_description', sa.String(length=500), nullable=True),
    sa.Column('order_index', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_series_category_id_categories')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_series')),
    sa.UniqueConstraint('slug', name=op.f('uq_series_slug'))
    )
    op.create_index('ix_series_category_id', 'series', ['category_id'], unique=False)
    op.add_column('posts', sa.Column('series_id', sa.UUID(), nullable=True))
    op.add_column('posts', sa.Column('series_order', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_posts_series_id_series', 'posts', 'series', ['series_id'], ['id'])
    op.create_index('ix_posts_series_id', 'posts', ['series_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_posts_series_id', table_name='posts')
    op.drop_constraint('fk_posts_series_id_series', 'posts', type_='foreignkey')
    op.drop_column('posts', 'series_order')
    op.drop_column('posts', 'series_id')
    op.drop_index('ix_series_category_id', table_name='series')
    op.drop_table('series')
