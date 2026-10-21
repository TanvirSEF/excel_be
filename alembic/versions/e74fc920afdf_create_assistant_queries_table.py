"""create assistant_queries table

Revision ID: e74fc920afdf
Revises: c1d2e3f4a5b6
Create Date: 2026-10-21 13:53:12.963794

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e74fc920afdf'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'assistant_queries',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=True),
        sa.Column('query_text', sa.Text(), nullable=False),
        sa.Column('matched_articles_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('matched_calculators_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('user_feedback', sa.String(length=16), nullable=True),
        sa.Column('client_ip', sa.String(length=45), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_assistant_queries')),
    )
    op.create_index(op.f('ix_assistant_queries_created_at'), 'assistant_queries', ['created_at'], unique=False)
    op.create_index(op.f('ix_assistant_queries_session_id'), 'assistant_queries', ['session_id'], unique=False)
    op.create_index(op.f('ix_assistant_queries_user_feedback'), 'assistant_queries', ['user_feedback'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_assistant_queries_user_feedback'), table_name='assistant_queries')
    op.drop_index(op.f('ix_assistant_queries_session_id'), table_name='assistant_queries')
    op.drop_index(op.f('ix_assistant_queries_created_at'), table_name='assistant_queries')
    op.drop_table('assistant_queries')
