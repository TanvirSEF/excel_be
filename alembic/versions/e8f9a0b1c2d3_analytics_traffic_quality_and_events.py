"""analytics traffic quality columns and events table

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-10-21 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e8f9a0b1c2d3'
down_revision: Union[str, Sequence[str], None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('analytics_sessions', sa.Column('ua_bot', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('analytics_sessions', sa.Column('is_internal', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('analytics_sessions', sa.Column('traffic_class', sa.String(length=16), server_default='unknown', nullable=False))
    op.add_column('analytics_sessions', sa.Column('class_reason', sa.String(length=120), nullable=True))
    op.add_column('analytics_sessions', sa.Column('class_confidence', sa.String(length=8), nullable=True))
    op.add_column('analytics_sessions', sa.Column('exit_path', sa.String(length=300), nullable=True))
    op.execute("UPDATE analytics_sessions SET traffic_class = CASE WHEN is_bot THEN 'suspicious' ELSE 'unknown' END")
    op.create_index('ix_analytics_sessions_class_started_at', 'analytics_sessions', ['traffic_class', 'started_at'])

    op.create_table(
        'analytics_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=32), nullable=False),
        sa.Column('path', sa.String(length=300), nullable=False),
        sa.Column('target', sa.String(length=255), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['session_id'],
            ['analytics_sessions.id'],
            name=op.f('fk_analytics_events_session_id_analytics_sessions'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_analytics_events')),
    )
    op.create_index('ix_analytics_events_session_id', 'analytics_events', ['session_id'])
    op.create_index('ix_analytics_events_name_occurred_at', 'analytics_events', ['name', 'occurred_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('analytics_events')
    op.drop_index('ix_analytics_sessions_class_started_at', table_name='analytics_sessions')
    for column in ('exit_path', 'class_confidence', 'class_reason', 'traffic_class', 'is_internal', 'ua_bot'):
        op.drop_column('analytics_sessions', column)
