"""add slug to users

Revision ID: b1c2d3e4f5a6
Revises: f2a3b4c5d6e7
Create Date: 2026-10-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('slug', sa.String(length=100), nullable=True))
    op.create_unique_constraint('uq_users_slug', 'users', ['slug'])
    op.create_index('ix_users_slug', 'users', ['slug'])


def downgrade() -> None:
    op.drop_index('ix_users_slug', table_name='users')
    op.drop_constraint('uq_users_slug', table_name='users', type_='unique')
    op.drop_column('users', 'slug')
