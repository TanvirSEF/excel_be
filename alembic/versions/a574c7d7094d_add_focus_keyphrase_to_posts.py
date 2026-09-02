"""add focus keyphrase to posts

Revision ID: a574c7d7094d
Revises: a8235522730b
Create Date: 2026-09-02 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a574c7d7094d'
down_revision: Union[str, Sequence[str], None] = 'a8235522730b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('posts', sa.Column('focus_keyphrase', sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('posts', 'focus_keyphrase')
