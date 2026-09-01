"""create contact_messages

Revision ID: a8235522730b
Revises: a3f8c2d91b47
Create Date: 2026-09-01 21:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a8235522730b'
down_revision: Union[str, Sequence[str], None] = 'a3f8c2d91b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'contact_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('service', sa.String(50), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_contact_messages_created_at', 'contact_messages', ['created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_contact_messages_created_at', table_name='contact_messages')
    op.drop_table('contact_messages')
