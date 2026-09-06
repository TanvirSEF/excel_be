"""drop extra created_at index on contact_messages

Revision ID: c536d6c5858a
Revises: a574c7d7094d
Create Date: 2026-09-06 09:52:05.803502

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c536d6c5858a'
down_revision: Union[str, Sequence[str], None] = 'a574c7d7094d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index('ix_contact_messages_created_at', table_name='contact_messages')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index('ix_contact_messages_created_at', 'contact_messages', ['created_at'])
