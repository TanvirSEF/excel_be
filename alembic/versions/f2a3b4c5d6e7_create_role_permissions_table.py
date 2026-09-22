"""create role_permissions table

Revision ID: f2a3b4c5d6e7
Revises: a7b8c9d1e2f3
Create Date: 2026-09-19 22:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'role_permissions',
        sa.Column(
            'role',
            postgresql.ENUM('super_admin', 'senior_editor', 'technical_writer', 'seo_specialist', name='user_role', create_type=False),
            nullable=False
        ),
        sa.Column('permission', sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint('role', 'permission')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('role_permissions')
